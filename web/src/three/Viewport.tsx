import { Suspense, useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { CameraControls, ContactShadows, Environment, Grid, Lightformer, Html, MeshReflectorMaterial } from "@react-three/drei";
import { Bloom, EffectComposer, N8AO, Outline, Selection, Select, ToneMapping, Vignette } from "@react-three/postprocessing";
import { ToneMappingMode } from "postprocessing";
import { useStore } from "@/store";
import type { Part, Vec3 } from "@/types";
import { PartShape, extents } from "./PartMesh";
import { finishFor, STATUS_COLOR } from "./looks";
import { SiteView } from "./SiteView";
import { ViewportControls } from "./ViewportControls";
import { fmtMass } from "./format";

const MM = 0.001;

function rotationMatrix(rot: Vec3) {
  return new THREE.Matrix4().makeRotationFromEuler(new THREE.Euler(...rot.map(THREE.MathUtils.degToRad) as Vec3, "XYZ"));
}

/** World-axis bounding box in mm (rotation included), like design.aabb. */
function aabb(p: Part) {
  const half = new THREE.Vector3(...extents(p)).multiplyScalar(0.5);
  const box = new THREE.Box3();
  const m = rotationMatrix(p.rotation);
  for (const sx of [-1, 1]) for (const sy of [-1, 1]) for (const sz of [-1, 1]) {
    box.expandByPoint(new THREE.Vector3(sx * half.x, sy * half.y, sz * half.z).applyMatrix4(m).add(new THREE.Vector3(...p.position)));
  }
  return box;
}

interface Anim { born: number; pulse: number }

function PartNode({ p, centre, step, anims, opacity, tint, explode, selected }: {
  p: Part; centre: THREE.Vector3; step: number; anims: React.MutableRefObject<Record<string, Anim>>;
  opacity: number; tint: string | null; explode: number; selected: boolean;
}) {
  const g = useRef<THREE.Group>(null!);
  const mats = useStore((s) => s.materials);
  const [fade, setFade] = useState(1);
  const finish = useMemo(() => finishFor(p.material, mats, p.color), [p.material, mats, p.color]);
  const home = useMemo(() => new THREE.Vector3(...p.position), [p.position]);
  const dir = useMemo(() => {
    const d = home.clone().sub(centre);
    d.z += 1e-6 + step * 0.0;
    return d;
  }, [home, centre, step]);
  const set = useStore((s) => s.set);
  const reduced = useMemo(() => matchMedia("(prefers-reduced-motion: reduce)").matches, []);

  useFrame(() => {
    const a = anims.current[p.id];
    const now = performance.now();
    let rise = 0, f = 1, pulse = 0;
    if (a && !reduced) {
      const t = Math.min(1, (now - a.born) / 900);
      const e = 1 - Math.pow(1 - t, 3);
      rise = (1 - e) * 0.35;
      f = e;
      if (a.pulse) {
        const tp = (now - a.pulse) / 1400;
        pulse = tp < 1 ? Math.sin(tp * Math.PI) : 0;
      }
    }
    if (Math.abs(f - fade) > 0.02 || (f === 1 && fade !== 1)) setFade(f);
    const target = home.clone().add(dir.clone().multiplyScalar(explode * 0.9));
    target.z += explode * step * 40;
    const size = Math.max(...extents(p));
    target.z += rise * Math.max(size, 200);
    g.current.position.lerp(target, 0.18);
    const s = 1 + pulse * 0.04;
    g.current.scale.setScalar(s);
    (g.current.userData as { pulse: number }).pulse = pulse;
  });

  const hovered = useStore((s) => s.hovered === p.id);
  const o = {
    finish,
    opacity: opacity * fade,
    tint: selected || hovered ? "#38bdf8" : tint,
    tintAmount: selected ? 0.28 : hovered ? 0.16 : 0.07,
    highlight: selected || hovered,
  };
  return (
    <group
      ref={g}
      position={home}
      rotation={p.rotation.map(THREE.MathUtils.degToRad) as Vec3}
      onClick={(e) => { e.stopPropagation(); set({ selected: p.id }); }}
      onPointerOver={(e) => { e.stopPropagation(); set({ hovered: p.id }); document.body.style.cursor = "pointer"; }}
      onPointerOut={() => { set({ hovered: null }); document.body.style.cursor = ""; }}
    >
      <Select enabled={selected}>
        <PartShape p={p} o={o} />
      </Select>
      {selected && <PartCard p={p} />}
    </group>
  );
}

function PartCard({ p }: { p: Part }) {
  const row = useStore((s) => s.summary?.parts.find((r) => r.id === p.id));
  const mats = useStore((s) => s.materials);
  const e = extents(p);
  return (
    <Html position={[0, 0, e[2] / 2]} center={false} zIndexRange={[20, 0]} style={{ pointerEvents: "none", transform: "translate(14px, -50%)" }}>
      <div className="w-60 rounded-lg border bg-popover/95 p-3 text-xs text-popover-foreground shadow-xl backdrop-blur">
        <div className="text-sm font-semibold leading-tight">{p.name}</div>
        <div className="mt-0.5 text-muted-foreground">{(mats[p.material]?.name as string) ?? p.material}{row ? ` · ${fmtMass(row.mass_g)}` : ""}</div>
        {p.purpose && <div className="mt-2 leading-snug">{p.purpose}</div>}
        {p.specs && Object.keys(p.specs).length > 0 && (
          <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5">
            {Object.entries(p.specs).map(([k, v]) => (
              <div key={k} className="contents"><dt className="text-muted-foreground">{k}</dt><dd className="font-mono">{v}</dd></div>
            ))}
          </dl>
        )}
      </div>
    </Html>
  );
}

function Model() {
  const design = useStore((s) => s.design);
  const version = useStore((s) => s.designVersion);
  const added = useStore((s) => s.added);
  const changed = useStore((s) => s.changed);
  const tests = useStore((s) => s.tests);
  const tintOn = useStore((s) => s.tint);
  const xray = useStore((s) => s.xray);
  const isolate = useStore((s) => s.isolate);
  const explode = useStore((s) => s.explode);
  const selected = useStore((s) => s.selected);
  const anims = useRef<Record<string, Anim>>({});

  useEffect(() => {
    const now = performance.now();
    for (const id of added) anims.current[id] = { born: now, pulse: 0 };
    for (const id of changed) anims.current[id] = { ...(anims.current[id] ?? { born: 0 }), pulse: now };
  }, [version]); // eslint-disable-line react-hooks/exhaustive-deps

  const centre = useMemo(() => {
    if (!design) return new THREE.Vector3();
    const b = new THREE.Box3();
    design.parts.forEach((p) => b.union(aabb(p)));
    return b.getCenter(new THREE.Vector3());
  }, [design]);

  const stepOf = useMemo(() => {
    const m: Record<string, number> = {};
    design?.assembly.forEach((s, i) => s.parts.forEach((id) => { if (m[id] === undefined) m[id] = i; }));
    return m;
  }, [design]);

  const statusOf = (id: string): string | null => {
    if (!tintOn) return null;
    let st: string | null = null;
    for (const t of Object.values(tests)) {
      if (t.part !== id) continue;
      if (!t.result) st = st ?? "pending";
      else if (t.result.status !== "pass") return STATUS_COLOR.fail;
      else if (st !== "pending") st = "pass";
    }
    return st === "pass" ? STATUS_COLOR.pass : st === "pending" ? STATUS_COLOR.pending : null;
  };

  if (!design) return null;
  return (
    <group rotation={[-Math.PI / 2, 0, 0]} scale={MM}>
      {design.parts.map((p) => (
        <PartNode
          key={p.id}
          p={p}
          centre={centre}
          step={stepOf[p.id] ?? 0}
          anims={anims}
          explode={explode}
          selected={selected === p.id}
          opacity={(xray ? 0.28 : 1) * (isolate && !isolate.includes(p.id) ? 0.07 : 1)}
          tint={statusOf(p.id)}
        />
      ))}
    </group>
  );
}

/** Frame the bounding sphere so the model fills ~70% of the viewport height, from a
 *  three-quarter view in front of +x (35 deg toward +y, 18 deg up): the side the main
 *  antenna faces. Re-frames whenever the design's bounds or the explode amount change. */
const VIEW_AZ = THREE.MathUtils.degToRad(35);
const VIEW_EL = THREE.MathUtils.degToRad(18);

function AutoFrame({ controls }: { controls: React.RefObject<CameraControls | null> }) {
  const design = useStore((s) => s.design);
  const explode = useStore((s) => s.explode);
  const nonce = useStore((s) => s.frameNonce);
  const camera = useThree((s) => s.camera) as THREE.PerspectiveCamera;
  const aspect = useThree((s) => s.size.width / Math.max(1, s.size.height));
  const last = useRef<string>("");
  const lastNonce = useRef(nonce);
  useEffect(() => {
    if (!design || !controls.current) return;
    const b = new THREE.Box3();
    design.parts.forEach((p) => b.union(aabb(p)));
    // z-up mm -> y-up m
    const min = new THREE.Vector3(b.min.x, b.min.z, -b.max.y).multiplyScalar(MM);
    const max = new THREE.Vector3(b.max.x, b.max.z, -b.min.y).multiplyScalar(MM);
    const sphere = new THREE.Box3(min, max).getBoundingSphere(new THREE.Sphere());
    const key = [sphere.center.x, sphere.center.y, sphere.center.z, sphere.radius].map((v) => Math.round(v * 50)).join(",")
      + `|${explode.toFixed(2)}|${aspect.toFixed(1)}`;
    if (key === last.current && nonce === lastNonce.current) return;
    const first = last.current === "";
    last.current = key;
    lastNonce.current = nonce;
    const r = sphere.radius * (1 + explode * 0.9);
    const half = THREE.MathUtils.degToRad(camera.fov / 2);
    const fitH = r / Math.sin(Math.atan(0.7 * Math.tan(half)));
    const fitW = r / Math.sin(Math.atan(0.85 * Math.tan(half) * aspect));
    const dist = Math.max(fitH, fitW);
    const dir = new THREE.Vector3(Math.cos(VIEW_EL) * Math.cos(VIEW_AZ), Math.sin(VIEW_EL), -Math.cos(VIEW_EL) * Math.sin(VIEW_AZ));
    const pos = sphere.center.clone().add(dir.multiplyScalar(dist));
    void controls.current.setLookAt(pos.x, pos.y, pos.z, sphere.center.x, sphere.center.y, sphere.center.z, !first);
  }, [design, explode, controls, nonce, camera, aspect]);
  return null;
}

function Ground() {
  const design = useStore((s) => s.design);
  const dark = useIsDark();
  const span = useMemo(() => {
    if (!design) return 1;
    const b = new THREE.Box3();
    design.parts.forEach((p) => b.union(aabb(p)));
    const s = b.getSize(new THREE.Vector3());
    return Math.max(s.x, s.y, s.z) * MM;
  }, [design]);
  const floor = useMemo(() => {
    if (!design) return 0;
    let z = Infinity;
    design.parts.forEach((p) => { z = Math.min(z, aabb(p).min.z); });
    return z * MM;
  }, [design]);
  const cell = niceStep(span / 12);
  const fade = useMemo(() => radialFade(), []);
  return (
    <group position={[0, floor - 0.0005, 0]}>
      <Grid
        position={[0, 0.0002, 0]}
        args={[span * 6, span * 6]}
        cellSize={cell}
        sectionSize={cell * 5}
        cellThickness={0.6}
        sectionThickness={1}
        cellColor={dark ? "#2a2f37" : "#d4d8de"}
        sectionColor={dark ? "#3a4250" : "#b9c0c9"}
        fadeDistance={span * 5}
        fadeStrength={1.5}
        infiniteGrid
      />
      <ContactShadows position={[0, 0.0006, 0]} scale={span * 3} blur={2.4} far={span * 1.2} opacity={dark ? 0.75 : 0.45} resolution={1024} />
      {/* a subtle, slightly rough mirror under the grid */}
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, -0.0004, 0]}>
        <circleGeometry args={[span * 2.2, 96]} />
        <MeshReflectorMaterial
          color={dark ? "#06080b" : "#eef1f5"}
          transparent
          alphaMap={fade}
          blur={[400, 120]}
          resolution={1024}
          mixBlur={1}
          mixStrength={dark ? 0.9 : 0.5}
          mirror={0.3}
          roughness={1}
          metalness={0.4}
          depthScale={1}
          minDepthThreshold={0.6}
          maxDepthThreshold={1.2}
        />
      </mesh>
    </group>
  );
}

/** White centre fading to black at the rim: an alpha map that melts the floor into the background. */
function radialFade() {
  const c = document.createElement("canvas");
  c.width = c.height = 256;
  const g = c.getContext("2d")!;
  const grad = g.createRadialGradient(128, 128, 0, 128, 128, 128);
  grad.addColorStop(0, "#fff");
  grad.addColorStop(0.45, "#bbb");
  grad.addColorStop(1, "#000");
  g.fillStyle = grad;
  g.fillRect(0, 0, 256, 256);
  return new THREE.CanvasTexture(c);
}

export function niceStep(x: number) {
  const p = Math.pow(10, Math.floor(Math.log10(x)));
  const m = x / p;
  return (m < 1.5 ? 1 : m < 3.5 ? 2 : m < 7.5 ? 5 : 10) * p;
}

export function useIsDark() {
  const [dark, setDark] = useState(() => document.documentElement.classList.contains("dark"));
  useEffect(() => {
    const ob = new MutationObserver(() => setDark(document.documentElement.classList.contains("dark")));
    ob.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
    return () => ob.disconnect();
  }, []);
  return dark;
}

function Studio() {
  // a soft photo studio: big key softbox overhead, cool and warm strip lights,
  // a front fill and a dim floor bounce, so metals always have something to reflect
  return (
    <Environment resolution={512} frames={1} environmentIntensity={1.15}>
      <color attach="background" args={["#1a1f27"]} />
      <Lightformer form="rect" intensity={3} position={[0, 7, 0]} rotation-x={Math.PI / 2} scale={[12, 12, 1]} color="#ffffff" />
      <Lightformer form="rect" intensity={2.2} position={[0, 3, -8]} scale={[16, 5, 1]} color="#ffffff" />
      <Lightformer form="rect" intensity={1.8} position={[-8, 2, 1]} rotation-y={Math.PI / 2} scale={[12, 3, 1]} color="#dbeafe" />
      <Lightformer form="rect" intensity={1.4} position={[8, 2.5, 1]} rotation-y={-Math.PI / 2} scale={[12, 3, 1]} color="#fff4e6" />
      <Lightformer form="rect" intensity={1.2} position={[0, 1.5, 9]} rotation-y={Math.PI} scale={[14, 4, 1]} color="#ffffff" />
      <Lightformer form="rect" intensity={0.5} position={[0, -3, 0]} rotation-x={-Math.PI / 2} scale={[20, 20, 1]} color="#93a4bb" />
    </Environment>
  );
}

function Effects({ site }: { site: boolean }) {
  const dark = useIsDark();
  return (
    <EffectComposer multisampling={4} autoClear={false}>
      <N8AO aoRadius={site ? 2 : 0.25} intensity={site ? 0 : 2.2} distanceFalloff={1} halfRes />
      <Outline visibleEdgeColor={0x7dd3fc} hiddenEdgeColor={0x38bdf8} edgeStrength={3} blur width={1200} />
      <Bloom intensity={site ? 0.9 : 0.35} luminanceThreshold={0.85} luminanceSmoothing={0.2} mipmapBlur />
      <Vignette offset={0.25} darkness={dark ? 0.45 : 0.2} />
      <ToneMapping mode={ToneMappingMode.ACES_FILMIC} />
    </EffectComposer>
  );
}

function Empty() {
  return (
    <div className="pointer-events-none absolute inset-0 grid place-items-center">
      <div className="text-center">
        <div className="mx-auto mb-4 size-14 rounded-2xl border border-dashed border-muted-foreground/30" />
        <p className="text-sm text-muted-foreground">Describe something, or pick an example above.</p>
      </div>
    </div>
  );
}

function FrameOnSite({ controls, active, extent }: { controls: React.RefObject<CameraControls | null>; active: boolean; extent: number }) {
  const { camera } = useThree();
  useEffect(() => {
    const ctl = controls.current;
    if (!ctl) return;
    if (active) {
      // kilometre scale: a 5 mm near plane would leave no depth precision for the map layers
      camera.near = Math.max(1, extent * 0.0005);
      camera.far = extent * 30;
      camera.updateProjectionMatrix();
      void ctl.setLookAt(extent * 0.62, extent * 0.62, extent * 0.78, 0, 0, 0, true);
    } else {
      camera.near = 0.005;
      camera.far = 2000;
      camera.updateProjectionMatrix();
    }
  }, [active, camera, controls, extent]);
  return null;
}

export default function Viewport() {
  const design = useStore((s) => s.design);
  const view = useStore((s) => s.view);
  const set = useStore((s) => s.set);
  const controls = useRef<CameraControls | null>(null);
  const site = view === "site";
  const overlayTest = useStore((s) => Object.values(s.tests).find((t) => t.result?.overlay)?.result);
  useEffect(() => { if (!overlayTest && site) set({ view: "model" }); }, [overlayTest, site, set]);
  const siteExtent = useMemo(() => {
    const f = overlayTest?.sim?.fields?.[overlayTest.overlay!.field];
    if (!f || f.kind !== "image") return 2500;
    const xs = f.x as number[], ys = f.y as number[];
    return Math.max(xs[xs.length - 1] - xs[0], ys[ys.length - 1] - ys[0]) / 2;
  }, [overlayTest]);

  return (
    <div className="relative size-full overflow-hidden bg-viewport">
      <Canvas
        shadows="percentage"
        dpr={[1, 2]}
        camera={{ position: [3, 2.5, 4], fov: 35, near: 0.005, far: 2000 }}
        gl={{ antialias: false, powerPreference: "high-performance" }}
        onPointerMissed={() => set({ selected: null })}
        aria-label="3D view of the design"
      >
        <Suspense fallback={null}>
          <Studio />
          <hemisphereLight args={["#e8f0ff", "#20242b", 0.6]} />
          {/* key: high, from the camera side */}
          <directionalLight position={[6, 8, -2]} intensity={2.4} castShadow shadow-mapSize={[2048, 2048]} shadow-bias={-0.0001} />
          {/* rim: from behind the model, so metal edges separate from the dark background */}
          <directionalLight position={[-6, 4, 4]} intensity={1.7} color="#dbeafe" />
          <directionalLight position={[-2, 2, -6]} intensity={0.45} color="#fff1e0" />
          <Selection>
            <Effects site={site} />
            {site && overlayTest ? <SiteView test={overlayTest} /> : (
              <>
                <Model />
                <Ground />
              </>
            )}
          </Selection>
          <CameraControls ref={controls} makeDefault smoothTime={0.35} minDistance={0.02} maxDistance={site ? siteExtent * 8 : 60} />
          {!site && <AutoFrame controls={controls} />}
          <FrameOnSite controls={controls} active={site} extent={siteExtent} />
        </Suspense>
      </Canvas>
      {!design && <Empty />}
      {design && <ViewportControls hasSite={!!overlayTest} />}
    </div>
  );
}
