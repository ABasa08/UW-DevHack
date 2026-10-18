"use client";

import Link from "next/link";
import { useState } from "react";
import { useAdmin } from "@/components/AdminShell";
import { Badge, Button, Card, Empty, ErrorBox, Eyebrow, Loading } from "@/components/ui";
import { api } from "@/lib/api";
import { CLASSIFICATION_LABELS, dateTime, money } from "@/lib/format";
import { useLoad } from "@/lib/hooks";

export default function MerchantClaims() {
  const { version, bump, config } = useAdmin();
  const res = useLoad(() => api.admin("/api/merchant/claims"), [version]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const demo = config?.mode === "demo";

  async function execute(remedyId: string) {
    setBusy(remedyId);
    setError(null);
    try {
      await api.adminPost(`/api/merchant/remedies/${remedyId}/execute`);
      bump();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(null);
    }
  }

  if (res.loading && !res.data) return <Loading />;
  if (res.error) return <ErrorBox error={res.error} onRetry={res.reload} />;
  const claims = res.data.claims;
  const waiting = claims.filter((c: any) => c.status === "awaiting_merchant");
  return (
    <div className="space-y-6">
      <div>
        <Eyebrow>Post-purchase</Eyebrow>
        <h1 className="mt-1 font-display text-3xl font-semibold">Claims</h1>
        <p className="mt-1 text-sm text-ink-mute">
          Every classification links to the evidence it used. On Shopify test orders the shopper&apos;s chosen remedy runs immediately;
          on real orders it waits here for your approval.
        </p>
      </div>
      <ErrorBox error={error} />

      {waiting.length > 0 && (
        <Card className="overflow-hidden ring-2 ring-warn/30">
          <div className="border-b border-paper-line px-5 py-4">
            <p className="font-semibold">Waiting for your approval</p>
            <p className="text-sm text-ink-mute">Approving runs the remedy in Shopify: a refund through refundCreate, or a free replacement order.</p>
          </div>
          <ul className="divide-y divide-paper-line">
            {waiting.map((c: any) => (
              <li key={c.claim_id} className="flex flex-wrap items-center justify-between gap-3 px-5 py-3 text-sm">
                <div>
                  <p className="font-medium">{c.accepted_remedy?.title} {c.accepted_remedy?.amount_cents > 0 && <span className="tabular-nums">· {money(c.accepted_remedy.amount_cents)}</span>}</p>
                  <p className="text-ink-mute">“{c.complaint_text}” · {CLASSIFICATION_LABELS[c.classification]} · order {c.purchase_id}</p>
                </div>
                <Button size="sm" variant="success" loading={busy === c.accepted_remedy_id} onClick={() => execute(c.accepted_remedy_id)}>
                  Approve in Shopify
                </Button>
              </li>
            ))}
          </ul>
        </Card>
      )}

      {claims.length === 0 ? (
        <Empty title="No claims yet">Shoppers report problems from My orders in the storefront assistant.</Empty>
      ) : (
        <Card className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-paper-sunk text-left text-xs text-ink-mute">
              <tr>
                <th className="px-5 py-2 font-medium">Claim</th>
                <th className="px-3 py-2 font-medium">Complaint</th>
                <th className="px-3 py-2 font-medium">Classification</th>
                <th className="px-3 py-2 font-medium">Evidence</th>
                <th className="px-3 py-2 font-medium">Remedy</th>
                <th className="px-3 py-2 font-medium">Status</th>
                <th className="px-3 py-2 font-medium">Opened</th>
              </tr>
            </thead>
            <tbody>
              {claims.map((c: any) => (
                <tr key={c.claim_id} className="border-t border-paper-line align-top">
                  <td className="px-5 py-2">
                    {demo && c.source !== "seed" ? (
                      <Link className="font-mono text-xs underline" href={`/embed/claims/${c.claim_id}`}>{c.claim_id}</Link>
                    ) : (
                      <Link className="font-mono text-xs underline" href={`/app/orders/${c.purchase_id}`}>{c.claim_id}</Link>
                    )}
                    {c.source === "seed" && <Badge className="ml-2">sample</Badge>}
                  </td>
                  <td className="max-w-xs px-3 py-2 italic text-ink-soft">“{c.complaint_text}”</td>
                  <td className="px-3 py-2">{CLASSIFICATION_LABELS[c.classification]}</td>
                  <td className="px-3 py-2 text-ink-soft">{(c.evidence_status ?? "").replace(/_/g, " ")}</td>
                  <td className="px-3 py-2 text-ink-soft">{c.accepted_remedy?.title ?? "—"}</td>
                  <td className="px-3 py-2">{c.status.replace(/_/g, " ")}</td>
                  <td className="px-3 py-2 text-xs text-ink-mute">{dateTime(c.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </div>
  );
}
