import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { motion } from "motion/react";
import { Download, FileJson, Printer } from "lucide-react";
import { useStore } from "@/store";
import { download } from "@/api";
import { Button } from "@/components/ui/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { NumberTicker } from "@/components/ui/number-ticker";
import { ShineBorder } from "@/components/ui/shine-border";
import { StatusIcon } from "./StatusIcon";
import { fmtLoad, loadBand } from "./Load";
import { cn } from "@/lib/utils";

export function slug(s: string) {
  return s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "design";
}

export function exportDesign() {
  const s = useStore.getState();
  if (s.design) download(`${slug(s.design.name)}.design.json`, s.design);
}

export function exportReport() {
  const s = useStore.getState();
  if (!s.design) return;
  download(`${slug(s.design.name)}.report.json`, {
    generated: new Date().toISOString(),
    design: s.design, summary: s.summary, issues: s.issues,
    evaluation: s.evaluation ?? { tests: s.testOrder.map((id) => s.tests[id]?.result).filter(Boolean) },
    before: s.before, answer: s.answer, verified: s.verified,
  });
}

export function ResultCard() {
  const run = useStore((s) => s.run);
  const answer = useStore((s) => s.answer);
  const verified = useStore((s) => s.verified);
  const summary = useStore((s) => s.summary);
  const ev = useStore((s) => s.evaluation);
  const limitingTitle = useStore((s) => (s.evaluation?.limiting ? s.tests[s.evaluation.limiting]?.title : undefined));
  if (run !== "done" || answer === null) return null;
  const total = ev?.tests.length ?? 0;
  const passed = ev?.counts.pass ?? 0;
  const ok = verified && ev?.all_pass;
  const kg = (summary?.mass_g ?? 0) / 1000;
  return (
    <motion.section
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.45 }}
      className="relative mx-1 mb-4 overflow-hidden rounded-xl border bg-card p-4"
      aria-label="Result"
    >
      {ok && <ShineBorder shineColor={["#38bdf8", "#22c55e", "#38bdf8"]} borderWidth={1} duration={10} />}
      <div className="flex items-center gap-2.5">
        <StatusIcon status={ok ? "pass" : "fail"} className="size-6" />
        <div className="text-sm font-semibold">{ok ? "Ready to build" : "Not ready yet"}</div>
        <span className="ml-auto text-xs text-muted-foreground">{passed}/{total} tests pass</span>
      </div>
      {ev?.load !== undefined && ev.load !== null && (
        <div className="mt-1.5 pl-8 text-xs text-muted-foreground">
          Load <span className={cn("font-semibold tabular-nums", loadBand(ev.load).text)}>{fmtLoad(ev.load)}</span>
          {limitingTitle && <> · limited by {limitingTitle}</>}
        </div>
      )}
      <div className="mt-3 grid grid-cols-3 gap-2">
        {[
          { label: "parts", value: summary?.parts.length ?? 0, dec: 0, unit: "" },
          { label: "mass", value: kg >= 1 ? kg : kg * 1000, dec: kg >= 1 ? 2 : 0, unit: kg >= 1 ? "kg" : "g" },
          { label: "materials", value: summary?.material_cost ?? 0, dec: 0, unit: "$", pre: true },
        ].map((m) => (
          <div key={m.label} className="rounded-lg bg-muted/60 px-2.5 py-2">
            <div className="text-[11px] text-muted-foreground">{m.label}</div>
            <div className="text-lg font-semibold tracking-tight">
              {m.pre && <span className="text-sm text-muted-foreground">{m.unit}</span>}
              <NumberTicker value={m.value} decimalPlaces={m.dec} className="text-foreground" />
              {!m.pre && m.unit && <span className="ml-0.5 text-xs font-normal text-muted-foreground">{m.unit}</span>}
            </div>
          </div>
        ))}
      </div>
      <div className="prose-sm mt-3 text-[13px] leading-relaxed text-foreground/90 [&_li]:ml-4 [&_li]:list-disc [&_p]:my-1.5 [&_strong]:font-semibold [&_em]:text-muted-foreground">
        <Markdown remarkPlugins={[remarkGfm]}>{answer}</Markdown>
      </div>
      <div className="mt-3 flex gap-2">
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button size="sm" variant="secondary" className="h-8"><Download className="size-3.5" /> Export</Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start">
            <DropdownMenuItem onClick={exportDesign}><FileJson className="size-4" /> Design (JSON)</DropdownMenuItem>
            <DropdownMenuItem onClick={exportReport}><FileJson className="size-4" /> Full report (JSON)</DropdownMenuItem>
            <DropdownMenuItem onClick={() => window.print()}><Printer className="size-4" /> Print report</DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </motion.section>
  );
}
