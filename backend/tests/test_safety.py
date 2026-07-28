"""Tests for the safety rule and queue ordering.

These cover the second success criterion — every safety-keyword report surfaces
at the top — and the property that makes it hold: the rule is deterministic and
runs after the model, so no classification the model returns can suppress it.
"""
from __future__ import annotations

import pytest

from app.safety import (
    SAFETY_CRITICAL,
    URGENCY_LEVELS,
    apply_safety_override,
    scan_for_safety_signals,
    sort_proposals,
    triage_sort_key,
)

# --------------------------------------------------------------------------- #
# Reports that must be flagged
# --------------------------------------------------------------------------- #
INJURY_REPORTS = [
    ("someone was hurt", "Operator hurt his wrist on the return stroke."),
    ("an injury", "There was an injury on line 3 this morning."),
    ("a burn", "One of the techs caught a minor burn on his forearm."),
    ("a laceration", "Laceration to the hand while clearing the infeed."),
    ("first aid", "Took him to first aid after the guard swung shut."),
    ("a near miss", "Near miss on the press — the ram came down early."),
    ("nearly caught", "He nearly got his sleeve caught in the drive."),
    ("a pinch point", "The pinch point on the case erector is exposed."),
    ("a crush hazard", "Load could crush someone if the strap lets go."),
    ("a missing guard", "The guard is missing from the chain drive."),
    ("a bypassed interlock", "The interlock has been bypassed with a jumper."),
    ("a light curtain fault", "Light curtain on press 4 is not stopping the ram."),
    ("an e-stop fault", "Emergency stop on the mixer does not cut power."),
    ("lockout/tagout", "No lockout point on the new conveyor drive."),
    ("an exposed conductor", "There is an exposed wire behind the panel door."),
    ("arc flash", "Arc flash from the breaker when the line starts."),
    ("sparking", "You can see sparks coming off the lower breaker."),
    ("an electric shock", "Operator got a shock off the guard rail."),
    ("a gas leak", "Ammonia leak in the chiller room."),
    ("a chemical spill", "Chemical spill by the wash bay."),
    ("fumes", "Fumes coming off the curing oven, no extraction."),
    ("a failed ventilation system", "Bay ventilation is not running."),
    ("a burning smell", "Burning smell coming off panel E3."),
    ("smoke", "Smoke from the motor housing on the extractor."),
    ("a steam leak", "Steam leak blowing across the walkway."),
    ("a hot surface", "Hot vapour venting at head height by the boiler."),
    ("reaching into a machine", "Operators are reaching in to clear jams."),
]


@pytest.mark.parametrize("label,description", INJURY_REPORTS,
                         ids=[label for label, _ in INJURY_REPORTS])
def test_injury_risk_is_flagged(label: str, description: str) -> None:
    assert scan_for_safety_signals(description), (
        f"{label!r} should raise a safety signal: {description!r}"
    )


# --------------------------------------------------------------------------- #
# Reports that must NOT be flagged
#
# These are the false positives that would matter most: ordinary machine-shop
# language that happens to contain a word from a naive safety word list. A
# dashboard that flagged these would train the lead to ignore the flag.
# --------------------------------------------------------------------------- #
BENIGN_REPORTS = [
    ("cutting is the machine's job", "The mill is cutting oversize on the X axis."),
    ("a cut part", "Cut parts are coming off the saw with a burr."),
    ("a falling temperature", "Barrel temperature is falling on zone 3."),
    ("a shock absorber", "The shock absorber on the tailstock needs replacing."),
    ("a guard rail bolt", "Cover plate over the infeed has a loose bolt."),
    ("a calibration drift", "Mill 12 is drifting 0.04 mm over a long program."),
    ("a stopped line", "Drive motor is humming but the belt will not move."),
    ("low coolant", "Coolant level is low on lathe 3, top up next PM round."),
    ("a filter change", "Dust extraction filter indicator is showing amber."),
    ("a squeak", "Chip conveyor on mill 12 is squeaking, needs lubrication."),
    ("crooked labels", "Label applicator is putting labels on crooked."),
    ("a robot fault", "Welder 2 is faulting on a tool centre point error."),
]


@pytest.mark.parametrize("label,description", BENIGN_REPORTS,
                         ids=[label for label, _ in BENIGN_REPORTS])
def test_ordinary_faults_are_not_flagged(label: str, description: str) -> None:
    signals = scan_for_safety_signals(description)
    assert not signals, (
        f"{label!r} should not raise a safety signal, got "
        f"{[s.label for s in signals]}: {description!r}"
    )


def test_signals_are_deduplicated_per_label() -> None:
    decision = apply_safety_override(
        "routine", "Burn risk. Another burn risk. A third burn risk."
    )
    labels = [s.label for s in decision.signals]
    assert labels.count("burn") == 1


# --------------------------------------------------------------------------- #
# The override itself
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("model_urgency", URGENCY_LEVELS)
def test_override_wins_regardless_of_what_the_model_said(model_urgency: str) -> None:
    """No classification the model returns can bury an injury-risk report."""
    decision = apply_safety_override(
        model_urgency,
        "Line output is fine, but the guard is missing and operators reach in.",
    )
    assert decision.urgency == SAFETY_CRITICAL
    assert decision.model_urgency == model_urgency
    assert decision.overridden is (model_urgency != SAFETY_CRITICAL)


def test_override_never_lowers_urgency() -> None:
    decision = apply_safety_override("production_stopping", "Conveyor belt slipping.")
    assert decision.urgency == "production_stopping"
    assert decision.overridden is False
    assert decision.signals == ()


def test_unknown_model_urgency_degrades_to_routine_not_to_a_crash() -> None:
    decision = apply_safety_override("VERY URGENT!!", "Coolant is low on lathe 3.")
    assert decision.urgency == "routine"
    assert decision.model_urgency == "routine"


def test_unknown_model_urgency_still_gets_the_safety_override() -> None:
    decision = apply_safety_override("nonsense", "Exposed wire behind the panel.")
    assert decision.urgency == SAFETY_CRITICAL
    assert decision.overridden is True


def test_empty_description_is_not_flagged() -> None:
    assert scan_for_safety_signals("") == []


# --------------------------------------------------------------------------- #
# Ordering — the success criterion
# --------------------------------------------------------------------------- #
def _proposal(number: str, urgency: str, reported_at: str) -> dict:
    return {
        "work_order_number": number,
        "urgency": urgency,
        "reported_at": reported_at,
    }


def test_safety_sorts_above_everything_even_when_it_is_the_oldest() -> None:
    """The whole point: an old safety report still beats a fresh routine one."""
    queue = [
        _proposal("WO-1", "routine", "2026-07-28T11:55:00"),
        _proposal("WO-2", "production_stopping", "2026-07-28T11:50:00"),
        _proposal("WO-3", "safety_critical", "2026-07-28T06:00:00"),
        _proposal("WO-4", "routine", "2026-07-28T11:59:00"),
        _proposal("WO-5", "safety_critical", "2026-07-28T07:30:00"),
    ]
    ordered = [p["work_order_number"] for p in sort_proposals(queue)]
    assert ordered == ["WO-3", "WO-5", "WO-2", "WO-1", "WO-4"]


def test_every_safety_row_precedes_every_non_safety_row() -> None:
    queue = [
        _proposal(f"WO-{i}", level, f"2026-07-28T{i:02d}:00:00")
        for i, level in enumerate(
            ["routine", "safety_critical", "production_stopping",
             "routine", "safety_critical", "production_stopping"],
            start=1,
        )
    ]
    ordered = sort_proposals(queue)
    urgencies = [p["urgency"] for p in ordered]
    last_safety = max(
        i for i, u in enumerate(urgencies) if u == "safety_critical"
    )
    first_other = min(
        i for i, u in enumerate(urgencies) if u != "safety_critical"
    )
    assert last_safety < first_other


def test_ties_break_on_oldest_report_first() -> None:
    queue = [
        _proposal("WO-B", "routine", "2026-07-28T09:00:00"),
        _proposal("WO-A", "routine", "2026-07-28T08:00:00"),
    ]
    assert [p["work_order_number"] for p in sort_proposals(queue)] == ["WO-A", "WO-B"]


def test_missing_or_unparseable_fields_sort_last_without_raising() -> None:
    queue = [
        {"work_order_number": "WO-BAD"},
        _proposal("WO-OK", "routine", "2026-07-28T08:00:00"),
        _proposal("WO-SAFE", "safety_critical", "2026-07-28T08:00:00"),
    ]
    ordered = [p.get("work_order_number") for p in sort_proposals(queue)]
    assert ordered[0] == "WO-SAFE"
    assert ordered[-1] == "WO-BAD"


def test_sort_key_ranks_an_unknown_urgency_below_the_known_levels() -> None:
    known = triage_sort_key(_proposal("WO-1", "routine", "2026-07-28T08:00:00"))
    unknown = triage_sort_key(_proposal("WO-2", "mystery", "2026-07-28T08:00:00"))
    assert unknown[0] > known[0]
