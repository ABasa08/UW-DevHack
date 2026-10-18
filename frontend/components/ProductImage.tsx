import ShoeArt from "./ShoeArt";

export function colorsOf(product: any): { color: string; hex: string }[] {
  const seen = new Map<string, string>();
  for (const v of product.variants) if (!seen.has(v.color)) seen.set(v.color, v.color_hex);
  return [...seen.entries()].map(([color, hex]) => ({ color, hex }));
}

export function ProductImage({ product, color, className = "" }: { product: any; color?: string; className?: string }) {
  const colors = colorsOf(product);
  const hex = colors.find((c) => c.color === color)?.hex ?? colors[0]?.hex;
  if (product.image.url) {
    // eslint-disable-next-line @next/next/no-img-element -- Shopify CDN image, no next/image domain config needed
    return <img src={product.image.url} alt={product.name} className={`rounded-xl bg-white object-contain ${className}`} />;
  }
  if (product.source === "shopify") {
    // A Shopify product without a featured image: a neutral tile, never a stand-in picture of something else.
    return (
      <div className={`flex items-center justify-center rounded-xl bg-paper-sunk font-display text-3xl text-ink-faint ${className}`} aria-hidden="true">
        {product.name.charAt(0)}
      </div>
    );
  }
  return (
    <div
      className={`flex items-center justify-center rounded-xl ${className}`}
      style={{ background: `linear-gradient(135deg, ${product.image.accent_hex}14, ${product.image.accent_hex}2e)` }}
    >
      <ShoeArt color={hex} accent={product.image.accent_hex} className="w-4/5" />
    </div>
  );
}
