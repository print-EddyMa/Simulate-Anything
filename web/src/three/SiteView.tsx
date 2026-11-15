import { useMemo, useRef } from "react";
import * as THREE from "three";
import { useFrame } from "@react-three/fiber";
import { Grid, Html, Line } from "@react-three/drei";
import type { ImageField, TestResult } from "@/types";
import { snrColor } from "./looks";

/** Bilinear sampler over an image field (x[i], y[j], values[i][j]). */
export function makeSampler(f: ImageField) {
  const xs = f.x as number[], ys = f.y as number[];
  const nx = xs.length, ny = ys.length;
  const x0 = xs[0], x1 = xs[nx - 1], y0 = ys[0], y1 = ys[ny - 1];
  return (x: number, y: number) => {
    if (x < x0 || x > x1 || y < y0 || y > y1) return -Infinity;
    const fx = ((x - x0) / (x1 - x0)) * (nx - 1), fy = ((y - y0) / (y1 - y0)) * (ny - 1);
    const i = Math.min(nx - 2, Math.floor(fx)), j = Math.min(ny - 2, Math.floor(fy));
    const tx = fx - i, ty = fy - j;
    const v = (a: number, b: number) => (f.values[a][b] ?? -Infinity) as number;
    return (1 - tx) * (1 - ty) * v(i, j) + tx * (1 - ty) * v(i + 1, j) + (1 - tx) * ty * v(i, j + 1) + tx * ty * v(i + 1, j + 1);
  };
}

export function fieldRange(f: ImageField) {
  let lo = Infinity, hi = -Infinity;
  for (const col of f.values) for (const v of col) if (v !== null && Number.isFinite(v)) { lo = Math.min(lo, v); hi = Math.max(hi, v); }
  return [lo, hi];
}

function coverageTexture(f: ImageField, thr: number) {
  const N = 640;
  const sample = makeSampler(f);
  const xs = f.x as number[], ys = f.y as number[];
  const [lo, hi] = fieldRange(f);
  const c = document.createElement("canvas");
  c.width = N;
  c.height = N;
  const g = c.getContext("2d")!;
  const img = g.createImageData(N, N);
  const vals = new Float32Array(N * N);
  for (let r = 0; r < N; r++) {
    const y = ys[ys.length - 1] - ((r + 0.5) / N) * (ys[ys.length - 1] - ys[0]); // top row = north
    for (let q = 0; q < N; q++) {
      const x = xs[0] + ((q + 0.5) / N) * (xs[xs.length - 1] - xs[0]);
      vals[r * N + q] = sample(x, y);
    }
  }
  for (let r = 0; r < N; r++) {
    for (let q = 0; q < N; q++) {
      const v = vals[r * N + q];
      let [cr, cg, cb, ca] = snrColor(v, thr, Math.max(lo, thr - 30), Math.min(hi, thr + 30));
      const above = v >= thr;
      const edge = (q < N - 1 && (vals[r * N + q + 1] >= thr) !== above) || (r < N - 1 && (vals[(r + 1) * N + q] >= thr) !== above);
      if (edge) [cr, cg, cb, ca] = [235, 252, 255, 255];
      // fade the square's corners into the ground
      const dx = (q / N - 0.5) * 2, dy = (r / N - 0.5) * 2;
      const fall = Math.max(0, Math.min(1, (1 - Math.hypot(dx, dy)) * 3));
      const o = (r * N + q) * 4;
      img.data[o] = cr; img.data[o + 1] = cg; img.data[o + 2] = cb; img.data[o + 3] = ca * fall;
    }
  }
  g.putImageData(img, 0, 0);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  t.anisotropy = 8;
  return t;
}

interface DronePath { r: number; cx: number; cy: number; w: number; phase: number; alt: number; ex: number }

function Drone({ path, sample, thr, scale }: { path: DronePath; sample: (x: number, y: number) => number; thr: number; scale: number }) {
  const g = useRef<THREE.Group>(null!);
  const body = useRef<THREE.MeshStandardMaterial>(null!);
  const echo = useRef<THREE.Mesh>(null!);
  const trail = useRef<THREE.BufferGeometry>(null!);
  const pts = useRef<number[]>([]);
  const rotors = useRef<THREE.Group>(null!);
  const ping = useRef<THREE.Mesh>(null!);
  useFrame(({ clock }) => {
    const t = clock.elapsedTime * path.w + path.phase;
    const x = path.cx + path.r * Math.cos(t);
    const y = path.cy + path.r * path.ex * Math.sin(t);
    const seen = sample(x, y) >= thr;
    g.current.position.set(x, path.alt + Math.sin(t * 3) * 4, -y);
    g.current.rotation.y = -t;
    rotors.current.rotation.y += 0.8;
    body.current.color.set(seen ? "#7dd3fc" : "#7a828c");
    body.current.emissive.set(seen ? "#38bdf8" : "#000000");
    body.current.emissiveIntensity = seen ? 2.2 : 0;
    // echo line back to the node
    const m = echo.current;
    m.visible = seen;
    if (seen) {
      const from = new THREE.Vector3(0, 20, 0), to = g.current.position;
      const d = to.clone().sub(from);
      m.position.copy(from).add(d.clone().multiplyScalar(0.5));
      m.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), d.clone().normalize());
      m.scale.set(1, d.length(), 1);
      (m.material as THREE.MeshBasicMaterial).opacity = 0.25 + 0.2 * Math.sin(clock.elapsedTime * 6);
    }
    const p = ping.current;
    p.visible = seen;
    const k = (clock.elapsedTime * 1.2 + path.phase) % 1;
    p.scale.setScalar(1 + k * 5);
    (p.material as THREE.MeshBasicMaterial).opacity = 0.7 * (1 - k);
    p.position.set(x, 5, -y);
    // trail
    const arr = pts.current;
    arr.push(x, path.alt, -y);
    if (arr.length > 3 * 90) arr.splice(0, 3);
    trail.current.setAttribute("position", new THREE.Float32BufferAttribute(arr, 3));
    trail.current.computeBoundingSphere();
  });
  const s = scale;
  return (
    <>
      <group ref={g}>
        <mesh>
          <boxGeometry args={[s * 1.2, s * 0.35, s * 1.2]} />
          <meshStandardMaterial ref={body} color="#7a828c" metalness={0.3} roughness={0.4} toneMapped={false} />
        </mesh>
        <group ref={rotors}>
          {[[1, 1], [1, -1], [-1, 1], [-1, -1]].map(([a, b], i) => (
            <group key={i} position={[a * s * 0.95, s * 0.2, b * s * 0.95]}>
              <mesh rotation={[-Math.PI / 2, 0, 0]}>
                <ringGeometry args={[s * 0.15, s * 0.6, 24]} />
                <meshBasicMaterial color="#cbd5e1" transparent opacity={0.35} side={THREE.DoubleSide} />
              </mesh>
            </group>
          ))}
        </group>
      </group>
      <mesh ref={echo} visible={false}>
        <cylinderGeometry args={[1.6, 1.6, 1, 6, 1, true]} />
        <meshBasicMaterial color="#7dd3fc" transparent opacity={0.3} toneMapped={false} />
      </mesh>
      <mesh ref={ping} rotation={[-Math.PI / 2, 0, 0]} visible={false}>
        <ringGeometry args={[s * 1.4, s * 1.7, 48]} />
        <meshBasicMaterial color="#7dd3fc" transparent opacity={0.5} toneMapped={false} />
      </mesh>
      <line>
        <bufferGeometry ref={trail} />
        <lineBasicMaterial color="#94a3b8" transparent opacity={0.35} />
      </line>
    </>
  );
}

function Wavefronts({ bearing, extent }: { bearing: number; extent: number }) {
  const g = useRef<THREE.Group>(null!);
  const n = 7;
  useFrame(({ clock }) => {
    g.current.children.forEach((c, i) => {
      const k = ((clock.elapsedTime * 0.08 + i / n) % 1);
      // travel from the transmitter side towards the far side
      const d = extent * (1 - 2 * k);
      c.position.set(Math.cos(bearing) * d, 8, -Math.sin(bearing) * d);
      const m = (c as THREE.Mesh).material as THREE.MeshBasicMaterial;
      m.opacity = 0.16 * Math.sin(Math.PI * k);
    });
  });
  return (
    <group ref={g}>
      {Array.from({ length: n }, (_, i) => (
        <mesh key={i} rotation={[-Math.PI / 2, 0, bearing + Math.PI / 2]}>
          <planeGeometry args={[extent * 2, 6]} />
          <meshBasicMaterial color="#fbbf24" transparent opacity={0.12} toneMapped={false} depthWrite={false} />
        </mesh>
      ))}
    </group>
  );
}

function Beacon({ reach }: { reach: number }) {
  const ring = useRef<THREE.Mesh>(null!);
  const ring2 = useRef<THREE.Mesh>(null!);
  useFrame(({ clock }) => {
    for (const [r, off] of [[ring, 0], [ring2, 0.5]] as const) {
      const k = (clock.elapsedTime * 0.35 + off) % 1;
      r.current.scale.setScalar(20 + k * reach);
      ((r.current.material as THREE.MeshBasicMaterial)).opacity = 0.5 * (1 - k) ** 2;
    }
  });
  return (
    <group>
      <mesh position={[0, 30, 0]}>
        <cylinderGeometry args={[3, 6, 60, 16]} />
        <meshStandardMaterial color="#e0f2fe" emissive="#38bdf8" emissiveIntensity={3} toneMapped={false} />
      </mesh>
      <mesh position={[0, 66, 0]}>
        <sphereGeometry args={[7, 24, 16]} />
        <meshStandardMaterial color="#ffffff" emissive="#7dd3fc" emissiveIntensity={4} toneMapped={false} />
      </mesh>
      {[ring, ring2].map((r, i) => (
        <mesh key={i} ref={r} position={[0, 5, 0]} rotation={[-Math.PI / 2, 0, 0]}>
          <ringGeometry args={[0.97, 1, 96]} />
          <meshBasicMaterial color="#7dd3fc" transparent opacity={0.4} toneMapped={false} depthWrite={false} />
        </mesh>
      ))}
      <Html position={[0, 90, 0]} center zIndexRange={[10, 0]} style={{ pointerEvents: "none" }}>
        <div className="whitespace-nowrap rounded-md border border-sky-300/30 bg-black/60 px-2 py-0.5 text-[11px] font-medium text-sky-100 backdrop-blur">Radar node</div>
      </Html>
    </group>
  );
}

export function SiteView({ test }: { test: TestResult }) {
  const ov = test.overlay!;
  const field = test.sim?.fields?.[ov.field] as ImageField | undefined;
  const tex = useMemo(() => (field ? coverageTexture(field, ov.threshold) : null), [field, ov.threshold]);
  const sample = useMemo(() => (field ? makeSampler(field) : () => -Infinity), [field]);
  if (!field || !tex) return null;
  const xs = field.x as number[], ys = field.y as number[];
  const cx = ov.center_mm[0] / 1000, cy = ov.center_mm[1] / 1000;
  const w = xs[xs.length - 1] - xs[0], h = ys[ys.length - 1] - ys[0];
  const mx = (xs[0] + xs[xs.length - 1]) / 2, my = (ys[0] + ys[ys.length - 1]) / 2;
  const extent = Math.max(w, h) / 2;
  const bearing = THREE.MathUtils.degToRad(ov.source_bearing_deg ?? 180);
  const range = ov.range_m;
  const ring = range ? Array.from({ length: 129 }, (_, i) => {
    const a = (i / 128) * Math.PI * 2;
    return [Math.cos(a) * range, 6, -Math.sin(a) * range] as [number, number, number];
  }) : null;
  const bx = Math.cos(bearing), bz = -Math.sin(bearing);
  const paths: DronePath[] = [
    { r: extent * 0.55, cx: extent * 0.15, cy: 0, w: 0.09, phase: 0, alt: 110, ex: 0.7 },
    { r: extent * 0.32, cx: extent * 0.35, cy: extent * 0.12, w: -0.14, phase: 1.3, alt: 90, ex: 1 },
    { r: extent * 0.75, cx: 0, cy: -extent * 0.1, w: 0.06, phase: 2.6, alt: 140, ex: 0.5 },
    { r: extent * 0.22, cx: -extent * 0.4, cy: extent * 0.3, w: 0.18, phase: 4.1, alt: 70, ex: 1 },
    { r: extent * 0.45, cx: extent * 0.2, cy: -extent * 0.35, w: -0.1, phase: 5.2, alt: 120, ex: 0.6 },
  ];
  return (
    <group position={[cx, 0, -cy]}>
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, -4, 0]}>
        <circleGeometry args={[extent * 3, 96]} />
        <meshBasicMaterial color="#0a0e13" />
      </mesh>
      <Grid
        position={[0, 0, 0]}
        args={[extent * 6, extent * 6]}
        cellSize={100}
        sectionSize={500}
        cellThickness={0.5}
        sectionThickness={0.9}
        cellColor="#18202a"
        sectionColor="#273242"
        fadeDistance={extent * 4}
        fadeStrength={2}
        infiniteGrid
      />
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[mx, 3, -my]} renderOrder={1}>
        <planeGeometry args={[w, h]} />
        <meshBasicMaterial map={tex} transparent toneMapped={false} depthWrite={false} polygonOffset polygonOffsetFactor={-2} />
      </mesh>
      {ring && (
        <>
          <Line points={ring} color="#fde68a" lineWidth={1.5} dashed dashSize={40} gapSize={30} transparent opacity={0.9} />
          <Html position={[range! * Math.cos(Math.PI / 4), 4, -range! * Math.sin(Math.PI / 4)]} center zIndexRange={[10, 0]} style={{ pointerEvents: "none" }}>
            <div className="whitespace-nowrap rounded-md border border-amber-200/30 bg-black/60 px-2 py-0.5 text-[11px] text-amber-100 backdrop-blur">required {range! >= 1000 ? `${(range! / 1000).toFixed(1)} km` : `${range} m`}</div>
          </Html>
        </>
      )}
      <Line points={[[bx * 80, 6, bz * 80], [bx * extent * 1.15, 6, bz * extent * 1.15]]} color="#fbbf24" lineWidth={1.2} transparent opacity={0.6} dashed dashSize={25} gapSize={20} />
      <Html position={[bx * extent * 1.2, 30, bz * extent * 1.2]} center zIndexRange={[10, 0]} style={{ pointerEvents: "none" }}>
        <div className="flex items-center gap-1.5 whitespace-nowrap rounded-md border border-amber-300/30 bg-black/60 px-2 py-0.5 text-[11px] text-amber-100 backdrop-blur">
          <span className="inline-block size-1.5 rounded-full bg-amber-300" /> TV transmitter
        </div>
      </Html>
      <Wavefronts bearing={bearing} extent={extent} />
      <Beacon reach={range ?? extent * 0.5} />
      {paths.map((p, i) => <Drone key={i} path={p} sample={sample} thr={ov.threshold} scale={14} />)}
    </group>
  );
}
