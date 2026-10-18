"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import { useShopper } from "./ShopperProvider";
import { ErrorBox, Loading, cx } from "./ui";

function Tab({ href, children, active }: { href: string; children: ReactNode; active: boolean }) {
  return (
    <Link href={href} aria-current={active ? "page" : undefined}
      className={cx("rounded-full px-3 py-1.5 text-sm transition", active ? "bg-ink text-white" : "text-ink-soft hover:bg-paper-sunk")}>
      {children}
    </Link>
  );
}

// The assistant as it appears inside the store's theme: the store's name and two tabs (the theme panel has the close button).
export default function EmbedShell({ children }: { children: ReactNode }) {
  const { ready, error, config, storeName } = useShopper();
  const path = usePathname();
  const ordersActive = path.startsWith("/embed/orders") || path.startsWith("/embed/claims");
  return (
    <div className="flex min-h-screen flex-col bg-paper">
      <header className="sticky top-0 z-20 border-b border-paper-line bg-paper/95 backdrop-blur">
        <div className="flex items-center gap-3 py-3 pl-4 pr-14">
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-ink font-display text-base italic text-white" aria-hidden="true">
            {storeName.charAt(0).toUpperCase()}
          </span>
          <div className="min-w-0 leading-tight">
            <p className="truncate text-[15px] font-semibold">{storeName}</p>
            <p className="text-[11px] text-ink-mute">Shopping assistant · verified by PerkPilot</p>
          </div>
          {/* Inside the store, the theme's panel provides the close button (top right). */}
        </div>
        <nav className="flex gap-1 px-3 pb-2" aria-label="Assistant">
          <Tab href="/embed" active={!ordersActive}>Assistant</Tab>
          <Tab href="/embed/orders" active={ordersActive}>My orders</Tab>
        </nav>
      </header>
      <main className="flex-1 px-4 pb-10 pt-5">
        {error ? <ErrorBox error={error} /> : !ready ? <Loading label="Starting the assistant…" /> : children}
      </main>
      <footer className="border-t border-paper-line px-4 py-3 text-center text-[11px] leading-relaxed text-ink-mute">
        {config?.mode === "shopify"
          ? `You pay on ${storeName}'s own Shopify checkout. PerkPilot never sees your card number.`
          : config?.mode === "demo"
            ? "Demo store: payments, refunds and shipping are simulated."
            : "PerkPilot checks every recommendation before you buy."}
      </footer>
    </div>
  );
}
