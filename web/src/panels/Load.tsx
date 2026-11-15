import { cn } from "@/lib/utils";

/** Margin of safety from a load (share of the limit used): allowable / actual - 1.
 *  +0.25 = 25% to spare, 0 = exactly at the limit, negative = fails. */
export function marginOf(load: number | null | undefined) {
  if (load === null || load === undefined || !Number.isFinite(load)) return null;
  return load <= 0.01 ? 99 : 1 / load - 1;
}

/** Bands are set on the load so margin and bar always agree. */
export function loadBand(l: number | null | undefined) {
  if (l === null || l === undefined || !Number.isFinite(l)) return { key: "none", label: "untrusted", text: "text-muted-foreground", bar: "bg-muted-foreground/40" };
  if (l > 1) return { key: "over", label: "fails: over the limit", text: "text-bad", bar: "bg-bad" };
  if (l > 0.8) return { key: "close", label: "tight margin", text: "text-warn", bar: "bg-warn" };
  if (l > 0.5) return { key: "fine", label: "comfortable margin", text: "text-good", bar: "bg-good" };
  return { key: "calm", label: "large margin", text: "text-primary", bar: "bg-primary" };
}

/** "+47%", "0%", "-12%"; very large margins read ">+300%". */
export function fmtMargin(load: number | null | undefined) {
  const m = marginOf(load);
  if (m === null) return "–";
  if (m > 3) return ">+300%";
  const p = Math.round(m * 100);
  return `${p > 0 ? "+" : p < 0 ? "−" : ""}${Math.abs(p)}%`;
}

/** Kept for callers that show load directly. */
export const fmtLoad = fmtMargin;

/** The margin with a slim bar: filled = what the design uses, the tick is the
 *  limit, and the gap between them is the margin (the same picture as the logo). */
export function LoadBar({ load, className, compact }: { load: number | null | undefined; className?: string; compact?: boolean }) {
  const b = loadBand(load);
  const SCALE = 1.5; // bar spans 0..150 % of the limit
  const w = load === null || load === undefined ? 0 : Math.min(1, Math.max(0, load / SCALE));
  return (
    <div className={cn(compact ? "w-16" : "w-28", "shrink-0", className)} title={`Margin of safety ${fmtMargin(load)}: ${b.label}`}>
      {!compact && (
        <div className="flex items-baseline justify-end gap-1">
          <span className={cn("text-sm font-semibold tabular-nums", b.text)}>{fmtMargin(load)}</span>
          <span className="text-[10px] text-muted-foreground">margin</span>
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
  return <span className={cn("rounded px-1 py-px font-mono text-[10.5px] tabular-nums", b.text, "bg-muted")}>{fmtMargin(load)}</span>;
}
