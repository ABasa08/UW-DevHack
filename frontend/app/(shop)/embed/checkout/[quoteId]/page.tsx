"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { ProductImage } from "@/components/ProductImage";
import CheckList from "@/components/CheckList";
import { useShopper } from "@/components/ShopperProvider";
import VerifiedSeal from "@/components/VerifiedSeal";
import { Badge, Button, Card, ErrorBox, Eyebrow, KV, Loading, SimLabel, Spinner, cx } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { dayLabel, money, sizeLabel } from "@/lib/format";
import { useLoad } from "@/lib/hooks";

function OptionCard({ o, recommended, onUse, busy, maxCharge }: { o: any; recommended: boolean; onUse: () => void; busy: boolean; maxCharge: number | null }) {
  const pass = o.status === "PASS";
  return (
    <div className={cx("flex flex-col rounded-2xl border bg-white p-5", recommended ? "border-pass ring-2 ring-pass/20" : "border-paper-line")}>
      <div className="flex items-center justify-between">
        <p className="font-display text-lg font-semibold">Option {o.option_id}</p>
        {recommended ? <Badge tone="pass">Recommended</Badge> : <Badge tone={pass ? "pass" : "fail"}>{pass ? "Passes" : "Fails"}</Badge>}
      </div>
      <p className="mt-0.5 text-sm text-ink-mute">{o.title}</p>
      <div className="mt-4 space-y-0.5">
        <KV k="Charged today" v={<span className={cx("font-semibold", !o.within_budget && "text-fail")}>{money(o.charge_cents)}</span>} />
        <KV k="Card-linked offer" v={o.card_offer?.eligible ? <span className="text-pass">eligible</span> : <span className="text-ink-mute">not eligible</span>} />
        <KV k="Possible later statement credit" v={o.later_statement_credit_cents ? money(o.later_statement_credit_cents) : "—"} />
        <KV k="Effective cost after credit" v={money(o.effective_cost_cents)} />
        <KV k={`Within your ${maxCharge !== null ? money(maxCharge) : ""} limit`} v={o.within_budget ? <span className="text-pass">yes</span> : <span className="font-semibold text-fail">no</span>} />
        <KV k="Arrives" v={dayLabel(o.estimated_delivery_date)} />
      </div>
      {!pass && <p className="mt-3 rounded-lg bg-fail-soft px-3 py-2 text-xs text-fail">{o.failure_summary}</p>}
      <div className="mt-auto pt-4">
        <Button className="w-full" variant={recommended ? "primary" : "secondary"} disabled={!pass} loading={busy} onClick={onUse}>
          {pass ? `Use option ${o.option_id}` : "Can't be used"}
        </Button>
      </div>
    </div>
  );
}

export default function CheckoutPage() {
  const { quoteId } = useParams<{ quoteId: string }>();
  const router = useRouter();
  const { persona, bump, storeName } = useShopper();
  const res = useLoad(() => api.get(`/api/checkout/quotes/${quoteId}`), [quoteId]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [approved, setApproved] = useState(false);
  const [newQuote, setNewQuote] = useState<string | null>(null);
  const [handoff, setHandoff] = useState<any>(null);
  const current = handoff ?? res.data?.handoff;

  // Shopify takes the payment; poll until the paid order is reconciled into the verified purchase record.
  useEffect(() => {
    if (!current || current.status !== "awaiting_payment") return;
    const id = window.setInterval(async () => {
      try {
        const out = await api.get(`/api/checkout/handoffs/${current.purchase_id}`);
        if (out.purchase) router.push(`/embed/orders/${out.purchase.purchase_id}?new=1`);
      } catch { /* keep waiting; the next poll retries */ }
    }, 3000);
    return () => window.clearInterval(id);
  }, [current, router]);

  if (res.loading && !res.data) return <Loading label="Running verification…" />;
  if (res.error) return <ErrorBox error={res.error} onRetry={res.reload} />;
  const { quote, intent, product, payment_method, platform } = res.data;
  const onShopify = platform === "shopify";
  const r = quote.result;
  const req = intent.confirmed_requirements;
  const bd = r.price_breakdown;
  const variant = product.variants.find((v: any) => v.variant_id === quote.proposal.variant_id);
  const fails = r.checks.filter((c: any) => c.status === "FAIL");
  const passed = r.checks.filter((c: any) => c.status === "PASS").length;
  const isPass = r.status === "PASS";

  async function useProposal(proposal: any, key: string) {
    setBusy(key);
    setError(null);
    try {
      const q = await api.post("/api/checkout/verify", { intent_id: intent.intent_id, proposal });
      router.replace(`/embed/checkout/${q.quote_id}`);
    } catch (e) {
      setError(e);
      setBusy(null);
    }
  }

  async function reviewSize(size: string) {
    setBusy("size");
    setError(null);
    try {
      await api.post("/api/intent/confirm", {
        intent_id: intent.intent_id,
        confirmed_requirements: { ...req, size },
        change_reason: `Shopper chose to review size ${size} after merchant-approved fit guidance`,
      });
      const v = product.variants.find((x: any) => x.color === variant.color && x.size === size);
      await useProposal({ ...quote.proposal, variant_id: v.variant_id, source: "shopper", claimed_charge_cents: null, claimed_effective_cost_cents: null }, "size");
    } catch (e) {
      setError(e);
      setBusy(null);
    }
  }

  async function placeOrder() {
    setBusy("order");
    setError(null);
    try {
      const out = await api.post("/api/checkout/confirm", {
        ...persona,
        quote_id: quote.quote_id,
        approved_charge_cents: bd.charge_cents,
        shopper_approved: approved,
      });
      bump();
      if (out.status === "AWAITING_PAYMENT") {
        setHandoff(out.handoff);
        setBusy(null);
        return;
      }
      router.push(`/embed/orders/${out.purchase.purchase_id}?new=1`);
    } catch (e) {
      if (e instanceof ApiError && e.detail?.new_quote) setNewQuote(e.detail.new_quote.quote_id);
      setError(e);
      setBusy(null);
    }
  }

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <Eyebrow>Step 3 · Checkout verification</Eyebrow>
          <h1 className="mt-1 font-display text-3xl font-semibold">{isPass ? "Review and approve" : "Blocked before checkout"}</h1>
        </div>
        <Link href={`/embed?intent=${intent.intent_id}`} className="text-sm text-ink-soft underline-offset-2 hover:underline">← Back to recommendations</Link>
      </div>

      {quote.status === "used" && (
        <div className="rounded-xl bg-pass-soft px-4 py-3 text-sm text-pass">
          This quote was already approved and purchased. <Link className="font-medium underline" href={`/embed/orders/${quote.purchase_id}`}>View the purchase record →</Link>
        </div>
      )}
      {quote.status === "superseded" && (
        <div className="rounded-xl bg-warn-soft px-4 py-3 text-sm text-warn">
          This quote is no longer valid because something changed after it was created. Please verify again.
        </div>
      )}

      {/* Status banner */}
      <div className={cx("animate-rise rounded-2xl px-6 py-5", isPass ? "bg-pass text-white" : "bg-fail text-white")}>
        <div className="flex flex-wrap items-center gap-4">
          <span className="flex h-11 w-11 items-center justify-center rounded-full bg-white/15 text-2xl">{isPass ? "✓" : "✕"}</span>
          <div className="flex-1">
            <p className="text-lg font-semibold">
              {isPass ? "Every rule passes against your confirmed requirements." : `The verifier caught ${fails.length} problem${fails.length > 1 ? "s" : ""} in this proposal.`}
            </p>
            <p className="text-sm text-white/80">
              {isPass
                ? "Amounts were recomputed on the server. Nothing is charged until you approve."
                : quote.proposal.source === "agent"
                  ? "The AI agent proposed it; deterministic rules rejected it. Nothing was charged."
                  : "This cart can't reach checkout. Pick a corrected option below."}
            </p>
          </div>
        </div>
      </div>

      <ErrorBox error={error} />
      {newQuote && (
        <div className="rounded-xl border border-warn/30 bg-warn-soft px-4 py-3 text-sm text-warn">
          The amount changed after you approved, so your earlier approval no longer applies.{" "}
          <Link href={`/embed/checkout/${newQuote}`} className="font-semibold underline">Review the updated quote →</Link>
        </div>
      )}

      {!isPass ? (
        <>
          <div className="grid gap-6 lg:grid-cols-[1fr_1.3fr]">
            <Card className="p-5">
              <p className="text-sm font-semibold">What was proposed</p>
              <div className="mt-3 flex gap-4">
                <ProductImage product={product} color={variant?.color} className="h-24 w-28 shrink-0" />
                <div className="text-sm">
                  <p className="font-medium">{product.name}</p>
                  <p className="capitalize text-ink-mute">{variant?.color} · {sizeLabel(variant?.size)} · qty {quote.proposal.quantity}</p>
                  <p className="mt-1 text-ink-mute">Coupons: {quote.proposal.coupon_codes.join(", ") || "none"}</p>
                </div>
              </div>
              <div className="mt-4 border-t border-paper-line pt-3">
                {quote.proposal.claimed_charge_cents !== null && quote.proposal.claimed_charge_cents !== undefined && (
                  <KV k="Proposal says you'll be charged" v={money(quote.proposal.claimed_charge_cents)} />
                )}
                {quote.proposal.claimed_effective_cost_cents !== null && quote.proposal.claimed_effective_cost_cents !== undefined && (
                  <KV k="Proposal says it effectively costs" v={<span className="text-fail line-through">{money(quote.proposal.claimed_effective_cost_cents)}</span>} />
                )}
                <KV k="Verified charge" v={<b>{money(bd?.charge_cents)}</b>} />
                <KV k="Verified effective cost" v={<b>{money(bd?.effective_cost_cents)}</b>} />
              </div>
            </Card>
            <Card className="p-5">
              <p className="text-sm font-semibold">Why this cart was stopped</p>
              <CheckList checks={fails} compact />
              <p className="mt-2 text-xs text-ink-mute">Everything else matched what you confirmed.</p>
            </Card>
          </div>

          {r.options.length > 0 && (
            <div>
              <div className="mb-3">
                <Eyebrow>Corrected options · each one re-verified</Eyebrow>
                <p className="mt-1 text-sm text-ink-soft">
                  A future statement credit can't override your maximum charge of {money(req.max_charge_cents)}. Here's the honest comparison:
                </p>
              </div>
              <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
                {r.options.map((o: any) => (
                  <OptionCard key={o.option_id} o={o} recommended={o.option_id === r.recommended_option} maxCharge={req.max_charge_cents}
                    busy={busy === o.option_id} onUse={() => useProposal({ ...o.proposal, source: "corrected_option" }, o.option_id)} />
                ))}
              </div>
            </div>
          )}
        </>
      ) : (
        <div className="grid gap-6 lg:grid-cols-[1.25fr_1fr]">
          <div className="space-y-6">
            {r.guidance?.length > 0 && (
              <div className="rounded-2xl border border-warn/30 bg-warn-soft p-4">
                <p className="text-sm font-semibold text-warn">Merchant-approved fit guidance</p>
                <p className="mt-1 text-[15px]">{r.guidance[0].message}</p>
                <p className="mt-1 text-xs text-ink-mute">Based on {r.guidance[0].evidence}. Your size will not change unless you choose it.</p>
                <div className="mt-3 flex gap-2">
                  <Button size="sm" onClick={() => reviewSize(r.guidance[0].suggest_size)} loading={busy === "size"} disabled={!r.guidance[0].suggest_in_stock}>
                    Switch to size {r.guidance[0].suggest_size} and re-verify
                  </Button>
                </div>
              </div>
            )}
            <Card className="p-5">
              <div className="flex gap-4">
                <ProductImage product={product} color={variant?.color} className="h-28 w-32 shrink-0" />
                <div>
                  <p className="font-display text-xl font-semibold">{product.name}</p>
                  <p className="capitalize text-ink-mute">{variant?.color} · {sizeLabel(variant?.size)} · qty {quote.proposal.quantity}</p>
                  <p className="mt-2 text-sm">
                    Arrives by <b>{dayLabel(r.estimated_delivery_date)}</b> · {bd.shipping_label}
                  </p>
                </div>
              </div>
            </Card>
            <VerifiedSeal
              verified
              passed={passed}
              total={passed + fails.length}
              title="Checked against what you confirmed"
              body={`Item, size, color, stock, price, offers, budget and delivery date all match. ${passed} of ${passed + fails.length} checks passed.`}
            />
          </div>
          <div className="space-y-4 lg:sticky lg:top-24 lg:self-start">
            <Card className="p-5">
              <p className="text-sm font-semibold">Price breakdown</p>
              <div className="mt-2">
                <KV k={`Item (${money(bd.unit_price_cents)} × ${bd.quantity})`} v={money(bd.merchandise_cents)} />
                <KV k={bd.shipping_label ?? "Shipping"} v={bd.shipping_cents ? money(bd.shipping_cents) : "Free"} />
                <KV k={bd.tax_source === "shopify" ? "Tax (calculated by Shopify)" : "Demo tax"} v={money(bd.tax_cents)} />
                {bd.coupon_evaluations.filter((c: any) => c.eligible).map((c: any) => (
                  <KV key={c.code} k={`Coupon ${c.code}`} v={<span className="text-pass">{money(-c.discount_cents)}</span>} />
                ))}
                <div className="my-2 border-t border-paper-line" />
                <KV strong k="Charged today" v={<span className="text-xl">{money(bd.charge_cents)}</span>} />
                <KV k="Your maximum" v={money(req.max_charge_cents)} />
              </div>
              <div className="mt-3 rounded-lg bg-paper-sunk px-3 py-2 text-xs text-ink-soft">
                {bd.later_statement_credit_cents
                  ? `Card-linked offer: ${money(bd.later_statement_credit_cents)} statement credit expected after settlement. It does not reduce today's charge.`
                  : "Card-linked offer: this charge doesn't qualify after discounts, so no credit is claimed."}
              </div>
            </Card>
            <Card className="p-5">
              <div className="flex items-center justify-between">
                <p className="text-sm font-semibold">Payment</p>
                {!onShopify && <SimLabel />}
              </div>
              <p className="mt-2 flex items-center gap-2 text-sm">
                {payment_method.network === "visa" && (
                  <span className="rounded bg-brand px-1.5 py-0.5 text-[10px] font-bold italic tracking-wide text-white">VISA</span>
                )}
                {payment_method.label}
              </p>
              <p className="mt-1 text-xs text-ink-mute">{payment_method.note}</p>
            </Card>
            {current && (
              <Card className="animate-rise p-5">
                <p className="text-sm font-semibold">Your verified checkout is ready</p>
                <p className="mt-1 text-sm text-ink-soft">
                  Shopify locked this cart at exactly <b>{money(current.approved_charge_cents)}</b>: the item, discount code and shipping you
                  approved. Stock is held for 30 minutes.
                </p>
                <a href={current.checkout_url} target="_top" rel="noopener"
                  className="mt-4 flex w-full items-center justify-center gap-2 rounded-lg bg-ink px-5 py-3 text-base font-medium text-white hover:bg-ink-soft">
                  Pay {money(current.approved_charge_cents)} on {storeName}&apos;s checkout →
                </a>
                <p className="mt-3 flex items-center gap-2 text-xs text-ink-mute">
                  <Spinner className="h-3.5 w-3.5" /> After you pay, your verified order record appears in My orders.
                </p>
              </Card>
            )}
            {quote.status === "open" && !current && (
              <Card className="p-5">
                <label className="flex cursor-pointer gap-3 text-sm">
                  <input type="checkbox" className="mt-1 h-4 w-4" checked={approved} onChange={(e) => setApproved(e.target.checked)} />
                  <span>
                    I approve a one-time {onShopify ? "" : <b>simulated </b>}charge of <b>{money(bd.charge_cents)}</b>{" "}
                    {onShopify ? `on ${storeName}'s Shopify checkout` : `to ${payment_method.label}`} for {product.name},{" "}
                    <span className="capitalize">{variant?.color}</span>, {sizeLabel(variant?.size)}, arriving by {dayLabel(r.estimated_delivery_date)}.
                  </span>
                </label>
                <Button className="mt-4 w-full" size="lg" disabled={!approved} loading={busy === "order"} onClick={placeOrder}>
                  {onShopify ? `Approve ${money(bd.charge_cents)} and continue` : `Place order · ${money(bd.charge_cents)}`}
                </Button>
                <p className="mt-2 text-center text-xs text-ink-mute">
                  {onShopify
                    ? "PerkPilot re-verifies the cart, then Shopify must calculate the same total before you're sent to pay."
                    : "The server re-verifies the cart at the moment you approve."}
                </p>
              </Card>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
