import { useMemo } from "react";
import * as THREE from "three";
import { Edges } from "@react-three/drei";
import type { Part, Vec3 } from "@/types";
import { mulberry, pcbTexture, solarTexture, type Finish } from "./looks";

/** All geometry is built in millimetres in the part's own z-up frame,
 *  centred on the bounding box (the parent group places and rotates it). */

const AX: Record<string, number> = { x: 0, y: 1, z: 2 };

export interface RenderOpts {
  finish: Finish;
  opacity: number;      // 1 = solid
  tint: string | null;  // emissive status colour
  tintAmount: number;
  highlight: boolean;
}

function Mat({ o, color, metalness, roughness, clearcoat, transmission, side, emissive, opacityScale = 1, map }: {
  o: RenderOpts; color?: string; metalness?: number; roughness?: number; clearcoat?: number;
  transmission?: number; side?: THREE.Side; emissive?: string; opacityScale?: number; map?: THREE.Texture;
}) {
  const f = o.finish;
  const op = Math.min(1, o.opacity * opacityScale);
  const em = o.tint ?? emissive ?? "#000000";
  const emI = o.tint ? o.tintAmount : emissive ? 0.6 : 0;
  return (
    <meshPhysicalMaterial
      color={color ?? f.color}
      metalness={metalness ?? f.metalness}
      roughness={roughness ?? f.roughness}
      clearcoat={clearcoat ?? f.clearcoat}
      clearcoatRoughness={0.25}
      transmission={transmission ?? 0}
      thickness={transmission ? 2 : 0}
      transparent={op < 1}
      opacity={op}
      depthWrite={op >= 0.99}
      side={side ?? THREE.FrontSide}
      emissive={em}
      emissiveIntensity={emI}
      map={map ?? null}
      envMapIntensity={1}
    />
  );
}

/** A rod between two points (mm). */
function Rod({ a, b, r, o, color, metalness, roughness }: { a: Vec3; b: Vec3; r: number; o: RenderOpts; color?: string; metalness?: number; roughness?: number }) {
  const { pos, quat, len } = useMemo(() => {
    const va = new THREE.Vector3(...a), vb = new THREE.Vector3(...b);
    const d = vb.clone().sub(va);
    const q = new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 1, 0), d.clone().normalize());
    return { pos: va.add(vb).multiplyScalar(0.5), quat: q, len: d.length() };
  }, [a, b]);
  return (
    <mesh position={pos} quaternion={quat} castShadow receiveShadow>
      <cylinderGeometry args={[r, r, len, 12]} />
      <Mat o={o} color={color} metalness={metalness} roughness={roughness} />
    </mesh>
  );
}

/** Rotation that turns three's default y-axis cylinder onto a part axis. */
function axisRot(axis: string): [number, number, number] {
  if (axis === "x") return [0, 0, -Math.PI / 2];
  if (axis === "z") return [Math.PI / 2, 0, 0];
  return [0, 0, 0];
}

function Box({ p, o }: { p: Part; o: RenderOpts }) {
  const s = p.size as Vec3;
  if (p.wall) {
    // hollow shell: translucent skin with crisp edges so the inside reads
    return (
      <group>
        <mesh castShadow={o.opacity > 0.9} receiveShadow>
          <boxGeometry args={s} />
          <Mat o={o} opacityScale={0.32} roughness={0.15} clearcoat={1} side={THREE.DoubleSide} />
        </mesh>
        <mesh scale={[(s[0] - 2 * p.wall) / s[0], (s[1] - 2 * p.wall) / s[1], (s[2] - 2 * p.wall) / s[2]]}>
          <boxGeometry args={s} />
          <Mat o={o} opacityScale={0.12} side={THREE.BackSide} />
        </mesh>
        <mesh>
          <boxGeometry args={s} />
          <meshBasicMaterial visible={false} />
          <Edges threshold={15} color={o.highlight ? "#7dd3fc" : "#9aa7b4"} lineWidth={1} transparent opacity={0.55 * o.opacity} />
        </mesh>
      </group>
    );
  }
  return (
    <mesh castShadow receiveShadow>
      <boxGeometry args={s} />
      <Mat o={o} />
    </mesh>
  );
}

function Tube({ p, o }: { p: Part; o: RenderOpts }) {
  const geo = useMemo(() => {
    const ro = p.radius!, ri = p.inner_radius!, h = p.length! / 2;
    const pts = [new THREE.Vector2(ri, -h), new THREE.Vector2(ro, -h), new THREE.Vector2(ro, h), new THREE.Vector2(ri, h), new THREE.Vector2(ri, -h)];
    const g = new THREE.LatheGeometry(pts, 48);
    g.computeVertexNormals();
    return g;
  }, [p.radius, p.inner_radius, p.length]);
  return (
    <mesh geometry={geo} rotation={axisRot(p.axis ?? "z")} castShadow receiveShadow>
      <Mat o={o} side={THREE.DoubleSide} />
    </mesh>
  );
}

function Heatsink({ p, o }: { p: Part; o: RenderOpts }) {
  const [w, d, t] = p.base!;
  const H = t + p.fin_height!;
  const n = p.n_fins!, ft = p.fin_thickness!;
  const xs = Array.from({ length: n }, (_, i) => (n === 1 ? 0 : -w / 2 + ft / 2 + (i * (w - ft)) / (n - 1)));
  return (
    <group>
      <mesh position={[0, 0, -H / 2 + t / 2]} castShadow receiveShadow>
        <boxGeometry args={[w, d, t]} />
        <Mat o={o} />
      </mesh>
      {xs.map((x, i) => (
        <mesh key={i} position={[x, 0, -H / 2 + t + p.fin_height! / 2]} castShadow receiveShadow>
          <boxGeometry args={[ft, d, p.fin_height!]} />
          <Mat o={o} />
        </mesh>
      ))}
    </group>
  );
}

function Yagi({ p, o }: { p: Part; o: RenderOpts }) {
  const ax = AX[p.axis ?? "x"], el = AX[p.element_axis ?? "z"];
  const L = p.boom_length!, n = Math.max(1, p.n_elements!), E = p.element_length!;
  const vec = (along: number, across: number): Vec3 => {
    const v: Vec3 = [0, 0, 0];
    v[ax] = along;
    v[el] = across;
    return v;
  };
  return (
    <group>
      <Rod a={vec(-L / 2, 0)} b={vec(L / 2, 0)} r={p.boom_radius ?? 6} o={o} />
      {Array.from({ length: n }, (_, i) => {
        const at = n === 1 ? 0 : -L / 2 + (i * L) / (n - 1);
        const len = E * (1 - (n === 1 ? 0 : (0.15 * i) / (n - 1)));
        return <Rod key={i} a={vec(at, -len / 2)} b={vec(at, len / 2)} r={p.element_radius ?? 3} o={o} />;
      })}
    </group>
  );
}

function MeshShape({ p, o }: { p: Part; o: RenderOpts }) {
  const geo = useMemo(() => {
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute(p.mesh!.vertices, 3));
    g.setIndex(p.mesh!.faces);
    g.computeVertexNormals();
    return g;
  }, [p.mesh]);
  return (
    <mesh geometry={geo} castShadow receiveShadow>
      <Mat o={o} side={THREE.DoubleSide} />
    </mesh>
  );
}

// ---------------- looks ----------------

function Pcb({ p, o }: { p: Part; o: RenderOpts }) {
  const [x, y, z] = p.size as Vec3;
  const board = Math.min(1.6, z * 0.3);
  const chips = useMemo(() => {
    const r = mulberry(p.id.length * 31 + Math.round(x));
    return Array.from({ length: 6 }, () => ({
      w: x * (0.12 + r() * 0.18), d: y * (0.12 + r() * 0.2), h: Math.min(z - board, 1.2 + r() * 3),
      cx: (r() - 0.5) * x * 0.6, cy: (r() - 0.5) * y * 0.6,
    }));
  }, [p.id, x, y, z, board]);
  const tex = pcbTexture();
  const zb = -z / 2 + board / 2;
  return (
    <group>
      <mesh position={[0, 0, zb]} castShadow receiveShadow>
        <boxGeometry args={[x, y, board]} />
        <Mat o={o} color="#ffffff" map={tex} metalness={0} roughness={0.55} clearcoat={0.6} />
      </mesh>
      {chips.map((c, i) => (
        <mesh key={i} position={[c.cx, c.cy, -z / 2 + board + c.h / 2]} castShadow>
          <boxGeometry args={[c.w, c.d, c.h]} />
          <Mat o={o} color="#15171a" metalness={0.1} roughness={0.5} />
        </mesh>
      ))}
      {/* connectors on one edge (SMA / USB) */}
      {[-0.25, 0.25].map((f, i) => (
        <mesh key={`c${i}`} position={[x / 2 - Math.min(6, x * 0.05), y * f, -z / 2 + board + Math.min(4, (z - board) / 2)]} rotation={[0, 0, Math.PI / 2]} castShadow>
          <cylinderGeometry args={[Math.min(3.2, y * 0.05), Math.min(3.2, y * 0.05), Math.min(12, x * 0.1), 16]} />
          <Mat o={o} color="#d9c27a" metalness={1} roughness={0.25} />
        </mesh>
      ))}
    </group>
  );
}

function Solar({ p, o }: { p: Part; o: RenderOpts }) {
  const [x, y, z] = p.size as Vec3;
  const cols = Math.max(2, Math.round(x / 160)), rows = Math.max(2, Math.round(y / 160));
  const frame = Math.min(25, Math.min(x, y) * 0.04);
  const tex = solarTexture(cols, rows);
  return (
    <group>
      <mesh castShadow receiveShadow>
        <boxGeometry args={[x, y, z]} />
        <Mat o={o} color="#b7bcc3" metalness={1} roughness={0.3} />
      </mesh>
      <mesh position={[0, 0, z / 2 + 0.3]}>
        <planeGeometry args={[x - 2 * frame, y - 2 * frame]} />
        <Mat o={o} color="#ffffff" map={tex} metalness={0.3} roughness={0.12} clearcoat={1} />
      </mesh>
    </group>
  );
}

function Battery({ p, o }: { p: Part; o: RenderOpts }) {
  const [x, y, z] = p.size as Vec3;
  const tr = Math.min(x, y) * 0.06;
  return (
    <group>
      <mesh castShadow receiveShadow>
        <boxGeometry args={[x, y, z * 0.94]} />
        <Mat o={o} color="#24282d" metalness={0.1} roughness={0.55} clearcoat={0.3} />
      </mesh>
      <mesh position={[0, -y / 2 - 0.2, z * 0.1]}>
        <planeGeometry args={[x * 0.7, z * 0.25]} />
        <Mat o={o} color="#38bdf8" metalness={0} roughness={0.6} emissive="#0ea5e9" />
      </mesh>
      {[[-x * 0.3, "#d33"], [x * 0.3, "#222"]].map(([cx, c], i) => (
        <mesh key={i} position={[cx as number, 0, z * 0.47 + tr * 0.5]} rotation={[Math.PI / 2, 0, 0]} castShadow>
          <cylinderGeometry args={[tr, tr, tr, 20]} />
          <Mat o={o} color={c as string} metalness={0.3} roughness={0.4} />
        </mesh>
      ))}
    </group>
  );
}

/** UHF 4-bay bowtie: four X elements in front (+x) of a wire-grid reflector. */
function Bowtie({ p, o }: { p: Part; o: RenderOpts }) {
  const [x, y, z] = p.size as Vec3;
  const r = Math.max(1.5, Math.min(x, y, z) * 0.012);
  const bays = 4;
  const bh = z / bays;
  const xr = -x / 2 + r, xe = x / 2 - r * 2;
  const grid = useMemo(() => {
    const pts: number[] = [];
    const ny = 7, nz = 16;
    for (let i = 0; i <= ny; i++) {
      const yy = -y / 2 + (i * y) / ny;
      pts.push(xr, yy, -z / 2, xr, yy, z / 2);
    }
    for (let j = 0; j <= nz; j++) {
      const zz = -z / 2 + (j * z) / nz;
      pts.push(xr, -y / 2, zz, xr, y / 2, zz);
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute(pts, 3));
    return g;
  }, [x, y, z, xr]);
  return (
    <group>
      <lineSegments geometry={grid}>
        <lineBasicMaterial color="#aeb6bf" transparent opacity={0.75 * o.opacity} />
      </lineSegments>
      {/* phasing harness: two vertical rods */}
      <Rod a={[xe, -y * 0.04, -z / 2 + bh / 2]} b={[xe, -y * 0.04, z / 2 - bh / 2]} r={r * 0.8} o={o} />
      <Rod a={[xe, y * 0.04, -z / 2 + bh / 2]} b={[xe, y * 0.04, z / 2 - bh / 2]} r={r * 0.8} o={o} />
      {Array.from({ length: bays }, (_, i) => {
        const zc = -z / 2 + bh * (i + 0.5);
        const wy = y * 0.46, wz = bh * 0.42;
        return (
          <group key={i}>
            {[[-1, -1], [-1, 1], [1, -1], [1, 1]].map(([sy, sz], k) => (
              <group key={k}>
                <Rod a={[xe, sy * y * 0.04, zc]} b={[xe, sy * wy, zc + sz * wz]} r={r} o={o} />
                <Rod a={[xe, sy * y * 0.04, zc]} b={[xe, sy * wy, zc + sz * wz * 0.35]} r={r} o={o} />
              </group>
            ))}
          </group>
        );
      })}
      {/* stand-off brackets to the reflector */}
      {[-1, 1].map((s) => (
        <Rod key={s} a={[xr, 0, s * z * 0.3]} b={[xe, 0, s * z * 0.3]} r={r * 1.2} o={o} color="#3a3f45" metalness={0.2} roughness={0.6} />
      ))}
    </group>
  );
}

function WireMesh({ p, o }: { p: Part; o: RenderOpts }) {
  const [x, y, z] = p.size as Vec3;
  // the thinnest axis is the panel normal
  const dims = [x, y, z];
  const nAx = dims.indexOf(Math.min(...dims));
  const [u, v] = [0, 1, 2].filter((a) => a !== nAx);
  const pitch = Math.max(12, Math.max(dims[u], dims[v]) / 60);
  const geo = useMemo(() => {
    const pts: number[] = [];
    const P = (a: number, b: number) => {
      const out = [0, 0, 0];
      out[u] = a;
      out[v] = b;
      return out;
    };
    const U = dims[u] / 2, V = dims[v] / 2;
    for (let a = -U; a <= U + 1e-6; a += pitch) pts.push(...P(a, -V), ...P(a, V));
    for (let b = -V; b <= V + 1e-6; b += pitch) pts.push(...P(-U, b), ...P(U, b));
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute(pts, 3));
    return g;
  }, [x, y, z]); // eslint-disable-line react-hooks/exhaustive-deps
  const fr = Math.max(4, Math.min(dims[u], dims[v]) * 0.012);
  const corner = (a: number, b: number): Vec3 => {
    const out: Vec3 = [0, 0, 0];
    out[u] = (a * dims[u]) / 2;
    out[v] = (b * dims[v]) / 2;
    return out;
  };
  return (
    <group>
      <lineSegments geometry={geo}>
        <lineBasicMaterial color={o.highlight ? "#7dd3fc" : "#9aa3ad"} transparent opacity={0.6 * o.opacity} />
      </lineSegments>
      <Rod a={corner(-1, -1)} b={corner(1, -1)} r={fr} o={o} />
      <Rod a={corner(1, -1)} b={corner(1, 1)} r={fr} o={o} />
      <Rod a={corner(1, 1)} b={corner(-1, 1)} r={fr} o={o} />
      <Rod a={corner(-1, 1)} b={corner(-1, -1)} r={fr} o={o} />
    </group>
  );
}

function Tripod({ p, o }: { p: Part; o: RenderOpts }) {
  const [x, y, z] = p.size as Vec3;
  const r = Math.max(6, Math.min(x, y) * 0.018);
  const top: Vec3 = [0, 0, z / 2 - r * 2];
  const feet: Vec3[] = [0, 1, 2].map((i) => {
    const a = (i * 2 * Math.PI) / 3 + Math.PI / 2;
    return [(Math.cos(a) * x) / 2 * 0.95, (Math.sin(a) * y) / 2 * 0.95, -z / 2 + r];
  });
  return (
    <group>
      <mesh position={top} castShadow>
        <cylinderGeometry args={[r * 3, r * 3.4, r * 4, 24]} />
        <Mat o={o} color="#2a2e33" metalness={0.4} roughness={0.5} />
      </mesh>
      {feet.map((f, i) => (
        <group key={i}>
          <Rod a={top} b={f} r={r} o={o} />
          <mesh position={f} castShadow>
            <sphereGeometry args={[r * 1.8, 16, 12]} />
            <Mat o={o} color="#1d1f22" metalness={0} roughness={0.9} />
          </mesh>
          <Rod a={[top[0], top[1], top[2] - z * 0.45]} b={[f[0] * 0.45, f[1] * 0.45, f[2] + z * 0.35]} r={r * 0.5} o={o} />
        </group>
      ))}
    </group>
  );
}

function Laptop({ p, o }: { p: Part; o: RenderOpts }) {
  const [x, y, z] = p.size as Vec3;
  const t = Math.min(z, 18);
  const open = z > 3 * t;
  return (
    <group>
      <mesh position={[0, 0, -z / 2 + t / 2]} castShadow receiveShadow>
        <boxGeometry args={[x, open ? y * 0.5 : y, t]} />
        <Mat o={o} color="#9aa0a6" metalness={1} roughness={0.35} />
      </mesh>
      {open && (
        <group position={[0, y * 0.25, -z / 2 + t]} rotation={[-0.25, 0, 0]}>
          <mesh position={[0, 0, (z - t) / 2]} castShadow>
            <boxGeometry args={[x, t * 0.4, z - t]} />
            <Mat o={o} color="#9aa0a6" metalness={1} roughness={0.35} />
          </mesh>
          <mesh position={[0, -t * 0.21, (z - t) / 2]}>
            <planeGeometry args={[x * 0.92, (z - t) * 0.9]} />
            <Mat o={o} color="#0b1220" emissive="#1e3a5f" metalness={0} roughness={0.2} />
          </mesh>
        </group>
      )}
    </group>
  );
}

export function PartShape({ p, o }: { p: Part; o: RenderOpts }) {
  if (p.shape === "box") {
    switch (p.look) {
      case "pcb": return <Pcb p={p} o={o} />;
      case "solar": return <Solar p={p} o={o} />;
      case "battery": return <Battery p={p} o={o} />;
      case "bowtie": return <Bowtie p={p} o={o} />;
      case "wire_mesh": return <WireMesh p={p} o={o} />;
      case "tripod": return <Tripod p={p} o={o} />;
      case "laptop": return <Laptop p={p} o={o} />;
      case "glass": return (
        <mesh castShadow receiveShadow><boxGeometry args={p.size} /><Mat o={o} color="#dbeafe" transmission={0.9} roughness={0.05} metalness={0} /></mesh>
      );
      case "screen": return (
        <mesh castShadow receiveShadow><boxGeometry args={p.size} /><Mat o={o} color="#0b1220" emissive="#1d4ed8" roughness={0.2} metalness={0} /></mesh>
      );
      case "rubber": return (
        <mesh castShadow receiveShadow><boxGeometry args={p.size} /><Mat o={o} color="#1c1d1f" roughness={0.95} metalness={0} clearcoat={0} /></mesh>
      );
      default: return <Box p={p} o={o} />;
    }
  }
  if (p.shape === "cylinder") {
    const rubber = p.look === "rubber" || p.look === "cable";
    return (
      <mesh rotation={axisRot(p.axis ?? "z")} castShadow receiveShadow>
        <cylinderGeometry args={[p.radius, p.radius, p.length, 40]} />
        <Mat o={o} color={rubber ? "#1c1d1f" : undefined} roughness={rubber ? 0.9 : undefined} metalness={rubber ? 0 : undefined} />
      </mesh>
    );
  }
  if (p.shape === "tube") return <Tube p={p} o={o} />;
  if (p.shape === "sphere") return (
    <mesh castShadow receiveShadow><sphereGeometry args={[p.radius, 48, 32]} /><Mat o={o} /></mesh>
  );
  if (p.shape === "heatsink") return <Heatsink p={p} o={o} />;
  if (p.shape === "yagi") return <Yagi p={p} o={o} />;
  if (p.shape === "mesh" && p.mesh) return <MeshShape p={p} o={o} />;
  return null;
}

/** Unrotated bounding-box size in mm (mirrors create_anything.design.extents). */
export function extents(p: Part): Vec3 {
  switch (p.shape) {
    case "box": return p.size as Vec3;
    case "cylinder":
    case "tube": {
      const e: Vec3 = [2 * p.radius!, 2 * p.radius!, 2 * p.radius!];
      e[AX[p.axis ?? "z"]] = p.length!;
      return e;
    }
    case "sphere": return [2 * p.radius!, 2 * p.radius!, 2 * p.radius!];
    case "heatsink": return [p.base![0], p.base![1], p.base![2] + p.fin_height!];
    case "yagi": {
      const br = 2 * (p.boom_radius ?? 6);
      const e: Vec3 = [br, br, br];
      e[AX[p.axis ?? "x"]] = p.boom_length!;
      e[AX[p.element_axis ?? "z"]] = Math.max(p.element_length!, br);
      return e;
    }
    case "mesh": return p.mesh!.size;
  }
  return [10, 10, 10];
}
