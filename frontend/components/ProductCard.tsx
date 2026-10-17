import Link from "next/link";
import { CATEGORY_LABELS, money } from "@/lib/format";
import ShoeArt from "./ShoeArt";

export function colorsOf(product: any): { color: string; hex: string }[] {
  const seen = new Map<string, string>();
  for (const v of product.variants) if (!seen.has(v.color)) seen.set(v.color, v.color_hex);
  return [...seen.entries()].map(([color, hex]) => ({ color, hex }));
}

export function ProductImage({ product, color, className = "" }: { product: any; color?: string; className?: string }) {
  const colors = colorsOf(product);
  const hex = colors.find((c) => c.color === color)?.hex ?? colors[0]?.hex;
  return (
    <div
      className={`flex items-center justify-center rounded-xl ${className}`}
      style={{ background: `linear-gradient(135deg, ${product.image.accent_hex}14, ${product.image.accent_hex}2e)` }}
    >
      <ShoeArt color={hex} accent={product.image.accent_hex} className="w-4/5" />
    </div>
  );
}

export default function ProductCard({ product }: { product: any }) {
  const colors = colorsOf(product);
  const stock = Object.values(product.inventory_by_variant as Record<string, number>).reduce((a, b) => a + b, 0);
  return (
    <Link href={`/products/${product.product_id}`} className="group block">
      <ProductImage product={product} className="aspect-[4/3] transition group-hover:shadow-lift" />
      <div className="mt-3 flex items-start justify-between gap-3">
        <div>
          <p className="text-xs text-ink-mute">{CATEGORY_LABELS[product.category]}</p>
          <p className="font-medium leading-snug group-hover:underline">{product.name}</p>
        </div>
        <p className="font-medium tabular-nums">{money(product.price_cents)}</p>
      </div>
      <div className="mt-2 flex items-center gap-1.5">
        {colors.map((c) => (
          <span key={c.color} title={c.color} className="h-3.5 w-3.5 rounded-full border border-black/10" style={{ background: c.hex }} />
        ))}
        <span className="ml-auto text-xs text-ink-mute">★ {product.rating} · {stock} in stock</span>
      </div>
    </Link>
  );
}
