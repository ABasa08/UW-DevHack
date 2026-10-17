"use client";

import { useParams } from "next/navigation";
import { useState } from "react";
import { ProductImage, colorsOf } from "@/components/ProductCard";
import { Badge, Button, Card, ErrorBox, Eyebrow, Loading, cx } from "@/components/ui";
import { useDemo } from "@/components/DemoProvider";
import { api } from "@/lib/api";
import { CATEGORY_LABELS, money } from "@/lib/format";
import { useLoad } from "@/lib/hooks";

export default function ProductPage() {
  const { id } = useParams<{ id: string }>();
  const { version } = useDemo();
  const res = useLoad(() => api.get(`/api/products/${id}`), [id, version]);
  const [color, setColor] = useState<string | null>(null);
  const [size, setSize] = useState<string | null>(null);

  if (res.loading && !res.data) return <Loading />;
  if (res.error) return <ErrorBox error={res.error} onRetry={res.reload} />;
  const { product, guidance_by_size, offers } = res.data;
  const colors = colorsOf(product);
  const activeColor = color ?? colors[0].color;
  const variants = product.variants.filter((v: any) => v.color === activeColor);
  const guidance = size ? guidance_by_size[size] : null;
  const q = `Find ${activeColor} ${CATEGORY_LABELS[product.category].toLowerCase()}${size ? ` in size ${size}` : ""}, under $${Math.ceil(
    (product.price_cents + 1600) / 100 / 10,
  ) * 10} including shipping and tax.`;

  return (
    <div className="grid gap-10 lg:grid-cols-[1.1fr_1fr]">
      <ProductImage product={product} color={activeColor} className="aspect-[4/3] w-full" />
      <div>
        <Eyebrow>{product.brand} · {CATEGORY_LABELS[product.category]}</Eyebrow>
        <h1 className="mt-2 font-display text-3xl font-semibold">{product.name}</h1>
        <p className="mt-2 text-2xl tabular-nums">{money(product.price_cents)}</p>
        <p className="mt-4 text-ink-soft">{product.description}</p>

        <div className="mt-6">
          <p className="text-sm font-medium">Color: <span className="capitalize text-ink-soft">{activeColor}</span></p>
          <div className="mt-2 flex gap-2">
            {colors.map((c) => (
              <button
                key={c.color}
                onClick={() => { setColor(c.color); setSize(null); }}
                className={cx("h-9 w-9 rounded-full border-2", c.color === activeColor ? "border-ink" : "border-transparent")}
                style={{ background: c.hex, boxShadow: "inset 0 0 0 1px rgba(0,0,0,0.12)" }}
                aria-label={c.color}
              />
            ))}
          </div>
        </div>

        <div className="mt-6">
          <p className="text-sm font-medium">Size (US)</p>
          <div className="mt-2 flex flex-wrap gap-2">
            {variants.map((v: any) => {
              const stock = product.inventory_by_variant[v.variant_id];
              return (
                <button
                  key={v.variant_id}
                  disabled={stock === 0}
                  onClick={() => setSize(v.size)}
                  className={cx(
                    "min-w-[64px] rounded-lg border px-3 py-2 text-sm",
                    size === v.size ? "border-ink bg-ink text-white" : "border-paper-line bg-white hover:border-ink-faint",
                    stock === 0 && "cursor-not-allowed line-through opacity-40",
                  )}
                >
                  {v.size}
                  <span className="block text-[10px] opacity-70">{stock === 0 ? "sold out" : `${stock} left`}</span>
                </button>
              );
            })}
          </div>
          {guidance && guidance.length > 0 && (
            <div className="mt-3 rounded-xl border border-warn/30 bg-warn-soft px-4 py-3 text-sm text-warn">
              <p className="font-medium">Fit note approved by StrideLab</p>
              <p className="mt-0.5">{guidance[0].message}</p>
            </div>
          )}
          <p className="mt-3 text-xs text-ink-mute">{product.size_information.fit_note}</p>
        </div>

        <div className="mt-8 flex flex-wrap gap-3">
          <Button href={`/assistant?q=${encodeURIComponent(q)}`} size="lg">Buy with the verified assistant</Button>
        </div>
        <p className="mt-2 text-xs text-ink-mute">Purchases go through PerkPilot's verification and your explicit approval.</p>

        <Card className="mt-8 p-5">
          <p className="text-sm font-semibold">Delivery &amp; offers</p>
          <ul className="mt-2 space-y-1 text-sm text-ink-soft">
            {product.delivery_options.map((o: any) => (
              <li key={o.id}>{o.label} — {o.price_cents ? money(o.price_cents) : "Free"}</li>
            ))}
            <li>Demo tax: {money(product.demo_tax_cents)} per pair (fixture)</li>
          </ul>
          <div className="mt-3 flex flex-wrap gap-2">
            {offers.map((o: any) => (
              <Badge key={o.offer_id} tone={o.offer_type === "coupon" ? "brand" : "info"}>{o.label}</Badge>
            ))}
          </div>
        </Card>

        <Card className="mt-4 p-5">
          <p className="text-sm font-semibold">Size chart</p>
          <table className="mt-2 w-full text-sm">
            <thead className="text-left text-ink-mute">
              <tr><th className="py-1 font-normal">US</th><th className="font-normal">EU</th><th className="font-normal">Foot length (cm)</th></tr>
            </thead>
            <tbody>
              {product.size_information.chart.map((r: any) => (
                <tr key={r.us} className="border-t border-paper-line"><td className="py-1">{r.us}</td><td>{r.eu}</td><td>{r.cm}</td></tr>
              ))}
            </tbody>
          </table>
        </Card>
      </div>
    </div>
  );
}
