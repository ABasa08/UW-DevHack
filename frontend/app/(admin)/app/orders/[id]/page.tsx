"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import AdminAccess from "@/components/AdminAccess";
import { useAdmin } from "@/components/AdminShell";
import CheckList from "@/components/CheckList";
import { EventLog } from "@/components/Timeline";
import { Badge, Button, Card, ErrorBox, KV } from "@/components/ui";
import { api } from "@/lib/api";
import { dateTime, money, sizeLabel, STATUS_LABELS } from "@/lib/format";

export default function OrderAudit() {
  const { id } = useParams<{ id: string }>();
  const { version, bump } = useAdmin();
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  async function act(key: string, path: string) {
    setBusy(key);
    setError(null);
    try {
      await api.adminPost(path);
      bump();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(null);
    }
  }
  return (
    <div className="space-y-6">
      <Link href="/app/orders" className="text-sm text-ink-soft underline-offset-2 hover:underline">All orders</Link>
      <AdminAccess key={version} path={`/api/admin/purchases/${id}/audit`}>
        {({ purchase: p, integrity, verification_results, events, shopify_admin_url, shipment }) => (
          <div className="space-y-6">
            <div className="flex flex-wrap items-end justify-between gap-3">
              <div>
                <p className="font-mono text-xs text-ink-mute">{p.shopify_order_name ? `Shopify ${p.shopify_order_name} · ` : ""}{p.purchase_id}</p>
                <h1 className="mt-1 font-display text-3xl font-semibold">{p.product_name}</h1>
                <p className="text-sm text-ink-mute">
                  <span className="capitalize">{p.color}</span> {sizeLabel(p.size)}, qty {p.quantity}, for {p.shopper_id}
                </p>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone={integrity.hash_matches && integrity.event_chain_valid ? "pass" : "fail"}>
                  {integrity.hash_matches && integrity.event_chain_valid ? "Record verified" : "Integrity failure"}
                </Badge>
                {p.shopify_order_id && (
                  <>
                    <Button size="sm" variant="secondary" loading={busy === "refresh"} onClick={() => act("refresh", `/api/merchant/purchases/${p.purchase_id}/refresh`)}>
                      Refresh from Shopify
                    </Button>
                    {p.shopify_test && !shipment?.delivered_at && (
                      <Button size="sm" loading={busy === "ship"} onClick={() => act("ship", `/api/merchant/purchases/${p.purchase_id}/ship-and-deliver`)}
                        title="Test orders only: creates the Shopify fulfillment and a DELIVERED fulfillment event">
                        Ship &amp; mark delivered (test order)
                      </Button>
                    )}
                    {shopify_admin_url && (
                      <a href={shopify_admin_url} target="_top" rel="noopener" className="rounded-lg border border-paper-line bg-white px-3 py-1.5 text-sm font-medium hover:border-ink-faint">
                        Open in Shopify →
                      </a>
                    )}
                  </>
                )}
              </div>
            </div>
            <ErrorBox error={error} />

            <div className="grid gap-6 lg:grid-cols-2">
              <Card className="p-5">
                <h2 className="font-semibold">Record integrity</h2>
                <div className="mt-2 flex flex-wrap gap-2">
                  <Badge tone={integrity.hash_matches ? "pass" : "fail"}>{integrity.hash_matches ? "Hash matches" : "Hash mismatch"}</Badge>
                  <Badge tone={integrity.event_chain_valid ? "pass" : "fail"}>{integrity.event_chain_valid ? "Event chain intact" : "Event chain broken"}</Badge>
                </div>
                <p className="mt-3 break-all font-mono text-xs text-ink-soft">SHA-256 {integrity.record_hash}</p>
                <p className="mt-2 text-xs text-ink-mute">{integrity.note}</p>
                <div className="mt-4 border-t border-paper-line pt-2">
                  <KV k="Status" v={STATUS_LABELS[p.status] ?? p.status} />
                  <KV k="Approved charge" v={money(p.approved_charge_cents)} />
                  <KV k="Payment" v={`${p.payment_method_reference.label}${p.payment_method_reference.simulated ? " (simulated)" : ""}`} />
                  <KV k={p.shopify_order_id ? "Shopify order" : "Transaction"} v={<span className="font-mono text-xs">{p.shopify_order_name ?? p.simulated_transaction_id}</span>} />
                  {p.shopify_financial_status && <KV k="Shopify status" v={`${p.shopify_financial_status.replace(/_/g, " ")} · ${p.shopify_fulfillment_status?.replace(/_/g, " ")}`} />}
                  <KV k="Authorization event" v={<span className="font-mono text-xs">{p.authorization_event_id ?? "none"}</span>} />
                  <KV k="Intent" v={<span className="font-mono text-xs">{p.intent_id ?? "none"} (v{p.confirmation_version ?? "-"})</span>} />
                  <KV k="Placed" v={dateTime(p.created_at)} />
                </div>
              </Card>
              <Card className="p-5">
                <h2 className="font-semibold">Verification at purchase ({verification_results.length} checks)</h2>
                <CheckList checks={verification_results} />
              </Card>
            </div>

            <Card className="p-5">
              <h2 className="font-semibold">Event log ({events.length})</h2>
              <div className="mt-3"><EventLog events={events} /></div>
            </Card>

            <details className="rounded-2xl border border-paper-line bg-white p-5 text-sm">
              <summary className="cursor-pointer font-semibold">Full purchase record</summary>
              <pre className="mt-3 overflow-x-auto rounded-lg bg-paper-sunk p-3 text-xs">{JSON.stringify(p, null, 2)}</pre>
            </details>
          </div>
        )}
      </AdminAccess>
    </div>
  );
}
