import { create } from "zustand";
import type {
  Design, Diagram, EngineEvent, Evaluation, Issue, Material, Phase, Summary, TestResult, TestStatus,
} from "./types";

export type FeedItem =
  | { key: string; kind: "phase"; phase: Phase; label: string }
  | { key: string; kind: "thought"; text: string }
  | { key: string; kind: "thinking"; text: string }
  | { key: string; kind: "tool"; name: string; label: string }
  | { key: string; kind: "design"; added: string[]; changed: string[]; parts: number; version: number }
  | { key: string; kind: "diagram"; nodes: number }
  | { key: string; kind: "test"; id: string; title: string; part: string; testType: string; result?: TestResult }
  | { key: string; kind: "change"; change: string; reason: string; fix: boolean; mass: [number, number] }
  | { key: string; kind: "error"; text: string }
  | { key: string; kind: "done"; verified: boolean };

export type RunState = "idle" | "running" | "done" | "error";
export type ViewMode = "model" | "site";

/** Every design snapshot is a version the user can reopen. */
export interface Version {
  n: number;
  label: string;
  design: Design;
  summary: Summary | null;
  issues: Issue[];
  tests: Record<string, TestStatus>;
  edited?: boolean;
}

interface Store {
  run: RunState;
  runTitle: string;
  source: "demo" | "agent" | "mock" | "manual" | null;
  phase: Phase | null;
  phasesSeen: Phase[];
  feed: FeedItem[];
  design: Design | null;
  summary: Summary | null;
  issues: Issue[];
  added: string[];
  changed: string[];
  designVersion: number;
  diagram: Diagram | null;
  tests: Record<string, { title: string; part: string; type: string; result?: TestResult }>;
  testOrder: string[];
  answer: string | null;
  verified: boolean | null;
  evaluation: Evaluation | null;
  before: Evaluation | null;
  error: string | null;

  versions: Version[];
  viewing: number | null;   // version number being viewed read-only, null = latest
  stale: boolean;           // the design was edited since its tests ran
  pendingLabel: string;

  // view state
  materials: Record<string, Material>;
  selected: string | null;
  hovered: string | null;
  isolate: string[] | null;
  explode: number;
  xray: boolean;
  tint: boolean;
  view: ViewMode;
  tab: string;
  focusTest: string | null;
  frameNonce: number;
  status: { agent: boolean; mock?: boolean } | null;

  reset: (title: string, source: Store["source"], keep?: boolean) => void;
  apply: (e: EngineEvent) => void;
  set: (patch: Partial<Store>) => void;
  viewVersion: (n: number | null) => void;
  edit: (design: Design, label: string) => void;
}

let seq = 0;
const k = () => `f${++seq}`;

const short = (t: string, n = 56) => (t.length > n ? `${t.slice(0, n - 1).trimEnd()}…` : t);

export const useStore = create<Store>((set, get) => ({
  run: "idle",
  runTitle: "",
  source: null,
  phase: null,
  phasesSeen: [],
  feed: [],
  design: null,
  summary: null,
  issues: [],
  added: [],
  changed: [],
  designVersion: 0,
  diagram: null,
  tests: {},
  testOrder: [],
  answer: null,
  verified: null,
  evaluation: null,
  before: null,
  error: null,

  versions: [],
  viewing: null,
  stale: false,
  pendingLabel: "",

  materials: {},
  selected: null,
  hovered: null,
  isolate: null,
  explode: 0,
  xray: false,
  tint: true,
  view: "model",
  tab: "tests",
  focusTest: null,
  frameNonce: 0,
  status: null,

  set: (patch) => set(patch),

  reset: (title, source, keep = false) => {
    const s = get();
    const latest = s.versions[s.versions.length - 1];
    set({
      run: "running", runTitle: title, source, phase: null, phasesSeen: [], feed: [],
      added: [], changed: [], tests: {}, testOrder: [], answer: null, verified: null,
      evaluation: null, before: null, error: null, selected: null, hovered: null, isolate: null,
      explode: 0, view: "model", focusTest: null, viewing: null, stale: false,
      pendingLabel: keep ? short(title) : "",
      ...(keep && latest
        ? { design: latest.design, summary: latest.summary, issues: latest.issues }
        : { design: null, summary: null, issues: [], diagram: null, versions: [] }),
    });
  },

  viewVersion: (n) => {
    const s = get();
    const v = n === null ? s.versions[s.versions.length - 1] : s.versions.find((x) => x.n === n);
    if (!v) return;
    const latest = n === null || v.n === s.versions[s.versions.length - 1]?.n;
    set({
      viewing: latest ? null : v.n, design: v.design, summary: v.summary, issues: v.issues,
      diagram: v.design.diagram ?? s.diagram, added: [], changed: [],
      designVersion: s.designVersion + 1, selected: null, isolate: null, view: "model",
    });
  },

  edit: (design, label) => {
    const s = get();
    const n = (s.versions[s.versions.length - 1]?.n ?? 0) + 1;
    const prev = s.design;
    const changed = prev ? design.parts.filter((p) => JSON.stringify(p) !== JSON.stringify(prev.parts.find((q) => q.id === p.id))).map((p) => p.id) : [];
    set({
      design, viewing: null, stale: true, added: [], changed,
      designVersion: s.designVersion + 1, diagram: design.diagram ?? s.diagram,
      versions: [...s.versions, { n, label: short(label), design, summary: s.summary, issues: s.issues, tests: {}, edited: true }],
    });
  },

  apply: (e) => {
    const s = get();
    switch (e.type) {
      case "status": {
        const seen = s.phasesSeen.includes(e.phase) ? s.phasesSeen : [...s.phasesSeen, e.phase];
        set({ phase: e.phase, phasesSeen: seen, feed: [...s.feed, { key: k(), kind: "phase", phase: e.phase, label: e.label }] });
        break;
      }
      case "thought":
        set({ pendingLabel: short(e.text), feed: [...s.feed, { key: k(), kind: "thought", text: e.text }] });
        break;
      case "thinking":
        set({ feed: [...s.feed, { key: k(), kind: "thinking", text: e.text }] });
        break;
      case "tool":
        set({ feed: [...s.feed, { key: k(), kind: "tool", name: e.name, label: e.label }] });
        break;
      case "diagram":
        set({ diagram: e.diagram, feed: [...s.feed, { key: k(), kind: "diagram", nodes: e.diagram.nodes.length }] });
        break;
      case "design": {
        const added = e.added ?? [];
        const changed = e.changed ?? [];
        const n = (s.versions[s.versions.length - 1]?.n ?? 0) + 1;
        const label = s.pendingLabel || (n === 1 ? "First sketch" : "Updated design");
        const version: Version = { n, label, design: e.design, summary: e.summary, issues: e.issues ?? [], tests: {} };
        set({
          design: e.design, summary: e.summary, issues: e.issues ?? [], added, changed,
          designVersion: s.designVersion + 1, viewing: null, stale: false, pendingLabel: "",
          diagram: e.design.diagram ?? s.diagram,
          versions: [...s.versions, version],
          feed: added.length || changed.length || !s.design
            ? [...s.feed, { key: k(), kind: "design", added, changed, parts: e.design.parts.length, version: n }]
            : s.feed,
        });
        break;
      }
      case "test_started": {
        const prev = s.tests[e.id];
        const tests = { ...s.tests, [e.id]: { title: e.title, part: e.part, type: e.test_type, result: prev?.result } };
        const order = s.testOrder.includes(e.id) ? s.testOrder : [...s.testOrder, e.id];
        set({
          tests, testOrder: order,
          feed: [...s.feed, { key: k(), kind: "test", id: e.id, title: e.title, part: e.part, testType: e.test_type }],
        });
        break;
      }
      case "test_result": {
        const t = e.test;
        const tests = { ...s.tests, [t.id]: { title: t.title, part: t.part, type: t.type, result: t } };
        const order = s.testOrder.includes(t.id) ? s.testOrder : [...s.testOrder, t.id];
        // fill the latest pending row for this test, or add a row (final verify has no test_started)
        let at = -1;
        for (let i = s.feed.length - 1; i >= 0; i--) {
          const f = s.feed[i];
          if (f.kind === "test" && f.id === t.id) { if (!f.result) at = i; break; }
        }
        const feed = [...s.feed];
        if (at >= 0) feed[at] = { ...(feed[at] as Extract<FeedItem, { kind: "test" }>), title: t.title, result: t };
        else feed.push({ key: k(), kind: "test", id: t.id, title: t.title, part: t.part, testType: t.type, result: t });
        const versions = s.versions.length
          ? [...s.versions.slice(0, -1), { ...s.versions[s.versions.length - 1], tests: { ...s.versions[s.versions.length - 1].tests, [t.id]: t.status } }]
          : s.versions;
        set({ tests, testOrder: order, feed, versions, stale: false });
        break;
      }
      case "change":
        set({
          pendingLabel: short(e.change),
          feed: [...s.feed, { key: k(), kind: "change", change: e.change, reason: e.reason, fix: e.kind === "fix", mass: e.mass_g }],
        });
        break;
      case "done": {
        const tests = { ...s.tests };
        const order = [...s.testOrder];
        const statuses: Record<string, TestStatus> = {};
        for (const t of e.evaluation?.tests ?? []) {
          tests[t.id] = { title: t.title, part: t.part, type: t.type, result: t };
          statuses[t.id] = t.status;
          if (!order.includes(t.id)) order.push(t.id);
        }
        let versions = s.versions;
        const last = versions[versions.length - 1];
        if (e.design && (!last || JSON.stringify(last.design.parts) !== JSON.stringify(e.design.parts))) {
          versions = [...versions, { n: (last?.n ?? 0) + 1, label: s.pendingLabel || "Optimised", design: e.design, summary: e.summary, issues: [], tests: statuses }];
        } else if (last) {
          versions = [...versions.slice(0, -1), { ...last, tests: { ...last.tests, ...statuses }, summary: e.summary ?? last.summary }];
        }
        set({
          run: "done", phase: "done",
          phasesSeen: s.phasesSeen.includes("done") ? s.phasesSeen : [...s.phasesSeen, "done"],
          design: e.design ?? s.design, summary: e.summary ?? s.summary,
          diagram: e.design?.diagram ?? s.diagram,
          designVersion: e.design ? s.designVersion + 1 : s.designVersion,
          added: [], changed: [], viewing: null, stale: false, pendingLabel: "", versions,
          tests, testOrder: order, answer: e.answer, verified: e.verified,
          evaluation: e.evaluation, before: e.before ?? null,
          feed: [...s.feed, { key: k(), kind: "done", verified: e.verified }],
        });
        break;
      }
      case "error":
        set({ run: "error", error: e.message, feed: [...s.feed, { key: k(), kind: "error", text: e.message }] });
        break;
    }
  },
}));

/** Worst (highest) load over the latest test results, and which test sets it. */
export function overallLoad(tests: Store["tests"]): { load: number | null; limiting: string | null } {
  let load: number | null = null, limiting: string | null = null;
  for (const [id, t] of Object.entries(tests)) {
    const l = t.result?.load;
    if (l === null || l === undefined || !Number.isFinite(l)) continue;
    if (load === null || l > load) { load = l; limiting = id; }
  }
  return { load, limiting };
}
