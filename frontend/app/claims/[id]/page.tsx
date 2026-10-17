"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import CheckList from "@/components/CheckList";
import { useDemo } from "@/components/DemoProvider";
import { ShipmentTimeline } from "@/components/Timeline";
import { Badge, Button, Card, ErrorBox, Eyebrow, Loading, SimLabel, cx, type Tone } from "@/components/ui";
import { api } from "@/lib/api";
import { CLASSIFICATION_LABELS, dateTime, money } from "@/lib/format";
import { useLoad } from "@/lib/hooks";

const CLASS_TONE: Record<string, Tone> = {
  ORDER_MISMATCH: "fail",
  PRICE_OR_DELIVERY_MISMATCH: "fail",
  SUSPECTED_PRODUCT_FIT: "warn",
  CUSTOMER_PREFERENCE: "info",
  NEEDS_REVIEW: "neutral",
};

const EVIDENCE_ICON: Record<string, string> = {
  purchase_record: "📄",
  fulfillment: "📦",
  payment: "💳",
  customer_report: "💬",
  prior_outcomes: "📊",
};

function Bubble({ who, children, tone = "assistant" }: { who: string; children: React.ReactNode; tone?: "assistant" | "shopper" }) {
  return (
    <div className={cx("flex gap-3", tone === "shopper" && "flex-row-reverse")}>
      <span className={cx("flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-xs font-bold",
        tone === "shopper" ? "bg-paper-sunk text-ink" : "bg-brand text-white")}>
        {tone === "shopper" ? who[0] : "AI"}
      </span>
      <div className={cx("max-w-[85%] rounded-2xl px-4 py-3 text-[15px]",
        tone === "shopper" ? "rounded-tr-sm bg-ink text-white" : "rounded-tl-sm border border-paper-line bg-white")}>
        {children}
      </div>
    </div>
  );
}

export default function ClaimPage() {
  const { id } = useParams<{ id: string }>();
  const { shopperId, version, bump, state } = useDemo();
  const res = useLoad(() => api.get(`/api/claims/${id}`), [id, version]);
  const [selected, setSelected] = useState<string | null>(null);
  const [payout, setPayout] = useState("original_payment_method");
  const [agree, setAgree] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [ruleProposal, setRuleProposal] = useState<any>(null);
  const [reply, setReply] = useState("");

  if (res.loading && !res.data) return <Loading />;
  if (res.error) return <ErrorBox error={res.error} onRetry={res.reload} />;
  const { claim, purchase, remedies, exchange, events } = res.data;
  const shopperName = state?.shoppers.find((s) => s.shopper_id === claim.shopper_id)?.display_name ?? "You";
  const accepted = remedies.find((r: any) => r.status === "accepted");
  const chosen = remedies.find((r: any) => r.remedy_id === selected);
  const isRefundLike = chosen && ["refund", "partial_refund", "keep_item_partial_refund"].includes(chosen.remedy_type);

  async function act(key: string, fn: () => Promise<any>) {
    setBusy(key);
    setError(null);
    setNotice(null);
    try {
      const out = await fn();
      if (out?.rule_proposal) setRuleProposal(out.rule_proposal);
      if (out?.duplicate && out?.message) setNotice(out.message);
      await bump();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(null);
    }
  }

  const accept = (rid: string, key = "accept") =>
    act(key, () => api.post(`/api/remedies/${rid}/accept`, {
      shopper_id: shopperId, confirm: true, idempotency_key: `${claim.claim_id}:${rid}`, payout_method: payout,
    }));

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <Eyebrow>Resolution center · {claim.claim_id}</Eyebrow>
          <h1 className="mt-1 font-display text-3xl font-semibold">{purchase.product_name}</h1>
          <Link href={`/orders/${purchase.purchase_id}`} className="text-sm capitalize text-ink-mute underline-offset-2 hover:underline">
            {purchase.color} · US {purchase.size} · order {purchase.purchase_id}
          </Link>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={CLASS_TONE[claim.classification]}>{CLASSIFICATION_LABELS[claim.classification]}</Badge>
          <Badge>{claim.status.replace(/_/g, " ")}</Badge>
        </div>
      </div>
      <ErrorBox error={error} />
      {notice && <div className="rounded-xl bg-info-soft px-4 py-3 text-sm text-info">{notice}</div>}

      <div className="grid gap-6 lg:grid-cols-[1.25fr_1fr]">
        {/* conversation + remedies */}
        <div className="space-y-6">
          <Card className="space-y-4 p-5">
            <Bubble who={shopperName} tone="shopper">{claim.complaint_text}</Bubble>
            <Bubble who="AI">
              <p>
                I pulled up your verified purchase record for <b>{purchase.purchase_id}</b>, the carrier record and the payment events.
              </p>
              <p className="mt-2 text-ink-soft">{claim.classification_reason}</p>
              <p className="mt-2 text-xs text-ink-mute">
                {claim.interpreter?.mode === "gemini" ? `Complaint read by Gemini (${claim.interpreter.model}); ` : "Complaint read by fallback rules; "}
                classification made by deterministic evidence rules.
              </p>
            </Bubble>
            {claim.clarifications.map((c: any, i: number) => (
              <Bubble key={i} who={shopperName} tone="shopper">{c.text}</Bubble>
            ))}
            {claim.follow_up_question && claim.status === "needs_info" && (
              <>
                <Bubble who="AI">{claim.follow_up_question}</Bubble>
                <form
                  className="flex gap-2"
                  onSubmit={(e) => {
                    e.preventDefault();
                    act("clarify", () => api.post(`/api/claims/${claim.claim_id}/clarify`, { text: reply })).then(() => setReply(""));
                  }}
                >
                  <input value={reply} onChange={(e) => setReply(e.target.value)} placeholder="Your answer…"
                    className="min-w-0 flex-1 rounded-xl border border-paper-line bg-paper px-4 py-2.5 text-sm outline-none focus:border-ink-faint focus:bg-white" />
                  <Button type="submit" loading={busy === "clarify"} disabled={reply.trim().length < 2}>Send</Button>
                </form>
              </>
            )}
          </Card>

          {!accepted && (
            <div>
              <Eyebrow className="mb-3">Your options · eligibility checked against store policy</Eyebrow>
              <div className="space-y-3">
                {remedies.map((r: any) => {
                  const ok = r.status === "eligible";
                  return (
                    <button
                      key={r.remedy_id}
                      disabled={!ok}
                      onClick={() => { setSelected(r.remedy_id); setAgree(false); }}
                      className={cx(
                        "flex w-full items-start gap-4 rounded-2xl border bg-white p-4 text-left transition",
                        selected === r.remedy_id ? "border-ink ring-2 ring-ink/10" : "border-paper-line hover:border-ink-faint",
                        !ok && "cursor-not-allowed opacity-55",
                      )}
                    >
                      <span className={cx("mt-1 h-4 w-4 shrink-0 rounded-full border-2", selected === r.remedy_id ? "border-ink bg-ink" : "border-ink-faint")} />
                      <div className="flex-1">
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <p className="font-medium">{r.title}</p>
                          {r.amount_cents > 0 && <span className="font-semibold tabular-nums">{money(r.amount_cents)}</span>}
                        </div>
                        <p className="mt-0.5 text-sm text-ink-soft">{r.customer_facing_description}</p>
                        {!ok && <p className="mt-1 text-xs text-fail">Not available: {r.eligibility_result.reason}</p>}
                      </div>
                    </button>
                  );
                })}
              </div>

              {chosen && (
                <Card className="mt-4 animate-rise p-5">
                  <p className="text-sm font-semibold">Confirm: {chosen.title}</p>
                  {chosen.remedy_type === "exchange" && (
                    <div className="mt-2 rounded-xl bg-paper-sunk p-3">
                      <p className="mb-1 text-xs font-medium text-ink-mute">Replacement re-verified</p>
                      <CheckList checks={chosen.exchange_verification.checks} compact />
                    </div>
                  )}
                  {isRefundLike && (
                    <fieldset className="mt-3 space-y-2 text-sm">
                      <legend className="mb-1 text-xs font-medium text-ink-mute">Where should the money go? <SimLabel /></legend>
                      <label className="flex gap-2">
                        <input type="radio" checked={payout === "original_payment_method"} onChange={() => setPayout("original_payment_method")} />
                        <span>Refund to {purchase.payment_method_reference.label} <span className="text-ink-mute">(standard refund; real refunds can take several business days)</span></span>
                      </label>
                      <label className="flex gap-2">
                        <input type="radio" checked={payout === "push_to_card_concept"} onChange={() => setPayout("push_to_card_concept")} />
                        <span>Push-to-card payout <span className="text-ink-mute">(concept only: a different mechanism from a refund; eligibility depends on the card and program)</span></span>
                      </label>
                    </fieldset>
                  )}
                  <label className="mt-4 flex gap-2 text-sm">
                    <input type="checkbox" checked={agree} onChange={(e) => setAgree(e.target.checked)} className="mt-1" />
                    <span>I choose this option. I understand it's a simulation and no real money or goods move.</span>
                  </label>
                  <Button className="mt-4" disabled={!agree} loading={busy === "accept"} onClick={() => accept(chosen.remedy_id)}>
                    Confirm {chosen.remedy_type === "exchange" ? "exchange" : chosen.remedy_type === "manual_review" ? "review request" : "choice"}
                  </Button>
                </Card>
              )}
            </div>
          )}

          {accepted && (
            <Card className="animate-rise p-5">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="text-sm font-semibold">You chose: {accepted.title}</p>
                <SimLabel />
              </div>
              {accepted.execution?.simulated_transaction_id && (
                <div className="mt-2 text-sm">
                  <p>{money(accepted.execution.amount_cents)} · <span className="font-mono text-xs">{accepted.execution.simulated_transaction_id}</span></p>
                  <p className="mt-1 text-xs text-ink-mute">{accepted.execution.settlement_note}</p>
                </div>
              )}
              {accepted.remedy_type === "manual_review" && <p className="mt-2 text-sm text-ink-soft">A StrideLab team member will review this case. Nothing was decided automatically.</p>}
              {exchange && (
                <div className="mt-4 grid gap-5 sm:grid-cols-2">
                  <div>
                    <p className="text-xs font-medium text-ink-mute">Replacement · {exchange.exchange_purchase_id}</p>
                    <div className="mt-3"><ShipmentTimeline shipment={exchange.shipment} /></div>
                  </div>
                  <div className="space-y-3">
                    {!exchange.delivered ? (
                      <>
                        <p className="text-sm text-ink-soft">Inventory reserved. Return label <span className="font-mono text-xs">{accepted.execution.return_label}</span> issued.</p>
                        <Button size="sm" variant="secondary" loading={busy === "advance"}
                          onClick={() => act("advance", () => api.post("/api/demo/advance", { until: "delivered", purchase_id: exchange.exchange_purchase_id }))}>
                          Demo: advance to replacement delivery →
                        </Button>
                      </>
                    ) : claim.status === "exchange_in_progress" ? (
                      <div className="rounded-xl border border-brand/20 bg-brand-soft/60 p-4">
                        <p className="text-sm font-semibold">How does the replacement fit?</p>
                        <p className="mt-1 text-xs text-ink-mute">Your answer confirms the outcome. Confirmed outcomes can help future shoppers, after merchant review.</p>
                        <div className="mt-3 flex gap-2">
                          <Button size="sm" variant="success" loading={busy === "fits"}
                            onClick={() => act("fits", () => api.post(`/api/claims/${claim.claim_id}/outcome`, { shopper_id: shopperId, outcome: "fits" }))}>
                            Fits well
                          </Button>
                          <Button size="sm" variant="secondary" loading={busy === "nope"}
                            onClick={() => act("nope", () => api.post(`/api/claims/${claim.claim_id}/outcome`, { shopper_id: shopperId, outcome: "still_not_right" }))}>
                            Still not right
                          </Button>
                        </div>
                      </div>
                    ) : claim.validated_outcome ? (
                      <div className="rounded-xl bg-pass-soft p-4 text-sm text-pass">
                        <p className="font-semibold">Resolved: size {claim.validated_outcome.to_size} fits.</p>
                        <p className="mt-1 text-ink-soft">This confirmed outcome is now evidence the merchant can review.</p>
                      </div>
                    ) : (
                      <p className="text-sm text-ink-soft">Sent to a person for review.</p>
                    )}
                  </div>
                </div>
              )}
              {ruleProposal && (
                <div className="mt-4 rounded-xl border border-warn/30 bg-warn-soft p-4 text-sm">
                  <p className="font-semibold text-warn">Learning signal created</p>
                  <p className="mt-1 text-ink-soft">
                    This was confirmed outcome #{ruleProposal.confirmed_outcome_count} for size {ruleProposal.variant_scope.size}. StrideLab has a proposed
                    size note to review. Nothing changes for shoppers until a person approves it.
                  </p>
                  <Link href="/merchant/rules" className="mt-2 inline-block font-medium underline">Open merchant rule review →</Link>
                </div>
              )}
              <button className="mt-4 text-xs text-ink-mute underline-offset-2 hover:underline" disabled={!!busy}
                onClick={() => accept(accepted.remedy_id, "dup")}>
                Re-send this confirmation (idempotency check)
              </button>
            </Card>
          )}
        </div>

        {/* evidence */}
        <div className="space-y-6">
          <Card className="p-5">
            <p className="text-sm font-semibold">Evidence used</p>
            <ul className="mt-3 space-y-3">
              {claim.evidence_references.map((e: any, i: number) => (
                <li key={i} className="flex gap-3 text-sm">
                  <span className="text-lg leading-none">{EVIDENCE_ICON[e.type] ?? "•"}</span>
                  <div className="flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-medium capitalize">{e.type.replace(/_/g, " ")}</span>
                      <Badge tone={e.status === "confirmed" ? "pass" : "warn"}>{e.status === "confirmed" ? "confirmed by records" : "reported by you"}</Badge>
                    </div>
                    <p className="mt-0.5 text-ink-soft">{e.summary}</p>
                  </div>
                </li>
              ))}
            </ul>
            <div className="mt-4 rounded-lg bg-paper-sunk px-3 py-2 text-xs text-ink-soft">
              <b>Who pays:</b> {claim.responsibility.replace(/_/g, " ")} · determined separately from the issue classification.
              {claim.classification === "SUSPECTED_PRODUCT_FIT" && " Fit exchanges are free under store policy; no defect or fault is assumed."}
            </div>
          </Card>
          <Card className="p-5">
            <p className="text-sm font-semibold">Timeline</p>
            <ol className="mt-3 space-y-2 text-sm">
              {events.map((e: any) => (
                <li key={e.event_id} className="flex justify-between gap-3">
                  <span>{e.event_type.replace(/[._]/g, " ")}</span>
                  <span className="shrink-0 text-xs text-ink-mute">{dateTime(e.timestamp)}</span>
                </li>
              ))}
            </ol>
          </Card>
        </div>
      </div>
    </div>
  );
}
