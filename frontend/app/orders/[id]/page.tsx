"use client";

import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import CheckList from "@/components/CheckList";
import { useDemo } from "@/components/DemoProvider";
import { ProductImage } from "@/components/ProductCard";
import { EventLog, ShipmentTimeline } from "@/components/Timeline";
import { Badge, Button, Card, ErrorBox, Eyebrow, KV, Loading, SimLabel } from "@/components/ui";
import { api } from "@/lib/api";
import { CATEGORY_LABELS, CLASSIFICATION_LABELS, STATUS_LABELS, dateTime, dayLabel, money } from "@/lib/format";
import { useLoad } from "@/lib/hooks";

function OrderInner() {
  const { id } = useParams<{ id: string }>();
  const params = useSearchParams();
  const { shopperId, version, bump } = useDemo();
  const res = useLoad(() => api.get(`/api/purchases/${id}?shopper_id=${shopperId}`), [id, shopperId, version]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [showChecks, setShowChecks] = useState(false);
  const [showEvents, setShowEvents] = useState(false);

  if (res.loading && !res.data) return <Loading />;
  if (res.error) return <ErrorBox error={res.error} onRetry={res.reload} />;
  const { purchase: p, shipment, events, integrity, claims, exchange_orders, product, intent } = res.data;
  const bd = p.full_price_breakdown;
  const req = p.confirmed_requirements;
  const delivered = !!shipment?.delivered_at;
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
    <div className="mx-auto max-w-6xl space-y-6">
      {params.get("new") && (
        <div className="animate-rise rounded-2xl bg-pass px-6 py-5 text-white">
          <p className="text-lg font-semibold">Order confirmed. Your verified purchase record is saved.</p>
          <p className="text-sm text-white/80">
            It holds exactly what you approved, so if anything goes wrong you won't have to explain it again.
          </p>
        </div>
      )}
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <Eyebrow>{p.kind === "exchange" ? "Exchange order" : "Verified purchase record"} · {p.purchase_id}</Eyebrow>
          <h1 className="mt-1 font-display text-3xl font-semibold">{p.product_name}</h1>
          <p className="capitalize text-ink-mute">{p.color} · US {p.size} · qty {p.quantity}</p>
        </div>
        <div className="flex items-center gap-2">
          <Badge tone={delivered ? "pass" : "neutral"}>{STATUS_LABELS[p.status] ?? p.status}</Badge>
          {p.source === "seed" && <Badge>Seeded scenario</Badge>}
        </div>
      </div>
      <ErrorBox error={error} />

      <div className="grid gap-6 lg:grid-cols-[1fr_1.1fr]">
        <div className="space-y-6">
          <Card className="p-5">
            <div className="flex gap-4">
              <ProductImage product={product} color={p.color} className="h-28 w-32 shrink-0" />
              <div className="flex-1">
                <p className="text-sm font-semibold">Delivery</p>
                <p className="text-sm text-ink-soft">
                  Promised by <b>{dayLabel(p.approved_delivery_date)}</b>
                  {delivered && <> · delivered {dayLabel(shipment.actual_delivery_date)}</>}
                </p>
                <p className="mt-0.5 text-xs text-ink-mute">{shipment?.carrier_fixture}</p>
              </div>
            </div>
            <div className="mt-5">
              <ShipmentTimeline shipment={shipment} />
            </div>
            {!delivered && shipment && (
              <Button className="mt-5" variant="secondary" size="sm" loading={busy} onClick={advance}>
                Demo: advance clock to delivery →
              </Button>
            )}
          </Card>

          {p.kind === "order" && (
            <Card className="p-5">
              <p className="text-sm font-semibold">Need help with this order?</p>
              {activeClaim ? (
                <div className="mt-2 flex flex-wrap items-center justify-between gap-3">
                  <div className="text-sm">
                    <p>
                      Issue <span className="font-mono text-xs">{activeClaim.claim_id}</span>:{" "}
                      <b>{CLASSIFICATION_LABELS[activeClaim.classification]}</b>
                    </p>
                    <p className="text-ink-mute">Status: {activeClaim.status.replace(/_/g, " ")}</p>
                  </div>
                  <Button href={`/claims/${activeClaim.claim_id}`} size="sm">Open resolution center</Button>
                </div>
              ) : delivered ? (
                <div className="mt-2 flex flex-wrap items-center justify-between gap-3">
                  <p className="text-sm text-ink-soft">Describe the problem in your own words. We already have your order details.</p>
                  <Button href={`/orders/${p.purchase_id}/report`} size="sm">Report a problem</Button>
                </div>
              ) : (
                <p className="mt-1 text-sm text-ink-mute">You can report a problem once the order is delivered.</p>
              )}
            </Card>
          )}

          {exchange_orders.length > 0 && (
            <Card className="p-5">
              <p className="text-sm font-semibold">Exchange orders</p>
              {exchange_orders.map((x: any) => (
                <Link key={x.purchase_id} href={`/orders/${x.purchase_id}`} className="mt-2 flex items-center justify-between rounded-lg bg-paper-sunk px-3 py-2 text-sm hover:bg-paper-line">
                  <span className="capitalize">{x.color} · US {x.size} · {x.purchase_id}</span>
                  <span className="text-ink-mute">{x.shipment?.delivered_at ? "Delivered" : `Arrives ${dayLabel(x.approved_delivery_date)}`}</span>
                </Link>
              ))}
            </Card>
          )}
          {p.parent_purchase_id && (
            <Link className="text-sm underline" href={`/orders/${p.parent_purchase_id}`}>← Original order {p.parent_purchase_id}</Link>
          )}
        </div>

        <div className="space-y-6">
          <Card className="p-5">
            <div className="flex items-center justify-between">
              <p className="text-sm font-semibold">What you approved</p>
              <SimLabel>Simulated payment</SimLabel>
            </div>
            {req && (
              <div className="mt-2 rounded-lg bg-paper-sunk px-3 py-2 text-sm">
                <p className="text-xs text-ink-mute">Confirmed requirements v{p.confirmation_version} · {dateTime(p.confirmation_time)}</p>
                <p className="mt-0.5">
                  {CATEGORY_LABELS[req.category]} · <span className="capitalize">{req.color ?? "any color"}</span> · US {req.size} · ≤{" "}
                  {money(req.max_charge_cents)} total{req.deadline_date && <> · by {dayLabel(req.deadline_date)}</>}
                </p>
                {intent?.original_request && <p className="mt-1 text-xs italic text-ink-mute">“{intent.original_request}”</p>}
              </div>
            )}
            <div className="mt-3">
              <KV k="Shoes" v={money(bd.merchandise_cents)} />
              <KV k="Shipping" v={money(bd.shipping_cents)} />
              <KV k="Demo tax" v={money(bd.tax_cents)} />
              {bd.coupon_discount_cents > 0 && <KV k="Coupon" v={money(-bd.coupon_discount_cents)} />}
              <div className="my-1 border-t border-paper-line" />
              <KV strong k="Approved charge" v={money(p.approved_charge_cents)} />
              <KV k="Card-linked credit" v={bd.later_statement_credit_cents ? `${money(bd.later_statement_credit_cents)} later` : "not eligible"} />
              <KV k="Payment method" v={p.payment_method_reference.label} />
              <KV k="Transaction" v={<span className="font-mono text-xs">{p.simulated_transaction_id}</span>} />
            </div>
          </Card>

          <Card className="p-5">
            <p className="text-sm font-semibold">Record integrity</p>
            <div className="mt-2 space-y-1.5 text-sm">
              <p className="flex items-center gap-2">
                <Badge tone={integrity.hash_matches ? "pass" : "fail"}>{integrity.hash_matches ? "✓ hash matches" : "✕ hash mismatch"}</Badge>
                <Badge tone={integrity.event_chain_valid ? "pass" : "fail"}>{integrity.event_chain_valid ? "✓ event chain intact" : "✕ chain broken"}</Badge>
              </p>
              <p className="break-all font-mono text-xs text-ink-mute">SHA-256 {integrity.record_hash}</p>
              <p className="text-xs text-ink-mute">{integrity.note}</p>
            </div>
            <div className="mt-4 flex gap-4 border-t border-paper-line pt-3 text-sm">
              <button className="font-medium underline-offset-2 hover:underline" onClick={() => setShowChecks((v) => !v)}>
                {showChecks ? "Hide" : "Show"} verification results ({p.verification_results.length})
              </button>
              <button className="font-medium underline-offset-2 hover:underline" onClick={() => setShowEvents((v) => !v)}>
                {showEvents ? "Hide" : "Show"} audit trail ({events.length})
              </button>
            </div>
            {showChecks && <div className="mt-3"><CheckList checks={p.verification_results} compact /></div>}
            {showEvents && <div className="mt-3"><EventLog events={events} /></div>}
          </Card>
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
