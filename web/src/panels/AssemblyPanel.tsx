import { useStore } from "@/store";
import { cn } from "@/lib/utils";
import { EmptyTab } from "./TestsPanel";

export function AssemblyPanel() {
  const design = useStore((s) => s.design);
  const isolate = useStore((s) => s.isolate);
  const set = useStore((s) => s.set);
  const steps = design?.assembly ?? [];
  if (!steps.length) return <EmptyTab text="Assembly steps appear here once the design is complete." />;
  const name = (id: string) => design!.parts.find((p) => p.id === id)?.name ?? id;
  return (
    <ol className="relative space-y-1">
      {steps.map((s, i) => {
        const upTo = new Set(steps.slice(0, i + 1).flatMap((x) => x.parts));
        const active = !!isolate && s.parts.every((p) => isolate.includes(p)) && isolate.length === upTo.size;
        return (
          <li key={i}>
            <button
              onClick={() => set(active ? { isolate: null, explode: 0 } : { isolate: [...upTo], explode: 0.15, view: "model", selected: null })}
              onMouseEnter={() => set({ hovered: s.parts[0] ?? null })}
              onMouseLeave={() => set({ hovered: null })}
              className={cn("flex w-full gap-3 rounded-lg p-3 text-left transition-colors hover:bg-accent/50", active && "bg-accent")}
              aria-pressed={active}
            >
              <span className={cn("grid size-6 shrink-0 place-items-center rounded-full border text-xs font-semibold tabular-nums", active && "border-primary bg-primary text-primary-foreground")}>{i + 1}</span>
              <span className="min-w-0">
                <span className="block text-sm leading-snug">{s.action}</span>
                <span className="mt-1 flex flex-wrap gap-1">
                  {s.parts.map((p) => <span key={p} className="rounded border px-1.5 py-px text-[11px] text-muted-foreground">{name(p)}</span>)}
                </span>
              </span>
            </button>
          </li>
        );
      })}
    </ol>
  );
}
