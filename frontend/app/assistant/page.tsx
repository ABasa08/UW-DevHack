"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";
import { useDemo } from "@/components/DemoProvider";
import { ProductImage } from "@/components/ProductCard";
import RequirementsForm, { type Req } from "@/components/RequirementsForm";
import { Badge, Button, Card, ErrorBox, Eyebrow, Loading, SimLabel, Spinner, cx } from "@/components/ui";
import { api } from "@/lib/api";
import { CATEGORY_LABELS, dayLabel, money } from "@/lib/format";

const EXAMPLES = [
  "Find black running shoes in size 10, under $120 including shipping and tax, delivered by Friday.",
  "Trail shoes size 11 in green, budget $160 all in",
  "Gym trainers size 10 under $110 total",
];

function Interpreter({ meta }: { meta: any }) {
  if (!meta) return null;
  return (
    <span title={meta.note ?? ""}>
      <Badge tone={meta.mode === "gemini" ? "pass" : "warn"}>
        {meta.mode === "gemini" ? `Interpreted by Gemini (${meta.model})` : "Interpreted by fallback rules (not live AI)"}
      </Badge>
    </span>
  );
}

function ReqSummary({ req, onEdit, version }: { req: Req; onEdit: () => void; version: number }) {
  const bits = [
    req.category && CATEGORY_LABELS[req.category],
    req.color && <span className="capitalize">{req.color}</span>,
    req.size && `US ${req.size}`,
    req.max_charge_cents !== null && `≤ ${money(req.max_charge_cents)} ${req.budget_includes_shipping_and_tax ? "total charge" : "before shipping/tax"}`,
    req.deadline_date ? `by ${dayLabel(req.deadline_date)}` : "no deadline",
    req.quantity > 1 && `qty ${req.quantity}`,
  ].filter(Boolean);
  return (
    <div className="flex flex-wrap items-center gap-2 rounded-xl border border-pass/20 bg-pass-soft/60 px-4 py-3 text-sm">
      <span className="font-medium text-pass">✓ You confirmed (v{version}):</span>
      {bits.map((b, i) => (
        <span key={i} className="rounded-md bg-white px-2 py-0.5 text-ink">{b}</span>
      ))}
      <button onClick={onEdit} className="ml-auto text-sm font-medium text-ink-soft underline-offset-2 hover:underline">
        Edit
      </button>
    </div>
  );
}

function AssistantInner() {
  const params = useSearchParams();
  const router = useRouter();
  const { ready, shopperId, state, version } = useDemo();
  const [text, setText] = useState(params.get("q") ?? "");
  const [intent, setIntent] = useState<any>(null);
  const [draft, setDraft] = useState<Req | null>(null);
  const [editing, setEditing] = useState(false);
  const [recs, setRecs] = useState<any>(null);
  const [applyRules, setApplyRules] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const autoRan = useRef(false);

  const shopper = state?.shoppers.find((s) => s.shopper_id === shopperId);
  const pms = state?.payment_methods.filter((p) => shopper?.payment_methods.includes(p.payment_method_id)) ?? [];

  async function loadRecs(intentId: string, rules = applyRules) {
    setBusy("recs");
    try {
      setRecs(await api.post("/api/recommendations", { intent_id: intentId, apply_learned_rules: rules }));
    } catch (e) {
      setError(e);
    } finally {
      setBusy(null);
    }
  }

  async function parse(t: string) {
    if (!t.trim()) return;
    setBusy("parse");
    setError(null);
    setRecs(null);
    try {
      const it = await api.post("/api/intent/parse", { shopper_id: shopperId, text: t });
      setIntent(it);
      setDraft(it.parsed_requirements);
      setEditing(true);
      router.replace(`${window.location.pathname}?intent=${it.intent_id}`);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(null);
    }
  }

  async function confirm(req: Req, reason?: string) {
    setBusy("confirm");
    setError(null);
    try {
      const it = await api.post("/api/intent/confirm", { intent_id: intent.intent_id, confirmed_requirements: req, change_reason: reason });
      setIntent(it);
      setDraft(it.confirmed_requirements);
      setEditing(false);
      await loadRecs(it.intent_id);
    } catch (e) {
      setError(e);
    } finally {
      setBusy((b) => (b === "confirm" ? null : b));
    }
  }

  async function verifyProposal(proposal: any) {
    setBusy("verify");
    setError(null);
    try {
      const q = await api.post("/api/checkout/verify", { intent_id: intent.intent_id, proposal });
      router.push(`/checkout/${q.quote_id}`);
    } catch (e) {
      setError(e);
      setBusy(null);
    }
  }

  // Restore from ?intent= or auto-run ?q= once.
  useEffect(() => {
    if (!ready || autoRan.current) return;
    autoRan.current = true;
    const iid = params.get("intent");
    const q = params.get("q");
    if (iid) {
      api.get(`/api/intent/${iid}`).then((it) => {
        setIntent(it);
        setText(it.original_request);
        setDraft(it.confirmed_requirements ?? it.parsed_requirements);
        if (it.confirmed_requirements) loadRecs(it.intent_id);
        else setEditing(true);
      }).catch(setError);
    } else if (q) {
      parse(q);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready]);

  // Clock advances / rule approvals elsewhere refresh recommendations.
  useEffect(() => {
    if (intent?.confirmed_requirements && !editing && version > 0) loadRecs(intent.intent_id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [version]);

  const flags: Record<string, string> = {};
  for (const a of intent?.ambiguities ?? []) flags[a.field === "max_charge_dollars" ? "max_charge_cents" : a.field] = a.question;
  const minDate = state?.now.slice(0, 10) ?? "";
  const purchased = intent?.status === "purchased";

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <div>
        <Eyebrow>PerkPilot shopping assistant</Eyebrow>
        <h1 className="mt-1 font-display text-3xl font-semibold">What are you looking for?</h1>
      </div>

      {/* 1 — request */}
      <Card className="p-5">
        <form
          className="flex flex-col gap-3 sm:flex-row"
          onSubmit={(e) => {
            e.preventDefault();
            parse(text);
          }}
        >
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={2}
            placeholder={EXAMPLES[0]}
            className="min-w-0 flex-1 resize-none rounded-xl border border-paper-line bg-paper px-4 py-3 text-[15px] outline-none focus:border-ink-faint focus:bg-white"
            aria-label="Shopping request"
          />
          <Button type="submit" size="lg" loading={busy === "parse"} disabled={!text.trim()}>
            {intent ? "Start over" : "Understand my request"}
          </Button>
        </form>
        {!intent && (
          <div className="mt-3 flex flex-wrap gap-2">
            {EXAMPLES.map((e) => (
              <button key={e} onClick={() => setText(e)} className="rounded-full border border-paper-line px-3 py-1 text-xs text-ink-soft hover:border-ink-faint">
                {e}
              </button>
            ))}
          </div>
        )}
      </Card>

      <ErrorBox error={error} />
      {busy === "parse" && <Loading label="Reading your request…" />}

      {/* 2 — confirm */}
      {intent && draft && editing && (
        <Card className="animate-rise p-6">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <Eyebrow>Step 1 · Confirm what I understood</Eyebrow>
              <p className="mt-2 max-w-2xl text-ink-soft">{intent.assistant_message}</p>
            </div>
            <Interpreter meta={intent.interpreter} />
          </div>
          {intent.notes?.map((n: string) => (
            <p key={n} className="mt-3 rounded-lg bg-info-soft px-3 py-2 text-sm text-info">{n}</p>
          ))}
          {(intent.ambiguities ?? []).length > 0 && (
            <div className="mt-3 rounded-lg bg-warn-soft px-3 py-2 text-sm text-warn">
              <p className="font-medium">I need a bit more before searching:</p>
              <ul className="mt-1 list-disc pl-5">
                {intent.ambiguities.map((a: any) => <li key={a.field}>{a.question}</li>)}
              </ul>
            </div>
          )}
          <div className="mt-5">
            <RequirementsForm value={draft} onChange={setDraft} parsed={intent.parsed_requirements} flags={flags} paymentMethods={pms} minDate={minDate} />
          </div>
          <div className="mt-6 flex flex-wrap items-center justify-between gap-3 border-t border-paper-line pt-5">
            <p className="text-xs text-ink-mute">
              Only these confirmed values are used for verification. The AI's interpretation is kept separately for audit.
            </p>
            <div className="flex gap-2">
              {intent.confirmed_requirements && (
                <Button variant="ghost" onClick={() => { setDraft(intent.confirmed_requirements); setEditing(false); }}>Cancel</Button>
              )}
              <Button
                onClick={() => confirm(draft, intent.confirmed_requirements ? "edited by shopper" : undefined)}
                loading={busy === "confirm"}
                disabled={!draft.category || !draft.size || draft.max_charge_cents === null || purchased}
              >
                Confirm requirements
              </Button>
            </div>
          </div>
        </Card>
      )}

      {/* 3 — recommendations */}
      {intent?.confirmed_requirements && !editing && (
        <div className="space-y-5">
          <ReqSummary req={intent.confirmed_requirements} version={intent.confirmation_version} onEdit={() => setEditing(true)} />
          {purchased && (
            <div className="rounded-xl bg-info-soft px-4 py-3 text-sm text-info">
              This request has already been purchased. <a className="underline" href="/orders">View your orders</a> or start a new request.
            </div>
          )}
          {busy === "recs" && !recs && <Loading label="Finding products that satisfy every requirement…" />}
          {recs && <Recommendations recs={recs} busy={busy} applyRules={applyRules}
            onToggleRules={(v) => { setApplyRules(v); loadRecs(intent.intent_id, v); }}
            onReviewSize={(size) => confirm({ ...intent.confirmed_requirements, size }, `Shopper chose to review size ${size} after merchant-approved fit guidance`)}
            onRelax={(r) => confirm(
              { ...intent.confirmed_requirements, ...Object.fromEntries(r.fields.map((f: string) => [f, null])),
                ...(r.fields.includes("deadline_date") ? { deadline_expression: null } : {}) },
              `Shopper widened the search: ${r.label.toLowerCase()}`,
            )}
            onEdit={() => setEditing(true)}
            onVerify={verifyProposal} disabled={purchased} />}
        </div>
      )}
    </div>
  );
}

function Recommendations({ recs, busy, applyRules, onToggleRules, onReviewSize, onRelax, onEdit, onVerify, disabled }: {
  recs: any; busy: string | null; applyRules: boolean; onToggleRules: (v: boolean) => void;
  onReviewSize: (size: string) => void; onRelax: (r: any) => void; onEdit: () => void; onVerify: (p: any) => void; disabled: boolean;
}) {
  const top = recs.top_pick;
  const agent = recs.agent_proposal;
  const [dismissed, setDismissed] = useState(false);
  const others = recs.candidates.filter((c: any) => c.product_id !== top?.product_id);
  const credit = agent ? agent.claimed_charge_cents - agent.claimed_effective_cost_cents : 0;
  const handoff = recs.candidates.some((c: any) => c.checkout_url);

  return (
    <>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Eyebrow>Step 2 · Recommendation</Eyebrow>
        <label className="flex items-center gap-2 text-xs text-ink-mute" title="Counterfactual view for the demo: show what the assistant would do without merchant-approved learned guidance.">
          <span>Merchant-approved learned guidance</span>
          <button
            role="switch"
            aria-checked={applyRules}
            onClick={() => onToggleRules(!applyRules)}
            className={cx("relative h-5 w-9 rounded-full transition", applyRules ? "bg-pass" : "bg-ink-faint")}
          >
            <span className={cx("absolute top-0.5 h-4 w-4 rounded-full bg-white transition", applyRules ? "left-[18px]" : "left-0.5")} />
          </button>
          <span className="w-6 font-medium">{applyRules ? "On" : "Off"}</span>
        </label>
      </div>

      {!top ? (
        <Card className="p-6">
          {handoff ? (
            <>
              <p className="font-medium">These products match what you confirmed.</p>
              <p className="mt-1 text-sm text-ink-mute">PerkPilot checked size, color, stock and item price. Shipping and tax are calculated at the store's checkout, so finish there.</p>
            </>
          ) : (
            <>
              <p className="font-medium">No product satisfies every requirement you confirmed.</p>
              {recs.notices?.map((n: string) => (
                <p key={n} className="mt-3 rounded-lg bg-info-soft px-3 py-2 text-sm text-info">{n}</p>
              ))}
              {recs.relaxations?.length > 0 ? (
                <div className="mt-4 space-y-2">
                  <p className="text-sm text-ink-soft">These changes would find something. Nothing changes unless you pick one:</p>
                  {recs.relaxations.map((r: any) => (
                    <div key={r.label} className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-paper-line px-4 py-3">
                      <p className="text-sm">
                        <span className="font-medium">{r.label}</span>
                        <span className="text-ink-mute"> · {r.count} match{r.count > 1 ? "es" : ""}, from {r.finds} at {money(r.charge_cents)}</span>
                      </p>
                      <Button size="sm" variant="secondary" loading={busy === "confirm"} disabled={disabled} onClick={() => onRelax(r)}>
                        {r.label}
                      </Button>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="mt-2 text-sm text-ink-mute">
                  Nothing in the catalog matches even with fewer filters. Try a different size, budget or product type.
                </p>
              )}
              <div className="mt-4 flex items-center gap-3">
                <Button size="sm" variant="ghost" onClick={onEdit}>Edit requirements</Button>
                <span className="text-xs text-ink-mute">Below: why each product was ruled out.</span>
              </div>
            </>
          )}
        </Card>
      ) : (
        <Card className="overflow-hidden">
          {recs.guidance.length > 0 && !dismissed && (
            <div className="animate-rise border-b border-warn/20 bg-warn-soft px-6 py-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="max-w-2xl">
                  <p className="text-sm font-semibold text-warn">Fit guidance from StrideLab</p>
                  <p className="mt-0.5 text-[15px] text-ink">{recs.guidance[0].message}</p>
                  <p className="mt-1 text-xs text-ink-mute">
                    Based on {recs.guidance[0].evidence}. Approved by the merchant. Your size stays {recs.confirmed_requirements.size} unless you change it.
                  </p>
                </div>
                <div className="flex gap-2">
                  <Button size="sm" variant="secondary" onClick={() => setDismissed(true)}>Keep size {recs.confirmed_requirements.size}</Button>
                  <Button size="sm" onClick={() => onReviewSize(recs.guidance[0].suggest_size)} disabled={!recs.guidance[0].suggest_in_stock || disabled}>
                    Review size {recs.guidance[0].suggest_size}
                  </Button>
                </div>
              </div>
            </div>
          )}
          <div className="grid gap-6 p-6 md:grid-cols-[240px_1fr]">
            <TopPickImage productId={top.product_id} variantId={top.variant_id} />
            <div>
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone="pass">Meets all your requirements</Badge>
                {!applyRules && <Badge tone="neutral">Learned guidance off</Badge>}
              </div>
              <h2 className="mt-2 font-display text-2xl font-semibold">{top.name}</h2>
              <p className="text-sm capitalize text-ink-mute">{recs.confirmed_requirements.color ?? "Any color"} · US {recs.confirmed_requirements.size}</p>

              {agent && (
                <div className="mt-5 rounded-2xl border border-brand/15 bg-brand-soft/60 p-4">
                  <div className="flex items-center gap-2">
                    <span className="flex h-7 w-7 items-center justify-center rounded-full bg-brand text-xs font-bold text-white">AI</span>
                    <span className="text-sm font-semibold text-brand-ink">Shopping agent proposal</span>
                    <span title={recs.agent_disclosure} className="cursor-help text-xs text-ink-mute underline decoration-dotted">how was this made?</span>
                  </div>
                  <p className="mt-2 text-[15px] text-ink">{recs.agent_message}</p>
                  <div className="mt-3 grid grid-cols-2 gap-2 text-sm sm:grid-cols-4">
                    <Mini label="Coupon" value={agent.coupon_codes.join(", ") || "none"} />
                    <Mini label="Card offer claimed" value={agent.card_offer_ids.length ? `${money(credit)} credit` : "none"} />
                    <Mini label="Agent's charge" value={money(agent.claimed_charge_cents)} />
                    <Mini label="Agent's effective cost" value={money(agent.claimed_effective_cost_cents)} highlight={credit > 0} />
                  </div>
                  <div className="mt-4 flex flex-wrap items-center gap-3">
                    <Button onClick={() => onVerify(agent)} loading={busy === "verify"} disabled={disabled}>
                      Verify before checkout →
                    </Button>
                    <span className="text-xs text-ink-mute">The deterministic verifier recomputes every amount and checks every rule.</span>
                  </div>
                </div>
              )}
            </div>
          </div>
        </Card>
      )}

      {others.length > 0 && (
        <div>
          <p className="mb-2 text-sm font-medium text-ink-soft">Other products considered</p>
          <div className="divide-y divide-paper-line rounded-2xl border border-paper-line bg-white">
            {others.map((c: any) => (
              <div key={c.product_id} className="flex flex-wrap items-center gap-3 px-5 py-3 text-sm">
                <span className="w-56 font-medium">{c.name}</span>
                {c.eligible ? (
                  <>
                    <Badge tone="pass">Eligible · {money(c.best_option.charge_cents)}</Badge>
                    <button className="ml-auto text-sm font-medium underline-offset-2 hover:underline disabled:opacity-50" disabled={disabled || busy === "verify"}
                      onClick={() => onVerify({ ...c.best_option.proposal, source: "shopper" })}>
                      Verify this instead
                    </button>
                  </>
                ) : c.checkout_url ? (
                  <>
                    <Badge tone="pass">Matches · {money(c.price_cents)} + shipping &amp; tax</Badge>
                    <a href={c.checkout_url} target="_top" rel="noopener" className="ml-auto text-sm font-medium underline-offset-2 hover:underline">
                      Continue to store checkout →
                    </a>
                  </>
                ) : (
                  <>
                    <Badge tone="fail">Ruled out</Badge>
                    <span className="flex-1 text-ink-mute">{c.reasons.join(" ")}</span>
                  </>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
      {busy === "verify" && <div className="flex items-center gap-2 text-sm text-ink-mute"><Spinner className="h-4 w-4" /> Verifying…</div>}
      <p className="text-xs text-ink-mute"><SimLabel /> Prices, offers and delivery estimates are demo fixtures evaluated on the demo clock.</p>
    </>
  );
}

function Mini({ label, value, highlight }: { label: string; value: string; highlight?: boolean }) {
  return (
    <div className="rounded-lg bg-white px-3 py-2">
      <p className="text-[11px] text-ink-mute">{label}</p>
      <p className={cx("font-semibold tabular-nums", highlight && "text-brand")}>{value}</p>
    </div>
  );
}

function TopPickImage({ productId, variantId }: { productId: string; variantId: string }) {
  const [p, setP] = useState<any>(null);
  useEffect(() => {
    api.get(`/api/products/${productId}`).then((r) => setP(r.product)).catch(() => setP(null));
  }, [productId]);
  if (!p) return <div className="aspect-square rounded-xl bg-paper-sunk" />;
  const v = p.variants.find((x: any) => x.variant_id === variantId);
  return <ProductImage product={p} color={v?.color} className="aspect-square" />;
}

export default function AssistantPage() {
  return (
    <Suspense fallback={<Loading />}>
      <AssistantInner />
    </Suspense>
  );
}
