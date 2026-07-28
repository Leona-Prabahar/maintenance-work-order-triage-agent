import { useEffect, useMemo, useRef, useState } from "react";
import { getHealth, postAssignment, postTriage } from "./api";
import type {
  AssignmentRequest,
  Health,
  TriageProposal,
  TriageResponse,
  Urgency,
} from "./types";
import { URGENCY_ORDER } from "./types";
import { URGENCY_META, formatTimestamp } from "./urgency";
import AssignedCard from "./components/AssignedCard";
import SummaryBar, { type JumpTarget } from "./components/SummaryBar";
import TriageRow from "./components/TriageRow";

const LEAD_STORAGE_KEY = "triage.lead";

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [data, setData] = useState<TriageResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lead, setLead] = useState(
    () => localStorage.getItem(LEAD_STORAGE_KEY) ?? ""
  );

  // Scroll targets for the summary chips: one per urgency band, plus the single
  // consolidated assigned section at the foot of the board.
  const bandRefs = useRef<Partial<Record<Urgency, HTMLElement | null>>>({});
  const assignedRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    getHealth().then(setHealth).catch(() => setHealth(null));
  }, []);

  useEffect(() => {
    localStorage.setItem(LEAD_STORAGE_KEY, lead);
  }, [lead]);

  async function runTriage() {
    setBusy(true);
    setError(null);
    try {
      const fresh = await postTriage();
      setData((prev) => {
        if (prev === null) return fresh;
        // A re-run asks the backend for open work only, so anything approved in
        // this session would vanish from the board. Carry those rows over so
        // "assigned this session" stays true across re-runs.
        const carried = prev.proposals.filter((p) => p.assignment !== null);
        const returned = new Set(fresh.proposals.map((p) => p.work_order_id));
        return {
          ...fresh,
          proposals: [
            ...fresh.proposals,
            ...carried.filter((p) => !returned.has(p.work_order_id)),
          ],
        };
      });
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  /**
   * Handle one approval.
   *
   * The proposal is updated in place with the assignment the backend confirmed.
   * Every count and every section on the board is derived from this one array,
   * so the row leaves the awaiting-approval list, joins the assigned band of its
   * original category, and both chips re-count in the same render — with no
   * refetch and no page reload.
   *
   * The row is only marked assigned once the backend has confirmed the write, so
   * an optimistic UI can never make it look like something was dispatched when
   * it wasn't.
   */
  async function handleApprove(body: AssignmentRequest) {
    const response = await postAssignment(body);
    if (response.assignment === null) {
      // Shouldn't happen — the backend only reports ok with an assignment — but
      // failing loudly beats leaving the row in a state that is neither
      // awaiting approval nor assigned.
      throw new Error(
        "The write was reported as successful but returned no assignment. " +
          "Re-run triage to check whether it was recorded."
      );
    }
    setData((prev) =>
      prev === null
        ? prev
        : {
            ...prev,
            proposals: prev.proposals.map((p) =>
              p.work_order_id === body.work_order_id
                ? { ...p, awaiting_approval: false, assignment: response.assignment }
                : p
            ),
          }
    );
  }

  function jumpTo(target: JumpTarget) {
    const reduceMotion = window.matchMedia(
      "(prefers-reduced-motion: reduce)"
    ).matches;
    const element =
      target === "assigned" ? assignedRef.current : bandRefs.current[target];

    element?.scrollIntoView({
      behavior: reduceMotion ? "auto" : "smooth",
      block: "start",
    });
  }

  const blockedReason = useMemo(() => {
    if (health && !health.approval_gate_configured) {
      return "The approval gate is not configured, so no assignment can be written.";
    }
    if (lead.trim().length < 2) {
      return "Enter your name before approving — it is recorded on the assignment.";
    }
    return null;
  }, [health, lead]);

  /**
   * The urgency bands hold only work still awaiting a decision. A band is still
   * rendered once it is cleared, so long as something in it was assigned this
   * session — "all clear" is worth seeing, and it keeps the band's summary chip
   * pointing at something.
   */
  const bands = useMemo(() => {
    const proposals = data?.proposals ?? [];
    return URGENCY_ORDER.map((level) => ({
      level,
      awaiting: proposals.filter(
        (p) => p.urgency === level && p.assignment === null
      ),
      assignedCount: proposals.filter(
        (p) => p.urgency === level && p.assignment !== null
      ).length,
    })).filter((band) => band.awaiting.length > 0 || band.assignedCount > 0);
  }, [data]);

  /**
   * Everything approved this session, in one section at the foot of the board.
   *
   * Keeping them together — rather than one assigned group per band — is what
   * makes the "assigned this session" chip able to reach all of them. Each card
   * carries its own category, since position no longer implies it.
   *
   * Ordered by the category the work order was *triaged* into, then by approval
   * time, so the section reads in the same priority order as the board above it.
   */
  const assigned = useMemo(() => {
    const proposals = data?.proposals ?? [];
    return proposals
      .filter((p) => p.assignment !== null)
      .sort(
        (a, b) =>
          URGENCY_ORDER.indexOf(a.urgency) - URGENCY_ORDER.indexOf(b.urgency) ||
          (a.assignment?.assigned_at ?? "").localeCompare(
            b.assignment?.assigned_at ?? ""
          )
      );
  }, [data]);

  return (
    <div className="mx-auto min-h-full max-w-5xl px-4 pb-16">
      {/* ---- Header ---- */}
      <header className="py-6">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h1 className="text-2xl font-bold text-slate-800">
              Maintenance Triage Desk
            </h1>
            <p className="mt-1 max-w-2xl text-sm text-slate-500">
              The agent reads the incoming work order queue, classifies urgency and
              proposes a crew.{" "}
              <span className="font-semibold text-slate-700">
                Nothing is dispatched until you approve it.
              </span>{" "}
              Reports that mention injury risk are raised to safety-critical
              automatically and sorted to the top.
            </p>
          </div>

          <div className="flex items-end gap-2">
            <label className="block">
              <span className="mb-1 block text-xs font-medium text-slate-600">
                Maintenance lead
              </span>
              <input
                value={lead}
                onChange={(e) => setLead(e.target.value)}
                placeholder="your name"
                className="w-44 rounded-md border border-slate-300 bg-white px-2.5 py-1.5 text-sm"
              />
            </label>
            <button
              type="button"
              onClick={runTriage}
              disabled={busy}
              className="rounded-md bg-brand-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-brand-700 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {busy ? "Triaging…" : data ? "Re-run triage" : "Run triage"}
            </button>
          </div>
        </div>
      </header>

      {/* ---- Warnings ---- */}
      {health && !health.approval_gate_configured && (
        <div className="mb-4 rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          <span className="font-semibold">Approval gate not configured.</span>{" "}
          APPROVAL_SIGNING_SECRET is unset, so the write tool will refuse every
          assignment. Set it on both the backend and the MCP server.
        </div>
      )}

      {error && (
        <div className="mb-4 rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800">
          {error}
        </div>
      )}

      {data && (
        <div className="mb-4">
          <SummaryBar
            proposals={data.proposals}
            health={health}
            onJump={jumpTo}
          />
        </div>
      )}

      {data?.notes.map((note) => (
        <div
          key={note}
          className="mb-2 rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm text-slate-600"
        >
          {note}
        </div>
      ))}

      {/* ---- Empty state ---- */}
      {!data && !busy && (
        <div className="rounded-xl border border-dashed border-slate-300 bg-white p-8 text-center">
          <p className="text-sm text-slate-600">
            Run triage to pull the open work order queue and see proposals.
          </p>
          <p className="mt-1 text-xs text-slate-400">
            Reading the queue writes nothing. Assignments happen only when you
            click Approve on a row.
          </p>
        </div>
      )}

      {busy && !data && (
        <div className="rounded-xl border border-slate-200 bg-white p-8 text-center text-sm text-slate-500">
          Reading the queue and classifying…
        </div>
      )}

      {/* ---- The queue, safety first ---- */}
      <div className="space-y-6">
        {bands.map(({ level, awaiting }) => (
          <section
            key={level}
            ref={(el) => {
              bandRefs.current[level] = el;
            }}
            className="scroll-mt-4"
          >
            <div
              className={`mb-2 flex items-baseline gap-2 rounded-lg border px-3 py-1.5 ${URGENCY_META[level].sectionClass}`}
            >
              <h2 className="text-sm font-bold uppercase tracking-wide">
                {URGENCY_META[level].label}
              </h2>
              <span className="text-xs font-medium opacity-80">
                {awaiting.length}{" "}
                {awaiting.length === 1 ? "work order" : "work orders"} ·{" "}
                {URGENCY_META[level].blurb}
              </span>
            </div>

            <div className="space-y-3">
              {awaiting.map((proposal: TriageProposal) => (
                <TriageRow
                  key={proposal.work_order_id}
                  proposal={proposal}
                  crews={data?.crews ?? []}
                  approvedBy={lead}
                  blockedReason={blockedReason}
                  onApprove={handleApprove}
                />
              ))}

              {awaiting.length === 0 && (
                <p className="rounded-lg border border-dashed border-slate-300 bg-white px-4 py-3 text-sm text-slate-500">
                  All clear — everything in this band has been assigned. See
                  Assigned this session at the foot of the board.
                </p>
              )}
            </div>
          </section>
        ))}
      </div>

      {/* ---- Everything approved this session, in one place ---- */}
      {assigned.length > 0 && (
        <section ref={assignedRef} className="mt-6 scroll-mt-4">
          <div className="mb-2 flex flex-wrap items-baseline gap-2 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-1.5 text-emerald-800">
            <h2 className="text-sm font-bold uppercase tracking-wide">
              Assigned this session
            </h2>
            <span className="text-xs font-medium opacity-80">
              {assigned.length}{" "}
              {assigned.length === 1 ? "work order" : "work orders"} · each one
              written only after you approved it
            </span>
          </div>

          <div className="space-y-2">
            {assigned.map((proposal: TriageProposal) => (
              <AssignedCard key={proposal.work_order_id} proposal={proposal} />
            ))}
          </div>
        </section>
      )}

      {data && (
        <footer className="mt-8 text-center text-xs text-slate-400">
          Queue read at {formatTimestamp(data.generated_at)} · classified by{" "}
          {data.model} · every assignment on this page was written only after an
          approval click.
        </footer>
      )}
    </div>
  );
}
