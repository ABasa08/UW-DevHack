import type { Metadata } from "next";
import { Fraunces, Inter } from "next/font/google";
import "../globals.css";

const sans = Inter({ subsets: ["latin"], variable: "--font-sans" });
const display = Fraunces({ subsets: ["latin"], variable: "--font-display" });

export const metadata: Metadata = {
  title: "PerkPilot",
  description: "Buy it right. Fix it fast. Learn from every return.",
};

export default function ShopRootLayout({ children }: { children: React.ReactNode }) {
  return (
    // suppressHydrationWarning: browser extensions (e.g. Grammarly) add attributes to <html>/<body> before React loads.
    <html lang="en" className={`${sans.variable} ${display.variable}`} suppressHydrationWarning>
      <body className="font-sans" suppressHydrationWarning>{children}</body>
    </html>
  );
}
