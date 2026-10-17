import { dateTime } from "@/lib/format";
import { cx } from "./ui";

const LABELS: Record<string, string> = {
  label_created: "Order received",
  in_transit: "Shipped",
  delayed: "Delayed in transit",
  out_for_delivery: "Out for delivery",
  delivered: "Delivered",
};

export function ShipmentTimeline({ shipment }: { shipment: any }) {
  if (!shipment) return null;
  const done = new Map<string, any>(shipment.events.map((e: any) => [e.status, e]));
  const steps = shipment.planned.filter((p: any) => p.status !== "delayed" || done.has("delayed"));
  return (
    <ol className="relative space-y-4 pl-6">
      <span className="absolute bottom-2 left-[7px] top-2 w-px bg-paper-line" />
      {steps.map((p: any) => {
        const ev = done.get(p.status);
        return (
          <li key={p.status} className="relative">
            <span
              className={cx(
                "absolute -left-6 top-1 h-[15px] w-[15px] rounded-full border-2",
                ev ? (p.status === "delayed" ? "border-warn bg-warn" : "border-pass bg-pass") : "border-paper-line bg-white",
              )}
            />
            <p className={cx("text-sm font-medium", !ev && "text-ink-mute")}>{LABELS[p.status] ?? p.status}</p>
            <p className="text-xs text-ink-mute">{ev ? dateTime(ev.at) : `Scheduled ${dateTime(p.at)}`}</p>
          </li>
        );
      })}
    </ol>
  );
}

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
