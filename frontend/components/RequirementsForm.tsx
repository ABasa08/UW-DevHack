"use client";

import { api } from "@/lib/api";
import { categoryLabel, dayLabel } from "@/lib/format";
import { useLoad } from "@/lib/hooks";
import { cx } from "./ui";

// What the store actually sells, from /api/catalog/facets.
type Facets = { categories: string[]; sized_categories: string[]; colors: string[]; sizes: string[] };
const withValue = (list: string[], v: string | null) => (v && !list.includes(v) ? [...list, v] : list);

export type Req = {
  category: string | null;
  color: string | null;
  size: string | null;
  size_system: string;
  max_charge_cents: number | null;
  budget_includes_shipping_and_tax: boolean;
  currency: string;
  deadline_expression: string | null;
  deadline_date: string | null;
  brand: string | null;
  payment_method_id: string | null;
  quantity: number;
};

function Field({ label, children, flag, inferred }: { label: string; children: React.ReactNode; flag?: string; inferred?: boolean }) {
  return (
    <label className="block">
      <span className="flex items-center gap-2 text-xs font-medium text-ink-mute">
        {label}
        {inferred && <span className="rounded bg-brand-soft px-1.5 text-[10px] text-brand">from your words</span>}
      </span>
      <div className="mt-1">{children}</div>
      {flag && <span className="mt-1 block text-xs text-warn">{flag}</span>}
    </label>
  );
}

const input = "w-full rounded-lg border border-paper-line bg-white px-3 py-2 text-sm outline-none focus:border-ink-faint";

export default function RequirementsForm({
  value,
  onChange,
  parsed,
  flags,
  paymentMethods,
  minDate,
}: {
  value: Req;
  onChange: (r: Req) => void;
  parsed: Req;
  flags: Record<string, string>;
  paymentMethods: { payment_method_id: string; label: string }[];
  minDate: string;
}) {
  const set = (patch: Partial<Req>) => onChange({ ...value, ...patch });
  const f = (useLoad<Facets>(() => api.get("/api/catalog/facets"), []).data ?? { categories: [], sized_categories: [], colors: [], sizes: [] });
  const needsSize = !value.category || f.sized_categories.includes(value.category);
  const had = (k: keyof Req) => parsed[k] !== null && parsed[k] !== undefined;
  return (
    <div className="grid gap-4 sm:grid-cols-2">
      <Field label="Product type" flag={flags.category} inferred={had("category")}>
        <select className={cx(input, !value.category && "border-warn")} value={value.category ?? ""} onChange={(e) => set({ category: e.target.value || null })}>
          <option value="">Choose…</option>
          {withValue(f.categories, value.category).map((k) => <option key={k} value={k}>{categoryLabel(k)}</option>)}
        </select>
      </Field>
      <Field label="Color" inferred={had("color")}>
        <select className={input} value={value.color ?? ""} onChange={(e) => set({ color: e.target.value || null })}>
          <option value="">Any color</option>
          {withValue(f.colors, value.color).map((c) => <option key={c} value={c} className="capitalize">{c}</option>)}
        </select>
      </Field>
      <Field label="Size" flag={needsSize ? flags.size : undefined} inferred={had("size")}>
        <select className={cx(input, needsSize && !value.size && "border-warn")} value={value.size ?? ""} onChange={(e) => set({ size: e.target.value || null })}>
          <option value="">{needsSize ? "Choose…" : "Any / one size"}</option>
          {withValue(f.sizes, value.size).map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
      </Field>
      <div>
        <Field label="Maximum charge (USD)" flag={flags.max_charge_cents} inferred={had("max_charge_cents")}>
          <div className="relative">
            <span className="pointer-events-none absolute left-3 top-2 text-sm text-ink-mute">$</span>
            <input
              type="number"
              min={1}
              step="0.01"
              className={cx(input, "pl-6", value.max_charge_cents === null && "border-warn")}
              value={value.max_charge_cents === null ? "" : value.max_charge_cents / 100}
              onChange={(e) => set({ max_charge_cents: e.target.value === "" ? null : Math.round(parseFloat(e.target.value) * 100) })}
            />
          </div>
        </Field>
        <label className="mt-1.5 flex items-center gap-2 text-xs text-ink-soft">
          <input
            type="checkbox"
            checked={value.budget_includes_shipping_and_tax}
            onChange={(e) => set({ budget_includes_shipping_and_tax: e.target.checked })}
          />
          Limit includes shipping and tax (total card charge)
        </label>
      </div>
      <Field label="Deliver by" inferred={had("deadline_date")}>
        <input
          type="date"
          className={input}
          min={minDate}
          value={value.deadline_date ?? ""}
          onChange={(e) => set({ deadline_date: e.target.value || null, deadline_expression: e.target.value ? value.deadline_expression : null })}
        />
        <span className="mt-1 block text-xs text-ink-mute">
          {value.deadline_date
            ? `${dayLabel(value.deadline_date)}${value.deadline_expression ? ` (you said “${value.deadline_expression}”)` : ""}`
            : "No deadline"}
        </span>
      </Field>
      <Field label="How you'll pay">
        <select className={input} value={value.payment_method_id ?? ""} onChange={(e) => set({ payment_method_id: e.target.value || null })}>
          <option value="">Default{paymentMethods[0] ? `: ${paymentMethods[0].label}` : ""}</option>
          {paymentMethods.map((p) => <option key={p.payment_method_id} value={p.payment_method_id}>{p.label}</option>)}
        </select>
      </Field>
      <Field label="Quantity">
        <select className={input} value={value.quantity} onChange={(e) => set({ quantity: Number(e.target.value) })}>
          {[1, 2, 3].map((n) => <option key={n} value={n}>{n}</option>)}
        </select>
      </Field>
      <Field label="Brand (optional)" inferred={had("brand")}>
        <input className={input} value={value.brand ?? ""} placeholder="Any brand" onChange={(e) => set({ brand: e.target.value || null })} />
      </Field>
    </div>
  );
}
