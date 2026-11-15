/* The start screen: the finished radar node as a gathering point cloud, under an
 * instrument overlay whose trackers follow real parts and read their margins. */
import { useEffect, useMemo, useRef, useState } from "react";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import * as THREE from "three";
import { MeshSurfaceSampler } from "three/examples/jsm/math/MeshSurfaceSampler.js";
import radar from "@/hero/radar.json";

type HeroPart = { id: string; name: string; shape: string; size?: number[]; wall?: number; radius?: number;
  inner_radius?: number; length?: number; axis?: string; position: number[]; rotation?: number[]; look?: string };

const MM = 0.001;
const reduced = typeof matchMedia !== "undefined" && matchMedia("(prefers-reduced-motion: reduce)").matches;

/** Geometry of one part in metres, z-up, placed in the design frame. */
function partGeometry(p: HeroPart): THREE.BufferGeometry | null {
  let g: THREE.BufferGeometry;
  if (p.shape === "box" && p.size) {
    g = new THREE.BoxGeometry(p.size[0] * MM, p.size[1] * MM, p.size[2] * MM);
  } else if ((p.shape === "cylinder" || p.shape === "tube") && p.radius && p.length) {
    g = new THREE.CylinderGeometry(p.radius * MM, p.radius * MM, p.length * MM, 24, 1, p.shape === "tube");
    // three's cylinder runs along y; turn it onto the part's axis
    if (p.axis === "z" || !p.axis) g.rotateX(Math.PI / 2);
    else if (p.axis === "x") g.rotateZ(Math.PI / 2);
  } else if (p.shape === "sphere" && p.radius) {
    g = new THREE.SphereGeometry(p.radius * MM, 24, 16);
  } else return null;
  const r = (p.rotation ?? [0, 0, 0]).map((d) => (d * Math.PI) / 180);
  g.applyMatrix4(new THREE.Matrix4().makeRotationFromEuler(new THREE.Euler(r[0], r[1], r[2], "XYZ")));
  g.translate(p.position[0] * MM, p.position[1] * MM, p.position[2] * MM);
  return g;
}

function area(g: THREE.BufferGeometry) {
  const pos = g.getAttribute("position"), idx = g.getIndex();
  const a = new THREE.Vector3(), b = new THREE.Vector3(), c = new THREE.Vector3();
  let s = 0;
  const n = idx ? idx.count : pos.count;
  for (let i = 0; i < n; i += 3) {
    const [i0, i1, i2] = idx ? [idx.getX(i), idx.getX(i + 1), idx.getX(i + 2)] : [i, i + 1, i + 2];
    a.fromBufferAttribute(pos, i0); b.fromBufferAttribute(pos, i1); c.fromBufferAttribute(pos, i2);
    s += b.sub(a).cross(c.sub(a)).length() / 2;
  }
  return s;
}

/** Sample points over every part's surface; open frames (mesh, bowtie, tripod) get fewer. */
function samplePoints(parts: HeroPart[], count: number) {
  const items = parts.map((p) => {
    const g = partGeometry(p);
    if (!g) return null;
    const open = p.look === "tripod" ? 0.12 : p.look === "wire_mesh" ? 0.35 : p.look === "bowtie" ? 0.7 : 1;
    // small parts would vanish under area weighting; give everything a floor
    return { p, g, w: (Math.sqrt(area(g)) + 0.05) * open };
  }).filter(Boolean) as { p: HeroPart; g: THREE.BufferGeometry; w: number }[];
  const total = items.reduce((s, it) => s + it.w, 0);
  const target = new Float32Array(count * 3), rand = new Float32Array(count * 3), seed = new Float32Array(count);
  const box = new THREE.Box3();
  let k = 0;
  const v = new THREE.Vector3();
  for (const it of items) {
    const n = it.p === items[items.length - 1].p ? count - k : Math.round((count * it.w) / total);
    const sampler = new MeshSurfaceSampler(new THREE.Mesh(it.g)).build();
    for (let i = 0; i < n && k < count; i++, k++) {
      sampler.sample(v);
      // z-up design frame -> y-up three frame
      target.set([v.x, v.z, -v.y], k * 3);
      box.expandByPoint(new THREE.Vector3(v.x, v.z, -v.y));
    }
  }
  const centre = box.getCenter(new THREE.Vector3()), size = box.getSize(new THREE.Vector3());
  const scale = 1.8 / Math.max(size.x, size.y, size.z);
  for (let i = 0; i < count; i++) {
    for (let j = 0; j < 3; j++) target[i * 3 + j] = (target[i * 3 + j] - centre.getComponent(j)) * scale;
    // the cloud the points start in: a soft gaussian ball
    const u = Math.random(), t = Math.random() * Math.PI * 2, c = Math.random() * 2 - 1;
    const r = 1.6 * Math.cbrt(u), s = Math.sqrt(1 - c * c);
    rand.set([r * s * Math.cos(t), r * c * 0.8, r * s * Math.sin(t)], i * 3);
    seed[i] = Math.random();
    if (i % 5 === 0) target.set([rand[i * 3] * 1.4, rand[i * 3 + 1] * 1.2, rand[i * 3 + 2] * 1.4], i * 3);
  }
  return { target, rand, seed, centre, scale };
}

const vert = /* glsl */ `
  attribute vec3 aTarget; attribute vec3 aRand; attribute float aSeed;
  uniform float uTime, uGather, uSize, uRepel, uPixel;
  uniform vec3 uMouse;
  varying float vAlpha; varying float vSeed;
  void main() {
    float g = smoothstep(0.0, 1.0, clamp(uGather * 1.6 - aSeed * 0.6, 0.0, 1.0));
    float t = uTime * 0.35 + aSeed * 40.0;
    vec3 drift = aRand + 0.18 * vec3(sin(t), cos(t * 0.83), sin(t * 0.61 + 1.7));
    vec3 p = mix(drift, aTarget, g);
    p += 0.006 * vec3(sin(uTime * 1.7 + aSeed * 90.0), cos(uTime * 1.3 + aSeed * 70.0), sin(uTime * 1.1 + aSeed * 50.0));
    vec3 d = p - uMouse;
    float f = exp(-dot(d, d) / 0.045) * uRepel;
    p += normalize(d + 1e-4) * f * 0.22;
    vec4 mv = modelViewMatrix * vec4(p, 1.0);
    gl_Position = projectionMatrix * mv;
    gl_PointSize = uSize * uPixel * (0.6 + 0.8 * fract(aSeed * 13.7)) / -mv.z;
    vAlpha = mix(0.18, 0.62, g) * (0.45 + 0.55 * fract(aSeed * 91.3)) * (1.0 + f * 1.5);
    vSeed = aSeed;
  }`;
const frag = /* glsl */ `
  uniform vec3 uInk, uAccent;
  varying float vAlpha; varying float vSeed;
  void main() {
    vec2 c = gl_PointCoord - 0.5;
    float d = dot(c, c);
    if (d > 0.25) discard;
    vec3 col = mix(uInk, uAccent, step(0.93, vSeed));
    gl_FragColor = vec4(col, vAlpha * smoothstep(0.25, 0.05, d));
  }`;

export type Tracker = { id: string; label: string; value: string; x: number; y: number; visible: boolean };

function Cloud({ dark, onTrack, scatter, hover }: { dark: boolean; onTrack: (t: Tracker[]) => void; scatter: number; hover: React.MutableRefObject<boolean> }) {
  const { size, gl, camera, pointer, raycaster } = useThree();
  const count = size.width < 700 ? 40000 : 80000;
  const data = useMemo(() => samplePoints(radar.parts as HeroPart[], count), [count]);
  const group = useRef<THREE.Group>(null);
  const mat = useRef<THREE.ShaderMaterial>(null);
  const gather = useRef(reduced ? 1 : 0);
  const plane = useMemo(() => new THREE.Plane(new THREE.Vector3(0, 0, 1), 0), []);
  const hit = useMemo(() => new THREE.Vector3(9, 9, 9), []);
  const last = useRef(0);
  const anchors = useMemo(() => {
    const pick = ["surv_antenna", "mast", "enclosure", "battery"];
    return pick.map((id) => {
      const p = (radar.parts as HeroPart[]).find((q) => q.id === id)!;
      const t = (radar.tests as Record<string, { title: string; margin: number | null }[]>)[id]?.[0];
      const w = new THREE.Vector3(p.position[0] * MM, p.position[2] * MM, -p.position[1] * MM);
      w.sub(data.centre).multiplyScalar(data.scale);
      const m = t?.margin;
      return { id, label: p.name.toUpperCase(), value: m == null ? "" : `${m >= 0 ? "+" : "−"}${Math.round(Math.abs(m) * 100)}%`, w };
    });
  }, [data]);

  const uniforms = useMemo(() => ({
    uTime: { value: 0 }, uGather: { value: gather.current }, uSize: { value: 2.6 }, uRepel: { value: 0 },
    uPixel: { value: gl.getPixelRatio() }, uMouse: { value: new THREE.Vector3(9, 9, 9) },
    uInk: { value: new THREE.Color() }, uAccent: { value: new THREE.Color() },
  }), [gl]);
  useEffect(() => {
    uniforms.uInk.value.set(dark ? "#f2f5f8" : "#0e141b");
    uniforms.uAccent.value.set(dark ? "#5cc8e6" : "#1673b5");
  }, [dark, uniforms]);
  useEffect(() => { if (scatter && !reduced) gather.current = 0; }, [scatter]);

  useFrame((state, dt) => {
    const u = mat.current?.uniforms;
    if (!u || !group.current) return;
    u.uTime.value = state.clock.elapsedTime;
    gather.current = Math.min(1, gather.current + dt / 3.2);
    u.uGather.value = gather.current;
    if (!reduced) group.current.rotation.y = -0.6 + state.clock.elapsedTime * 0.08;
    // cursor: where it meets the plane through the model, in the cloud's own frame
    raycaster.setFromCamera(pointer, camera);
    const inv = group.current.matrixWorld.clone().invert();
    const ray = raycaster.ray.clone().applyMatrix4(inv);
    const target = hover.current && ray.intersectPlane(plane, hit) ? hit : null;
    u.uRepel.value += ((target ? 1 : 0) - u.uRepel.value) * Math.min(1, dt * 6);
    if (target) u.uMouse.value.lerp(target, Math.min(1, dt * 10));
    // trackers: project the anchors ~15 times a second
    if (state.clock.elapsedTime - last.current > 0.066) {
      last.current = state.clock.elapsedTime;
      const out = anchors.map((a) => {
        const v = a.w.clone().applyMatrix4(group.current!.matrixWorld).project(camera);
        return { id: a.id, label: a.label, value: a.value, x: (v.x * 0.5 + 0.5) * size.width,
          y: (-v.y * 0.5 + 0.5) * size.height, visible: gather.current > 0.85 && v.z < 1 };
      });
      onTrack(out);
    }
  });

  return (
    <group ref={group} rotation={[0, -0.6, 0]}>
      <points frustumCulled={false}>
        <bufferGeometry>
          <bufferAttribute attach="attributes-position" args={[data.target, 3]} />
          <bufferAttribute attach="attributes-aTarget" args={[data.target, 3]} />
          <bufferAttribute attach="attributes-aRand" args={[data.rand, 3]} />
          <bufferAttribute attach="attributes-aSeed" args={[data.seed, 1]} />
        </bufferGeometry>
        <shaderMaterial ref={mat} vertexShader={vert} fragmentShader={frag} uniforms={uniforms} transparent
          depthWrite={false} blending={dark ? THREE.AdditiveBlending : THREE.NormalBlending} />
      </points>
    </group>
  );
}

function Ticks({ n, vertical }: { n: number; vertical?: boolean }) {
  return (
    <svg className={vertical ? "h-full w-3" : "h-3 w-full"} preserveAspectRatio="none" viewBox={vertical ? `0 0 12 ${n * 10}` : `0 0 ${n * 10} 12`} aria-hidden>
      {Array.from({ length: n + 1 }, (_, i) => {
        const L = i % 10 === 0 ? 12 : i % 5 === 0 ? 8 : 4;
        return vertical
          ? <line key={i} x1={0} x2={L} y1={i * 10} y2={i * 10} className="stroke-foreground/25" strokeWidth={1} vectorEffect="non-scaling-stroke" />
          : <line key={i} x1={i * 10} x2={i * 10} y1={0} y2={L} className="stroke-foreground/25" strokeWidth={1} vectorEffect="non-scaling-stroke" />;
      })}
    </svg>
  );
}

const Cross = ({ className }: { className: string }) => (
  <svg className={"absolute size-6 " + className} viewBox="0 0 24 24" aria-hidden>
    <path d="M12 0v24M0 12h24" className="stroke-foreground/35" strokeWidth={1} />
  </svg>
);

export default function Hero({ dark }: { dark: boolean }) {
  const [tracks, setTracks] = useState<Tracker[]>([]);
  const [scatter, setScatter] = useState(0);
  const hover = useRef(false);
  const mono = "font-mono text-[10px] uppercase tracking-[0.18em]";
  return (
    <div className="absolute inset-0 select-none" onClick={() => setScatter((s) => s + 1)}
      onPointerMove={() => { hover.current = true; }} onPointerLeave={() => { hover.current = false; }}>
      <Canvas dpr={[1, 2]} camera={{ position: [0, 0.25, 3.6], fov: 38 }} gl={{ antialias: true, alpha: true }}
        aria-label="The passive radar node as a cloud of points">
        <Cloud dark={dark} onTrack={setTracks} scatter={scatter} hover={hover} />
      </Canvas>

      {/* instrument frame */}
      <div className="pointer-events-none absolute inset-6 sm:inset-10">
        <div className="absolute inset-x-8 top-0"><Ticks n={80} /></div>
        <div className="absolute inset-x-8 bottom-0 rotate-180"><Ticks n={80} /></div>
        <div className="absolute inset-y-8 left-0"><Ticks n={50} vertical /></div>
        <div className="absolute inset-y-8 right-0 rotate-180"><Ticks n={50} vertical /></div>
        <Cross className="-left-3 -top-3" /><Cross className="-right-3 -top-3" />
        <Cross className="-bottom-3 -left-3" /><Cross className="-bottom-3 -right-3" />
        <div className={`absolute left-6 top-6 space-y-1 text-muted-foreground ${mono}`}>
          <div className="text-foreground/80">Field view</div>
          <div>{radar.name}</div>
          <div>{radar.passed}/{radar.total} real-world tests pass</div>
        </div>
        <div className={`absolute right-6 top-6 text-right text-muted-foreground ${mono}`}>
          <div>margin of safety</div>
          <div className="text-foreground/80">allowable / actual − 1</div>
        </div>
      </div>

      {/* trackers locked to real parts */}
      <div className="pointer-events-none absolute inset-0" aria-hidden>
        {tracks.map((t, i) => (
          <div key={t.id} className="absolute transition-opacity duration-500"
            style={{ left: t.x, top: t.y, opacity: t.visible ? 1 : 0 }}>
            <div className="absolute -left-2.5 -top-2.5 size-5 border border-primary/80" />
            <div className={`absolute top-[-0.6rem] whitespace-nowrap ${i % 2 ? "right-5 text-right" : "left-5"} ${mono}`}>
              <span className="text-foreground/70">{t.label}</span>
              {t.value && <span className="ml-2 text-primary">{t.value}</span>}
            </div>
          </div>
        ))}
      </div>

      <p className={`pointer-events-none absolute inset-x-0 bottom-3 text-center text-muted-foreground/70 ${mono}`}>
        Describe something above · click to scatter
      </p>
    </div>
  );
}
