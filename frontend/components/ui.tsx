"use client";

import Link from "next/link";
import type { ReactNode } from "react";

export function cx(...c: (string | false | null | undefined)[]) {
  return c.filter(Boolean).join(" ");
}

type BtnProps = {
  children: ReactNode;
  onClick?: () => void;
  href?: string;
  variant?: "primary" | "secondary" | "ghost" | "danger" | "success";
  size?: "sm" | "md" | "lg";
  disabled?: boolean;
  loading?: boolean;
  type?: "button" | "submit";
  className?: string;
  title?: string;
};

export function Button({ children, onClick, href, variant = "primary", size = "md", disabled, loading, type = "button", className, title }: BtnProps) {
  const cls = cx(
    "inline-flex items-center justify-center gap-2 rounded-lg font-medium transition focus:outline-none focus-visible:ring-2 focus-visible:ring-brand/40 disabled:cursor-not-allowed disabled:opacity-50 whitespace-nowrap",
    size === "sm" && "px-3 py-1.5 text-sm",
    size === "md" && "px-4 py-2 text-sm",
    size === "lg" && "px-5 py-3 text-base",
    variant === "primary" && "bg-ink text-white hover:bg-ink-soft",
    variant === "secondary" && "border border-paper-line bg-white text-ink hover:border-ink-faint",
    variant === "ghost" && "text-ink-soft hover:bg-paper-sunk",
    variant === "danger" && "bg-fail text-white hover:opacity-90",
    variant === "success" && "bg-pass text-white hover:opacity-90",
    className,
  );
  if (href && !disabled) {
    return (
      <Link href={href} className={cls} title={title}>
        {children}
      </Link>
    );
  }
  return (
    <button type={type} className={cls} onClick={onClick} disabled={disabled || loading} title={title}>
      {loading && <Spinner className="h-4 w-4" />}
      {children}
    </button>
  );
}

export function Card({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cx("rounded-2xl border border-paper-line bg-paper-card shadow-card", className)}>{children}</div>;
}

const TONES = {
  pass: "bg-pass-soft text-pass",
  fail: "bg-fail-soft text-fail",
  warn: "bg-warn-soft text-warn",
  info: "bg-info-soft text-info",
  brand: "bg-brand-soft text-brand",
  neutral: "bg-paper-sunk text-ink-soft",
  dark: "bg-ink text-white",
} as const;
export type Tone = keyof typeof TONES;

export function Badge({ children, tone = "neutral", className }: { children: ReactNode; tone?: Tone; className?: string }) {
  return (
    <span className={cx("inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium", TONES[tone], className)}>
      {children}
    </span>
  );
}

export function statusTone(s: string): Tone {
  if (s === "PASS") return "pass";
  if (s === "FAIL") return "fail";
  if (s === "WARN" || s === "NEEDS_RECONFIRMATION") return "warn";
  if (s === "INFO") return "info";
  return "neutral";
}

export function StatusIcon({ status, className }: { status: string; className?: string }) {
  const base = cx("inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[11px] font-bold", className);
  if (status === "PASS") return <span className={cx(base, "bg-pass text-white")} aria-label="pass">✓</span>;
  if (status === "FAIL") return <span className={cx(base, "bg-fail text-white")} aria-label="fail">✕</span>;
  if (status === "WARN") return <span className={cx(base, "bg-warn text-white")} aria-label="warning">!</span>;
  return <span className={cx(base, "bg-info text-white")} aria-label="info">i</span>;
}

export function Spinner({ className = "h-5 w-5" }: { className?: string }) {
  return (
    <svg className={cx("animate-spin", className)} viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <circle cx="12" cy="12" r="10" stroke="currentColor" strokeOpacity="0.2" strokeWidth="3" />
      <path d="M22 12a10 10 0 0 0-10-10" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
    </svg>
  );
}

export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="flex items-center justify-center gap-3 py-16 text-ink-mute">
      <Spinner /> <span className="text-sm">{label}</span>
    </div>
  );
}

export function ErrorBox({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  if (!error) return null;
  const msg = error instanceof Error ? error.message : String(error);
  return (
    <div role="alert" className="flex items-start justify-between gap-4 rounded-xl border border-fail/20 bg-fail-soft px-4 py-3 text-sm text-fail">
      <span>{msg}</span>
      {onRetry && (
        <button className="shrink-0 font-medium underline" onClick={onRetry}>
          Retry
        </button>
      )}
    </div>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="rounded-2xl border border-dashed border-paper-line bg-white/60 px-6 py-12 text-center">
      <p className="font-medium text-ink">{title}</p>
      {children && <div className="mt-2 text-sm text-ink-mute">{children}</div>}
    </div>
  );
}

export function Eyebrow({ children, className }: { children: ReactNode; className?: string }) {
  return <p className={cx("text-xs font-semibold uppercase tracking-[0.14em] text-ink-mute", className)}>{children}</p>;
}

export function KV({ k, v, strong }: { k: ReactNode; v: ReactNode; strong?: boolean }) {
  return (
    <div className={cx("flex items-baseline justify-between gap-4 py-1.5 text-sm", strong && "font-semibold text-ink")}>
      <span className={strong ? "" : "text-ink-mute"}>{k}</span>
      <span className="text-right tabular-nums">{v}</span>
    </div>
  );
}

export function SimLabel({ children = "Simulated" }: { children?: ReactNode }) {
  return (
    <span className="inline-flex items-center rounded border border-dashed border-ink-faint px-1.5 py-px text-[10px] font-semibold uppercase tracking-wider text-ink-mute">
      {children}
    </span>
  );
}
