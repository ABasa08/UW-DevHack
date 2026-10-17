"use client";

import { useCallback, useEffect, useRef, useState } from "react";

export function useLoad<T>(fn: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const fnRef = useRef(fn);
  fnRef.current = fn;
  const seq = useRef(0);
  const reload = useCallback(async () => {
    const mine = ++seq.current; // ignore responses from superseded requests
    setLoading(true);
    setError(null);
    try {
      const out = await fnRef.current();
      if (mine === seq.current) setData(out);
    } catch (e) {
      if (mine === seq.current) setError(e);
    } finally {
      if (mine === seq.current) setLoading(false);
    }
  }, []);
  useEffect(() => {
    reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return { data, error, loading, reload, setData };
}

export function safeStorage() {
  return {
    get(key: string): string | null {
      try {
        return window.localStorage.getItem(key);
      } catch {
        return null;
      }
    },
    set(key: string, value: string) {
      try {
        window.localStorage.setItem(key, value);
      } catch {
        /* storage unavailable: persona just won't be remembered */
      }
    },
  };
}
