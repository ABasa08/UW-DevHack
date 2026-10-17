"use client";

import Link from "next/link";
import { useDemo } from "@/components/DemoProvider";
import { Badge, Card, Empty, ErrorBox, Eyebrow, Loading } from "@/components/ui";
import { api } from "@/lib/api";
import { CLASSIFICATION_LABELS, dateTime } from "@/lib/format";
import { useLoad } from "@/lib/hooks";

export default function MerchantClaims() {
  const { version } = useDemo();
  const res = useLoad(() => api.get("/api/merchant/claims"), [version]);
  if (res.loading && !res.data) return <Loading />;
  if (res.error) return <ErrorBox error={res.error} onRetry={res.reload} />;
  const claims = res.data.claims;
  return (
    <div className="space-y-6">
      <div>
        <Eyebrow>Post-purchase</Eyebrow>
        <h1 className="mt-1 font-display text-3xl font-semibold">Claims</h1>
        <p className="mt-1 text-sm text-ink-mute">Every classification links to the evidence it used. Seeded history is labeled.</p>
      </div>
      {claims.length === 0 ? (
        <Empty title="No claims" />
      ) : (
        <Card className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-paper-sunk text-left text-xs text-ink-mute">
              <tr>
                <th className="px-5 py-2 font-medium">Claim</th>
                <th className="px-3 py-2 font-medium">Complaint</th>
                <th className="px-3 py-2 font-medium">Classification</th>
                <th className="px-3 py-2 font-medium">Evidence</th>
                <th className="px-3 py-2 font-medium">Status</th>
                <th className="px-3 py-2 font-medium">Opened</th>
              </tr>
            </thead>
            <tbody>
              {claims.map((c: any) => (
                <tr key={c.claim_id} className="border-t border-paper-line align-top">
                  <td className="px-5 py-2">
                    {c.source === "seed" ? <span className="font-mono text-xs">{c.claim_id}</span> : (
                      <Link className="font-mono text-xs underline" href={`/claims/${c.claim_id}`}>{c.claim_id}</Link>
                    )}
                    {c.source === "seed" && <Badge className="ml-2">seed</Badge>}
                  </td>
                  <td className="max-w-xs px-3 py-2 italic text-ink-soft">“{c.complaint_text}”</td>
                  <td className="px-3 py-2">{CLASSIFICATION_LABELS[c.classification]}</td>
                  <td className="px-3 py-2 text-ink-soft">{(c.evidence_status ?? "").replace(/_/g, " ")}</td>
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
