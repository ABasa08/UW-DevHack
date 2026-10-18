"use client";

import Link from "next/link";
import AdminAccess from "@/components/AdminAccess";
import { useAdmin } from "@/components/AdminShell";
import { Badge, Card, Empty } from "@/components/ui";
import { dateTime, money, STATUS_LABELS } from "@/lib/format";

export default function MerchantOrders() {
  const { version } = useAdmin();
  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-display text-3xl font-semibold">Orders</h1>
        <p className="mt-1 text-sm text-ink-mute">
          Every PerkPilot-verified Shopify order with its integrity status. Open one for the verification checks, the Shopify events read back
          (payment, fulfillment, refunds), the record hash and the event chain.
        </p>
      </div>
      <AdminAccess key={version} path="/api/admin/purchases">
        {(data) =>
          data.purchases.length === 0 && !data.awaiting_payment?.length ? (
            <Empty title="No verified orders yet">Orders appear here when a shopper pays for a PerkPilot-verified checkout.</Empty>
          ) : (
            <>
            {data.awaiting_payment?.length > 0 && (
              <Card className="p-5">
                <p className="text-sm font-semibold">Checkouts waiting for payment</p>
                <ul className="mt-2 space-y-1 text-sm text-ink-soft">
                  {data.awaiting_payment.map((h: any) => (
                    <li key={h.purchase_id}>Draft order {h.draft_order_name} · {money(h.approved_charge_cents)} approved · {dateTime(h.approved_at)}</li>
                  ))}
                </ul>
              </Card>
            )}
            <Card className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="bg-paper-sunk text-left text-xs text-ink-mute">
                  <tr>
                    <th className="px-5 py-2 font-medium">Order</th>
                    <th className="px-3 py-2 font-medium">Shopper</th>
                    <th className="px-3 py-2 font-medium">Item</th>
                    <th className="px-3 py-2 text-right font-medium">Charged</th>
                    <th className="px-3 py-2 font-medium">Status</th>
                    <th className="px-3 py-2 font-medium">Integrity</th>
                    <th className="px-3 py-2 font-medium">Placed</th>
                  </tr>
                </thead>
                <tbody>
                  {data.purchases.map((p: any) => (
                    <tr key={p.purchase_id} className="border-t border-paper-line">
                      <td className="px-5 py-2">
                        <Link className="font-mono text-xs underline" href={`/app/orders/${p.purchase_id}`}>{p.shopify_order_name ?? p.purchase_id}</Link>
                        {p.kind === "exchange" && <Badge className="ml-2" tone="info">exchange</Badge>}
                        {p.shopify_test && <Badge className="ml-2">test</Badge>}
                        {p.source === "seed" && <Badge className="ml-2">seed</Badge>}
                      </td>
                      <td className="px-3 py-2 text-ink-soft">{p.shopper_id}</td>
                      <td className="px-3 py-2">{p.product_name}</td>
                      <td className="px-3 py-2 text-right tabular-nums">{money(p.approved_charge_cents)}</td>
                      <td className="px-3 py-2">{STATUS_LABELS[p.status] ?? p.status}</td>
                      <td className="px-3 py-2"><Badge tone={p.verified ? "pass" : "fail"}>{p.verified ? "Verified" : "Failed"}</Badge></td>
                      <td className="px-3 py-2 text-xs text-ink-mute">{dateTime(p.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Card>
            </>
          )
        }
      </AdminAccess>
    </div>
  );
}
