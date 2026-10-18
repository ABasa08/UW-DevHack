"use client";

import { useEffect, useState } from "react";
import { useAdmin } from "@/components/AdminShell";
import { Badge, Button, Card, Empty, ErrorBox, Eyebrow, KV, Loading, cx } from "@/components/ui";
import { api } from "@/lib/api";
import { money } from "@/lib/format";
import { useLoad } from "@/lib/hooks";

const field = "w-full rounded-lg border border-paper-line bg-white px-3 py-2 text-sm outline-none focus:border-ink-faint";

function Step({ done, title, children }: { done: boolean | null; title: string; children: React.ReactNode }) {
  return (
    <li className="flex gap-3 py-3">
      <span className={cx("mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[11px] font-bold",
        done ? "bg-pass text-white" : done === false ? "bg-warn text-white" : "bg-paper-sunk text-ink-mute")} aria-hidden="true">
        {done ? "✓" : done === false ? "!" : "•"}
      </span>
      <div className="min-w-0 flex-1 text-sm">
        <p className="font-medium">{title}</p>
        <div className="mt-0.5 text-ink-soft">{children}</div>
      </div>
    </li>
  );
}

function Dollars({ label, cents, onChange }: { label: string; cents: number; onChange: (c: number) => void }) {
  return (
    <label className="block text-xs text-ink-mute">
      {label}
      <input type="number" min={0} step="0.01" className={cx(field, "mt-1")} value={cents / 100}
        onChange={(e) => onChange(Math.round(parseFloat(e.target.value || "0") * 100))} />
    </label>
  );
}

function Num({ label, value, onChange, min = 0 }: { label: string; value: number; onChange: (n: number) => void; min?: number }) {
  return (
    <label className="block text-xs text-ink-mute">
      {label}
      <input type="number" min={min} step={1} className={cx(field, "mt-1")} value={value} onChange={(e) => onChange(parseInt(e.target.value || "0", 10))} />
    </label>
  );
}

export default function SetupPage() {
  const { config, version, bump } = useAdmin();
  const onShopify = config?.mode === "shopify";
  const res = useLoad(async () => {
    const [settings, shopify, catalog] = await Promise.all([
      api.admin("/api/merchant/settings"),
      api.admin("/api/admin/shopify").catch(() => null),
      onShopify ? api.admin("/api/merchant/catalog") : Promise.resolve({ products: [], offers: [] }),
    ]);
    return { settings: settings.settings, shopify, catalog };
  }, [version, onShopify]);
  const [draft, setDraft] = useState<any>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [saved, setSaved] = useState(false);
  const [scenario, setScenario] = useState<any>(null);
  const [history, setHistory] = useState<any>(null);
  const [pick, setPick] = useState({ product_id: "", variant_id: "", to_size: "" });
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (res.data) setDraft(structuredClone(res.data.settings));
  }, [res.data]);

  async function act<T>(key: string, fn: () => Promise<T>): Promise<T | null> {
    setBusy(key);
    setError(null);
    try {
      const out = await fn();
      return out;
    } catch (e) {
      setError(e);
      return null;
    } finally {
      setBusy(null);
    }
  }

  if (res.loading && !res.data) return <Loading />;
  if (res.error) return <ErrorBox error={res.error} onRetry={res.reload} />;
  const { shopify, catalog } = res.data!;
  if (!onShopify) {
    return (
      <Empty title="Setup is for a connected Shopify store">
        This PerkPilot runs in {config?.mode ?? "demo"} mode. Set <code>PERKPILOT_MODE=shopify</code> plus your Shopify app credentials in backend/.env and restart.
      </Empty>
    );
  }
  const shop = config?.shop ?? "";
  const product = catalog.products.find((p: any) => p.product_id === pick.product_id);
  const variant = product?.variants.find((v: any) => v.variant_id === pick.variant_id);
  const sizes: string[] = product?.sizes ?? [];
  const larger = variant ? sizes.slice(sizes.indexOf(variant.size) + 1) : [];
  const missing: string[] = shopify?.webhooks?.missing_scopes ?? [];
  const editor = (q: string) => `https://${shop}/admin/themes/current/editor?${q}`;
  const ship = (id: string) => draft?.shipping_options?.find((o: any) => o.id === id);
  const setShip = (id: string, patch: any) =>
    setDraft({ ...draft, shipping_options: draft.shipping_options.map((o: any) => (o.id === id ? { ...o, ...patch } : o)) });

  return (
    <div className="space-y-8">
      <div>
        <Eyebrow>{config?.store_name} · {shop}</Eyebrow>
        <h1 className="mt-1 font-display text-3xl font-semibold">Setup</h1>
        <p className="mt-1 max-w-3xl text-sm text-ink-mute">
          Connect the store, put the assistant on your theme, and tune the policies PerkPilot enforces. Everything here changes real
          settings in this store.
        </p>
      </div>
      <ErrorBox error={error} />

      <Card className="p-5">
        <h2 className="font-semibold">Install checklist</h2>
        <ol className="mt-1 divide-y divide-paper-line">
          <Step done={!!shopify?.connected && missing.length === 0} title="Connect Shopify (webhooks, catalog, discount codes)">
            {shopify?.connected
              ? missing.length ? `Connected, but these scopes aren't granted yet: ${missing.join(", ")}. Deploy shopify.app.toml and approve the update.`
                : `Connected: ${shopify.catalog.products} products synced, ${shopify.webhooks.registered.length} webhook topics live.`
              : "Not connected yet."}
            <div className="mt-2">
              <Button size="sm" variant="secondary" loading={busy === "connect"}
                onClick={() => act("connect", () => api.adminPost("/api/admin/shopify/connect")).then((o) => o && bump())}>
                {shopify?.connected ? "Sync now" : "Connect store"}
              </Button>
            </div>
          </Step>
          <Step done={null} title="Turn on the shopping assistant in your theme">
            Online Store → Themes → Customize → App embeds → <b>PerkPilot assistant</b>.{" "}
            <a className="font-medium underline" href={editor("context=apps")} target="_top" rel="noopener">Open the theme editor →</a>
          </Step>
          <Step done={null} title="Add fit guidance to product pages">
            Adds the <b>PerkPilot fit guidance</b> block, which shows approved size guidance for the selected size.{" "}
            {config?.client_id && (
              <a className="font-medium underline" target="_top" rel="noopener"
                href={editor(`template=product&addAppBlockId=${config.client_id}/fit-guidance&target=mainSection`)}>
                Add it to the product template →
              </a>
            )}
          </Step>
        </ol>
      </Card>

      <Card className="p-5">
        <h2 className="font-semibold">Prepare the judge demo on a real product</h2>
        <p className="mt-1 text-sm text-ink-soft">
          Picks numbers from Shopify&apos;s own totals so the AI agent&apos;s classic mistake appears on your product: it creates a discount code,
          sets the card-linked offer threshold just above the discounted charge, and suggests a shopper request with a budget between the two.
        </p>
        <div className="mt-4 grid gap-3 sm:grid-cols-3">
          <label className="text-xs text-ink-mute">Product
            <select className={cx(field, "mt-1")} value={pick.product_id} onChange={(e) => setPick({ product_id: e.target.value, variant_id: "", to_size: "" })}>
              <option value="">Choose…</option>
              {catalog.products.map((p: any) => <option key={p.product_id} value={p.product_id}>{p.name}</option>)}
            </select>
          </label>
          <label className="text-xs text-ink-mute">Variant the shopper asks for
            <select className={cx(field, "mt-1")} value={pick.variant_id} disabled={!product}
              onChange={(e) => setPick({ ...pick, variant_id: e.target.value, to_size: "" })}>
              <option value="">Choose…</option>
              {product?.variants.map((v: any) => (
                <option key={v.variant_id} value={v.variant_id} disabled={!v.stock}>
                  {[v.color, v.size].filter(Boolean).join(" / ") || "Default"} · {money(v.price_cents)} · {v.stock} in stock
                </option>
              ))}
            </select>
          </label>
          <div className="flex items-end">
            <Button className="w-full" disabled={!variant} loading={busy === "scenario"}
              onClick={() => act("scenario", () => api.adminPost("/api/merchant/setup/scenario", { product_id: pick.product_id, variant_id: pick.variant_id }))
                .then((o) => { if (o) { setScenario(o); bump(); } })}>
              Prepare scenario
            </Button>
          </div>
        </div>
        {scenario && (
          <div className="mt-4 grid gap-4 rounded-xl border border-pass/25 bg-pass-soft/50 p-4 lg:grid-cols-2">
            <div>
              <p className="text-sm font-semibold text-pass">Ready. Ask the storefront assistant:</p>
              <p className="mt-2 rounded-lg bg-white px-3 py-2 text-[15px]">“{scenario.shopper_prompt}”</p>
              <Button className="mt-2" size="sm" variant="secondary"
                onClick={() => navigator.clipboard?.writeText(scenario.shopper_prompt).then(() => setCopied(true))}>
                {copied ? "Copied" : "Copy request"}
              </Button>
            </div>
            <div className="text-sm">
              <KV k={`Discount code ${scenario.coupon_code}`} v={money(-scenario.coupon_cents)} />
              <KV k="Charge without the code (Shopify)" v={money(scenario.charge_without_coupon_cents)} />
              <KV k="Charge with the code (Shopify)" v={money(scenario.charge_with_coupon_cents)} />
              <KV k="Card-linked offer" v={`${money(scenario.card_offer_credit_cents)} credit at ${money(scenario.card_offer_threshold_cents)}+`} />
              <KV k="Shopper's budget" v={money(scenario.budget_cents)} />
            </div>
          </div>
        )}
        {variant && larger.length > 0 && (
          <div className="mt-5 border-t border-paper-line pt-4">
            <p className="text-sm font-medium">Fit-learning history (optional)</p>
            <p className="mt-0.5 text-sm text-ink-soft">
              A new store has no return history. This loads labeled synthetic orders: 20 delivered size-{variant.size} orders and 2 confirmed
              exchanges. Your live exchange then becomes the third confirmed outcome that proposes a rule. Sample records are labeled
              everywhere and never counted as live activity.
            </p>
            <div className="mt-3 flex flex-wrap items-end gap-3">
              <label className="text-xs text-ink-mute">Shoppers exchanged size {variant.size} for
                <select className={cx(field, "mt-1 w-32")} value={pick.to_size} onChange={(e) => setPick({ ...pick, to_size: e.target.value })}>
                  <option value="">Choose…</option>
                  {larger.map((s) => <option key={s} value={s}>{s}</option>)}
                </select>
              </label>
              <Button variant="secondary" disabled={!pick.to_size} loading={busy === "history"}
                onClick={() => act("history", () => api.adminPost("/api/merchant/setup/sample-history",
                  { product_id: pick.product_id, size: variant.size, to_size: pick.to_size })).then((o) => { if (o) { setHistory(o); bump(); } })}>
                Load sample history
              </Button>
            </div>
            {history && <p className="mt-2 text-sm text-pass">{history.note}</p>}
          </div>
        )}
      </Card>

      {draft && (
        <Card className="p-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h2 className="font-semibold">Policies PerkPilot enforces</h2>
            <div className="flex items-center gap-3">
              {saved && <span className="text-sm text-pass">Saved</span>}
              <Button loading={busy === "save"}
                onClick={() => act("save", () => api.adminPost("/api/merchant/settings", draft)).then((o) => { if (o) { setSaved(true); bump(); } })}>
                Save settings
              </Button>
            </div>
          </div>
          <div className="mt-4 grid gap-6 lg:grid-cols-2">
            <div className="space-y-3">
              <p className="text-sm font-medium">Shipping options</p>
              <p className="text-xs text-ink-mute">Applied as the shipping line on the verified Shopify checkout; business days set the delivery promise.</p>
              {["standard", "express"].filter(ship).map((id) => (
                <div key={id} className="grid grid-cols-[1.4fr_1fr_1fr] gap-2">
                  <label className="block text-xs text-ink-mute">Label
                    <input className={cx(field, "mt-1")} value={ship(id).label} onChange={(e) => setShip(id, { label: e.target.value })} />
                  </label>
                  <Dollars label="Price" cents={ship(id).price_cents} onChange={(c) => setShip(id, { price_cents: c })} />
                  <Num label="Business days" value={ship(id).business_days} onChange={(n) => setShip(id, { business_days: n })} />
                </div>
              ))}
              <p className="pt-2 text-sm font-medium">Card-linked offer <Badge>simulated issuer program</Badge></p>
              <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" checked={draft.card_offer.enabled} onChange={(e) => setDraft({ ...draft, card_offer: { ...draft.card_offer, enabled: e.target.checked } })} />
                Offer it to shoppers paying with Visa
              </label>
              <div className="grid grid-cols-2 gap-2">
                <Dollars label="Statement credit" cents={draft.card_offer.credit_cents} onChange={(c) => setDraft({ ...draft, card_offer: { ...draft.card_offer, credit_cents: c } })} />
                <Dollars label="Minimum charge after discounts" cents={draft.card_offer.threshold_cents}
                  onChange={(c) => setDraft({ ...draft, card_offer: { ...draft.card_offer, threshold_cents: c } })} />
              </div>
            </div>
            <div className="space-y-3">
              <p className="text-sm font-medium">Returns and remedies</p>
              <div className="grid grid-cols-3 gap-2">
                <Num label="Return window (days)" value={draft.return_window_days} onChange={(n) => setDraft({ ...draft, return_window_days: n })} />
                <Dollars label="Changed-mind return fee" cents={draft.preference_return_fee_cents} onChange={(c) => setDraft({ ...draft, preference_return_fee_cents: c })} />
                <Num label="Keep-item refund %" value={draft.keep_item_partial_refund_pct} onChange={(n) => setDraft({ ...draft, keep_item_partial_refund_pct: n })} />
              </div>
              <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" checked={draft.allow_store_credit} onChange={(e) => setDraft({ ...draft, allow_store_credit: e.target.checked })} />
                Offer store credit (signed-in customers only; never ranked above a refund)
              </label>
              <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" checked={draft.auto_execute_test_remedies} onChange={(e) => setDraft({ ...draft, auto_execute_test_remedies: e.target.checked })} />
                Run remedies on Shopify test orders immediately (real orders always wait for your approval)
              </label>
              <p className="pt-2 text-sm font-medium">Fit-learning thresholds</p>
              <div className="grid grid-cols-2 gap-2">
                <Num label="Fulfilled orders in the group" min={1} value={draft.learning_thresholds.min_fulfilled_orders}
                  onChange={(n) => setDraft({ ...draft, learning_thresholds: { ...draft.learning_thresholds, min_fulfilled_orders: n } })} />
                <Num label="Confirmed exchange outcomes" min={1} value={draft.learning_thresholds.min_validated_outcomes}
                  onChange={(n) => setDraft({ ...draft, learning_thresholds: { ...draft.learning_thresholds, min_validated_outcomes: n } })} />
              </div>
              <p className="text-xs text-ink-mute">{draft.learning_thresholds.note}</p>
            </div>
          </div>
        </Card>
      )}

      <Card className="p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="font-semibold">Discount codes from Shopify</h2>
            <p className="text-sm text-ink-mute">Basic code discounts sync automatically. PerkPilot checks their rules, and Shopify applies them again at checkout.</p>
          </div>
          <Button size="sm" variant="secondary" loading={busy === "codes"} onClick={() => act("codes", () => api.adminPost("/api/merchant/setup/sync-discounts")).then((o) => o && bump())}>
            Sync codes
          </Button>
        </div>
        <ul className="mt-3 divide-y divide-paper-line text-sm">
          {catalog.offers.length === 0 && <li className="py-2 text-ink-mute">No active offers.</li>}
          {catalog.offers.map((o: any) => (
            <li key={o.offer_id} className="flex flex-wrap items-center justify-between gap-2 py-2">
              <span>{o.label}</span>
              <Badge tone={o.offer_type === "coupon" ? "brand" : "neutral"}>{o.offer_type === "coupon" ? "Shopify code" : "card-linked (simulated)"}</Badge>
            </li>
          ))}
        </ul>
      </Card>
    </div>
  );
}
