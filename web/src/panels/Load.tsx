import { cn } from "@/lib/utils";

/** Load = share of a limit used (0.5 half, 1 at the limit, >1 over). */
export function loadBand(l: number | null | undefined) {
  if (l === null || l === undefined || !Number.isFinite(l)) return { key: "none", label: "untrusted", text: "text-muted-foreground", bar: "bg-muted-foreground/40" };
  if (l <= 0.5) return { key: "calm", label: "plenty of margin", text: "text-primary", bar: "bg-primary" };
  if (l <= 0.8) return { key: "fine", label: "comfortable", text: "text-good", bar: "bg-good" };
  if (l <= 1) return { key: "close", label: "close to the limit", text: "text-warn", bar: "bg-warn" };
  return { key: "over", label: "over the limit", text: "text-bad", bar: "bg-bad" };
}

export function fmtLoad(l: number | null | undefined) {
  if (l === null || l === undefined || !Number.isFinite(l)) return "–";
  const p = l * 100;
  return `${p >= 995 ? Math.round(p / 100) * 100 : p >= 10 ? Math.round(p) : p.toFixed(1)}%`;
}

/** "64%" with a slim bar; the tick marks 100% (the limit). */
export function LoadBar({ load, className, compact }: { load: number | null | undefined; className?: string; compact?: boolean }) {
  const b = loadBand(load);
  const SCALE = 1.5; // bar spans 0..150 %
  const w = load === null || load === undefined ? 0 : Math.min(1, Math.max(0, load / SCALE));
  return (
    <div className={cn(compact ? "w-16" : "w-28", "shrink-0", className)} title={`${fmtLoad(load)} of the limit used: ${b.label}`}>
      {!compact && (
        <div className="flex items-baseline justify-end gap-1">
          <span className={cn("text-sm font-semibold tabular-nums", b.text)}>{fmtLoad(load)}</span>
          <span className="text-[10px] text-muted-foreground">of limit</span>
        </div>
      )}
      <div className={cn("relative h-1.5 rounded-full bg-muted", !compact && "mt-1")}>
        <div className={cn("absolute inset-y-0 left-0 rounded-full transition-[width] duration-500", b.bar)} style={{ width: `${w * 100}%` }} />
        <div className="absolute -top-0.5 h-2.5 w-px bg-foreground/60" style={{ left: `${100 / SCALE}%` }} aria-hidden />
      </div>
    </div>
  );
}

export function LoadChip({ load }: { load: number | null | undefined }) {
  const b = loadBand(load);
  return <span className={cn("rounded px-1 py-px font-mono text-[10.5px] tabular-nums", b.text, "bg-muted")}>{fmtLoad(load)}</span>;
}
