import { dateTime } from "@/lib/format";
import { cx } from "./ui";

const LABELS: Record<string, string> = {
  label_created: "Order received",
  in_transit: "Shipped",
  delayed: "Delayed in transit",
  out_for_delivery: "Out for delivery",
  delivered: "Delivered",
};

export function ShipmentTimeline({ shipping }: { shipping: any }) {
  if (!shipping) return null;
  return (
    <ol className="relative space-y-4 pl-6">
      <span className="absolute bottom-2 left-[7px] top-2 w-px bg-paper-line" aria-hidden="true" />
      {shipping.steps.map((s: any) => (
        <li key={s.status} className="relative">
          <span
            aria-hidden="true"
            className={cx(
              "absolute -left-6 top-1 h-[15px] w-[15px] rounded-full border-2",
              s.done ? (s.status === "delayed" ? "border-warn bg-warn" : "border-pass bg-pass") : "border-paper-line bg-white",
            )}
          />
          <p className={cx("text-sm font-medium", !s.done && "text-ink-mute")}>{LABELS[s.status] ?? s.status}</p>
          <p className="text-xs text-ink-mute">{s.done ? dateTime(s.at) : `Expected ${dateTime(s.at)}`}</p>
        </li>
      ))}
    </ol>
  );
}

// Admin audit view only: raw hash-chained events. Customer pages never receive these.
export function EventLog({ events }: { events: any[] }) {
  return (
    <ol className="space-y-2">
      {events.map((e) => (
        <li key={e.event_id} className="flex items-start gap-3 text-xs">
          <span className="mt-0.5 font-mono text-ink-faint">{e.event_id}</span>
          <div className="min-w-0 flex-1">
            <p className="font-medium text-ink">
              {e.event_type} <span className="font-normal text-ink-mute">· {e.actor_type.replace(/_/g, " ")}</span>
            </p>
            <p className="text-ink-mute">
              {dateTime(e.timestamp)} · hash <span className="font-mono">{e.event_hash.slice(0, 12)}…</span>
              {e.prior_event_reference && <> · prev {e.prior_event_reference}</>}
            </p>
          </div>
        </li>
      ))}
    </ol>
  );
}
