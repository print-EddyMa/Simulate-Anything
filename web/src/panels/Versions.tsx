import { History } from "lucide-react";
import { useStore } from "@/store";
import { cn } from "@/lib/utils";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";

/** Compact version history: every design snapshot can be reopened read-only. */
export function VersionMenu() {
  const versions = useStore((s) => s.versions);
  const viewing = useStore((s) => s.viewing);
  const viewVersion = useStore((s) => s.viewVersion);
  if (versions.length < 2) return null;
  const latest = versions[versions.length - 1];
  const shown = viewing ?? latest.n;
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button className="flex h-8 items-center gap-1.5 rounded-md px-2.5 text-xs font-medium text-muted-foreground hover:bg-accent hover:text-foreground" aria-label={`Version ${shown} of ${latest.n}`}>
          <History className="size-3.5" /> v{shown}<span className="text-muted-foreground/70">/{latest.n}</span>
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="max-h-[60vh] w-80 overflow-y-auto">
        <DropdownMenuLabel className="text-xs text-muted-foreground">Versions</DropdownMenuLabel>
        {[...versions].reverse().map((v) => {
          const st = Object.values(v.tests);
          const pass = st.filter((x) => x === "pass").length;
          return (
            <DropdownMenuItem key={v.n} onSelect={() => viewVersion(v.n === latest.n ? null : v.n)} className={cn("items-start gap-2.5 py-2", v.n === shown && "bg-accent")}>
              <span className="mt-px w-7 shrink-0 font-mono text-[11px] text-muted-foreground">v{v.n}</span>
              <span className="min-w-0 flex-1 text-xs leading-snug">{v.label}{v.edited && <span className="ml-1 text-muted-foreground">(edited)</span>}</span>
              {st.length > 0 && (
                <span className={cn("shrink-0 font-mono text-[11px] tabular-nums", pass === st.length ? "text-good" : "text-bad")}>{pass}/{st.length}</span>
              )}
            </DropdownMenuItem>
          );
        })}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

export function VersionBanner() {
  const viewing = useStore((s) => s.viewing);
  const viewVersion = useStore((s) => s.viewVersion);
  if (viewing === null) return null;
  return (
    <div className="no-print absolute left-1/2 top-3 flex -translate-x-1/2 items-center gap-3 rounded-full border bg-card/90 py-1 pl-3 pr-1 text-xs shadow backdrop-blur">
      <span>Viewing <b className="font-semibold">v{viewing}</b> · read-only</span>
      <button onClick={() => viewVersion(null)} className="rounded-full bg-primary px-2.5 py-1 font-medium text-primary-foreground hover:opacity-90">Back to latest</button>
    </div>
  );
}
