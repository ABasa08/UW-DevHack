"use client";

import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api } from "@/lib/api";
import { safeStorage } from "@/lib/hooks";

export type DemoState = {
  now: string;
  store: string;
  ai: { mode: string; model: string | null; note: string | null };
  merchant: { name: string };
  shoppers: { shopper_id: string; display_name: string; role: string; payment_methods: string[]; default_payment_method: string }[];
  payment_methods: { payment_method_id: string; label: string; type: string; enrolled_offers: string[] }[];
  disclosure: string;
};

type Ctx = {
  ready: boolean;
  shopperId: string;
  setShopperId: (id: string) => void;
  state: DemoState | null;
  stateError: string | null;
  version: number;
  bump: () => Promise<void>;
};

const DemoContext = createContext<Ctx | null>(null);

export function DemoProvider({ children }: { children: ReactNode }) {
  const [shopperId, setShopper] = useState("shp_jordan");
  const [state, setState] = useState<DemoState | null>(null);
  const [stateError, setStateError] = useState<string | null>(null);
  const [version, setVersion] = useState(0);
  const [ready, setReady] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setState(await api.get<DemoState>("/api/demo/state"));
      setStateError(null);
    } catch (e) {
      setStateError(e instanceof Error ? e.message : "API unavailable");
    }
  }, []);

  useEffect(() => {
    const saved = safeStorage().get("perkpilot.shopper");
    if (saved) setShopper(saved);
    setReady(true);
    refresh();
  }, [refresh]);

  const setShopperId = useCallback((id: string) => {
    setShopper(id);
    safeStorage().set("perkpilot.shopper", id);
  }, []);

  const bump = useCallback(async () => {
    await refresh();
    setVersion((v) => v + 1);
  }, [refresh]);

  return (
    <DemoContext.Provider value={{ ready, shopperId, setShopperId, state, stateError, version, bump }}>{children}</DemoContext.Provider>
  );
}

export function useDemo() {
  const ctx = useContext(DemoContext);
  if (!ctx) throw new Error("useDemo must be used inside DemoProvider");
  return ctx;
}
