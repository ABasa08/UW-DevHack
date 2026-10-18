export class ApiError extends Error {
  status: number;
  code: string;
  detail: any;
  constructor(status: number, code: string, message: string, detail?: any) {
    super(message);
    this.status = status;
    this.code = code;
    this.detail = detail;
  }
}

// The shopper's PerkPilot session (set by ShopperProvider). Sent as a bearer token on every shopper call;
// the server derives who the shopper is from it and never from request bodies.
let shopperToken = "";
export const setShopperToken = (t: string) => {
  shopperToken = t;
};

async function request<T>(method: string, path: string, body?: unknown, auth?: string): Promise<T> {
  let res: Response;
  const bearer = auth ?? shopperToken;
  try {
    res = await fetch(path, {
      method,
      headers: { ...(body ? { "content-type": "application/json" } : {}), ...(bearer ? { authorization: `Bearer ${bearer}` } : {}) },
      body: body ? JSON.stringify(body) : undefined,
      cache: "no-store",
    });
  } catch {
    throw new ApiError(0, "network", "Can't reach PerkPilot right now. Check your connection and try again.");
  }
  const text = await res.text();
  let data: any = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = null;
  }
  if (!res.ok) {
    const err = data?.error;
    if (err) throw new ApiError(res.status, err.code, err.message, err.detail);
    const msg = Array.isArray(data?.detail)
      ? data.detail.map((d: any) => `${(d.loc || []).slice(-1)[0]}: ${d.msg}`).join("; ")
      : `Request failed (${res.status})`;
    throw new ApiError(res.status, "invalid_request", msg, data);
  }
  return data as T;
}

// Merchant auth. Inside Shopify admin, App Bridge issues a fresh session token per request. Outside it (local use),
// the PERKPILOT_ADMIN_TOKEN bearer is kept for this browser tab only.
const ADMIN_KEY = "perkpilot_admin_token";
let memoToken = ""; // fallback when storage is blocked: lasts until reload
export const adminToken = {
  get: (): string => {
    try { return sessionStorage.getItem(ADMIN_KEY) ?? memoToken; } catch { return memoToken; }
  },
  set: (t: string) => {
    memoToken = t;
    try { if (t) sessionStorage.setItem(ADMIN_KEY, t); else sessionStorage.removeItem(ADMIN_KEY); } catch { /* memoToken covers it */ }
  },
};

declare global {
  interface Window {
    shopify?: { idToken: () => Promise<string>; toast?: { show: (msg: string, opts?: any) => void } };
  }
}

export const embeddedInAdmin = () => typeof window !== "undefined" && !!window.shopify?.idToken && window.top !== window.self;

async function merchantToken(): Promise<string> {
  if (embeddedInAdmin()) {
    try {
      return await window.shopify!.idToken();
    } catch {
      /* fall through to the tab token */
    }
  }
  return adminToken.get();
}

export const api = {
  get: <T = any>(path: string) => request<T>("GET", path),
  post: <T = any>(path: string, body?: unknown) => request<T>("POST", path, body ?? {}),
  admin: async <T = any>(path: string) => request<T>("GET", path, undefined, await merchantToken()),
  adminPost: async <T = any>(path: string, body?: unknown) => request<T>("POST", path, body ?? {}, await merchantToken()),
};
