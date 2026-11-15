import { Boxes, Focus, Layers, Radar, ScanEye, ShieldCheck } from "lucide-react";
import { useStore } from "@/store";
import { Slider } from "@/components/ui/slider";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import type { ImageField } from "@/types";
import { fieldRange } from "./SiteView";
import { VersionBanner, VersionMenu } from "@/panels/Versions";

function IconToggle({ on, label, onClick, children }: { on: boolean; label: string; onClick: () => void; children: React.ReactNode }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          type="button"
          aria-label={label}
          aria-pressed={on}
          onClick={onClick}
          className={cn(
            "grid size-8 place-items-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-foreground",
            on && "bg-accent text-foreground",
          )}
        >
          {children}
        </button>
      </TooltipTrigger>
      <TooltipContent side="bottom">{label}</TooltipContent>
    </Tooltip>
  );
}

export function ViewportControls({ hasSite }: { hasSite: boolean }) {
  const s = useStore();
  const site = s.view === "site";
  return (
    <>
      <div className="no-print absolute left-3 top-3 flex items-center gap-1 rounded-lg border bg-card/80 p-1 shadow-sm backdrop-blur">
        {hasSite && (
          <div role="radiogroup" aria-label="View" className="mr-1 flex rounded-md bg-muted p-0.5 text-xs font-medium">
            {(["model", "site"] as const).map((v) => (
              <button
                key={v}
                role="radio"
                aria-checked={s.view === v}
                onClick={() => s.set({ view: v, selected: null })}
                className={cn("flex items-center gap-1.5 rounded px-2.5 py-1 capitalize transition-colors",
                  s.view === v ? "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground")}
              >
                {v === "model" ? <Boxes className="size-3.5" /> : <Radar className="size-3.5" />}
                {v}
              </button>
            ))}
          </div>
        )}
        {!site && (
          <>
            <IconToggle on={s.xray} label="X-ray" onClick={() => s.set({ xray: !s.xray })}><ScanEye className="size-4" /></IconToggle>
            <IconToggle on={s.tint} label="Colour parts by test result" onClick={() => s.set({ tint: !s.tint })}><ShieldCheck className="size-4" /></IconToggle>
            <IconToggle on={false} label="Reset view" onClick={() => s.set({ isolate: null, explode: 0, selected: null, frameNonce: s.frameNonce + 1 })}><Focus className="size-4" /></IconToggle>
            <div className="flex items-center gap-2 pl-2 pr-1 max-sm:hidden">
              <Layers className="size-4 text-muted-foreground" aria-hidden />
              <Slider aria-label="Explode" className="w-24" min={0} max={1} step={0.01} value={[s.explode]} onValueChange={([v]) => s.set({ explode: v })} />
            </div>
          </>
        )}
      </div>
      <div className="no-print absolute right-3 top-3 rounded-lg border bg-card/80 p-1 shadow-sm backdrop-blur empty:hidden">
        <VersionMenu />
      </div>
      <VersionBanner />
      {site && <SiteLegend />}
      {!site && s.isolate && (
        <button onClick={() => s.set({ isolate: null, explode: 0 })} className="no-print absolute bottom-3 left-1/2 -translate-x-1/2 rounded-full border bg-card/90 px-3 py-1 text-xs shadow backdrop-blur hover:bg-accent">
          Showing one assembly step · show all
        </button>
      )}
    </>
  );
}

function SiteLegend() {
  const test = useStore((s) => Object.values(s.tests).find((t) => t.result?.overlay)?.result);
  if (!test?.overlay) return null;
  const f = test.sim?.fields?.[test.overlay.field] as ImageField | undefined;
  if (!f) return null;
  const [lo, hi] = fieldRange(f);
  const thr = test.overlay.threshold;
  const a = Math.max(lo, thr - 30), b = Math.min(hi, thr + 30);
  const pos = ((thr - a) / (b - a)) * 100;
  return (
    <div className="no-print absolute bottom-3 left-3 w-64 rounded-lg border border-white/10 bg-black/55 p-3 text-[11px] text-slate-200 shadow-lg backdrop-blur">
      <div className="mb-1.5 flex items-center justify-between">
        <span className="font-medium text-slate-50">Detection coverage</span>
        <span className="text-slate-400">{f.unit === "dB" ? "signal-to-noise" : f.name}</span>
      </div>
      <div className="relative h-2 rounded-full" style={{ background: `linear-gradient(90deg, #1e3250 0%, #3a6aa8 ${pos}%, #18a0dc ${pos}%, #3cdcf0 ${pos + (100 - pos) / 2}%, #c8faff 100%)` }}>
        <div className="absolute -top-1 h-4 w-px bg-white" style={{ left: `${pos}%` }} />
      </div>
      <div className="mt-1 flex justify-between font-mono text-slate-400">
        <span>{a.toFixed(0)}</span><span className="text-slate-100">{thr} {f.unit}</span><span>{b.toFixed(0)}</span>
      </div>
      <div className="mt-2 flex items-center gap-2 text-slate-300">
        <span className="inline-block size-2 rounded-sm bg-sky-300 shadow-[0_0_8px_#38bdf8]" /> drone detected
        <span className="ml-2 inline-block size-2 rounded-sm bg-slate-500" /> not seen
      </div>
    </div>
  );
}
