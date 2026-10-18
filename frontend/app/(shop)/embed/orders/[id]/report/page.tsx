"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import { ProductImage } from "@/components/ProductImage";
import { useShopper, withPersona } from "@/components/ShopperProvider";
import { Button, Card, ErrorBox, Eyebrow, Loading } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { dayLabel, money, sizeLabel } from "@/lib/format";
import { useLoad } from "@/lib/hooks";

const EXAMPLES = ["This feels too tight.", "They sent the wrong size.", "I was charged more than I approved.", "This isn't what I expected."];

export default function ReportPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const { persona, personaQuery, bump } = useShopper();
  const res = useLoad(() => api.get(withPersona(`/api/purchases/${id}`, personaQuery)), [id, personaQuery]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [existing, setExisting] = useState<string | null>(null);

  if (res.loading && !res.data) return <Loading />;
  if (res.error) return <ErrorBox error={res.error} onRetry={res.reload} />;
  const { purchase: p, product } = res.data;

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      const v = await api.post("/api/claims", { ...persona, purchase_id: p.purchase_id, complaint_text: text });
      bump();
      router.push(`/embed/claims/${v.claim.claim_id}`);
    } catch (e) {
      if (e instanceof ApiError && e.detail?.claim_id) setExisting(e.detail.claim_id);
      setError(e);
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <div>
        <Eyebrow>Resolution center</Eyebrow>
        <h1 className="mt-1 font-display text-3xl font-semibold">What went wrong?</h1>
      </div>
      <Card className="flex items-center gap-4 p-4">
        <ProductImage product={product} color={p.color} className="h-16 w-20 shrink-0" />
        <div className="text-sm">
          <p className="font-medium">{p.product_name}{p.color && <> · <span className="capitalize">{p.color}</span></>} · {sizeLabel(p.size)}</p>
          <p className="text-ink-mute">
            {p.shopify_order_name ?? p.purchase_id} · {money(p.approved_charge_cents)} · {p.shipping?.delivered_on ? `delivered ${dayLabel(p.shipping.delivered_on)}` : "not delivered yet"}
          </p>
        </div>
        <span className="ml-auto hidden rounded-full bg-pass-soft px-3 py-1 text-xs text-pass sm:block">Order details attached automatically</span>
      </Card>
      <Card className="p-5">
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={3}
          placeholder="Tell us in your own words…"
          className="w-full resize-none rounded-xl border border-paper-line bg-paper px-4 py-3 text-[15px] outline-none focus:border-ink-faint focus:bg-white"
          aria-label="Describe the problem"
        />
        <div className="mt-3 flex flex-wrap gap-2">
          {EXAMPLES.map((e) => (
            <button key={e} onClick={() => setText(e)} className="rounded-full border border-paper-line px-3 py-1 text-xs text-ink-soft hover:border-ink-faint">{e}</button>
          ))}
        </div>
        <div className="mt-4 flex items-center justify-between gap-3">
          <p className="text-xs text-ink-mute">PerkPilot compares your report with your order, delivery and payment details. It won't guess.</p>
          <Button onClick={submit} loading={busy} disabled={text.trim().length < 2}>Submit</Button>
        </div>
      </Card>
      <ErrorBox error={error} />
      {existing && <Link className="text-sm font-medium underline" href={`/embed/claims/${existing}`}>Open the existing issue →</Link>}
    </div>
  );
}
