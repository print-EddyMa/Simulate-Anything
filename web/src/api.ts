import type { Design, EngineEvent, Evaluation, Example, Issue, Material, Summary } from "./types";
import { useStore } from "./store";

export interface Status {
  agent: boolean;
  version: string;
  model?: string;
  mock?: boolean;
}

const params = new URLSearchParams(location.search);
const MOCK = params.has("mock");
/** Pacing of recorded runs: 1 = watchable, 0 = as fast as the engine runs (?pace=0). */
const PACE = Number(params.get("pace") ?? 1);

export async function getStatus(): Promise<Status> {
  if (MOCK) return { agent: false, version: "mock", mock: true };
  try {
    const r = await fetch("api/status");
    if (!r.ok) throw new Error(String(r.status));
    return (await r.json()) as Status;
  } catch {
    return { agent: false, version: "mock", mock: true };
  }
}

export const MOCK_EXAMPLES: Example[] = [
  {
    id: "drone_radar", title: "Passive radar node", icon: "radar",
    tagline: "",
    prompt: "Design a passive radar that detects small drones around a US military base. Receive-only, using the TV broadcast signals already in the air. It has to run off-grid for days, survive storms and summer heat, and spot a small quadcopter 1.5 km out.",
  },

];

export async function getExamples(status: Status): Promise<Example[]> {
  if (status.mock) return MOCK_EXAMPLES;
  try {
    const r = await fetch("api/examples");
    if (!r.ok) throw new Error();
    return (await r.json()) as Example[];
  } catch {
    return MOCK_EXAMPLES;
  }
}

export async function getMaterials(status: Status): Promise<Record<string, Material>> {
  let list: Material[] = [];
  if (!status.mock) {
    try {
      const r = await fetch("api/materials");
      if (r.ok) list = (await r.json()) as Material[];
    } catch { /* fall through to the built-in list */ }
  }
  if (!list.length) list = (await import("./mock/materials.json")).default as Material[];
  return Object.fromEntries(list.map((m) => [m.id, m]));
}

/** POST and read a text/event-stream body, calling onEvent for each JSON event. */
export async function stream(url: string, body: unknown, onEvent: (e: EngineEvent) => void, signal?: AbortSignal) {
  const r = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(body ?? {}),
    signal,
  });
  if (!r.ok || !r.body) {
    let msg = `${r.status} ${r.statusText}`;
    try { msg = ((await r.json()) as { error?: string }).error ?? msg; } catch { /* keep status text */ }
    throw new Error(msg);
  }
  const reader = r.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let cut: number;
    while ((cut = buf.indexOf("\n\n")) >= 0) {
      const chunk = buf.slice(0, cut);
      buf = buf.slice(cut + 2);
      const data = chunk.split("\n").filter((l) => l.startsWith("data:")).map((l) => l.slice(5).trimStart()).join("\n");
      if (data) onEvent(JSON.parse(data) as EngineEvent);
    }
  }
}

const wait = (ms: number, signal?: AbortSignal) =>
  new Promise<void>((res, rej) => {
    const t = setTimeout(res, ms);
    signal?.addEventListener("abort", () => { clearTimeout(t); rej(new DOMException("aborted", "AbortError")); });
  });

const DELAY: Record<string, number> = {
  status: 500, thought: 800, thinking: 600, tool: 400, diagram: 600, design: 650,
  test_started: 400, test_result: 800, change: 600, done: 500, error: 0,
};

async function replayMock(onEvent: (e: EngineEvent) => void, signal?: AbortSignal) {
  const events = (await import("./mock/drone_radar.json")).default as EngineEvent[];
  for (const e of events) {
    await wait(DELAY[e.type] ?? 500, signal);
    onEvent(e);
  }
}

let current: AbortController | null = null;

/** A suggested prompt: the real agent when a key is set, otherwise the recorded run
 *  (recorded design steps, every simulation re-run live). */
export async function runSuggestion(example: Example, status: Status) {
  if (status.agent && !status.mock) return runCreate(example.prompt, {}, status, false);
  current?.abort();
  const ctrl = new AbortController();
  current = ctrl;
  const { reset, apply } = useStore.getState();
  reset(example.prompt, status.mock ? "mock" : "demo", false, example.prompt);
  try {
    if (status.mock) await replayMock(apply, ctrl.signal);
    else await stream(`api/demo/${encodeURIComponent(example.id)}`, { pace: PACE }, apply, ctrl.signal);
  } catch (err) {
    if ((err as Error).name !== "AbortError") apply({ type: "error", message: String((err as Error).message ?? err) });
  }
}

export async function runCreate(prompt: string, attach: { image?: string; mesh?: unknown }, status: Status, change = false) {
  current?.abort();
  const ctrl = new AbortController();
  current = ctrl;
  const s = useStore.getState();
  const base = change ? currentDesign() : null;
  s.reset(prompt, "agent", !!base, prompt);
  const { apply } = useStore.getState();
  try {
    if (status.mock) await replayMock(apply, ctrl.signal);
    else await stream("api/create", { prompt, ...attach, ...(base ? { design: base } : {}) }, apply, ctrl.signal);
  } catch (err) {
    if ((err as Error).name !== "AbortError") apply({ type: "error", message: String((err as Error).message ?? err) });
  }
}

/** The design to act on. Acting on an older version reopens it as the newest. */
function currentDesign() {
  const s = useStore.getState();
  if (!s.design) return null;
  if (s.viewing !== null) s.edit(s.design, `Reopened v${s.viewing}`);
  return useStore.getState().design;
}

export async function runEvaluate() {
  const design = currentDesign();
  if (!design) return;
  current?.abort();
  const ctrl = new AbortController();
  current = ctrl;
  const { reset } = useStore.getState();
  reset("Running every test", "manual", true);
  const { apply, set } = useStore.getState();
  apply({ type: "status", phase: "test", label: "Running every test" });
  try {
    const r = await fetch("api/evaluate", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ design }), signal: ctrl.signal,
    });
    const out = (await r.json()) as { error?: string; design: Design; summary: Summary; issues: Issue[]; evaluation: Evaluation };
    if (!r.ok) throw new Error(out.error ?? `${r.status}`);
    for (const t of out.evaluation.tests) apply({ type: "test_result", test: t });
    set({ issues: out.issues ?? [] });
    const c = out.evaluation.counts;
    const n = out.evaluation.tests.length;
    apply({
      type: "done", design: out.design, summary: out.summary, evaluation: out.evaluation,
      verified: out.evaluation.all_pass,
      answer: `**${c.pass ?? 0} of ${n} tests pass**${out.evaluation.all_pass ? " on verified simulations." : "."}`,
    });
  } catch (err) {
    if ((err as Error).name !== "AbortError") apply({ type: "error", message: String((err as Error).message ?? err) });
  }
}

export async function runOptimise(status: Status) {
  const design = currentDesign();
  if (!design || status.mock) return;
  current?.abort();
  const ctrl = new AbortController();
  current = ctrl;
  useStore.getState().reset("Optimising", "manual", true);
  const { apply } = useStore.getState();
  try {
    await stream("api/optimise", { design }, apply, ctrl.signal);
  } catch (err) {
    if ((err as Error).name !== "AbortError") apply({ type: "error", message: String((err as Error).message ?? err) });
  }
}

export function stopRun() {
  current?.abort();
  current = null;
  const s = useStore.getState();
  if (s.run === "running") useStore.setState({ run: s.design ? "done" : "idle" });
}

export async function importStl(file: File): Promise<unknown> {
  const buf = new Uint8Array(await file.arrayBuffer());
  let bin = "";
  for (let i = 0; i < buf.length; i += 0x8000) bin += String.fromCharCode(...buf.subarray(i, i + 0x8000));
  const r = await fetch("api/import", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: file.name, data: btoa(bin) }),
  });
  if (!r.ok) throw new Error(`import failed (${r.status})`);
  return ((await r.json()) as { part: unknown }).part;
}

export function fileToDataUrl(file: File): Promise<string> {
  return new Promise((res, rej) => {
    const fr = new FileReader();
    fr.onload = () => res(String(fr.result));
    fr.onerror = () => rej(fr.error);
    fr.readAsDataURL(file);
  });
}

export function download(name: string, data: unknown) {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}
