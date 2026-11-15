import { useEffect, useRef, useState } from "react";
import { Check, ChevronDown, Copy, ShieldCheck } from "lucide-react";
import { useStore } from "@/store";
import type { TestResult } from "@/types";
import { cn } from "@/lib/utils";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { StatusIcon, type St } from "./StatusIcon";
import { fmt, Heatmap, LineChart, prettyUnit } from "./Charts";
import { fmtLoad, loadBand, LoadBar } from "./Load";

function Reproduce({ cmd }: { cmd: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="flex items-stretch gap-2">
      <pre className="scrollbar-thin min-w-0 flex-1 overflow-x-auto whitespace-pre rounded-md border bg-muted/50 px-2.5 py-1.5 font-mono text-[11px] leading-relaxed">{cmd}</pre>
      <button
        type="button"
        onClick={async () => {
          try { await navigator.clipboard.writeText(cmd); setCopied(true); setTimeout(() => setCopied(false), 1500); } catch { /* clipboard blocked */ }
        }}
        aria-label="Copy command"
        className="grid w-8 shrink-0 place-items-center rounded-md border text-muted-foreground hover:bg-accent hover:text-foreground"
      >
        {copied ? <Check className="size-3.5 text-good" /> : <Copy className="size-3.5" />}
      </button>
    </div>
  );
}

function Checks({ t }: { t: TestResult }) {
  const checks = t.sim?.verification.checks ?? [];
  const cats = [...new Set(checks.map((c) => c.category))];
  return (
    <div className="space-y-3">
      {cats.map((cat) => (
        <div key={cat}>
          <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">{cat}</div>
          <ul className="divide-y rounded-lg border">
            {checks.filter((c) => c.category === cat).map((c, i) => (
              <li key={i} className={cn("flex gap-2.5 px-3 py-2", c.status === "skip" && "opacity-60")}>
                <StatusIcon status={c.status as St} className="mt-0.5" />
                <div className="min-w-0">
                  <div className="text-xs font-medium">{c.name}</div>
                  <div className="text-xs leading-snug text-muted-foreground [overflow-wrap:anywhere]">{c.detail}</div>
                </div>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}

function TestCard({ id }: { id: string }) {
  const t = useStore((s) => s.tests[id]);
  const focus = useStore((s) => s.focusTest === id);
  const partName = useStore((s) => s.design?.parts.find((p) => p.id === t?.part)?.name);
  const set = useStore((s) => s.set);
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (focus) {
      setOpen(true);
      ref.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
  }, [focus]);
  if (!t) return null;
  const r = t.result;
  const checks = r?.sim?.verification.checks ?? [];
  const nPass = checks.filter((c) => c.status === "pass").length;
  const stale = useStore((s) => s.stale);
  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <div
        ref={ref}
        className={cn("rounded-xl border bg-card transition-[box-shadow,opacity]", focus && "ring-2 ring-ring/60", stale && "opacity-55")}
        onMouseEnter={() => set({ hovered: t.part })}
        onMouseLeave={() => set({ hovered: null })}
      >
        <CollapsibleTrigger className="flex w-full items-start gap-3 rounded-xl p-3.5 text-left outline-none hover:bg-accent/40 focus-visible:ring-2 focus-visible:ring-ring">
          <StatusIcon status={r ? r.status : "pending"} className="mt-0.5" />
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
              <span className="text-sm font-semibold">{r?.title ?? t.title}</span>
              {partName && <span className="text-xs text-muted-foreground">{partName}</span>}
            </div>
            <p className="mt-1 text-[13px] leading-snug text-muted-foreground">{r?.headline ?? "Running…"}</p>
            {r?.checks?.verified && checks.length > 0 && (
              <span className="mt-2 inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] text-muted-foreground">
                <ShieldCheck className="size-3 text-good" /> Verified by {nPass} independent checks
              </span>
            )}
          </div>
          {r && <LoadBar load={r.load} />}
          <ChevronDown className={cn("mt-1 size-4 shrink-0 text-muted-foreground transition-transform", open && "rotate-180")} aria-hidden />
        </CollapsibleTrigger>
        <CollapsibleContent>
          {r && (
            <div className="space-y-4 border-t px-3.5 pb-4 pt-3">
              {r.metrics.length > 0 && (
                <table className="w-full text-xs">
                  <thead className="text-muted-foreground"><tr><th className="py-1 text-left font-medium">Requirement</th><th className="text-right font-medium">Result</th><th className="text-right font-medium">Limit</th><th className="text-right font-medium">Margin</th><th className="w-8" /></tr></thead>
                  <tbody>
                    {r.metrics.map((m) => (
                      <tr key={m.label} className="border-t">
                        <td className="py-1.5">{m.label}</td>
                        <td className="text-right font-mono">{fmt(m.value)} {prettyUnit(m.unit)}</td>
                        <td className="text-right font-mono text-muted-foreground">{m.kind === "min" ? "≥" : "≤"} {fmt(m.limit)}</td>
                        <td className={cn("text-right font-mono", loadBand(m.load).text)}>{fmtLoad(m.load)}</td>
                        <td className="text-right"><StatusIcon status={m.ok ? "pass" : "fail"} className="ml-auto size-4" /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
              {r.sim?.fields?.map((f, i) => (
                <div key={i} className="overflow-hidden">{f.kind === "image" ? <Heatmap f={f} /> : <LineChart f={f} />}</div>
              ))}
              {r.sim && Object.keys(r.sim.results).length > 0 && (
                <Sub title="All results">
                  <dl className="grid grid-cols-[1fr_auto] gap-x-4 gap-y-1 text-xs">
                    {Object.entries(r.sim.results).map(([k, v]) => (
                      <div key={k} className="contents"><dt className="text-muted-foreground">{k.replace(/_/g, " ")}</dt><dd className="text-right font-mono">{fmt(v.value)} {prettyUnit(v.unit)}</dd></div>
                    ))}
                  </dl>
                </Sub>
              )}
              {r.notes.length > 0 && (
                <Sub title="Modelling notes">
                  <ul className="list-disc space-y-1 pl-4 text-xs text-muted-foreground">{r.notes.map((n, i) => <li key={i}>{n}</li>)}</ul>
                </Sub>
              )}
              {checks.length > 0 && (
                <Sub title={`Verification · ${r.sim?.verification.summary ?? ""}`}><Checks t={r} /></Sub>
              )}
              {r.reproduce && (
                <div>
                  <div className="mb-1 text-xs font-medium text-muted-foreground">Reproduce</div>
                  <Reproduce cmd={r.reproduce} />
                </div>
              )}
              {r.simulation && <div className="text-[11px] text-muted-foreground">Simulation: <span className="font-mono">{r.simulation}</span>{r.sim?.resolution ? ` · resolution ${r.sim.resolution}` : ""}{r.sim?.elapsed_s !== undefined ? ` · ${r.sim.elapsed_s} s` : ""}</div>}
            </div>
          )}
        </CollapsibleContent>
      </div>
    </Collapsible>
  );
}

function Sub({ title, children }: { title: string; children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <CollapsibleTrigger className="flex w-full items-center gap-1.5 rounded text-left text-xs font-medium text-muted-foreground hover:text-foreground">
        <ChevronDown className={cn("size-3.5 transition-transform", !open && "-rotate-90")} /> {title}
      </CollapsibleTrigger>
      <CollapsibleContent className="pt-2">{children}</CollapsibleContent>
    </Collapsible>
  );
}

export function TestsPanel() {
  const order = useStore((s) => s.testOrder);
  if (!order.length) return <EmptyTab text="Tests appear here as they run." />;
  return <div className="grid grid-cols-[minmax(0,1fr)] gap-2.5">{order.map((id) => <TestCard key={id} id={id} />)}</div>;
}

export function EmptyTab({ text }: { text: string }) {
  return <p className="py-10 text-center text-sm text-muted-foreground">{text}</p>;
}
