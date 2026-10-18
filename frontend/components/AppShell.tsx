"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useState, type ReactNode } from "react";
import { api } from "@/lib/api";
import { dateTime } from "@/lib/format";
import { useDemo } from "./DemoProvider";
import { cx } from "./ui";

function DemoBar() {
  const { state, stateError, bump } = useDemo();
  const router = useRouter();
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  async function run(label: string, fn: () => Promise<unknown>) {
    setBusy(label);
    setMsg(null);
    try {
      await fn();
      await bump();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="bg-ink text-[13px] text-white/80">
      <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-4 gap-y-2 px-4 py-2 sm:px-6">
        <span className="rounded bg-white/10 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-white">Demo</span>
        <span className="hidden md:inline">Fictional store · simulated payments, refunds &amp; shipping</span>
        <span className="ml-auto flex items-center gap-2">
          <span className="text-white/50">Demo clock</span>
          <span className="font-medium tabular-nums text-white">{state ? dateTime(state.now) : stateError ? "offline" : "…"} PT</span>
        </span>
        <span className="flex items-center gap-1">
          <button
            className="rounded px-2 py-1 hover:bg-white/10 disabled:opacity-50"
            disabled={!!busy}
            onClick={() => run("day", () => api.post("/api/demo/advance", { days: 1 }))}
          >
            {busy === "day" ? "…" : "+1 day"}
          </button>
          <button
            className="rounded px-2 py-1 hover:bg-white/10 disabled:opacity-50"
            disabled={!!busy}
            onClick={() => run("delivery", () => api.post("/api/demo/advance", { until: "next_delivery" }))}
          >
            {busy === "delivery" ? "…" : "Next delivery →"}
          </button>
          <button
            className="rounded px-2 py-1 text-white/60 hover:bg-white/10 hover:text-white disabled:opacity-50"
            disabled={!!busy}
            onClick={() => {
              if (window.confirm("Reset the demo? All orders, claims and rules from this session will be cleared and the seed restored.")) {
                run("reset", async () => {
                  await api.post("/api/demo/reset");
                  router.push("/");
                });
              }
            }}
          >
            {busy === "reset" ? "Resetting…" : "Reset"}
          </button>
        </span>
        {state && (
          <span
            title={state.ai.note || `Live model: ${state.ai.model}`}
            className={cx(
              "rounded-full px-2 py-0.5 text-[11px] font-medium",
              state.ai.mode === "gemini" ? "bg-emerald-400/15 text-emerald-200" : "bg-amber-400/15 text-amber-200",
            )}
          >
            {state.ai.mode === "gemini" ? `Gemini live · ${state.ai.model}` : "AI: fallback interpreter"}
          </span>
        )}
      </div>
      {(msg || stateError) && <div className="bg-fail px-4 py-1.5 text-center text-white">{msg || stateError}</div>}
    </div>
  );
}

function ShopperSwitcher() {
  const { state, shopperId, setShopperId, bump } = useDemo();
  if (!state) return null;
  return (
    <label className="flex items-center gap-2 text-sm">
      <span className="hidden text-ink-mute sm:inline">Shopping as</span>
      <select
        className="w-28 truncate rounded-lg border border-paper-line bg-white px-2 py-1.5 text-sm font-medium sm:w-auto"
        value={shopperId}
        onChange={(e) => {
          setShopperId(e.target.value);
          bump();
        }}
      >
        {state.shoppers.map((s) => (
          <option key={s.shopper_id} value={s.shopper_id}>
            {s.display_name} — {s.role}
          </option>
        ))}
      </select>
    </label>
  );
}

function NavLink({ href, children, exact }: { href: string; children: ReactNode; exact?: boolean }) {
  const path = usePathname();
  const active = exact ? path === href : path === href || path.startsWith(href + "/");
  return (
    <Link
      href={href}
      className={cx("rounded-lg px-3 py-1.5 text-sm transition", active ? "bg-paper-sunk font-medium text-ink" : "text-ink-soft hover:text-ink")}
    >
      {children}
    </Link>
  );
}

export default function AppShell({ children }: { children: ReactNode }) {
  const path = usePathname();
  const merchant = path.startsWith("/merchant");
  if (path.startsWith("/embed")) return <main className="min-h-screen bg-paper p-4">{children}</main>;
  return (
    <div className="min-h-screen bg-paper">
      <DemoBar />
      <header className="sticky top-0 z-30 border-b border-paper-line bg-paper/90 backdrop-blur">
        <div className="mx-auto flex max-w-7xl items-center gap-2 px-4 py-3 sm:gap-3 sm:px-6">
          <Link href={merchant ? "/merchant" : "/"} className="flex shrink-0 items-center gap-2.5 sm:mr-2">
            <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-ink font-display text-lg italic text-white">S</span>
            <span className="leading-tight">
              <span className="block whitespace-nowrap font-display text-[17px] font-semibold tracking-tight">StrideLab<span className="hidden sm:inline"> Running</span></span>
              <span className="hidden text-[11px] text-ink-mute sm:block">
                {merchant ? "Merchant console · PerkPilot" : "Shopping assistant by PerkPilot"}
              </span>
            </span>
          </Link>
          <nav className="hidden items-center gap-1 md:flex">
            {merchant ? (
              <>
                <NavLink href="/merchant" exact>Dashboard</NavLink>
                <NavLink href="/merchant/rules">Rule review</NavLink>
                <NavLink href="/merchant/claims">Claims</NavLink>
                <NavLink href="/merchant/evaluation">Evaluation</NavLink>
              </>
            ) : (
              <>
                <NavLink href="/" exact>Shop</NavLink>
                <NavLink href="/assistant">AI assistant</NavLink>
                <NavLink href="/orders">Orders</NavLink>
              </>
            )}
          </nav>
          <div className="ml-auto flex items-center gap-2 sm:gap-3">
            {!merchant && <ShopperSwitcher />}
            <Link
              href={merchant ? "/" : "/merchant"}
              className="whitespace-nowrap rounded-lg border border-paper-line bg-white px-3 py-1.5 text-sm font-medium hover:border-ink-faint"
            >
              {merchant ? "← Storefront" : <>Merchant<span className="hidden sm:inline"> console</span></>}
            </Link>
          </div>
        </div>
        <nav className="flex gap-1 overflow-x-auto px-4 pb-2 md:hidden">
          {merchant ? (
            <>
              <NavLink href="/merchant" exact>Dashboard</NavLink>
              <NavLink href="/merchant/rules">Rules</NavLink>
              <NavLink href="/merchant/claims">Claims</NavLink>
              <NavLink href="/merchant/evaluation">Evaluation</NavLink>
            </>
          ) : (
            <>
              <NavLink href="/" exact>Shop</NavLink>
              <NavLink href="/assistant">Assistant</NavLink>
              <NavLink href="/orders">Orders</NavLink>
            </>
          )}
        </nav>
      </header>
      <main className="mx-auto max-w-7xl px-4 pb-24 pt-8 sm:px-6">{children}</main>
      <footer className="border-t border-paper-line py-8 text-center text-xs text-ink-mute">
        PerkPilot demo for DubHacks 2026 · StrideLab Running is fictional · “Visa Demo Card” is a test fixture, not a card credential ·
        No real payments, refunds or shipments occur.
      </footer>
    </div>
  );
}
