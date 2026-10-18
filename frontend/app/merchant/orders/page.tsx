"use client";

import Link from "next/link";
import AdminAccess from "@/components/AdminAccess";
import { useDemo } from "@/components/DemoProvider";
import { Badge, Card, Empty } from "@/components/ui";
import { dateTime, money, STATUS_LABELS } from "@/lib/format";

export default function MerchantOrders() {
  const { version } = useDemo();
  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-display text-3xl font-semibold">Order audit</h1>
        <p className="mt-1 text-sm text-ink-mute">Integrity status for every order. Open one for its hash, event log and verification checks.</p>
      </div>
      <AdminAccess key={version} path="/api/admin/purchases">
        {(data) =>
          data.purchases.length === 0 ? (
            <Empty title="No orders yet" />
          ) : (
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
                        <Link className="font-mono text-xs underline" href={`/merchant/orders/${p.purchase_id}`}>{p.purchase_id}</Link>
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
          )
        }
      </AdminAccess>
    </div>
  );
}
