"use client";

import { Button, Card, Loading } from "@/components/ui";
import { api } from "@/lib/api";
import { useLoad } from "@/lib/hooks";

const PILLARS = [
  ["Buy it right", "The assistant reads what the shopper wants, the shopper confirms it, and deterministic checks verify the product, stock, discount code, card offer, budget and delivery date against Shopify's own totals before checkout."],
  ["Fix it fast", "The approved purchase becomes a verified record built from the paid Shopify order. A complaint is classified against that record and Shopify's fulfillment and payment data, and remedies run as Shopify refunds, returns and replacement orders."],
  ["Learn from every return", "Confirmed exchange outcomes propose size guidance. Once the merchant approves it, the guidance is published to the product page and shown to the next shopper."],
];

// The app's public home: what PerkPilot is and where its two surfaces live (the storefront and Shopify admin).
export default function Home() {
  const cfg = useLoad(() => api.get("/api/app/config"), []);
  const shop: string = cfg.data?.shop ?? "";
  const handle = shop.replace(/\.myshopify\.com$/, "");
  return (
    <div className="min-h-screen bg-paper">
      <div className="mx-auto max-w-4xl px-6 py-16">
        <p className="text-xs font-semibold uppercase tracking-[0.14em] text-ink-mute">PerkPilot for Shopify</p>
        <h1 className="mt-3 font-display text-5xl font-semibold leading-tight">Buy it right. Fix it fast. Learn from every return.</h1>
        <p className="mt-4 max-w-2xl text-lg text-ink-soft">
          An AI shopping and post-purchase assistant that lives in your Shopify store. AI interprets language, deterministic code enforces the
          rules, and shoppers and merchants approve every consequential step.
        </p>
        {cfg.loading ? (
          <Loading />
        ) : (
          <div className="mt-8 flex flex-wrap gap-3">
            {shop && <Button href={`https://${shop}`} size="lg">Open {cfg.data?.store_name ?? "the store"} →</Button>}
            {handle && <Button href={`https://admin.shopify.com/store/${handle}/apps`} size="lg" variant="secondary">PerkPilot in Shopify admin</Button>}
            <Button href="/embed" size="lg" variant="ghost">Preview the shopper assistant</Button>
          </div>
        )}
        <div className="mt-12 grid gap-4 md:grid-cols-3">
          {PILLARS.map(([t, d]) => (
            <Card key={t} className="p-5">
              <p className="font-display text-xl font-semibold">{t}</p>
              <p className="mt-2 text-sm text-ink-soft">{d}</p>
            </Card>
          ))}
        </div>
        <p className="mt-10 text-xs text-ink-mute">
          Payments happen on the store&apos;s own Shopify checkout; PerkPilot never sees card numbers. The card-linked offer is a simulated issuer
          program, and PerkPilot isn&apos;t certified by or integrated with Visa.
        </p>
      </div>
    </div>
  );
}
