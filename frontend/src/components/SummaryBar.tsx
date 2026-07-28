import type { Health, TriageProposal, Urgency } from "../types";
import { URGENCY_ORDER } from "../types";
import { URGENCY_META } from "../urgency";

/** What a chip can scroll the board to. */
export type JumpTarget = Urgency | "assigned";

interface Props {
  proposals: TriageProposal[];
  health: Health | null;
  onJump: (target: JumpTarget) => void;
}

/**
 * Live counts across the top of the board.
 *
 * The urgency counts are of work still *awaiting approval*, so approving a
 * safety-critical row takes that count down by one and the assigned count up by
 * one in the same render. Everything is derived from the proposals array rather
 * than stored separately — there is no second copy of the numbers to drift.
 */
export default function SummaryBar({ proposals, health, onJump }: Props) {
  const awaiting = proposals.filter((p) => p.assignment === null);
  const assigned = proposals.filter((p) => p.assignment !== null);
  const countFor = (level: Urgency) =>
    awaiting.filter((p) => p.urgency === level).length;

  return (
    <div className="flex flex-wrap items-center gap-2 rounded-xl border border-slate-200 bg-white px-4 py-3 shadow-sm">
      {URGENCY_ORDER.map((level) => {
        const meta = URGENCY_META[level];
        const count = countFor(level);
        // Nothing to scroll to once a band is cleared.
        const empty = count === 0 && !assigned.some((p) => p.urgency === level);
        return (
          <button
            key={level}
            type="button"
            onClick={() => onJump(level)}
            disabled={empty}
            title={empty ? undefined : `Jump to ${meta.label}`}
            className={`rounded-full border px-2.5 py-1 text-xs font-semibold transition ${meta.sectionClass} ${
              empty
                ? "cursor-default opacity-40"
                : "cursor-pointer hover:shadow-sm focus:outline-none focus:ring-2 focus:ring-brand-500 focus:ring-offset-1"
            }`}
          >
            {count} {meta.label.toLowerCase()}
          </button>
        );
      })}

      <button
        type="button"
        onClick={() => onJump("assigned")}
        disabled={assigned.length === 0}
        title={
          assigned.length === 0 ? undefined : "Jump to the first assigned work order"
        }
        className={`rounded-full border border-emerald-200 bg-emerald-50 px-2.5 py-1 text-xs font-semibold text-emerald-800 transition ${
          assigned.length === 0
            ? "cursor-default opacity-40"
            : "cursor-pointer hover:shadow-sm focus:outline-none focus:ring-2 focus:ring-emerald-500 focus:ring-offset-1"
        }`}
      >
        {assigned.length} assigned this session
      </button>

      <div className="ml-auto flex flex-wrap items-center gap-3 text-xs text-slate-500">
        <span>{awaiting.length} awaiting approval</span>
        {health && (
          <>
            <span className="font-mono">{health.model}</span>
            <span className="flex items-center gap-1.5">
              <span
                className={`h-2 w-2 rounded-full ${
                  health.mcp_connected ? "bg-emerald-500" : "bg-rose-500"
                }`}
                aria-hidden="true"
              />
              MCP {health.mcp_connected ? "connected" : "unreachable"}
            </span>
          </>
        )}
      </div>
    </div>
  );
}
