import { useState } from "react";
import type { AssignmentRequest, Crew, TriageProposal, Urgency } from "../types";
import { URGENCY_ORDER } from "../types";
import { URGENCY_META, formatRelativeTime, formatTimestamp } from "../urgency";
import HighlightedDescription from "./HighlightedDescription";

interface Props {
  proposal: TriageProposal;
  crews: Crew[];
  approvedBy: string;
  /** Disabled when the desk isn't ready to approve (no lead name, gate off). */
  blockedReason: string | null;
  onApprove: (body: AssignmentRequest) => Promise<void>;
}

/**
 * One work order still awaiting approval: the report, the safety evidence, the
 * proposal, and the Approve / Change controls.
 *
 * Rows that have been approved are rendered by `AssignedCard` instead, so this
 * component only ever deals with the undecided case.
 */
export default function TriageRow({
  proposal,
  crews,
  approvedBy,
  blockedReason,
  onApprove,
}: Props) {
  const meta = URGENCY_META[proposal.urgency];

  const [editing, setEditing] = useState(false);
  const [crewId, setCrewId] = useState<number | null>(proposal.proposed_crew_id);
  const [urgency, setUrgency] = useState<Urgency>(proposal.urgency);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const changed =
    crewId !== proposal.proposed_crew_id || urgency !== proposal.urgency;

  async function approve(withCrewId: number | null, withUrgency: Urgency) {
    if (withCrewId === null) {
      setError("Choose a crew before approving.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await onApprove({
        work_order_id: proposal.work_order_id,
        crew_id: withCrewId,
        urgency: withUrgency,
        approved_by: approvedBy.trim(),
        rationale: proposal.rationale,
        // Recorded on the assignment so the trail distinguishes "the lead
        // accepted the proposal" from "the lead corrected it".
        proposed_by:
          withCrewId === proposal.proposed_crew_id &&
          withUrgency === proposal.urgency
            ? "agent"
            : "human",
      });
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <article className="flex overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
      <div className={`w-1.5 shrink-0 ${meta.stripe}`} aria-hidden="true" />

      <div className="min-w-0 flex-1 p-4">
        {/* ---- Work order header ---- */}
        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
          <span className="font-mono text-sm font-bold text-slate-900">
            {proposal.work_order_number}
          </span>
          <span className="text-sm font-semibold text-slate-800">
            {proposal.machine_name}
          </span>
          <span className="font-mono text-xs text-slate-400">
            {proposal.machine_code}
          </span>
          <span className="text-xs text-slate-500">· {proposal.machine_area}</span>
          {proposal.machine_criticality !== "Standard" && (
            <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[11px] font-medium uppercase tracking-wide text-slate-600">
              {proposal.machine_criticality} asset
            </span>
          )}
          <span
            className="ml-auto text-xs text-slate-400"
            title={formatTimestamp(proposal.reported_at)}
          >
            {formatRelativeTime(proposal.reported_at)}
          </span>
        </div>

        <p className="mt-0.5 text-xs text-slate-500">
          Reported by {proposal.reported_by}
          {proposal.reporter_role ? ` · ${proposal.reporter_role}` : ""}
        </p>

        <div className="mt-2">
          <HighlightedDescription
            description={proposal.description}
            signals={proposal.safety_signals}
          />
        </div>

        {/* ---- Why the safety rule fired ---- */}
        {proposal.safety_signals.length > 0 && (
          <div className="mt-2 flex flex-wrap items-center gap-1.5">
            <span className="text-[11px] font-semibold uppercase tracking-wide text-rose-700">
              Safety rule
            </span>
            {proposal.safety_signals.map((signal) => (
              <span
                key={`${signal.category}-${signal.label}`}
                title={signal.category_label}
                className="rounded-full border border-rose-200 bg-rose-50 px-2 py-0.5 text-[11px] font-medium text-rose-800"
              >
                {signal.label}
              </span>
            ))}
          </div>
        )}

        {/* ---- The proposal ---- */}
        <div className="mt-3 rounded-lg border border-slate-200 bg-slate-50 p-3">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
              Proposed
            </span>
            <span
              className={`rounded-full px-2 py-0.5 text-[11px] font-bold ring-1 ${meta.badgeClass}`}
            >
              {meta.badge}
            </span>
            {proposal.safety_override && (
              <span className="text-[11px] font-medium text-rose-700">
                raised by the safety rule — the model classified this as{" "}
                {URGENCY_META[proposal.model_urgency].label.toLowerCase()}
              </span>
            )}
          </div>

          <dl className="mt-2 space-y-1 text-sm">
            <div className="flex gap-2">
              <dt className="w-14 shrink-0 text-xs text-slate-500">Crew</dt>
              <dd className="text-slate-800">
                {proposal.proposed_crew_code ? (
                  <>
                    <span className="font-mono text-xs font-semibold">
                      {proposal.proposed_crew_code}
                    </span>{" "}
                    {proposal.proposed_crew_name}
                  </>
                ) : (
                  <span className="italic text-amber-700">
                    none proposed — choose one with Change
                  </span>
                )}
              </dd>
            </div>
            <div className="flex gap-2">
              <dt className="w-14 shrink-0 text-xs text-slate-500">Why</dt>
              <dd className="text-slate-700">{proposal.rationale}</dd>
            </div>
          </dl>
        </div>

        {/* ---- Decision ---- */}
        <div className="mt-3">
            {editing && (
              <div className="mb-2 grid gap-2 rounded-lg border border-brand-100 bg-brand-50 p-3 sm:grid-cols-2">
                <label className="block">
                  <span className="mb-1 block text-xs font-medium text-slate-600">
                    Urgency
                  </span>
                  <select
                    value={urgency}
                    onChange={(e) => setUrgency(e.target.value as Urgency)}
                    className="w-full rounded-md border border-slate-300 bg-white px-2 py-1.5 text-sm"
                  >
                    {URGENCY_ORDER.map((level) => (
                      <option key={level} value={level}>
                        {URGENCY_META[level].label}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="block">
                  <span className="mb-1 block text-xs font-medium text-slate-600">
                    Crew
                  </span>
                  <select
                    value={crewId ?? ""}
                    onChange={(e) =>
                      setCrewId(e.target.value ? Number(e.target.value) : null)
                    }
                    className="w-full rounded-md border border-slate-300 bg-white px-2 py-1.5 text-sm"
                  >
                    <option value="">— choose a crew —</option>
                    {crews.map((crew) => (
                      <option key={crew.id} value={crew.id}>
                        {crew.crew_code} · {crew.name} ({crew.shift})
                      </option>
                    ))}
                  </select>
                </label>
                {crewId !== null && (
                  <p className="text-xs text-slate-500 sm:col-span-2">
                    {crews.find((c) => c.id === crewId)?.specialty}
                  </p>
                )}
              </div>
            )}

            <div className="flex flex-wrap items-center gap-2">
              <span className="rounded-full border border-slate-300 bg-white px-2 py-0.5 text-[11px] font-medium text-slate-500">
                Awaiting approval — nothing written
              </span>

              <div className="ml-auto flex gap-2">
                {editing ? (
                  <>
                    <button
                      type="button"
                      onClick={() => {
                        setEditing(false);
                        setCrewId(proposal.proposed_crew_id);
                        setUrgency(proposal.urgency);
                        setError(null);
                      }}
                      disabled={busy}
                      className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-600 transition hover:bg-slate-50 disabled:opacity-50"
                    >
                      Cancel
                    </button>
                    <button
                      type="button"
                      onClick={() => approve(crewId, urgency)}
                      disabled={busy || crewId === null || blockedReason !== null}
                      title={blockedReason ?? undefined}
                      className="rounded-md bg-emerald-600 px-3 py-1.5 text-sm font-semibold text-white transition hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      {busy
                        ? "Assigning…"
                        : changed
                          ? "Approve change & assign"
                          : "Approve & assign"}
                    </button>
                  </>
                ) : (
                  <>
                    <button
                      type="button"
                      onClick={() => setEditing(true)}
                      disabled={busy}
                      className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 transition hover:bg-slate-50 disabled:opacity-50"
                    >
                      Change
                    </button>
                    <button
                      type="button"
                      onClick={() => approve(proposal.proposed_crew_id, proposal.urgency)}
                      disabled={
                        busy ||
                        proposal.proposed_crew_id === null ||
                        blockedReason !== null
                      }
                      title={
                        blockedReason ??
                        (proposal.proposed_crew_id === null
                          ? "No crew proposed — use Change to pick one."
                          : undefined)
                      }
                      className="rounded-md bg-emerald-600 px-3 py-1.5 text-sm font-semibold text-white transition hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      {busy ? "Assigning…" : "Approve & assign"}
                    </button>
                  </>
                )}
              </div>
            </div>

          {error && (
            <p className="mt-2 rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-800">
              {error}
            </p>
          )}
        </div>
      </div>
    </article>
  );
}
