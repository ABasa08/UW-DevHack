"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import AdminAccess from "./AdminAccess";
import { api, embeddedInAdmin } from "@/lib/api";
import { useLoad } from "@/lib/hooks";
import { cx } from "./ui";
import type { AppConfig } from "./ShopperProvider";

const NAV = [
  ["/app", "Dashboard"],
  ["/app/rules", "Rule review"],
  ["/app/claims", "Claims"],
  ["/app/orders", "Orders"],
  ["/app/setup", "Setup"],
  ["/app/evaluation", "Evaluation"],
] as const;

type Ctx = { config: AppConfig | null; version: number; bump: () => void; embedded: boolean; adminUrl: (path: string) => string | null };
const AdminContext = createContext<Ctx | null>(null);

export function useAdmin() {
  const ctx = useContext(AdminContext);
  if (!ctx) throw new Error("useAdmin must be used inside AdminShell");
  return ctx;
}

declare module "react" {
  // eslint-disable-next-line @typescript-eslint/no-namespace
  namespace JSX {
    interface IntrinsicElements {
      "ui-nav-menu": React.DetailedHTMLProps<React.HTMLAttributes<HTMLElement>, HTMLElement>;
    }
  }
}

function TopNav() {
  const path = usePathname();
  return (
    <header className="sticky top-0 z-30 border-b border-paper-line bg-paper/90 backdrop-blur">
      <div className="mx-auto flex max-w-7xl items-center gap-3 overflow-x-auto px-4 py-3 sm:px-6">
        <Link href="/app" className="mr-2 flex shrink-0 items-center gap-2.5">
          <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-ink font-display text-lg italic text-white">P</span>
          <span className="whitespace-nowrap font-display text-[17px] font-semibold tracking-tight">PerkPilot</span>
        </Link>
        <nav className="flex items-center gap-1">
          {NAV.map(([href, label]) => {
            const active = href === "/app" ? path === href : path.startsWith(href);
            return (
              <Link key={href} href={href}
                className={cx("whitespace-nowrap rounded-lg px-3 py-1.5 text-sm transition", active ? "bg-paper-sunk font-medium text-ink" : "text-ink-soft hover:text-ink")}>
                {label}
              </Link>
            );
          })}
        </nav>
      </div>
    </header>
  );
}

// The merchant console. Inside Shopify admin it uses App Bridge's navigation and session tokens; opened directly
// (local use), it shows its own navigation and asks for the admin token.
export default function AdminShell({ children }: { children: ReactNode }) {
  const [embedded, setEmbedded] = useState(false);
  const [version, setVersion] = useState(0);
  const bump = useCallback(() => setVersion((v) => v + 1), []);
  const cfg = useLoad(() => api.get<AppConfig>("/api/app/config"), []);
  useEffect(() => setEmbedded(embeddedInAdmin()), []);
  const shop = cfg.data?.shop ?? "";
  const adminUrl = useCallback(
    (p: string) => (shop ? `https://admin.shopify.com/store/${shop.replace(/\.myshopify\.com$/, "")}${p}` : null),
    [shop],
  );

  return (
    <AdminContext.Provider value={{ config: cfg.data, version, bump, embedded, adminUrl }}>
      <div className="min-h-screen bg-paper">
        {embedded ? (
          <ui-nav-menu>
            {NAV.map(([href, label]) => (
              <a key={href} href={href} rel={href === "/app" ? "home" : undefined}>{label}</a>
            ))}
          </ui-nav-menu>
        ) : (
          <TopNav />
        )}
        <main className={cx("mx-auto max-w-7xl px-4 pb-24 sm:px-6", embedded ? "pt-5" : "pt-8")}>
          <AdminAccess path="/api/merchant/settings" lockable>{() => children}</AdminAccess>
        </main>
      </div>
    </AdminContext.Provider>
  );
}
