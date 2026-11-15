import { useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { Brain, ChevronRight, CircleAlert, GitBranch, Plus, RefreshCw, Scissors, Wrench } from "lucide-react";
import { useStore, type FeedItem } from "@/store";
import type { Phase } from "@/types";
import { cn } from "@/lib/utils";
import { BorderBeam } from "@/components/ui/border-beam";
import { StatusIcon } from "./StatusIcon";
import { ResultCard } from "./ResultCard";
import { LoadChip } from "./Load";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

const PIPELINE: { phase: Phase; label: string }[] = [
  { phase: "understand", label: "Understand" },
  { phase: "design", label: "Design" },
  { phase: "build", label: "Build" },
  { phase: "test", label: "Test" },
  { phase: "optimise", label: "Optimise" },
  { phase: "verify", label: "Verify" },
];

function Pipeline() {
  const phase = useStore((s) => s.phase);
  const seen = useStore((s) => s.phasesSeen);
  const run = useStore((s) => s.run);
  const current = PIPELINE.findIndex((p) => p.phase === phase);
  return (
    <ol className="grid grid-cols-6 gap-1.5" aria-label="Progress">
      {PIPELINE.map((p, i) => {
        const done = run === "done" || (seen.includes(p.phase) && i < current) || (phase === "done");
        const active = run === "running" && p.phase === phase;
        const skipped = run === "done" && !seen.includes(p.phase);
        return (
          <li key={p.phase} className="min-w-0" aria-current={active ? "step" : undefined}>
            <div className={cn("h-1 overflow-hidden rounded-full bg-muted", skipped && "opacity-40")}>
              <motion.div
                className={cn("h-full rounded-full", active ? "bg-primary" : "bg-primary/70")}
                initial={false}
                animate={{ width: done && !skipped ? "100%" : active ? "55%" : "0%" }}
                transition={{ duration: 0.6, ease: "easeOut" }}
              />
            </div>
            <div className={cn("mt-1.5 truncate text-[10px] font-medium", active ? "text-foreground" : "text-muted-foreground")} title={p.label}>{p.label}</div>
          </li>
        );
      })}
    </ol>
  );
}

function names(ids: string[]) {
  const parts = useStore.getState().design?.parts ?? [];
  return ids.map((id) => parts.find((p) => p.id === id)?.name ?? id);
}

function Row({ item, last, attempt }: { item: FeedItem; last: boolean; attempt: number }) {
  const set = useStore((s) => s.set);
  const running = useStore((s) => s.run === "running");
  const [open, setOpen] = useState(false);

  switch (item.kind) {
    case "phase":
      return (
        <div className={cn("relative mt-5 flex items-center gap-2 rounded-md px-2 py-1.5 first:mt-0", last && running && "bg-accent/60")}>
          <span className="text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">{item.label}</span>
          {last && running && <BorderBeam size={60} duration={4} colorFrom="var(--primary)" colorTo="transparent" />}
        </div>
      );
    case "thought":
      return <p className="px-2 py-1 text-[13px] leading-relaxed text-foreground/90">{item.text}</p>;
    case "thinking":
      return (
        <div className="px-2 py-0.5">
          <button onClick={() => setOpen(!open)} className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground" aria-expanded={open}>
            <Brain className="size-3.5" /> Reasoning <ChevronRight className={cn("size-3 transition-transform", open && "rotate-90")} />
          </button>
          {open && <p className="mt-1 border-l pl-3 text-xs leading-relaxed text-muted-foreground whitespace-pre-wrap">{item.text}</p>}
        </div>
      );
    case "tool":
      return (
        <div className="mx-2 my-0.5 flex items-center gap-2 rounded-md bg-muted/60 px-2 py-1 font-mono text-[11px] text-muted-foreground">
          <Wrench className="size-3" /> <span className="truncate">{item.label}</span>
        </div>
      );
    case "design": {
      const list = item.added.length ? names(item.added) : names(item.changed);
      const Icon = item.added.length ? Plus : RefreshCw;
      return (
        <div className="flex items-start gap-2 px-2 py-1 text-xs text-muted-foreground">
          <Icon className="mt-0.5 size-3.5 shrink-0 text-primary" />
          <button onClick={() => useStore.getState().viewVersion(item.version)} className="shrink-0 rounded bg-muted px-1 py-0.5 font-mono text-[10px] hover:text-foreground" title="Open this version">v{item.version}</button>
          <div className="flex flex-wrap gap-1">
            {list.length ? list.map((n) => (
              <span key={n} className="rounded border bg-card px-1.5 py-0.5 text-[11px] text-foreground">{n}</span>
            )) : <span>{item.parts} parts</span>}
          </div>
        </div>
      );
    }
    case "diagram":
      return (
        <button onClick={() => set({ tab: "system" })} className="flex items-center gap-2 px-2 py-1 text-xs text-muted-foreground hover:text-foreground">
          <GitBranch className="size-3.5 text-primary" /> System diagram · {item.nodes} blocks
        </button>
      );
    case "test": {
      const r = item.result;
      return (
        <button
          onClick={() => set({ tab: "tests", focusTest: item.id, selected: item.part })}
          className="group flex w-full items-start gap-2.5 rounded-md px-2 py-1.5 text-left hover:bg-accent/60"
        >
          <StatusIcon status={r ? r.status : "pending"} className="mt-0.5" />
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2 text-[13px] font-medium leading-snug">
              {item.title}
              {attempt > 1 && <span className="rounded bg-muted px-1 py-px text-[10px] font-normal text-muted-foreground">run {attempt}</span>}
              {r && r.load !== undefined && <span className="ml-auto"><LoadChip load={r.load} /></span>}
            </div>
            {r && <div className="mt-0.5 text-xs leading-snug text-muted-foreground">{r.headline}</div>}
          </div>
        </button>
      );
    }
    case "change": {
      const [a, b] = item.mass;
      const d = b - a;
      return (
        <div className="mx-2 my-1 rounded-md border bg-card px-2.5 py-2">
          <div className="flex items-center gap-2 text-[13px] font-medium">
            {item.fix ? <Wrench className="size-3.5 text-primary" /> : <Scissors className="size-3.5 text-good" />}
            <span>{item.change}</span>
          </div>
          <div className="mt-1 flex items-center justify-between gap-2 text-[11px] text-muted-foreground">
            <span className="line-clamp-1">{item.fix ? "fixes: " : ""}{item.reason}</span>
            <span className={cn("shrink-0 font-mono", d > 0 ? "text-warn" : "text-good")}>{d > 0 ? "+" : ""}{Math.abs(d) >= 1000 ? `${(d / 1000).toFixed(2)} kg` : `${d.toFixed(0)} g`}</span>
          </div>
        </div>
      );
    }
    case "error":
      return (
        <div className="mx-2 my-1 flex items-start gap-2 rounded-md border border-bad/40 bg-bad/10 px-2.5 py-2 text-xs text-bad">
          <CircleAlert className="mt-0.5 size-3.5 shrink-0" /> {item.text}
        </div>
      );
    case "done":
      return null;
  }
}

export function ProcessPanel() {
  const feed = useStore((s) => s.feed);
  const run = useStore((s) => s.run);
  const name = useStore((s) => s.design?.name);
  const request = useStore((s) => s.request);
  const source = useStore((s) => s.source);
  const end = useRef<HTMLDivElement>(null);
  const box = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  const visible = useMemo(() => feed.filter((f) => f.kind !== "done"), [feed]);
  const attempts = useMemo(() => {
    const seen: Record<string, number> = {};
    return visible.map((f) => (f.kind === "test" ? (seen[f.id] = (seen[f.id] ?? 0) + 1) : 0));
  }, [visible]);

  // when the run finishes, bring the result card (top of the feed) into view
  useEffect(() => {
    if (run === "done") box.current?.scrollTo({ top: 0, behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
  }, [run]);

  useEffect(() => {
    if (stick.current && run === "running") end.current?.scrollIntoView({ block: "end", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
  }, [visible.length]);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="border-b px-4 pb-3 pt-4">
        <div className="mb-3 flex items-center gap-2">
          <h2 className="min-w-0 truncate text-sm font-semibold">{name ?? "Process"}</h2>
          {(source === "demo" || source === "mock") && (
            <Tooltip>
              <TooltipTrigger asChild>
                <span tabIndex={0} className="ml-auto shrink-0 cursor-default rounded border px-1.5 py-px text-[10px] text-muted-foreground">Offline run</span>
              </TooltipTrigger>
              <TooltipContent side="bottom">Design steps recorded; every simulation ran live just now.</TooltipContent>
            </Tooltip>
          )}
        </div>
        <Pipeline />
      </div>
      <div
        ref={box}
        onScroll={(e) => {
          const el = e.currentTarget;
          stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
        }}
        className="scrollbar-thin min-h-0 flex-1 overflow-y-auto px-2 py-3"
        aria-live="polite"
      >
        {run === "idle" && <Intro />}
        {request && (
          <blockquote className="mx-1 mb-4 rounded-xl rounded-tr-sm bg-accent/70 px-3.5 py-2.5 text-[13px] leading-relaxed text-foreground">
            {request}
          </blockquote>
        )}
        <ResultCard />
        <AnimatePresence initial={false}>
          {visible.map((item, i) => (
            <motion.div
              key={item.key}
              initial={{ opacity: 0, y: 6, filter: "blur(2px)" }}
              animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
              transition={{ duration: 0.35, ease: "easeOut" }}
            >
              <Row item={item} attempt={attempts[i]} last={i === visible.length - 1 || (item.kind === "phase" && !visible.slice(i + 1).some((v) => v.kind === "phase"))} />
            </motion.div>
          ))}
        </AnimatePresence>
        {run === "running" && (
          <div className="flex items-center gap-2 px-2 py-2 text-xs text-muted-foreground">
            <span className="relative flex size-2"><span className="absolute inline-flex size-full animate-ping rounded-full bg-primary/60" /><span className="relative inline-flex size-2 rounded-full bg-primary" /></span>
            working
          </div>
        )}
        <div ref={end} />
      </div>
    </div>
  );
}

function Intro() {
  return (
    <div className="px-2 py-2 text-[13px] leading-relaxed text-muted-foreground">
      <p className="text-foreground">Describe a thing. Watch it get designed, built and tested against the real world.</p>
      <ol className="mt-4 space-y-2.5">
        {[
          ["Design", "parts, materials and how it goes together"],
          ["Test", "load, wind, heat, fire, current, flow, signal coverage"],
          ["Fix", "changes the design until every test passes"],
        ].map(([a, b], i) => (
          <li key={a} className="flex gap-3">
            <span className="grid size-5 shrink-0 place-items-center rounded-full border text-[10px] font-semibold text-foreground">{i + 1}</span>
            <span><span className="font-medium text-foreground">{a}</span> · {b}</span>
          </li>
        ))}
      </ol>
      <p className="mt-4 text-xs">Every number comes from a simulation that checks itself.</p>
    </div>
  );
}
