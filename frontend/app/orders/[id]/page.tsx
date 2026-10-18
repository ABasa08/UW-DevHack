"use client";

import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { Suspense, useState, type ReactNode } from "react";
import { useDemo } from "@/components/DemoProvider";
import { ProductImage } from "@/components/ProductCard";
import { ShipmentTimeline } from "@/components/Timeline";
import { Button, Card, ErrorBox, KV, Loading, cx } from "@/components/ui";
import VerifiedSeal from "@/components/VerifiedSeal";
import { api } from "@/lib/api";
import { categoryLabel, CLASSIFICATION_LABELS, dateTime, dayLabel, money, sizeLabel, STATUS_LABELS } from "@/lib/format";
import { useLoad } from "@/lib/hooks";

const DELIVERY_LABELS: Record<string, string> = {
  pending: "Preparing your order",
  label_created: "Preparing your order",
  in_transit: "In transit",
  out_for_delivery: "Out for delivery",
  delayed: "Delayed in transit",
  delivered: "Delivered",
};

type Tone = "pass" | "warn" | "info" | "neutral";
const DOT: Record<Tone, string> = { pass: "bg-pass", warn: "bg-warn", info: "bg-info", neutral: "bg-ink-faint" };

function Status({ label, value, tone, children }: { label: string; value: ReactNode; tone: Tone; children?: ReactNode }) {
  return (
    <div className="px-5 py-4">
      <p className="text-xs text-ink-mute">{label}</p>
      <p className="mt-1 flex items-center gap-2 font-medium">
        <span className={cx("h-2 w-2 shrink-0 rounded-full", DOT[tone])} aria-hidden="true" />
        {value}
      </p>
      {children && <div className="mt-0.5 pl-4 text-xs text-ink-mute">{children}</div>}
    </div>
  );
}

function orderTone(status: string): Tone {
  if (status === "delivered" || status === "exchanged") return "pass";
  if (status === "exchange_in_progress") return "info";
  return "neutral";
}

function OrderInner() {
  const { id } = useParams<{ id: string }>();
  const params = useSearchParams();
  const { shopperId, version, bump } = useDemo();
  const res = useLoad(() => api.get(`/api/purchases/${id}?shopper_id=${shopperId}`), [id, shopperId, version]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  if (res.loading && !res.data) return <Loading />;
  if (res.error) return <ErrorBox error={res.error} onRetry={res.reload} />;
  const { purchase: p, claims, exchange_orders, product } = res.data;
  const price = p.price;
  const ship = p.shipping;
  const req = p.confirmed_requirements;
  const delivered = !!ship?.delivered_on;
  const activeClaim = claims.find((c: any) => !["resolved", "closed_refunded"].includes(c.status)) ?? claims[0];

  async function advance() {
    setBusy(true);
    setError(null);
    try {
      await api.post("/api/demo/advance", { until: "delivered", purchase_id: p.purchase_id });
      await bump();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      {params.get("new") && (
        <div role="status" className="animate-rise flex items-center gap-3 rounded-2xl bg-pass px-5 py-4 text-white">
          <svg viewBox="0 0 24 24" className="h-6 w-6 shrink-0" fill="none" aria-hidden="true">
            <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="1.6" />
            <path d="m7.5 12.3 3 3 6-6.3" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          <div>
            <p className="font-semibold">Thanks, your order is placed.</p>
            <p className="text-sm text-white/85">We saved exactly what you approved, so if anything goes wrong you won't have to explain it again.</p>
          </div>
        </div>
      )}

      <header>
        <p className="text-sm text-ink-mute">
          {p.kind === "exchange" ? "Exchange order" : "Order"} <span className="font-medium text-ink-soft">{p.purchase_id}</span>
          <span className="mx-2 text-ink-faint" aria-hidden="true">|</span>
          Placed {dateTime(p.created_at)}
          {p.source === "seed" && <span className="ml-2 rounded-full bg-paper-sunk px-2 py-0.5 text-xs">Sample order</span>}
        </p>
        <h1 className="mt-1 font-display text-3xl font-semibold leading-tight sm:text-4xl">{p.product_name}</h1>
      </header>

      <section aria-label="Order status" className="grid divide-y divide-paper-line overflow-hidden rounded-2xl border border-paper-line bg-white shadow-card sm:grid-cols-3 sm:divide-x sm:divide-y-0">
        <Status label="Order" value={STATUS_LABELS[p.status] ?? p.status} tone={orderTone(p.status)} />
        <Status label="Payment" value={p.payment.status} tone={p.payment.status === "Paid" ? "pass" : "neutral"}>
          {p.payment.method}
          {p.payment.simulated && <p>Demo payment. No money moved.</p>}
        </Status>
        <Status
          label="Delivery"
          value={delivered ? `Delivered ${dayLabel(ship.delivered_on)}` : `Arrives by ${dayLabel(p.approved_delivery_date)}`}
          tone={delivered ? "pass" : ship?.status === "delayed" ? "warn" : "neutral"}
        >
          {ship && !delivered && DELIVERY_LABELS[ship.status]}
          {ship?.simulated && <p>Demo carrier</p>}
        </Status>
      </section>
      <ErrorBox error={error} />

      <div className="grid gap-6 lg:grid-cols-[1.35fr_1fr]">
        <div className="space-y-6">
          <Card className="flex gap-4 p-5">
            <ProductImage product={product} color={p.color} className="h-28 w-32 shrink-0" />
            <div className="min-w-0 flex-1">
              <p className="font-medium">{p.product_name}</p>
              <p className="mt-0.5 text-sm text-ink-mute">
                {[p.color && <span key="c" className="capitalize">{p.color}</span>, p.size && sizeLabel(p.size), `Qty ${p.quantity}`]
                  .filter(Boolean)
                  .map((x, i) => <span key={i}>{i > 0 && ", "}{x}</span>)}
              </p>
              <p className="mt-2 text-sm tabular-nums">{money(price.unit_price_cents)} each</p>
            </div>
          </Card>

          {ship && (
            <Card className="p-5">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <h2 className="font-semibold">Tracking</h2>
                <p className="text-xs text-ink-mute">{ship.carrier}</p>
              </div>
              <div className="mt-4">
                <ShipmentTimeline shipping={ship} />
              </div>
              {!delivered && ship.simulated && (
                <Button className="mt-5" variant="secondary" size="sm" loading={busy} onClick={advance}>
                  Demo: skip ahead to delivery
                </Button>
              )}
            </Card>
          )}

          {p.kind === "order" && (
            <Card className="p-5">
              <h2 className="font-semibold">Need help with this order?</h2>
              {activeClaim ? (
                <div className="mt-2 flex flex-wrap items-center justify-between gap-3">
                  <p className="text-sm text-ink-soft">
                    {CLASSIFICATION_LABELS[activeClaim.classification]}: {activeClaim.status.replace(/_/g, " ")}
                  </p>
                  <Button href={`/claims/${activeClaim.claim_id}`} size="sm">View your request</Button>
                </div>
              ) : delivered ? (
                <div className="mt-2 flex flex-wrap items-center justify-between gap-3">
                  <p className="text-sm text-ink-soft">Tell us what's wrong in your own words. Your order details are already attached.</p>
                  <Button href={`/orders/${p.purchase_id}/report`} size="sm">Report a problem</Button>
                </div>
              ) : (
                <p className="mt-1 text-sm text-ink-mute">You can report a problem once the order arrives.</p>
              )}
            </Card>
          )}

          {exchange_orders.length > 0 && (
            <Card className="p-5">
              <h2 className="font-semibold">Replacement orders</h2>
              {exchange_orders.map((x: any) => (
                <Link key={x.purchase_id} href={`/orders/${x.purchase_id}`} className="mt-2 flex items-center justify-between gap-3 rounded-lg bg-paper-sunk px-3 py-2 text-sm hover:bg-paper-line">
                  <span><span className="capitalize">{x.color}</span> {sizeLabel(x.size)}</span>
                  <span className="text-ink-mute">{x.shipping?.delivered_on ? "Delivered" : `Arrives ${dayLabel(x.approved_delivery_date)}`}</span>
                </Link>
              ))}
            </Card>
          )}
          {p.parent_purchase_id && (
            <Link className="inline-block text-sm underline underline-offset-2" href={`/orders/${p.parent_purchase_id}`}>Back to the original order</Link>
          )}
        </div>

        <div className="space-y-6 lg:sticky lg:top-24 lg:self-start">
          <Card className="p-5">
            <h2 className="font-semibold">Order summary</h2>
            <div className="mt-2">
              <KV k={`Item${price.quantity > 1 ? `s (${price.quantity})` : ""}`} v={money(price.merchandise_cents)} />
              <KV k={price.shipping_label ?? "Shipping"} v={price.shipping_cents ? money(price.shipping_cents) : "Free"} />
              <KV k="Tax" v={money(price.tax_cents)} />
              {price.coupon_discount_cents > 0 && (
                <KV k={`Discount${price.coupons.length ? ` (${price.coupons.join(", ")})` : ""}`} v={<span className="text-pass">{money(-price.coupon_discount_cents)}</span>} />
              )}
            </div>
            <div className="mt-3 flex items-baseline justify-between border-t border-paper-line pt-3">
              <span className="text-sm font-medium">Total charged</span>
              <span className="font-display text-3xl font-semibold tabular-nums">{money(p.approved_charge_cents)}</span>
            </div>
            {price.later_statement_credit_cents > 0 && (
              <p className="mt-1 text-right text-xs text-ink-mute">
                Plus a {money(price.later_statement_credit_cents)} card statement credit, posted after settlement
              </p>
            )}
            {p.verification.checks_total > 0 || !p.verification.verified ? (
              <VerifiedSeal className="mt-4" verified={p.verification.verified} passed={p.verification.checks_passed} total={p.verification.checks_total} />
            ) : (
              <p className="mt-4 rounded-xl bg-paper-sunk px-4 py-3 text-sm text-ink-soft">Sample order created for the demo. It didn't go through checkout verification.</p>
            )}
          </Card>

          {req && (
            <Card className="p-5">
              <h2 className="font-semibold">What you asked for</h2>
              {p.original_request && <p className="mt-2 text-sm italic text-ink-soft">“{p.original_request}”</p>}
              <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-sm">
                <dt className="text-ink-mute">Product</dt>
                <dd>{categoryLabel(req.category)}</dd>
                <dt className="text-ink-mute">Color</dt>
                <dd className="capitalize">{req.color ?? "Any"}</dd>
                {req.size && (
                  <>
                    <dt className="text-ink-mute">Size</dt>
                    <dd>{sizeLabel(req.size)}</dd>
                  </>
                )}
                <dt className="text-ink-mute">Budget</dt>
                <dd>{money(req.max_charge_cents)} {req.budget_includes_shipping_and_tax ? "including shipping and tax" : "before shipping and tax"}</dd>
                {req.deadline_date && (
                  <>
                    <dt className="text-ink-mute">Needed by</dt>
                    <dd>{dayLabel(req.deadline_date)}</dd>
                  </>
                )}
              </dl>
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}

export default function OrderPage() {
  return (
    <Suspense fallback={<Loading />}>
      <OrderInner />
    </Suspense>
  );
}
