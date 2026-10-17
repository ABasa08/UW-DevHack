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
    <html lang="en" className={`${sans.variable} ${display.variable}`}>
      <body className="font-sans">
        <DemoProvider>
          <AppShell>{children}</AppShell>
        </DemoProvider>
      </body>
    </html>
  );
}
