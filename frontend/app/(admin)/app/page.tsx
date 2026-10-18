"use client";

import Link from "next/link";
import { useState } from "react";
import BarList from "@/components/BarList";
import ShopifyPanel from "@/components/ShopifyPanel";
import { useAdmin } from "@/components/AdminShell";
import { Badge, Button, Card, ErrorBox, Eyebrow, Loading, SimLabel } from "@/components/ui";
import { api } from "@/lib/api";
import { CLASSIFICATION_LABELS, dateTime, money } from "@/lib/format";
import { useLoad } from "@/lib/hooks";

const RULE_LABELS: Record<string, string> = {
  CARD_OFFER_ELIGIBILITY: "Ineligible card-linked offer",
  CLAIMED_SAVINGS: "Overstated savings",
  CLAIMED_CHARGE: "Wrong quoted charge",
  BUDGET: "Over shopper's max charge",
  DELIVERY_DEADLINE: "Misses delivery deadline",
  COUPON_EXPIRED: "Expired coupon",
  COUPON_ELIGIBILITY: "Ineligible coupon",
  OFFER_STACKING: "Offers can't stack",
  STOCK: "Out of stock",
  COLOR: "Wrong color",
  SIZE: "Wrong size",
  PRODUCT_CATEGORY: "Wrong product type",
  SHOPIFY_TOTAL_MATCH: "Shopify total disagreed",
  TOTAL_UNKNOWN: "Total couldn't be verified",
};

function Stat({ label, value, sub }: { label: string; value: React.ReactNode; sub?: string }) {
  return (
    <Card className="p-4">
      <p className="text-xs text-ink-mute">{label}</p>
      <p className="mt-1 font-display text-3xl font-semibold tabular-nums">{value}</p>
      {sub && <p className="mt-0.5 text-xs text-ink-mute">{sub}</p>}
    </Card>
  );
}

export default function MerchantDashboard() {
  const { version, bump, config } = useAdmin();
  const onShopify = config?.mode === "shopify";
  const res = useLoad(async () => {
    const [d, r] = await Promise.all([api.admin("/api/merchant/dashboard"), api.admin("/api/merchant/rules")]);
    return { d, rules: r.rules };
  }, [version]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);

  if (res.loading && !res.data) return <Loading />;
  if (res.error) return <ErrorBox error={res.error} onRetry={res.reload} />;
  const { d, rules } = res.data!;
  const m = d.session_metrics;
  const proposed = rules.filter((r: any) => r.status === "proposed");
  const ev = d.latest_evaluation;

  async function act(key: string, fn: () => Promise<unknown>) {
    setBusy(key);
    setError(null);
    try {
      await fn();
      bump();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="space-y-8">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <Eyebrow>{config?.store_name ?? "Your store"} · PerkPilot</Eyebrow>
          <h1 className="mt-1 font-display text-3xl font-semibold">Dashboard</h1>
          <p className="mt-1 text-sm text-ink-mute">{d.labels.session_metrics}</p>
        </div>
      </div>
      <ErrorBox error={error} />

      <ShopifyPanel />

      <h2 className="-mb-4 font-semibold">PerkPilot assistant activity</h2>
      {proposed.length > 0 && (
        <Link href="/app/rules" className="flex animate-rise flex-wrap items-center justify-between gap-3 rounded-2xl border border-warn/30 bg-warn-soft px-5 py-4">
          <div>
            <p className="font-semibold text-warn">{proposed.length} proposed rule{proposed.length > 1 ? "s" : ""} waiting for your review</p>
            <p className="text-sm text-ink-soft">{proposed[0].merchant_summary}</p>
          </div>
          <span className="rounded-lg bg-ink px-4 py-2 text-sm font-medium text-white">Review evidence →</span>
        </Link>
      )}

      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <Stat label="Proposals verified" value={m.proposals_verified} sub={`${m.proposals_passed} passed`} />
        <Stat label="Blocked before checkout" value={m.proposals_blocked} sub={m.post_approval_changes_caught ? `${m.post_approval_changes_caught} changed after approval` : "invalid carts stopped"} />
        <Stat label="Verified orders" value={m.orders_confirmed} sub={onShopify ? `${m.checkouts_handed_off} Shopify checkouts started` : "with purchase records"} />
        <Stat label="Claims filed" value={m.claims_filed} sub={`${m.claims_needing_review} needed more info`} />
        <Stat label="Exchanges completed" value={m.exchanges_completed} sub={onShopify ? "free Shopify replacement orders" : "simulated"} />
        <Stat label="Refunds completed" value={m.refunds_completed}
          sub={onShopify ? `${money(m.refund_amount_cents)} via Shopify${m.remedies_awaiting_merchant ? ` · ${m.remedies_awaiting_merchant} awaiting you` : ""}` : `${money(m.refund_amount_cents)} simulated`} />
        <Stat label="Rules approved" value={`${m.rules_approved}/${m.rules_proposed}`} sub={`${m.rules_active} active`} />
        <Stat label="Complaint → options" value={m.avg_complaint_to_remedy_ms === null ? "—" : `${(m.avg_complaint_to_remedy_ms / 1000).toFixed(1)}s`} sub="avg in this app" />
      </div>
      <p className="-mt-4 text-xs text-ink-mute">
        Order value kept through exchange instead of refund: <b>{money(m.order_value_kept_via_exchange_cents)}</b> {!onShopify && <SimLabel />}{" "}
        {d.labels.order_value_kept_via_exchange_cents}
      </p>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card className="p-5">
          <p className="text-sm font-semibold">Why proposals were blocked</p>
          <p className="mb-4 text-xs text-ink-mute">Failed rules across blocked carts</p>
          <BarList
            empty="No blocked proposals yet."
            rows={Object.entries(d.blocked_by_rule as Record<string, number>)
              .sort((a, b) => b[1] - a[1])
              .map(([k, v]) => ({ label: RULE_LABELS[k] ?? k, value: v }))}
          />
        </Card>
        <Card className="p-5">
          <p className="text-sm font-semibold">Issue categories</p>
          <p className="mb-4 text-xs text-ink-mute">All claims incl. labeled sample history; live count noted</p>
          <BarList
            empty="No claims yet."
            rows={Object.entries(d.issue_distribution.all as Record<string, number>)
              .sort((a, b) => b[1] - a[1])
              .map(([k, v]) => ({ label: CLASSIFICATION_LABELS[k] ?? k, value: v, note: `${d.issue_distribution.live[k] ?? 0} live` }))}
          />
        </Card>
      </div>

      <div className="grid gap-6 lg:grid-cols-[1.4fr_1fr]">
        <Card className="overflow-hidden">
          <div className="px-5 pt-5">
            <p className="text-sm font-semibold">Verified purchases</p>
          </div>
          {d.recent_purchases.length === 0 ? (
            <p className="px-5 py-8 text-sm text-ink-mute">No verified orders yet. Open your storefront and ask the shopping assistant for something.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="mt-3 w-full text-sm">
                <thead className="bg-paper-sunk text-left text-xs text-ink-mute">
                  <tr>
                    <th className="px-5 py-2 font-medium">Order</th>
                    <th className="px-3 py-2 font-medium">Item</th>
                    <th className="px-3 py-2 font-medium">Charge</th>
                    <th className="px-3 py-2 font-medium">Checks</th>
                    <th className="px-3 py-2 font-medium">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {d.recent_purchases.map((p: any) => (
                    <tr key={p.purchase_id} className="border-t border-paper-line">
                      <td className="px-5 py-2"><Link className="font-mono text-xs underline" href={`/app/orders/${p.purchase_id}`}>{p.shopify_order_name ?? p.purchase_id}</Link></td>
                      <td className="px-3 py-2"><span>{p.product_name}</span> <span className="capitalize text-ink-mute">· {p.color} {p.size}</span></td>
                      <td className="px-3 py-2 tabular-nums">{p.approved_charge}</td>
                      <td className="px-3 py-2"><Badge tone="pass">{p.checks_passed}/{p.checks_total} pass</Badge></td>
                      <td className="px-3 py-2 text-ink-soft">{p.status.replace(/_/g, " ")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
        <Card className="p-5">
          <p className="text-sm font-semibold">Live activity</p>
          {d.activity.length === 0 ? (
            <p className="py-6 text-sm text-ink-mute">Nothing yet.</p>
          ) : (
            <ol className="mt-3 max-h-80 space-y-2 overflow-y-auto pr-1 text-sm">
              {d.activity.map((e: any) => (
                <li key={e.event_id} className="flex justify-between gap-3">
                  <span>
                    <span className="font-medium">{e.event_type}</span>{" "}
                    <span className="text-xs text-ink-mute">{e.subject_id}</span>
                    {(e.payload?.status === "PASS" || e.payload?.status === "FAIL") && <Badge className="ml-1" tone={e.payload.status === "PASS" ? "pass" : "fail"}>{e.payload.status}</Badge>}
                  </span>
                  <span className="shrink-0 text-xs text-ink-mute">{dateTime(e.timestamp)}</span>
                </li>
              ))}
            </ol>
          )}
        </Card>
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card className="p-5">
          <div className="flex items-center justify-between">
            <p className="text-sm font-semibold">Latest evaluation run</p>
            <Button size="sm" variant="secondary" loading={busy === "eval"} onClick={() => act("eval", () => api.adminPost("/api/eval/run"))}>
              Run evaluation
            </Button>
          </div>
          {ev ? (
            <>
              <div className="mt-3 grid grid-cols-2 gap-x-6 text-sm">
                {Object.entries(ev.calculated_metrics as Record<string, string>).map(([k, v]) => (
                  <div key={k} className="flex justify-between border-b border-paper-line py-1.5">
                    <span className="text-ink-mute">{k.replace(/_/g, " ")}</span>
                    <span className="font-medium tabular-nums">{v}</span>
                  </div>
                ))}
              </div>
              <Link href="/app/evaluation" className="mt-3 inline-block text-sm font-medium underline">See every case →</Link>
            </>
          ) : (
            <p className="mt-3 text-sm text-ink-mute">No run yet. Evaluations run in an isolated sandbox with labeled fixtures and never touch your store.</p>
          )}
        </Card>
      </div>
    </div>
  );
}
