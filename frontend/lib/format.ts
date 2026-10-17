export const STORE_TZ = "America/Los_Angeles";

export function money(cents: number | null | undefined, opts: { sign?: boolean } = {}): string {
  if (cents === null || cents === undefined) return "—";
  const v = Math.abs(cents) / 100;
  const s = v.toLocaleString("en-US", { style: "currency", currency: "USD" });
  if (cents < 0) return `−${s}`;
  return opts.sign && cents > 0 ? `+${s}` : s;
}

export function dateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("en-US", {
    timeZone: STORE_TZ, weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
  });
}

export function dayLabel(isoDate: string | null | undefined): string {
  if (!isoDate) return "—";
  const [y, m, d] = isoDate.slice(0, 10).split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d, 12)).toLocaleDateString("en-US", {
    timeZone: "UTC", weekday: "long", month: "short", day: "numeric",
  });
}

export function titleCase(s: string | null | undefined): string {
  if (!s) return "";
  return s.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export const CATEGORY_LABELS: Record<string, string> = {
  running_shoes: "Running shoes",
  trail_running: "Trail running",
  walking_shoes: "Walking shoes",
  training_shoes: "Training shoes",
  casual_sneakers: "Casual sneakers",
  recovery_slides: "Recovery slides",
};

export const CLASSIFICATION_LABELS: Record<string, string> = {
  ORDER_MISMATCH: "Order mismatch",
  PRICE_OR_DELIVERY_MISMATCH: "Price or delivery mismatch",
  SUSPECTED_PRODUCT_FIT: "Suspected fit issue",
  CUSTOMER_PREFERENCE: "Customer preference",
  NEEDS_REVIEW: "Needs more information",
};

export const STATUS_LABELS: Record<string, string> = {
  confirmed: "Confirmed",
  shipped: "In transit",
  delivered: "Delivered",
  exchange_in_progress: "Exchange in progress",
  exchanged: "Exchanged",
  refunded: "Refunded",
  partially_refunded: "Partially refunded",
  returned_for_credit: "Returned for store credit",
};
