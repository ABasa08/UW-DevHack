import type { Metadata } from "next";
import { Fraunces, Inter } from "next/font/google";
import AdminShell from "@/components/AdminShell";
import "../globals.css";

const sans = Inter({ subsets: ["latin"], variable: "--font-sans" });
const display = Fraunces({ subsets: ["latin"], variable: "--font-display" });

export const metadata: Metadata = { title: "PerkPilot · Merchant", description: "Verified purchases, claims and approved fit guidance." };
export const dynamic = "force-dynamic"; // the App Bridge API key is read from the backend at request time

async function clientId(): Promise<string> {
  try {
    const r = await fetch(`${process.env.PERKPILOT_API_URL || "http://127.0.0.1:8000"}/api/app/config`, { cache: "no-store" });
    return r.ok ? ((await r.json()).client_id as string) || "" : "";
  } catch {
    return "";
  }
}

// App Bridge must load synchronously before any other script, and only inside Shopify admin's iframe;
// opened directly, the console runs without it and asks for the admin token instead.
const LOAD_APP_BRIDGE = `if (window.top !== window.self && document.querySelector('meta[name="shopify-api-key"]').content) {
  document.write('<script src="https://cdn.shopify.com/shopifycloud/app-bridge.js"><\\/script>');
}`;

export default async function AdminRootLayout({ children }: { children: React.ReactNode }) {
  const key = await clientId();
  return (
    <html lang="en" className={`${sans.variable} ${display.variable}`} suppressHydrationWarning>
      <head>
        <meta name="shopify-api-key" content={key} />
        {/* eslint-disable-next-line @next/next/no-sync-scripts -- App Bridge requires synchronous loading */}
        <script dangerouslySetInnerHTML={{ __html: LOAD_APP_BRIDGE }} />
      </head>
      <body className="font-sans" suppressHydrationWarning>
        <AdminShell>{children}</AdminShell>
      </body>
    </html>
  );
}
