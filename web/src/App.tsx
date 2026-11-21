import { lazy, Suspense, useEffect, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { useStore } from "@/store";
import { getExamples, getMaterials, getStatus, openDesign, type Status } from "@/api";
import type { Example } from "@/types";
import { cn } from "@/lib/utils";
import { TooltipProvider } from "@/components/ui/tooltip";
import { TopBar } from "@/panels/TopBar";
import { ProcessPanel } from "@/panels/ProcessPanel";
import { TestsPanel } from "@/panels/TestsPanel";
import { PartsPanel } from "@/panels/PartsPanel";
import { AssemblyPanel } from "@/panels/AssemblyPanel";
import { PrintReport } from "@/panels/PrintReport";
import { DesignTools } from "@/panels/DesignTools";

const Viewport = lazy(() => import("@/three/Viewport"));
const SystemPanel = lazy(() => import("@/panels/SystemPanel"));

const TABS = [
  { id: "tests", label: "Tests" },
  { id: "parts", label: "Parts" },
  { id: "assembly", label: "Assembly" },
  { id: "system", label: "System" },
] as const;

function Results() {
  const tab = useStore((s) => s.tab);
  const set = useStore((s) => s.set);
  const counts = useStore(useShallow((s) => ({
    tests: s.testOrder.length,
    parts: s.design?.parts.length ?? 0,
    assembly: s.design?.assembly.length ?? 0,
    system: s.diagram?.nodes.length ?? 0,
  })));
  const failing = useStore((s) => Object.values(s.tests).filter((t) => t.result && t.result.status !== "pass").length);
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex items-center border-b px-3">
      <div role="tablist" aria-label="Results" className="scrollbar-thin flex gap-1 overflow-x-auto">
        {TABS.map((t) => (
          <button
            key={t.id}
            role="tab"
            aria-selected={tab === t.id}
            onClick={() => set({ tab: t.id })}
            className={cn("relative -mb-px flex items-center gap-1.5 border-b-2 px-3 py-2.5 text-sm transition-colors",
              tab === t.id ? "border-primary text-foreground" : "border-transparent text-muted-foreground hover:text-foreground")}
          >
            {t.label}
            {counts[t.id] > 0 && (
              <span className={cn("rounded-full px-1.5 text-[10px] font-semibold tabular-nums",
                t.id === "tests" && failing ? "bg-bad/15 text-bad" : "bg-muted text-muted-foreground")}>{t.id === "tests" && failing ? `${failing} failing` : counts[t.id]}</span>
            )}
          </button>
        ))}
      </div>
      <DesignTools />
      </div>
      <div role="tabpanel" className="scrollbar-thin min-h-0 flex-1 overflow-y-auto p-3 sm:p-4">
        {tab === "tests" && <TestsPanel />}
        {tab === "parts" && <PartsPanel />}
        {tab === "assembly" && <AssemblyPanel />}
        {tab === "system" && <Suspense fallback={null}><SystemPanel /></Suspense>}
      </div>
    </div>
  );
}

export default function App() {
  const [status, setStatus] = useState<Status | null>(null);
  const [examples, setExamples] = useState<Example[]>([]);
  const set = useStore((s) => s.set);
  const hasDesign = useStore((s) => !!s.design);
  // presenter mode: the presenter page can ask for the 3D view alone, full screen
  const [focus, setFocus] = useState(false);
  useEffect(() => {
    const on = (e: MessageEvent) => {
      if (e.origin === location.origin && e.data && typeof e.data.focus === "boolean") setFocus(e.data.focus);
    };
    window.addEventListener("message", on);
    return () => window.removeEventListener("message", on);
  }, []);
  useEffect(() => {
    // the 3D canvas remounts in the new layout; nudge it to measure itself
    const ts = [50, 300, 900].map((ms) => setTimeout(() => window.dispatchEvent(new Event("resize")), ms));
    return () => ts.forEach(clearTimeout);
  }, [focus]);

  useEffect(() => {
    void (async () => {
      const st = await getStatus();
      setStatus(st);
      set({ status: st });
      const [ex, mats] = await Promise.all([getExamples(st), getMaterials(st)]);
      setExamples(ex);
      set({ materials: mats });
      const open = new URLSearchParams(location.search).get("open");
      if (open && !st.mock) void openDesign(open);
    })();
  }, [set]);

  return (
    <TooltipProvider delayDuration={250}>
      <div className="flex min-h-dvh flex-col lg:h-dvh">
        <TopBar status={status} examples={examples} />
        <main className="no-print grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[minmax(0,1fr)_400px] lg:grid-rows-[minmax(0,1.45fr)_minmax(0,1fr)]">
          <section aria-label="3D view" className={focus ? "fixed inset-0 z-50" : "relative h-[52vh] min-h-[320px] border-b lg:h-auto lg:min-h-0"}>
            <Suspense fallback={<div className="size-full bg-viewport" />}>
              <Viewport />
            </Suspense>
          </section>
          <aside aria-label="Process" className="h-[60vh] min-h-0 border-b lg:col-start-2 lg:row-span-2 lg:row-start-1 lg:h-auto lg:border-b-0 lg:border-l">
            <ProcessPanel />
          </aside>
          <section aria-label="Results" className={cn("min-h-[50vh] lg:min-h-0", !hasDesign && "max-lg:hidden")}>
            <Results />
          </section>
        </main>
        <PrintReport />
      </div>
    </TooltipProvider>
  );
}
