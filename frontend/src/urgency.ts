import type { Urgency } from "./types";

interface UrgencyMeta {
  label: string;
  /** Short form used inside the row badge. */
  badge: string;
  /** Left-hand stripe on the card. */
  stripe: string;
  badgeClass: string;
  sectionClass: string;
  blurb: string;
}

export const URGENCY_META: Record<Urgency, UrgencyMeta> = {
  safety_critical: {
    label: "Safety-critical",
    badge: "SAFETY-CRITICAL",
    stripe: "bg-rose-600",
    badgeClass: "bg-rose-600 text-white ring-rose-700",
    sectionClass: "text-rose-800 bg-rose-50 border-rose-200",
    blurb: "Someone is at risk. Isolate and make safe first.",
  },
  production_stopping: {
    label: "Production-stopping",
    badge: "PRODUCTION-STOPPING",
    stripe: "bg-amber-500",
    badgeClass: "bg-amber-500 text-white ring-amber-600",
    sectionClass: "text-amber-900 bg-amber-50 border-amber-200",
    blurb: "Nobody at risk, but output has stopped.",
  },
  routine: {
    label: "Routine",
    badge: "ROUTINE",
    stripe: "bg-slate-400",
    badgeClass: "bg-slate-200 text-slate-700 ring-slate-300",
    sectionClass: "text-slate-700 bg-slate-100 border-slate-200",
    blurb: "Schedule into the normal maintenance round.",
  },
};

/**
 * Format a timestamp as "18 min ago".
 *
 * MySQL hands back naive datetimes and the database container runs in UTC, so a
 * timestamp with no zone marker is treated as UTC rather than as browser-local
 * time — otherwise every row is off by the viewer's offset.
 */
export function formatRelativeTime(value: string): string {
  if (!value) return "—";

  const hasZone = /(?:Z|[+-]\d{2}:?\d{2})$/.test(value);
  const parsed = new Date(hasZone ? value : `${value}Z`);
  if (Number.isNaN(parsed.getTime())) return value;

  const minutes = Math.round((Date.now() - parsed.getTime()) / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;

  const hours = Math.floor(minutes / 60);
  const remainder = minutes % 60;
  if (hours < 24) {
    return remainder ? `${hours}h ${remainder}m ago` : `${hours}h ago`;
  }

  const days = Math.floor(hours / 24);
  return days === 1 ? "1 day ago" : `${days} days ago`;
}

export function formatTimestamp(value: string): string {
  if (!value) return "—";
  const hasZone = /(?:Z|[+-]\d{2}:?\d{2})$/.test(value);
  const parsed = new Date(hasZone ? value : `${value}Z`);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString();
}
