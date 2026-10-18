"use client";

import { useState } from "react";
import { useAdmin } from "@/components/AdminShell";
import { Badge, Button, Card, Empty, ErrorBox, Eyebrow, Loading, StatusIcon } from "@/components/ui";
import { api } from "@/lib/api";
import { dateTime } from "@/lib/format";
import { useLoad } from "@/lib/hooks";

const TITLES: Record<string, string> = {
  checkout_verifier: "Checkout verifier",
  request_parsing: "Request parsing",
  claim_classification: "Claim classification & abstention",
  remedy_policy: "Remedy policy compliance",
  workflow: "End-to-end workflow",
};

function show(v: any) {
  return typeof v === "string" ? v : JSON.stringify(v);
}

export default function EvaluationPage() {
  const { version, bump } = useAdmin();
  const res = useLoad(() => api.admin("/api/eval/runs/latest"), [version]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const run = res.data?.run;

  async function go() {
    setBusy(true);
    setError(null);
    try {
      await api.adminPost("/api/eval/run");
      bump();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <Eyebrow>Labeled fixtures · isolated sandbox</Eyebrow>
          <h1 className="mt-1 font-display text-3xl font-semibold">Evaluation</h1>
          <p className="mt-1 max-w-3xl text-sm text-ink-mute">
            Exact pass/fail counts for program behavior on synthetic scenarios. These are not real-world business results.
          </p>
        </div>
        <Button onClick={go} loading={busy}>Run evaluation</Button>
      </div>
      <ErrorBox error={error} />
      {res.loading && !res.data ? (
        <Loading />
      ) : !run ? (
        <Empty title="No evaluation run yet">Click “Run evaluation”. It runs labeled fixtures in a private sandbox, so your store is untouched.</Empty>
      ) : (
        <>
          <p className="text-sm text-ink-mute">
            {run.run_id} · {run.scenarios_evaluated} scenarios · {run.passed} passed · demo time {dateTime(run.timestamp)}
          </p>
          <ul className="list-disc pl-5 text-xs text-ink-mute">
            {run.assumptions.map((a: string) => <li key={a}>{a}</li>)}
          </ul>
          {Object.entries(TITLES).map(([key, title]) => {
            const s = run.sections[key];
            const items = s.cases ?? s.steps;
            const summary = Object.entries(s).filter(([k, v]) => typeof v === "string" && k !== "note");
            return (
              <Card key={key} className="overflow-hidden">
                <div className="flex flex-wrap items-center gap-3 border-b border-paper-line px-5 py-4">
                  <p className="font-semibold">{title}</p>
                  {summary.map(([k, v]) => <Badge key={k} tone="brand">{k.replace(/_/g, " ")}: {v as string}</Badge>)}
                  {key === "request_parsing" && (
                    <Badge tone={s.interpreter.mode === "gemini" ? "pass" : "warn"}>
                      {s.interpreter.mode === "gemini" ? `Gemini ${s.interpreter.model}` : "fallback interpreter"} · {s.runs} run
                    </Badge>
                  )}
                </div>
                <ul className="divide-y divide-paper-line">
                  {items.map((c: any, i: number) => (
                    <li key={i} className="flex gap-3 px-5 py-2.5 text-sm">
                      <StatusIcon status={c.pass ? "PASS" : "FAIL"} className="mt-0.5" />
                      <div className="min-w-0 flex-1">
                        <p className="font-medium">{c.name ?? c.step}</p>
                        {c.expected !== undefined && (
                          <p className="truncate text-xs text-ink-mute">expected {show(c.expected)} · observed {show(c.observed)}</p>
                        )}
                        {c.detail && !c.pass && <p className="text-xs text-fail">{c.detail}</p>}
                      </div>
                    </li>
                  ))}
                </ul>
              </Card>
            );
          })}
          {(() => {
            const cc = run.sections.controlled_comparison;
            return (
              <Card className="overflow-hidden">
                <div className="flex flex-wrap items-center gap-3 border-b border-paper-line px-5 py-4">
                  <p className="font-semibold">Controlled comparison · learned guidance off vs on</p>
                  <Badge tone="neutral">baseline: {cc.baseline_matches}</Badge>
                  <Badge tone="pass">intervention: {cc.intervention_matches}</Badge>
                  <Badge tone="brand">constraints preserved: {cc.constraints_preserved}</Badge>
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead className="bg-paper-sunk text-left text-xs text-ink-mute">
                      <tr>
                        <th className="px-5 py-2 font-medium">Held-out request</th>
                        <th className="px-3 py-2 font-medium">Labeled target</th>
                        <th className="px-3 py-2 font-medium">Baseline guidance</th>
                        <th className="px-3 py-2 font-medium">With approved rule</th>
                      </tr>
                    </thead>
                    <tbody>
                      {cc.rows.map((r: any) => (
                        <tr key={r.name} className="border-t border-paper-line">
                          <td className="px-5 py-2"><p className="font-medium">{r.name}</p><p className="text-xs text-ink-mute">“{r.request}”</p></td>
                          <td className="px-3 py-2">{r.labeled_target ? `suggest ${r.labeled_target}` : "no guidance"}</td>
                          <td className="px-3 py-2"><StatusIcon status={r.baseline.matches_target ? "PASS" : "FAIL"} /> <span className="ml-1">{r.baseline.guidance ?? "none"}</span></td>
                          <td className="px-3 py-2"><StatusIcon status={r.intervention.matches_target ? "PASS" : "FAIL"} /> <span className="ml-1">{r.intervention.guidance ?? "none"}</span></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <p className="px-5 py-3 text-xs text-ink-mute">{cc.note}</p>
              </Card>
            );
          })()}
        </>
      )}
    </div>
  );
}
