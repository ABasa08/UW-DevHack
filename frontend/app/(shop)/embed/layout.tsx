"use client";

import EmbedShell from "@/components/EmbedShell";
import { ShopperProvider } from "@/components/ShopperProvider";

// The shopper assistant, loaded inside the store's theme by the PerkPilot app embed.
export default function EmbedLayout({ children }: { children: React.ReactNode }) {
  return (
    <ShopperProvider>
      <EmbedShell>{children}</EmbedShell>
    </ShopperProvider>
  );
}
