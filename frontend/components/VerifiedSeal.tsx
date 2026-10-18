import { cx } from "./ui";

// The one verification signal shoppers see. The checks themselves, the record hash and the event chain
// are served only to admins (/api/admin); this shows the server's yes/no and how many checks passed.
export default function VerifiedSeal({
  verified,
  passed,
  total,
  title,
  body,
  className,
}: {
  verified: boolean;
  passed: number;
  total: number;
  title?: string;
  body?: string;
  className?: string;
}) {
  return (
    <div
      role="status"
      className={cx(
        "flex items-start gap-3 rounded-xl border px-4 py-3",
        verified ? "border-pass/25 bg-pass-soft text-pass" : "border-warn/30 bg-warn-soft text-warn",
        className,
      )}
    >
      <svg viewBox="0 0 24 24" className="mt-0.5 h-5 w-5 shrink-0" fill="none" aria-hidden="true">
        <path d="M12 2.5 4.5 5.5v6c0 4.6 3.1 8.6 7.5 10 4.4-1.4 7.5-5.4 7.5-10v-6L12 2.5Z" fill="currentColor" fillOpacity="0.14" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" />
        {verified ? (
          <path d="m8.5 12.2 2.4 2.4 4.6-5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
        ) : (
          <path d="M12 8v4.5M12 15.8v.2" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
        )}
      </svg>
      <div className="text-sm">
        <p className="font-semibold">{title ?? (verified ? "Verified purchase" : "We couldn't confirm this order")}</p>
        <p className="mt-0.5 text-ink-soft">
          {body ??
            (verified
              ? `Matches what you approved: item, price, payment and delivery date. ${passed} of ${total} checks passed.`
              : "This order no longer matches what you approved. Contact the store before relying on it.")}
        </p>
      </div>
    </div>
  );
}
