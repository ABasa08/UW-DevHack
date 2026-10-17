"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import ProductCard from "@/components/ProductCard";
import ShoeArt from "@/components/ShoeArt";
import { Button, ErrorBox, Eyebrow, Loading } from "@/components/ui";
import { useDemo } from "@/components/DemoProvider";
import { api } from "@/lib/api";
import { useLoad } from "@/lib/hooks";

const EXAMPLE = "Find black running shoes in size 10, under $120 including shipping and tax, delivered by Friday.";

export default function Storefront() {
  const router = useRouter();
  const { version, state, shopperId } = useDemo();
  const [text, setText] = useState("");
  const products = useLoad(() => api.get("/api/products"), [version]);
  const shopper = state?.shoppers.find((s) => s.shopper_id === shopperId);

  return (
    <div className="space-y-14">
      <section className="grid items-center gap-10 overflow-hidden rounded-3xl bg-white p-8 shadow-card md:grid-cols-[1.2fr_1fr] md:p-12">
        <div>
          <Eyebrow>Hi {shopper?.display_name ?? "there"} · Fall running collection</Eyebrow>
          <h1 className="mt-3 font-display text-4xl font-semibold leading-[1.05] tracking-tight md:text-5xl">
            Tell us what you need.
            <br />
            <span className="text-ink-mute">We'll check it before you buy.</span>
          </h1>
          <p className="mt-4 max-w-lg text-ink-soft">
            The PerkPilot assistant turns your request into requirements you confirm, then verifies size, total charge, offers and delivery
            date before anything reaches checkout.
          </p>
          <form
            className="mt-6 flex flex-col gap-2 sm:flex-row"
            onSubmit={(e) => {
              e.preventDefault();
              router.push(`/assistant?q=${encodeURIComponent(text || EXAMPLE)}`);
            }}
          >
            <input
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder={EXAMPLE}
              className="min-w-0 flex-1 rounded-xl border border-paper-line bg-paper px-4 py-3 text-sm outline-none focus:border-ink-faint focus:bg-white"
              aria-label="Describe what you're shopping for"
            />
            <Button type="submit" size="lg">Ask the assistant</Button>
          </form>
          <button className="mt-2 text-xs text-ink-mute underline-offset-2 hover:underline" onClick={() => setText(EXAMPLE)}>
            Use the demo request
          </button>
        </div>
        <div className="relative hidden md:block">
          <div className="absolute inset-0 rounded-full bg-gradient-to-br from-ember/20 to-brand/10 blur-2xl" />
          <ShoeArt color="#1f2328" accent="#e07a2f" className="relative w-full -rotate-6 drop-shadow-xl" />
        </div>
      </section>

      <section className="grid gap-4 md:grid-cols-3">
        {[
          ["Buy it right", "An AI reads your request; deterministic rules verify the cart against what you confirmed."],
          ["Fix it fast", "Problems are diagnosed against your verified purchase record. No re-explaining."],
          ["Learn from every return", "Confirmed outcomes become merchant-approved guidance for the next shopper."],
        ].map(([t, d], i) => (
          <div key={t} className="rounded-2xl border border-paper-line bg-white/60 p-5">
            <p className="font-display text-sm text-ink-mute">0{i + 1}</p>
            <p className="mt-1 font-semibold">{t}</p>
            <p className="mt-1 text-sm text-ink-soft">{d}</p>
          </div>
        ))}
      </section>

      <section>
        <div className="mb-5 flex items-end justify-between">
          <div>
            <Eyebrow>Catalog</Eyebrow>
            <h2 className="font-display text-2xl font-semibold">Shop all footwear</h2>
          </div>
        </div>
        {products.loading && !products.data ? (
          <Loading label="Loading catalog…" />
        ) : products.error ? (
          <ErrorBox error={products.error} onRetry={products.reload} />
        ) : (
          <div className="grid grid-cols-1 gap-x-6 gap-y-10 sm:grid-cols-2 lg:grid-cols-4">
            {products.data.products.map((p: any) => (
              <ProductCard key={p.product_id} product={p} />
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
