import type { TriageProposal } from "../types";
import { URGENCY_META, formatTimestamp } from "../urgency";

interface Props {
  proposal: TriageProposal;
}

/**
 * A work order that has been approved and written.
 *
 * These all live together in one "Assigned this session" section at the foot of
 * the board, so the card has to carry its own context: it shows the category the
 * work order was triaged into, which is no longer implied by where it sits. Where
 * the lead approved it at a different urgency than proposed, both are shown.
 */
export default function AssignedCard({ proposal }: Props) {
  const assignment = proposal.assignment;
  if (assignment === null) return null;

  // The band this work order was triaged into, not the urgency it was approved
  // at — those differ when the lead changed urgency before approving.
  const category = URGENCY_META[proposal.urgency];
  const urgencyChanged = assignment.urgency !== proposal.urgency;

  return (
    <article className="flex overflow-hidden rounded-xl border border-emerald-200 bg-emerald-50/70 shadow-sm">
      <div className={`w-1.5 shrink-0 ${category.stripe}`} aria-hidden="true" />

      <div className="min-w-0 flex-1 p-3">
        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
          <span className="text-sm font-bold text-emerald-700" aria-hidden="true">
            ✓
          </span>
          <span className="font-mono text-sm font-bold text-slate-900">
            {proposal.work_order_number}
          </span>
          <span className="text-sm font-semibold text-slate-800">
            {proposal.machine_name}
          </span>
          <span className="font-mono text-xs text-slate-400">
            {proposal.machine_code}
          </span>
          <span className="ml-auto rounded-full bg-emerald-600 px-2 py-0.5 text-[11px] font-bold text-white">
            ASSIGNED
          </span>
        </div>

        <dl className="mt-2 grid gap-x-4 gap-y-1 text-sm sm:grid-cols-2">
          <div className="flex items-baseline gap-2">
            <dt className="w-20 shrink-0 text-xs text-emerald-700">Category</dt>
            <dd className="flex flex-wrap items-baseline gap-1.5">
              <span
                className={`rounded-full px-2 py-0.5 text-[11px] font-bold ring-1 ${category.badgeClass}`}
              >
                {category.badge}
              </span>
              {urgencyChanged && (
                <span className="text-xs text-emerald-700">
                  approved as{" "}
                  {URGENCY_META[assignment.urgency].label.toLowerCase()}
                </span>
              )}
            </dd>
          </div>
          <div className="flex gap-2">
            <dt className="w-20 shrink-0 text-xs text-emerald-700">Crew</dt>
            <dd className="text-slate-800">
              <span className="font-mono text-xs font-semibold">
                {assignment.crew_code}
              </span>{" "}
              {assignment.crew_name}
            </dd>
          </div>
          <div className="flex gap-2">
            <dt className="w-20 shrink-0 text-xs text-emerald-700">Approved by</dt>
            <dd className="font-medium text-slate-800">{assignment.approved_by}</dd>
          </div>
          <div className="flex gap-2">
            <dt className="w-20 shrink-0 text-xs text-emerald-700">Approved at</dt>
            <dd className="text-slate-800">
              {assignment.assigned_at
                ? formatTimestamp(assignment.assigned_at)
                : "—"}
            </dd>
          </div>
        </dl>

        {assignment.proposed_by === "human" && (
          <p className="mt-2 text-xs text-emerald-700">
            Crew or urgency was changed by the lead before approving.
          </p>
        )}
      </div>
    </article>
  );
}
