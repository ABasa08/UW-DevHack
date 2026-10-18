"use client";

import Link from "next/link";
import { useDemo } from "@/components/DemoProvider";
import { ProductImage } from "@/components/ProductCard";
import { Badge, Button, Empty, ErrorBox, Eyebrow, Loading } from "@/components/ui";
import { api } from "@/lib/api";
import { CLASSIFICATION_LABELS, dayLabel, money, sizeLabel, STATUS_LABELS } from "@/lib/format";
import { useLoad } from "@/lib/hooks";

export default function OrdersPage() {
  const { shopperId, version, state } = useDemo();
  const res = useLoad(async () => {
    const [orders, products] = await Promise.all([
      api.get(`/api/purchases?shopper_id=${shopperId}`),
      api.get("/api/products"),
    ]);
    return { orders: orders.purchases, products: Object.fromEntries(products.products.map((p: any) => [p.product_id, p])) };
  }, [shopperId, version]);
  const shopper = state?.shoppers.find((s) => s.shopper_id === shopperId);

  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <div>
        <Eyebrow>{shopper?.display_name}'s account</Eyebrow>
        <h1 className="mt-1 font-display text-3xl font-semibold">Orders</h1>
      </div>
      {res.loading && !res.data ? (
        <Loading />
      ) : res.error ? (
        <ErrorBox error={res.error} onRetry={res.reload} />
      ) : res.data!.orders.length === 0 ? (
        <Empty title="No orders yet">
          <p>Ask the assistant for what you need; verified purchases show up here.</p>
          <div className="mt-4"><Button href="/assistant">Open the assistant</Button></div>
        </Empty>
      ) : (
        <div className="space-y-3">
          {res.data!.orders.map((o: any) => {
            const p = res.data!.products[o.product_id];
            const claim = o.claims[0];
            return (
              <Link key={o.purchase_id} href={`/orders/${o.purchase_id}`} className="flex items-center gap-4 rounded-2xl border border-paper-line bg-white p-4 shadow-card transition hover:shadow-lift">
                {p && <ProductImage product={p} color={o.color} className="h-20 w-24 shrink-0" />}
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <p className="font-medium">{o.product_name}</p>
                    {o.kind === "exchange" && <Badge tone="info">Exchange order</Badge>}
                    {o.source === "seed" && <Badge>Seeded scenario</Badge>}
                  </div>
                  <p className="text-sm capitalize text-ink-mute">{o.color} · {sizeLabel(o.size)} · {o.purchase_id}</p>
                  <p className="mt-1 text-xs text-ink-mute">
                    {o.shipment?.delivered_at ? `Delivered ${dayLabel(o.shipment.actual_delivery_date)}` : `Arrives by ${dayLabel(o.approved_delivery_date)}`}
                  </p>
                </div>
                <div className="text-right">
                  <p className="font-medium tabular-nums">{money(o.approved_charge_cents)}</p>
                  <Badge tone={o.status === "delivered" || o.status === "exchanged" ? "pass" : "neutral"}>{STATUS_LABELS[o.status] ?? o.status}</Badge>
                  {claim && <p className="mt-1 text-xs text-warn">{CLASSIFICATION_LABELS[claim.classification]} · {claim.status.replace(/_/g, " ")}</p>}
                </div>
              </Link>
            );
          })}
        </div>
      )}
    </div>
  );
}
