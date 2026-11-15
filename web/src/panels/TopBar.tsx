import { useRef, useState } from "react";
import { ArrowUp, Box, Flame, ImagePlus, Moon, Radar, Ruler, Sparkles, Square, Sun, Thermometer, X } from "lucide-react";
import { useStore } from "@/store";
import { fileToDataUrl, importStl, runCreate, runSuggestion, stopRun, type Status } from "@/api";
import type { Example } from "@/types";
import { cn } from "@/lib/utils";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

/** Short, natural labels for the suggested prompts. */
const SHORT: Record<string, string> = {
  drone_radar: "Passive radar to spot drones around a base",
  bracket: "Shelf bracket for 8 kg of books",
  heatsink: "Heat sink for a 40 W LED",
};

const ICONS: Record<string, typeof Radar> = { radar: Radar, ruler: Ruler, bracket: Ruler, flame: Flame, thermometer: Thermometer, box: Box, sparkles: Sparkles };

/** The mark: a design bar, the margin (brand colour), then the limit line. */
export function Mark({ className }: { className?: string }) {
  return (
    <svg viewBox="8 8 48 48" className={className} aria-hidden>
      <rect x="13" y="17" width="19" height="30" rx="3.5" className="fill-foreground" />
      <rect x="34.5" y="17" width="9" height="30" rx="1.5" className="fill-primary" opacity="0.9" />
      <rect x="47" y="11" width="4" height="42" rx="2" className="fill-foreground" />
    </svg>
  );
}

function Logo() {
  return (
    <div className="flex items-center gap-2" aria-label="Margin: Create anything">
      <Mark className="size-7" />
      <div className="leading-none">
        <div className="text-[19px] font-semibold tracking-[-0.025em]">margin</div>
        <div className="mt-0.5 text-[11px] text-muted-foreground">Create anything</div>
      </div>
    </div>
  );
}

export function ThemeToggle() {
  const [dark, setDark] = useState(() => document.documentElement.classList.contains("dark"));
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          aria-label={dark ? "Switch to light theme" : "Switch to dark theme"}
          onClick={() => {
            const next = !dark;
            document.documentElement.classList.toggle("dark", next);
            try { localStorage.setItem("theme", next ? "dark" : "light"); } catch { /* private mode */ }
            setDark(next);
          }}
          className="grid size-9 place-items-center rounded-lg text-muted-foreground hover:bg-accent hover:text-foreground"
        >
          {dark ? <Sun className="size-4" /> : <Moon className="size-4" />}
        </button>
      </TooltipTrigger>
      <TooltipContent>{dark ? "Light theme" : "Dark theme"}</TooltipContent>
    </Tooltip>
  );
}

export function TopBar({ status, examples }: { status: Status | null; examples: Example[] }) {
  const run = useStore((s) => s.run);
  const hasDesign = useStore((s) => !!s.design);
  const [mode, setMode] = useState<"change" | "new">("change");
  const changing = hasDesign && mode === "change";
  const [text, setText] = useState("");
  const [image, setImage] = useState<{ name: string; url: string } | null>(null);
  const [mesh, setMesh] = useState<{ name: string; part: unknown } | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const photo = useRef<HTMLInputElement>(null);
  const stl = useRef<HTMLInputElement>(null);
  const agent = !!status?.agent && !status?.mock;
  const running = run === "running";

  const submit = () => {
    if (running) { stopRun(); return; }
    const prompt = text.trim();
    if (!prompt || !status) return;
    // offline demo (?mock=1): any typed request replays the recorded run
    if (!agent && !status.mock) {
      setNote("Designing from your own words needs an Anthropic API key on the server (ANTHROPIC_API_KEY).");
      return;
    }
    void runCreate(prompt, { image: image?.url, mesh: mesh?.part }, status, changing);
    setText("");
  };

  return (
    <header className="no-print border-b bg-background/80 backdrop-blur">
      <div className="mx-auto flex max-w-[1800px] flex-wrap items-center gap-x-6 gap-y-3 px-4 py-3 lg:flex-nowrap">
        <Logo />
        <form
          className="order-3 w-full lg:order-none lg:max-w-3xl lg:flex-1"
          onSubmit={(e) => { e.preventDefault(); submit(); }}
        >
          <div className="flex items-center gap-1 rounded-xl border bg-card px-1.5 py-1 shadow-sm focus-within:ring-2 focus-within:ring-ring/50">
            {hasDesign && (
              <div role="radiogroup" aria-label="What the prompt does" className="mr-1 flex shrink-0 rounded-lg bg-muted p-0.5 text-[11px] font-medium">
                {(["change", "new"] as const).map((m) => (
                  <button key={m} type="button" role="radio" aria-checked={mode === m} onClick={() => setMode(m)}
                    className={cn("rounded-md px-2 py-1 transition-colors", mode === m ? "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground")}>
                    {m === "change" ? "Change this" : "New design"}
                  </button>
                ))}
              </div>
            )}
            <Tooltip>
              <TooltipTrigger asChild>
                <button type="button" aria-label="Attach a photo" onClick={() => photo.current?.click()} className="grid size-8 shrink-0 place-items-center rounded-lg text-muted-foreground hover:bg-accent hover:text-foreground"><ImagePlus className="size-4" /></button>
              </TooltipTrigger>
              <TooltipContent>Photo</TooltipContent>
            </Tooltip>
            <Tooltip>
              <TooltipTrigger asChild>
                <button type="button" aria-label="Import an STL model" onClick={() => stl.current?.click()} className="grid size-8 shrink-0 place-items-center rounded-lg text-muted-foreground hover:bg-accent hover:text-foreground"><Box className="size-4" /></button>
              </TooltipTrigger>
              <TooltipContent>3D model (STL)</TooltipContent>
            </Tooltip>
            {(image || mesh) && (
              <span className="flex shrink-0 items-center gap-1 rounded-md bg-muted px-2 py-1 text-xs">
                {image && <img src={image.url} alt="" className="size-5 rounded object-cover" />}
                <span className="max-w-28 truncate">{image?.name ?? mesh?.name}</span>
                <button type="button" aria-label="Remove attachment" onClick={() => { setImage(null); setMesh(null); }} className="text-muted-foreground hover:text-foreground"><X className="size-3" /></button>
              </span>
            )}
            <input
              value={text}
              onChange={(e) => { setText(e.target.value); setNote(null); }}
              placeholder={changing ? "Describe a change to this design" : "Describe anything you want to build"}
              aria-label={changing ? "Describe a change to this design" : "Describe anything you want to build"}
              className="h-9 min-w-0 flex-1 bg-transparent px-2 text-sm outline-none placeholder:text-muted-foreground"
            />
            <button
              type="submit"
              aria-label={running ? "Stop" : "Create"}
              className={cn("grid size-8 shrink-0 place-items-center rounded-lg transition-colors",
                running ? "bg-foreground text-background" : text.trim() ? "bg-primary text-primary-foreground hover:opacity-90" : "bg-muted text-muted-foreground")}
            >
              {running ? <Square className="size-3 fill-current" /> : <ArrowUp className="size-4" />}
            </button>
          </div>
          <input ref={photo} type="file" accept="image/*" hidden onChange={async (e) => {
            const f = e.target.files?.[0];
            if (f) setImage({ name: f.name, url: await fileToDataUrl(f) });
            e.target.value = "";
          }} />
          <input ref={stl} type="file" accept=".stl,model/stl" hidden onChange={async (e) => {
            const f = e.target.files?.[0];
            e.target.value = "";
            if (!f) return;
            try { setMesh({ name: f.name, part: await importStl(f) }); }
            catch (err) { setNote(String((err as Error).message)); }
          }} />
          {note && <p className="mt-1.5 px-1 text-xs text-muted-foreground" role="status">{note}</p>}
        </form>
        <div className="ml-auto flex items-center gap-1 lg:ml-0">
          <ThemeToggle />
        </div>
      </div>
      <div className="mx-auto flex max-w-[1800px] gap-2 overflow-x-auto px-4 pb-3 scrollbar-thin" role="list" aria-label="Suggested prompts">
        {examples.map((ex, i) => {
          const Icon = ICONS[ex.icon ?? ""] ?? Sparkles;
          return (
            <button
              key={ex.id}
              role="listitem"
              disabled={!status}
              onClick={() => { setText(ex.prompt); setNote(null); setMode("new"); if (status) void runSuggestion(ex, status); }}
              className={cn(
                "group flex shrink-0 items-center gap-2 rounded-full border bg-card px-3 py-1.5 text-left text-xs transition-colors hover:border-ring/60 hover:bg-accent/50",
                i === 0 && "border-primary/40",
              )}
              title={ex.prompt}
            >
              <Icon className={cn("size-3.5", i === 0 ? "text-primary" : "text-muted-foreground group-hover:text-foreground")} />
              <span>{SHORT[ex.id] ?? ex.title}</span>
            </button>
          );
        })}
      </div>
    </header>
  );
}
