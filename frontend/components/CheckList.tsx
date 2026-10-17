"use client";

import { StatusIcon, cx } from "./ui";

export default function CheckList({ checks, compact }: { checks: any[]; compact?: boolean }) {
  return (
    <ul className="divide-y divide-paper-line">
      {checks.map((c, i) => (
        <li key={i} className={cx("flex gap-3", compact ? "py-2" : "py-3")}>
          <StatusIcon status={c.status} className="mt-0.5" />
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-baseline justify-between gap-x-3">
              <p className={cx("text-sm font-medium", c.status === "FAIL" && "text-fail")}>{c.label}</p>
              {!compact && c.observed !== null && c.observed !== undefined && (
                <p className="text-xs tabular-nums text-ink-mute">
                  <span className={cx(c.status === "FAIL" && "font-semibold text-fail")}>{String(c.observed)}</span>
                  {c.required !== null && c.required !== undefined && <span> · needs {String(c.required)}</span>}
                </p>
              )}
            </div>
            {(!compact || c.status !== "PASS") && <p className="mt-0.5 text-sm text-ink-soft">{c.explanation}</p>}
          </div>
        </li>
      ))}
    </ul>
  );
}
