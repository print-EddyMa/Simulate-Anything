import { memo, useEffect, useMemo, useRef } from "react";
import { Background, Handle, MarkerType, Position, ReactFlow, ReactFlowProvider, useReactFlow, type Edge, type Node, type NodeProps } from "@xyflow/react";
import "@xyflow/react/dist/base.css";
import dagre from "@dagrejs/dagre";
import { motion } from "motion/react";
import { Antenna, Cloud, Cpu, Radio, Signal, Zap, type LucideIcon } from "lucide-react";
import { useStore } from "@/store";
import type { Diagram, DiagramNode } from "@/types";
import { cn } from "@/lib/utils";
import { useIsDarkDom } from "./useIsDark";

const W = 172, H = 46;
const KIND_ICON: Record<string, LucideIcon> = { component: Radio, signal: Signal, software: Cpu, environment: Cloud, power: Zap };
const EDGE_VAR: Record<string, string> = { rf: "var(--rf)", data: "var(--data)", power: "var(--power)", mechanical: "var(--mech)", heat: "var(--heat)" };

type NData = { node: DiagramNode; fresh: boolean; selected: boolean };

const BlockNode = memo(function BlockNode({ data }: NodeProps<Node<NData>>) {
  const { node, fresh, selected } = data;
  const antenna = (node.kind ?? "component") === "component" && /antenna|dipole|yagi|bowtie|surveillance panel/i.test(node.label);
  const Icon = antenna ? Antenna : (KIND_ICON[node.kind ?? "component"] ?? Radio);
  const env = node.kind === "environment";
  return (
    <motion.div
      initial={fresh ? { opacity: 0, scale: 0.85, y: 6 } : false}
      animate={{ opacity: 1, scale: 1, y: 0 }}
      transition={{ duration: 0.45, ease: "easeOut" }}
      className={cn(
        "flex h-[46px] w-[172px] items-center gap-2.5 rounded-lg border bg-card px-3 text-left shadow-sm",
        env && "border-dashed bg-transparent",
        node.part && "cursor-pointer hover:border-ring",
        selected && "border-ring ring-2 ring-ring/40",
      )}
    >
      <Handle type="target" position={Position.Left} className="!size-1.5 !border-0 !bg-muted-foreground/40" />
      <Icon className={cn("size-4 shrink-0", env ? "text-muted-foreground" : "text-primary")} />
      <div className="min-w-0">
        <div className="truncate text-xs font-medium leading-tight">{node.label}</div>
        {node.group && <div className="truncate text-[10px] text-muted-foreground">{node.group}</div>}
      </div>
      <Handle type="source" position={Position.Right} className="!size-1.5 !border-0 !bg-muted-foreground/40" />
    </motion.div>
  );
});

const nodeTypes = { block: BlockNode };

function layout(d: Diagram) {
  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: "LR", nodesep: 22, ranksep: 64, marginx: 16, marginy: 16 });
  g.setDefaultEdgeLabel(() => ({}));
  d.nodes.forEach((n) => g.setNode(n.id, { width: W, height: H }));
  d.edges.forEach((e) => { if (g.hasNode(e.from) && g.hasNode(e.to)) g.setEdge(e.from, e.to); });
  dagre.layout(g);
  const pos: Record<string, { x: number; y: number }> = {};
  d.nodes.forEach((n) => {
    const p = g.node(n.id);
    pos[n.id] = { x: p.x - W / 2, y: p.y - H / 2 };
  });
  return pos;
}

function Flow() {
  const diagram = useStore((s) => s.diagram);
  const selected = useStore((s) => s.selected);
  const set = useStore((s) => s.set);
  const dark = useIsDarkDom();
  const seen = useRef(new Set<string>());
  const { fitView } = useReactFlow();

  const { nodes, edges } = useMemo(() => {
    if (!diagram) return { nodes: [], edges: [] };
    const pos = layout(diagram);
    const nodes: Node<NData>[] = diagram.nodes.map((n) => ({
      id: n.id, type: "block", position: pos[n.id], draggable: true,
      data: { node: n, fresh: !seen.current.has(n.id), selected: !!n.part && n.part === selected },
    }));
    const edges: Edge[] = diagram.edges.map((e, i) => {
      const color = EDGE_VAR[e.kind ?? "data"] ?? "var(--mech)";
      return {
        id: `${e.from}-${e.to}-${i}`, source: e.from, target: e.to, label: e.label,
        animated: e.kind === "rf" || e.kind === "data",
        style: { stroke: color, strokeWidth: 1.6, strokeDasharray: e.kind === "mechanical" ? "4 4" : undefined },
        markerEnd: { type: MarkerType.ArrowClosed, color, width: 14, height: 14 },
        labelStyle: { fontSize: 10, fill: "var(--muted-foreground)" },
        labelBgStyle: { fill: "var(--background)" },
        labelBgPadding: [4, 2] as [number, number],
      };
    });
    return { nodes, edges };
  }, [diagram, selected]);

  useEffect(() => {
    diagram?.nodes.forEach((n) => seen.current.add(n.id));
    const t = setTimeout(() => fitView({ padding: 0.12, duration: 500 }), 60);
    return () => clearTimeout(t);
  }, [diagram, fitView]);

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      colorMode={dark ? "dark" : "light"}
      fitView
      minZoom={0.3}
      maxZoom={1.8}
      proOptions={{ hideAttribution: true }}
      nodesConnectable={false}
      onNodeClick={(_, n) => { const part = (n.data as NData).node.part; if (part) set({ selected: part, view: "model" }); }}
    >
      <Background gap={18} size={1} color={dark ? "#2a2f37" : "#d9dde3"} />
    </ReactFlow>
  );
}

const LEGEND: [NonNullable<Diagram["edges"][number]["kind"]>, string][] = [["rf", "RF"], ["data", "data"], ["power", "power"], ["mechanical", "mechanical"], ["heat", "heat"]];

export default function SystemPanel() {
  const diagram = useStore((s) => s.diagram);
  if (!diagram) return <p className="py-10 text-center text-sm text-muted-foreground">The system diagram draws itself here while the design is worked out.</p>;
  const kinds = new Set(diagram.edges.map((e) => e.kind ?? "data"));
  return (
    <div>
      <div className="mb-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-muted-foreground">
        {LEGEND.filter(([k]) => kinds.has(k)).map(([k, l]) => (
          <span key={k} className="flex items-center gap-1.5"><i className="inline-block h-0.5 w-4 rounded" style={{ background: EDGE_VAR[k] }} />{l}</span>
        ))}
      </div>
      <div className="h-[420px] overflow-hidden rounded-xl border bg-card/40">
        <ReactFlowProvider><Flow /></ReactFlowProvider>
      </div>
    </div>
  );
}
