"use client";

import { useState } from "react";
import { useAdmin } from "@/components/AdminShell";
import { Badge, Button, Card, Empty, ErrorBox, Eyebrow, Loading, cx } from "@/components/ui";
import { api } from "@/lib/api";
import { dateTime } from "@/lib/format";
import { useLoad } from "@/lib/hooks";

const STATUS_TONE: Record<string, "warn" | "pass" | "neutral"> = { proposed: "warn", approved: "pass", rejected: "neutral" };

function RuleCard({ r, onAct, busy }: { r: any; onAct: (key: string, path: string, body: any) => void; busy: string | null }) {
  const pct = Math.round(r.observed_rate * 100);
  return (
    <Card className={cx("overflow-hidden", r.status === "proposed" && "ring-2 ring-warn/30")}>
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-paper-line px-6 py-5">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone={STATUS_TONE[r.status]}>{r.status === "approved" ? (r.enabled ? "Approved · live" : "Approved · paused") : r.status}</Badge>
            <span className="font-mono text-xs text-ink-mute">{r.rule_id} · v{r.version}</span>
          </div>
          <h2 className="mt-2 font-display text-xl font-semibold">{r.product_name} · size {r.variant_scope.size}</h2>
          <p className="text-sm text-ink-soft">Issue: tight fit · proposed action: suggest reviewing size {r.proposed_action.suggest_size} (never auto-changes a selection)</p>
          {r.storefront_sync && (
            <p className={cx("mt-1 text-xs", r.storefront_sync.synced ? "text-pass" : "text-warn")}>
              {r.storefront_sync.synced
                ? r.storefront_sync.sizes?.length ? "✓ Published to the Shopify product page (metafield perkpilot.fit_guidance)" : "✓ Removed from the Shopify product page"
                : `Shopify product page not updated: ${r.storefront_sync.reason}`}
            </p>
          )}
        </div>
        <div className="flex gap-2">
          {r.status === "proposed" && (
            <>
              <Button variant="secondary" loading={busy === `rej-${r.rule_id}`} onClick={() => onAct(`rej-${r.rule_id}`, `/api/merchant/rules/${r.rule_id}/reject`, {})}>
                Reject
              </Button>
              <Button variant="success" loading={busy === `app-${r.rule_id}`} onClick={() => onAct(`app-${r.rule_id}`, `/api/merchant/rules/${r.rule_id}/approve`, {})}>
                Approve guidance
              </Button>
            </>
          )}
          {r.status === "approved" && (
            <Button variant="secondary" loading={busy === `tog-${r.rule_id}`}
              onClick={() => onAct(`tog-${r.rule_id}`, `/api/merchant/rules/${r.rule_id}/toggle`, { enabled: !r.enabled })}>
              {r.enabled ? "Pause rule" : "Resume rule"}
            </Button>
          )}
        </div>
      </div>
      <div className="grid gap-6 px-6 py-5 lg:grid-cols-[1fr_1.2fr]">
        <div className="space-y-5">
          <div className="grid grid-cols-3 gap-3">
            <div className="rounded-xl bg-paper-sunk p-3">
              <p className="text-xs text-ink-mute">Confirmed outcomes</p>
              <p className="font-display text-2xl font-semibold">{r.confirmed_outcome_count}</p>
            </div>
            <div className="rounded-xl bg-paper-sunk p-3">
              <p className="text-xs text-ink-mute">Fulfilled orders</p>
              <p className="font-display text-2xl font-semibold">{r.affected_order_count}</p>
            </div>
            <div className="rounded-xl bg-paper-sunk p-3">
              <p className="text-xs text-ink-mute">Observed rate</p>
              <p className="font-display text-2xl font-semibold">{pct}%</p>
            </div>
          </div>
          <div>
            <p className="text-xs font-medium text-ink-mute">Shoppers would see</p>
            <div className="mt-1 rounded-xl border border-warn/30 bg-warn-soft px-4 py-3 text-[15px]">{r.customer_message}</div>
            <p className="mt-1 text-xs text-ink-mute">
              Wording drafted by {r.wording_source?.mode === "gemini" ? `Gemini (${r.wording_source.model})` : "a fixed template (fallback)"}; thresholds and
              counts computed by code.
            </p>
          </div>
          <div>
            <p className="text-xs font-medium text-ink-mute">Summary</p>
            <p className="mt-1 text-sm">{r.merchant_summary}</p>
          </div>
          <div className="rounded-xl border border-ink/10 bg-paper-sunk p-4">
            <p className="text-sm font-semibold">Limitations</p>
            <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-ink-soft">
              {r.limitations.map((l: string) => <li key={l}>{l}</li>)}
            </ul>
            <p className="mt-2 text-xs text-ink-mute">
              Thresholds: ≥ {r.thresholds.min_fulfilled_orders} fulfilled orders and ≥ {r.thresholds.min_validated_outcomes} confirmed outcomes. {r.thresholds.note}
            </p>
          </div>
        </div>
        <div className="space-y-5">
          <div>
            <p className="text-sm font-semibold">Supporting evidence</p>
            <ul className="mt-2 divide-y divide-paper-line rounded-xl border border-paper-line">
              {r.evidence.map((e: any) => (
                <li key={e.claim_id} className="px-4 py-3 text-sm">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="font-mono text-xs">{e.claim_id} · order {e.purchase_id}</span>
                    <Badge tone={e.source === "live" ? "brand" : "neutral"}>{e.source === "live" ? "live order" : "labeled sample history"}</Badge>
                  </div>
                  <p className="mt-1 italic text-ink-soft">“{e.complaint}”</p>
                  <p className="mt-0.5 text-xs text-pass">
                    ✓ Exchanged {e.outcome.from_size} → {e.outcome.to_size}; shopper confirmed fit {dateTime(e.outcome.confirmed_at)}
                  </p>
                </li>
              ))}
            </ul>
          </div>
          <div>
            <p className="text-sm font-semibold">History</p>
            <ol className="mt-2 space-y-1 text-sm">
              {r.history.map((h: any, i: number) => (
                <li key={i} className="flex justify-between gap-3">
                  <span className="capitalize">{h.action} <span className="text-ink-mute">by {h.actor.replace(/_/g, " ")}</span></span>
                  <span className="text-xs text-ink-mute">{dateTime(h.at)}</span>
                </li>
              ))}
            </ol>
          </div>
        </div>
      </div>
    </Card>
  );
}

export default function RulesPage() {
  const { version, bump } = useAdmin();
  const res = useLoad(() => api.admin("/api/merchant/rules"), [version]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);

  async function act(key: string, path: string, body: any) {
    setBusy(key);
    setError(null);
    try {
      await api.adminPost(path, body);
      bump();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(null);
    }
  }

  if (res.loading && !res.data) return <Loading />;
  if (res.error) return <ErrorBox error={res.error} onRetry={res.reload} />;
  const { rules, watchlist, thresholds } = res.data;

  return (
    <div className="space-y-6">
      <div>
        <Eyebrow>Return-to-rule learning</Eyebrow>
        <h1 className="mt-1 font-display text-3xl font-semibold">Rule review</h1>
        <p className="mt-1 max-w-3xl text-sm text-ink-mute">
          Rules are proposed only from confirmed outcomes, never from raw complaints. Nothing reaches shoppers until you approve it, and approved
          guidance only suggests; it never changes a shopper's selection or an existing order.
        </p>
      </div>
      <ErrorBox error={error} />
      {rules.length === 0 ? (
        <Empty title="No proposed rules yet">
          A proposal appears when a product/size group reaches {thresholds.min_validated_outcomes} confirmed outcomes among ≥ {thresholds.min_fulfilled_orders} fulfilled orders.
        </Empty>
      ) : (
        rules.map((r: any) => <RuleCard key={r.rule_id} r={r} onAct={act} busy={busy} />)
      )}

      <Card className="overflow-hidden">
        <div className="px-5 pt-5">
          <p className="text-sm font-semibold">Watchlist: groups with fit reports</p>
          <p className="text-xs text-ink-mute">Progress toward the thresholds (set them in Setup). Unconfirmed reports never count as evidence.</p>
        </div>
        <div className="overflow-x-auto">
          <table className="mt-3 w-full text-sm">
            <thead className="bg-paper-sunk text-left text-xs text-ink-mute">
              <tr>
                <th className="px-5 py-2 font-medium">Product · size</th>
                <th className="px-3 py-2 font-medium">Fulfilled orders</th>
                <th className="px-3 py-2 font-medium">Confirmed outcomes</th>
                <th className="px-3 py-2 font-medium">Unconfirmed reports</th>
              </tr>
            </thead>
            <tbody>
              {watchlist.map((w: any) => (
                <tr key={`${w.product_id}-${w.size}`} className="border-t border-paper-line">
                  <td className="px-5 py-2">{w.product_name} · {w.size}</td>
                  <td className="px-3 py-2 tabular-nums">{w.fulfilled_order_count} / {thresholds.min_fulfilled_orders}</td>
                  <td className="px-3 py-2 tabular-nums">{w.validated_outcome_count} / {thresholds.min_validated_outcomes}</td>
                  <td className="px-3 py-2 tabular-nums text-ink-mute">{w.unvalidated_report_ids.length}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
