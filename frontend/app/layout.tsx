import type { Metadata } from "next";
import { Fraunces, Inter } from "next/font/google";
import AppShell from "@/components/AppShell";
import { DemoProvider } from "@/components/DemoProvider";
import "./globals.css";

const sans = Inter({ subsets: ["latin"], variable: "--font-sans" });
const display = Fraunces({ subsets: ["latin"], variable: "--font-display" });

export const metadata: Metadata = {
  title: "PerkPilot · StrideLab Running",
  description: "Buy it right. Fix it fast. Learn from every return.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    // suppressHydrationWarning: browser extensions (e.g. Grammarly) add attributes to <html>/<body> before React loads.
    // It only ignores attribute differences on these two elements, not on anything inside them.
    <html lang="en" className={`${sans.variable} ${display.variable}`} suppressHydrationWarning>
      <body className="font-sans" suppressHydrationWarning>
        <DemoProvider>
          <AppShell>{children}</AppShell>
        </DemoProvider>
      </body>
    </html>
  );
}
