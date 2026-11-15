// Types for docs/protocol.md (engine <-> interface contract).

export type Vec3 = [number, number, number];

export interface Part {
  id: string;
  name: string;
  shape: "box" | "cylinder" | "tube" | "sphere" | "heatsink" | "yagi" | "mesh";
  position: Vec3;
  rotation: Vec3;
  material: string;
  quantity?: number;
  process?: string;
  purpose?: string;
  group?: string;
  look?: string | null;
  color?: string;
  locked?: boolean;
  specs?: Record<string, string>;
  // box
  size?: Vec3;
  wall?: number | null;
  // cylinder / tube / sphere
  radius?: number;
  inner_radius?: number;
  length?: number;
  axis?: "x" | "y" | "z";
  // heatsink
  base?: Vec3;
  fin_height?: number;
  fin_thickness?: number;
  n_fins?: number;
  // yagi
  boom_length?: number;
  n_elements?: number;
  element_length?: number;
  boom_radius?: number;
  element_radius?: number;
  element_axis?: "x" | "y" | "z";
  // mesh
  mesh?: { vertices: number[]; faces: number[]; size: Vec3; volume: number };
}

export interface DiagramNode {
  id: string;
  label: string;
  kind?: "component" | "signal" | "software" | "environment" | "power";
  group?: string;
  part?: string;
}
export interface DiagramEdge {
  from: string;
  to: string;
  label?: string;
  kind?: "rf" | "data" | "power" | "mechanical" | "heat";
}
export interface Diagram {
  nodes: DiagramNode[];
  edges: DiagramEdge[];
}

export interface Design {
  name: string;
  description?: string;
  parts: Part[];
  assembly: { parts: string[]; action: string }[];
  scenarios: Record<string, unknown>[];
  diagram?: Diagram;
}

export interface SummaryRow {
  id: string;
  name: string;
  material: string;
  material_id: string;
  process: string;
  quantity: number;
  mass_g: number;
  cost: number;
  purpose: string;
  size_mm: Vec3;
}
export interface Summary {
  parts: SummaryRow[];
  mass_g: number;
  material_cost: number;
  size_mm: Vec3;
  centre_mm: Vec3;
}
export interface Issue {
  level: string;
  where: string;
  message: string;
}

export interface Metric {
  label: string;
  value: number;
  unit: string;
  limit: number;
  kind: "min" | "max";
  ok: boolean;
  load?: number | null;
}

export interface LineField {
  kind: "line";
  name: string;
  x: (number | null)[];
  series: Record<string, (number | null)[]>;
  xlabel: string;
  ylabel: string;
  unit: string;
}
export interface ImageField {
  kind: "image";
  name: string;
  x: (number | null)[];
  y: (number | null)[];
  values: (number | null)[][];
  xlabel: string;
  ylabel: string;
  unit: string;
}
export type SimField = LineField | ImageField;

export interface SimCheck {
  name: string;
  category: string;
  status: "pass" | "warn" | "fail" | "skip";
  detail: string;
}
export interface SimResult {
  name: string;
  title: string;
  results: Record<string, { value: number | null; unit: string }>;
  fields?: SimField[];
  verification: { verdict: string; ok: boolean; summary: string; checks: SimCheck[] };
  notes?: string[];
  params?: Record<string, unknown>;
  resolution?: number;
  elapsed_s?: number;
}

export interface Overlay {
  kind: "ground";
  field: number;
  threshold: number;
  unit: string;
  center_mm: [number, number];
  source_bearing_deg?: number;
  range_m?: number;
}

export type TestStatus = "pass" | "fail" | "unverified" | "error";
export interface TestResult {
  id: string;
  type: string;
  part: string;
  title: string;
  status: TestStatus;
  headline: string;
  metrics: Metric[];
  notes: string[];
  simulation: string | null;
  checks: { verdict: string; summary: string; verified: boolean } | null;
  sim?: SimResult;
  overlay?: Overlay;
  /** share of the limit used by the most loaded metric; null if untrusted */
  load?: number | null;
  /** shell command that re-runs this simulation on its own */
  reproduce?: string;
}

export interface Evaluation {
  tests: TestResult[];
  counts: Record<string, number>;
  all_pass: boolean;
  violation: number;
  load?: number | null;
  limiting?: string | null;
}

export type Phase = "understand" | "design" | "build" | "test" | "optimise" | "verify" | "done";

export type EngineEvent =
  | { type: "status"; phase: Phase; label: string }
  | { type: "thought"; text: string }
  | { type: "thinking"; text: string }
  | { type: "tool"; name: string; label: string }
  | { type: "diagram"; diagram: Diagram }
  | { type: "design"; design: Design; summary: Summary; issues: Issue[]; added?: string[]; changed?: string[] }
  | { type: "test_started"; id: string; title: string; part: string; test_type: string }
  | { type: "test_result"; test: TestResult }
  | { type: "change"; change: string; reason: string; kind: "fix" | "trim"; mass_g: [number, number]; tests: Record<string, string> }
  | { type: "done"; design: Design; summary: Summary; evaluation: Evaluation; answer: string; verified: boolean; before?: Evaluation }
  | { type: "error"; message: string };

export interface Example {
  id: string;
  title: string;
  tagline: string;
  prompt: string;
  icon?: string;
}

export interface Material {
  id: string;
  name: string;
  family: string;
  density: number;
  color: string;
  [k: string]: unknown;
}
