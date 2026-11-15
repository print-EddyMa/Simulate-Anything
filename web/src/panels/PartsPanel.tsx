import { useMemo, useState } from "react";
import { ArrowDown, ArrowUp } from "lucide-react";
import { useStore } from "@/store";
import type { SummaryRow } from "@/types";
import { cn } from "@/lib/utils";
import { fmtMass } from "@/three/format";
import { EmptyTab } from "./TestsPanel";

type Key = "name" | "material" | "process" | "quantity" | "mass_g" | "cost";
const COLS: { key: Key; label: string; num?: boolean; hide?: string }[] = [
  { key: "name", label: "Part" },
  { key: "material", label: "Material", hide: "max-sm:hidden" },
  { key: "process", label: "Process", hide: "max-md:hidden" },
  { key: "quantity", label: "Qty", num: true },
  { key: "mass_g", label: "Mass", num: true },
  { key: "cost", label: "Cost", num: true, hide: "max-sm:hidden" },
];

export function PartsPanel() {
  const summary = useStore((s) => s.summary);
  const design = useStore((s) => s.design);
  const selected = useStore((s) => s.selected);
  const hovered = useStore((s) => s.hovered);
  const set = useStore((s) => s.set);
  const [sort, setSort] = useState<{ key: Key; asc: boolean }>({ key: "mass_g", asc: false });
  const rows = useMemo(() => {
    const list = [...(summary?.parts ?? [])];
    list.sort((a, b) => {
      const x = a[sort.key] as string | number, y = b[sort.key] as string | number;
      const c = typeof x === "number" ? (x as number) - (y as number) : String(x).localeCompare(String(y));
      return sort.asc ? c : -c;
    });
    return list;
  }, [summary, sort]);
  if (!summary || !design) return <EmptyTab text="Parts appear here once the design takes shape." />;
  const specs = (id: string) => design.parts.find((p) => p.id === id)?.specs;
  return (
    <div className="overflow-x-auto rounded-xl border">
      <table className="w-full text-sm">
        <thead className="bg-muted/50 text-xs text-muted-foreground">
          <tr>
            {COLS.map((c) => (
              <th key={c.key} className={cn("px-3 py-2 font-medium", c.num ? "text-right" : "text-left", c.hide)}
                aria-sort={sort.key === c.key ? (sort.asc ? "ascending" : "descending") : "none"}>
                <button className="inline-flex items-center gap-1 hover:text-foreground" onClick={() => setSort({ key: c.key, asc: sort.key === c.key ? !sort.asc : !c.num })}>
                  {c.label}
                  {sort.key === c.key && (sort.asc ? <ArrowUp className="size-3" /> : <ArrowDown className="size-3" />)}
                </button>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r: SummaryRow) => (
            <tr
              key={r.id}
              className={cn("cursor-pointer border-t transition-colors hover:bg-accent/50", (selected === r.id || hovered === r.id) && "bg-accent/70")}
              onMouseEnter={() => set({ hovered: r.id })}
              onMouseLeave={() => set({ hovered: null })}
              onClick={() => set({ selected: r.id, view: "model" })}
            >
              <td className="px-3 py-2">
                <div className="font-medium">{r.name}</div>
                {r.purpose && <div className="line-clamp-1 text-xs text-muted-foreground">{r.purpose}</div>}
                {specs(r.id) && Object.keys(specs(r.id)!).length > 0 && (
                  <div className="mt-1 flex flex-wrap gap-1">
                    {Object.entries(specs(r.id)!).slice(0, 4).map(([k, v]) => (
                      <span key={k} className="rounded bg-muted px-1.5 py-px font-mono text-[10.5px] text-muted-foreground">{k} {v}</span>
                    ))}
                  </div>
                )}
              </td>
              <td className="px-3 py-2 max-sm:hidden">{r.material}</td>
              <td className="px-3 py-2 text-muted-foreground max-md:hidden">{r.process.replace(/_/g, " ")}</td>
              <td className="px-3 py-2 text-right tabular-nums">{r.quantity}</td>
              <td className="px-3 py-2 text-right tabular-nums">{fmtMass(r.mass_g)}</td>
              <td className="px-3 py-2 text-right tabular-nums max-sm:hidden">${r.cost.toFixed(2)}</td>
            </tr>
          ))}
        </tbody>
        <tfoot className="border-t bg-muted/30 text-sm font-semibold">
          <tr>
            <td className="px-3 py-2">{rows.length} parts</td>
            <td className="max-sm:hidden" />
            <td className="max-md:hidden" />
            <td />
            <td className="px-3 py-2 text-right tabular-nums">{fmtMass(summary.mass_g)}</td>
            <td className="px-3 py-2 text-right tabular-nums max-sm:hidden">${summary.material_cost.toFixed(2)}</td>
          </tr>
        </tfoot>
      </table>
    </div>
  );
}
