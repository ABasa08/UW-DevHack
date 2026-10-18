"use client";

import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api, setShopperToken } from "@/lib/api";

export type AppConfig = {
  mode: "demo" | "live" | "shopify";
  client_id: string;
  shop: string;
  store_name: string | null;
  now: string;
  ai: { mode: string; model: string | null; note: string | null };
  payment_methods: { payment_method_id: string; label: string; note?: string; network?: string }[] | null;
};

type Ctx = {
  ready: boolean;
  config: AppConfig | null;
  error: string | null;
  framed: boolean;
  storeName: string;
  shopperId: string | null;
  /** Demo mode only: the persona the server should act as. Shopify sessions never send one. */
  persona: { shopper_id?: string };
  personaQuery: string;
  version: number;
  bump: () => void;
  close: () => void;
  navigateTop: (url: string) => void;
};

const ShopperContext = createContext<Ctx | null>(null);
const SESSION_KEY = "perkpilot_session";

function readHashToken(): string | null {
  const m = window.location.hash.match(/pp_token=([^&]+)/);
  if (!m) return null;
  history.replaceState(null, "", window.location.pathname + window.location.search);
  return decodeURIComponent(m[1]);
}

function stored(): any {
  try { return JSON.parse(sessionStorage.getItem(SESSION_KEY) || localStorage.getItem(SESSION_KEY) || "null"); } catch { return null; }
}

function remember(s: any) {
  try {
    sessionStorage.setItem(SESSION_KEY, JSON.stringify(s));
    if (window.top === window.self) localStorage.setItem(SESSION_KEY, JSON.stringify(s)); // direct preview keeps its session
  } catch { /* storage blocked: the session lasts for this page */ }
}

function sidOf(token: string): string | null {
  try {
    const body = token.split(".")[0].replace(/-/g, "+").replace(/_/g, "/");
    return JSON.parse(atob(body + "=".repeat((4 - (body.length % 4)) % 4))).sid ?? null;
  } catch {
    return null;
  }
}

export function ShopperProvider({ children }: { children: ReactNode }) {
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [shopperId, setShopperId] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const [version, setVersion] = useState(0);
  const [framed, setFramed] = useState(false);
  const [persona, setPersona] = useState<{ shopper_id?: string }>({});

  useEffect(() => {
    setFramed(window.top !== window.self);
    (async () => {
      try {
        const cfg = await api.get<AppConfig>("/api/app/config");
        setConfig(cfg);
        if (cfg.mode === "demo") {
          // The fixture store's personas; ?as=shp_sam switches to the "next shopper".
          const as = new URLSearchParams(window.location.search).get("as") || "shp_jordan";
          setPersona({ shopper_id: as });
          setShopperId(as);
        } else {
          // A token handed over by the store's embed script is fresh. A remembered one is re-issued, which keeps the
          // same guest identity while it's valid and replaces it if the server no longer accepts it.
          let token = readHashToken();
          if (token) {
            remember({ ...(stored() ?? {}), token });
          } else {
            const s = await api.post<any>("/api/session/anonymous", { previous_token: stored()?.token ?? null });
            remember(s);
            token = s.token as string;
          }
          setShopperToken(token);
          setShopperId(sidOf(token));
        }
        setReady(true);
      } catch (e) {
        setError(e instanceof Error ? e.message : "PerkPilot is unavailable");
      }
    })();
  }, []);

  const post = useCallback((msg: any) => window.parent?.postMessage({ source: "perkpilot", ...msg }, "*"), []);
  const close = useCallback(() => post({ type: "close" }), [post]);
  const navigateTop = useCallback(
    (url: string) => {
      if (window.top !== window.self) post({ type: "navigate", url });
      else window.location.assign(url);
    },
    [post],
  );
  const bump = useCallback(() => setVersion((v) => v + 1), []);
  const storeName = config?.store_name || (config?.mode === "demo" ? "StrideLab Running (demo)" : "this store");
  const personaQuery = persona.shopper_id ? `shopper_id=${persona.shopper_id}` : "";

  return (
    <ShopperContext.Provider value={{ ready, config, error, framed, storeName, shopperId, persona, personaQuery, version, bump, close, navigateTop }}>
      {children}
    </ShopperContext.Provider>
  );
}

export function useShopper() {
  const ctx = useContext(ShopperContext);
  if (!ctx) throw new Error("useShopper must be used inside ShopperProvider");
  return ctx;
}

/** Path with the demo persona appended when there is one (Shopify sessions travel in the Authorization header). */
export function withPersona(path: string, personaQuery: string) {
  if (!personaQuery) return path;
  return path + (path.includes("?") ? "&" : "?") + personaQuery;
}
