import { useState } from "react";
import { Braces, Play, Wand2 } from "lucide-react";
import { useStore } from "@/store";
import { runEvaluate, runOptimise } from "@/api";
import type { Design } from "@/types";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

function ToolButton({ label, onClick, icon: Icon, primary, disabled }: { label: string; onClick: () => void; icon: typeof Play; primary?: boolean; disabled?: boolean }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button size="sm" variant={primary ? "default" : "ghost"} disabled={disabled} onClick={onClick} className={cn("h-8 gap-1.5 px-2.5 text-xs", !primary && "text-muted-foreground")}>
          <Icon className="size-3.5" /><span className="max-sm:sr-only">{label}</span>
        </Button>
      </TooltipTrigger>
      <TooltipContent className="sm:hidden">{label}</TooltipContent>
    </Tooltip>
  );
}

/** Run tests / Optimise / Edit JSON on the design that is loaded. */
export function DesignTools() {
  const design = useStore((s) => s.design);
  const run = useStore((s) => s.run);
  const stale = useStore((s) => s.stale);
  const status = useStore((s) => s.status);
  const [json, setJson] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  if (!design) return null;
  const busy = run === "running";
  const canOptimise = !!status && !status.mock;
  return (
    <div className="ml-auto flex items-center gap-0.5 py-1">
      {stale && <span className="mr-1 hidden text-[11px] text-warn sm:inline">edited</span>}
      <ToolButton label="Run tests" icon={Play} primary={stale} disabled={busy || !canOptimise} onClick={() => void runEvaluate()} />
      <ToolButton label="Optimise" icon={Wand2} disabled={busy || !canOptimise} onClick={() => status && void runOptimise({ agent: status.agent, version: "", mock: status.mock })} />
      <ToolButton label="Edit JSON" icon={Braces} disabled={busy} onClick={() => { setErr(null); setJson(JSON.stringify(design, null, 2)); }} />
      <Dialog open={json !== null} onOpenChange={(o) => !o && setJson(null)}>
        <DialogContent className="max-w-3xl">
          <DialogHeader>
            <DialogTitle>Design JSON</DialogTitle>
            <DialogDescription>Lengths in millimetres, z up. Applying creates a new version.</DialogDescription>
          </DialogHeader>
          <textarea
            value={json ?? ""}
            onChange={(e) => setJson(e.target.value)}
            spellCheck={false}
            aria-label="Design JSON"
            className="scrollbar-thin h-[55vh] w-full resize-none rounded-lg border bg-muted/40 p-3 font-mono text-xs leading-relaxed outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
          />
          {err && <p className="text-xs text-bad">{err}</p>}
          <DialogFooter>
            <Button variant="ghost" onClick={() => setJson(null)}>Cancel</Button>
            <Button onClick={() => {
              try {
                const d = JSON.parse(json ?? "") as Design;
                if (!d || !Array.isArray(d.parts) || !d.parts.length) throw new Error("a design needs a non-empty \"parts\" list");
                d.assembly ??= [];
                d.scenarios ??= [];
                useStore.getState().edit(d, "Edited JSON");
                setJson(null);
              } catch (e) {
                setErr(e instanceof SyntaxError ? `Not valid JSON: ${e.message}` : String((e as Error).message));
              }
            }}>Apply</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
