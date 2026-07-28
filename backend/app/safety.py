"""The safety rule — deterministic, and deliberately not the model's job.

Plant rule: *anything mentioning injury risk is safety-critical.* That is a rule,
not a judgement call, so it is implemented as one. The model classifies urgency,
and then this module overrides it: if an operator's description contains a signal
of injury risk, the work order becomes `safety_critical` regardless of what the
model decided. The override only ever raises urgency, never lowers it.

Two consequences worth being explicit about:

  * A model that misses a hazard cannot downgrade a safety report. The lexicon
    catches it independently.
  * A model that is prompt-injected, confused, or simply having a bad day cannot
    bury a hazard either — the override runs after the model, on the raw operator
    text, and the queue is then ordered by urgency.

The lexicon is intentionally tuned for a machine shop. Ambiguous words that are
routine in this setting ("cut", "fall", "guard") are only matched in phrasings
that actually indicate a hazard, so the queue does not fill with false alarms
that teach the maintenance lead to ignore the safety flag.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable

#: Urgency levels, most severe first. Index order *is* the triage priority.
URGENCY_LEVELS = ("safety_critical", "production_stopping", "routine")

URGENCY_RANK = {level: rank for rank, level in enumerate(URGENCY_LEVELS)}

SAFETY_CRITICAL = "safety_critical"

# --------------------------------------------------------------------------- #
# The lexicon: (pattern, category, human-readable label)
# --------------------------------------------------------------------------- #
_PATTERNS: tuple[tuple[str, str, str], ...] = (
    # ---- Someone has already been hurt ----
    (r"\binjur(?:y|ies|ed|ing)\b",                 "injury_reported", "injury"),
    (r"\bhurt\b",                                  "injury_reported", "someone hurt"),
    (r"\blacerat(?:ion|ions|ed)\b",                "injury_reported", "laceration"),
    (r"\bamputat(?:ion|ed|e)\b",                   "injury_reported", "amputation"),
    (r"\bfractur(?:e|ed|es)\b",                    "injury_reported", "fracture"),
    (r"\bbleed(?:ing|s)?\b",                       "injury_reported", "bleeding"),
    # Suffix group is optional so a bare "burn" matches; "burning" deliberately
    # does not (it lands in fire_hazard as "burning smell" instead).
    (r"\bburn(?:s|ed|t)?\b",                       "injury_reported", "burn"),
    (r"\bcut\s+(?:his|her|their|my|the\s+\w+'?s?)\s+\w+",
                                                   "injury_reported", "cut injury"),
    (r"\bfirst[\s-]?aid\b",                        "injury_reported", "first aid"),
    (r"\bmedical attention\b",                     "injury_reported", "medical attention"),

    # ---- Someone nearly was ----
    (r"\bnear[\s-]?miss\b",                        "injury_hazard", "near miss"),
    (r"\b(?:nearly|almost)\s+(?:got|had|caught|lost|hit|struck|fell)\b",
                                                   "injury_hazard", "near miss"),
    (r"\bcaught\s+(?:in|between|on)\b",             "injury_hazard", "caught in machinery"),
    (r"\bcrush(?:ed|ing|es)?\b",                   "injury_hazard", "crush hazard"),
    (r"\bpinch[\s-]?point\b",                      "injury_hazard", "pinch point"),
    (r"\b(?:pinned|trapped)\b",                    "injury_hazard", "entrapment"),
    (r"\b(?:struck|hit)\s+by\b",                   "injury_hazard", "struck by"),
    (r"\bfalling\s+(?:object|load|debris|part|tool)\w*\b",
                                                   "injury_hazard", "falling object"),
    (r"\boverhead\s+load\b",                       "injury_hazard", "overhead load"),
    (r"\bfell\s+(?:from|off|on|onto|into|against)\b",
                                                   "injury_hazard", "fall"),
    (r"\bfall\s+hazard\b",                         "injury_hazard", "fall hazard"),
    (r"\btrip(?:ping)?\s+hazard\b",                "injury_hazard", "trip hazard"),
    (r"\bslip(?:pery|ping)?\s+(?:hazard|floor|surface)\b",
                                                   "injury_hazard", "slip hazard"),
    (r"\breach(?:ing|es|ed)?\s+in(?:to)?\b",       "injury_hazard", "reaching into machine"),
    (r"\bscald(?:s|ed|ing)?\b",                    "injury_hazard", "scald hazard"),
    (r"\bsteam\s+leak\b",                          "injury_hazard", "steam leak"),
    (r"\bhot\s+(?:surface|vapou?r|steam|metal|oil|water)\b",
                                                   "injury_hazard", "hot surface"),
    (r"\b(?:unsafe|dangerous)\b",                  "injury_hazard", "described as unsafe"),

    # ---- Protective systems defeated ----
    (r"\bguard\w*\b[^.!?]{0,40}\b(?:missing|removed|off|broken|damaged|open|"
     r"bypass\w*|disabled|defeat\w*)\b",           "guarding_defeated", "guard defeated"),
    (r"\b(?:missing|removed|broken|damaged|no)\s+(?:\w+\s+){0,2}guard\w*\b",
                                                   "guarding_defeated", "guard defeated"),
    (r"\blight\s+curtain\b[^.!?]{0,60}\b(?:not|no longer|fail\w*|isn'?t|"
     r"does\s?n[o']?t|won'?t|bypass\w*|disabled)\b",
                                                   "guarding_defeated", "light curtain fault"),
    (r"\binterlock\w*\b[^.!?]{0,40}\b(?:bypass\w*|jumper|disabled|defeat\w*|"
     r"removed|broken|not working)\b",             "guarding_defeated", "interlock bypassed"),
    (r"\bbypass\w*\b[^.!?]{0,30}\b(?:guard\w*|interlock\w*|safety|e-?stop)\b",
                                                   "guarding_defeated", "safety device bypassed"),
    (r"\b(?:e-?stop|emergency stop)\b[^.!?]{0,40}\b(?:not|fail\w*|does\s?n[o']?t|"
     r"won'?t|broken|disabled|stuck)\b",           "guarding_defeated", "e-stop fault"),
    (r"\block[\s-]?out|\btag[\s-]?out\b|\bLOTO\b", "guarding_defeated", "lockout/tagout"),

    # ---- Uncontrolled energy ----
    (r"\bexposed\s+(?:live\s+)?(?:wire|wiring|conductor|terminal|busbar|cable)\w*\b",
                                                   "energy_hazard", "exposed conductor"),
    (r"\blive\s+(?:wire|wiring|conductor|terminal|circuit|panel)\w*\b",
                                                   "energy_hazard", "live conductor"),
    (r"\barc\s+flash\b",                           "energy_hazard", "arc flash"),
    (r"\belectrocut(?:ion|ed|e)\b",                "energy_hazard", "electrocution"),
    (r"\bshock(?:ed|s)?\b(?!\s+absorber)",         "energy_hazard", "electric shock"),
    (r"\bspark(?:s|ed|ing)?\b",                    "energy_hazard", "sparking"),
    (r"\bhigh\s+voltage\b",                        "energy_hazard", "high voltage"),
    (r"\bstored\s+energy\b",                       "energy_hazard", "stored energy"),

    # ---- Chemical / atmosphere ----
    (r"\b(?:gas|ammonia|refrigerant|nitrogen|propane|acetylene|coolant|hydraulic)"
     r"\s+leak\b",                                 "chemical_hazard", "hazardous leak"),
    (r"\bchemical\s+(?:spill|leak|exposure|burn)\b",
                                                   "chemical_hazard", "chemical release"),
    (r"\b(?:acid|caustic|solvent)\b",              "chemical_hazard", "hazardous substance"),
    (r"\bfume\w*\b|\btoxic\b|\basphyxiat\w*\b",    "chemical_hazard", "toxic atmosphere"),
    (r"\bventilation\b[^.!?]{0,30}\b(?:not\s+(?:running|working)|off|failed|down)\b",
                                                   "chemical_hazard", "ventilation failure"),
    (r"\bbattery\b[^.!?]{0,40}\b(?:leak\w*|crack\w*|spill\w*|swollen|venting)\b",
                                                   "chemical_hazard", "battery leak"),

    # ---- Fire ----
    (r"\bburning\s+smell\b|\bsmell\w*\s+(?:of|like)\s+burning\b",
                                                   "fire_hazard", "burning smell"),
    (r"\bsmok(?:e|ing)\b",                         "fire_hazard", "smoke"),
    (r"\bfire\b|\bflame\w*\b",                     "fire_hazard", "fire"),
    (r"\boverheat(?:ed|ing|s)?\b",                 "fire_hazard", "overheating"),
)

_COMPILED = tuple(
    (re.compile(pattern, re.IGNORECASE), category, label)
    for pattern, category, label in _PATTERNS
)

#: Human-readable names for the categories, used in the dashboard.
CATEGORY_LABELS = {
    "injury_reported": "Injury reported",
    "injury_hazard": "Injury risk",
    "guarding_defeated": "Guarding defeated",
    "energy_hazard": "Uncontrolled energy",
    "chemical_hazard": "Chemical or atmosphere",
    "fire_hazard": "Fire risk",
}


@dataclass(frozen=True)
class SafetySignal:
    """One piece of evidence that a report involves injury risk."""

    category: str
    label: str
    matched_text: str

    def as_dict(self) -> dict[str, str]:
        return {
            "category": self.category,
            "category_label": CATEGORY_LABELS.get(self.category, self.category),
            "label": self.label,
            "matched_text": self.matched_text,
        }


@dataclass(frozen=True)
class SafetyDecision:
    """The outcome of applying the safety rule to one work order."""

    urgency: str
    signals: tuple[SafetySignal, ...]
    #: True when the rule raised the urgency above what the model proposed.
    overridden: bool
    #: What the model said, kept for the audit trail and the dashboard.
    model_urgency: str

    @property
    def is_safety_critical(self) -> bool:
        return self.urgency == SAFETY_CRITICAL


def scan_for_safety_signals(text: str) -> list[SafetySignal]:
    """Find every injury-risk signal in an operator's description.

    Returns one signal per distinct (category, label) pair, so a description that
    says "burn" three times contributes one signal, not three.
    """
    if not text:
        return []

    signals: list[SafetySignal] = []
    seen: set[tuple[str, str]] = set()

    for pattern, category, label in _COMPILED:
        match = pattern.search(text)
        if match is None:
            continue
        key = (category, label)
        if key in seen:
            continue
        seen.add(key)
        signals.append(
            SafetySignal(
                category=category,
                label=label,
                matched_text=match.group(0).strip(),
            )
        )

    return signals


def apply_safety_override(model_urgency: str, description: str) -> SafetyDecision:
    """Apply the plant safety rule on top of the model's classification.

    Any description carrying injury risk becomes safety-critical. An urgency the
    model returned that is not a recognised level is treated as `routine`, so a
    malformed response degrades into "needs a human", never into "urgent work
    silently dropped".
    """
    normalised = model_urgency if model_urgency in URGENCY_RANK else "routine"
    signals = scan_for_safety_signals(description)

    if signals:
        return SafetyDecision(
            urgency=SAFETY_CRITICAL,
            signals=tuple(signals),
            overridden=normalised != SAFETY_CRITICAL,
            model_urgency=normalised,
        )

    return SafetyDecision(
        urgency=normalised,
        signals=(),
        overridden=False,
        model_urgency=normalised,
    )


def _reported_at_key(value: Any) -> str:
    """Sort key for report time; oldest first, unparseable values last."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str) and value:
        return value
    return "9999"


def triage_sort_key(proposal: dict) -> tuple[int, str, str]:
    """Order the queue: urgency first, then oldest report, then work order number.

    Because `safety_critical` is rank 0, every safety report sorts above every
    production-stopping or routine one — including reports that arrived hours
    earlier. Time only breaks ties within a band.
    """
    urgency = proposal.get("urgency", "routine")
    return (
        URGENCY_RANK.get(urgency, len(URGENCY_LEVELS)),
        _reported_at_key(proposal.get("reported_at")),
        str(proposal.get("work_order_number", "")),
    )


def sort_proposals(proposals: Iterable[dict]) -> list[dict]:
    """Return the queue in triage order."""
    return sorted(proposals, key=triage_sort_key)
