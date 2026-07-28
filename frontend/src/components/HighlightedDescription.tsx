import { Fragment, useMemo } from "react";
import type { SafetySignal } from "../types";

interface Props {
  description: string;
  signals: SafetySignal[];
}

function escapeRegExp(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/**
 * Show the operator's report with the exact phrases that tripped the safety rule
 * marked up.
 *
 * The point is auditability: the lead can see at a glance *why* a row was raised
 * to safety-critical, without taking the classification on trust.
 */
export default function HighlightedDescription({ description, signals }: Props) {
  const segments = useMemo(() => {
    const phrases = Array.from(
      new Set(signals.map((s) => s.matched_text).filter(Boolean))
    ).sort((a, b) => b.length - a.length); // longest first, so overlaps win big

    if (phrases.length === 0) return [{ text: description, hit: false }];

    const pattern = new RegExp(`(${phrases.map(escapeRegExp).join("|")})`, "gi");
    return description
      .split(pattern)
      .filter((part) => part !== "")
      .map((part) => ({
        text: part,
        hit: phrases.some((p) => p.toLowerCase() === part.toLowerCase()),
      }));
  }, [description, signals]);

  return (
    <p className="text-sm leading-relaxed text-slate-700">
      {segments.map((segment, i) =>
        segment.hit ? (
          <mark
            key={i}
            className="rounded bg-rose-100 px-1 font-medium text-rose-900 decoration-rose-400 decoration-dotted underline-offset-2"
          >
            {segment.text}
          </mark>
        ) : (
          <Fragment key={i}>{segment.text}</Fragment>
        )
      )}
    </p>
  );
}
