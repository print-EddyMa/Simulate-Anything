/* The empty viewport as a field of points. Idle, they drift. Once a request is sent
 * they gather into a slowly turning orb while the design is being worked out. */
import { useEffect, useMemo, useRef } from "react";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import * as THREE from "three";

const reduced = typeof matchMedia !== "undefined" && matchMedia("(prefers-reduced-motion: reduce)").matches;

function makePoints(count: number) {
  const target = new Float32Array(count * 3), rand = new Float32Array(count * 3), seed = new Float32Array(count);
  const golden = Math.PI * (3 - Math.sqrt(5));
  for (let i = 0; i < count; i++) {
    // where the points drift: a wide, flattened cloud
    const u = Math.random(), t = Math.random() * Math.PI * 2, c = Math.random() * 2 - 1;
    const r = 2.2 * Math.cbrt(u), s = Math.sqrt(1 - c * c);
    rand.set([r * s * Math.cos(t) * 1.5, r * c * 0.7, r * s * Math.sin(t)], i * 3);
    seed[i] = Math.random();
    // where they gather: a sphere shell, a few points left inside as a core haze
    const y = 1 - (2 * (i + 0.5)) / count, rr = Math.sqrt(1 - y * y), th = golden * i;
    const R = i % 6 === 0 ? 0.25 + 0.4 * Math.cbrt(Math.random()) : 0.62 + (Math.random() - 0.5) * 0.04;
    target.set([Math.cos(th) * rr * R, y * R, Math.sin(th) * rr * R], i * 3);
  }
  return { target, rand, seed };
}

const vert = /* glsl */ `
  attribute vec3 aTarget; attribute vec3 aRand; attribute float aSeed;
  uniform float uTime, uGather, uSize, uPixel;
  varying float vAlpha; varying float vSeed;
  void main() {
    float g = smoothstep(0.0, 1.0, clamp(uGather * 1.6 - aSeed * 0.6, 0.0, 1.0));
    float t = uTime * 0.25 + aSeed * 40.0;
    vec3 drift = aRand + 0.22 * vec3(sin(t), cos(t * 0.83), sin(t * 0.61 + 1.7));
    vec3 orb = aTarget * (1.0 + 0.035 * sin(uTime * 2.2 + aTarget.y * 6.0));
    vec3 p = mix(drift, orb, g);
    vec4 mv = modelViewMatrix * vec4(p, 1.0);
    gl_Position = projectionMatrix * mv;
    gl_PointSize = uSize * uPixel * (0.6 + 0.8 * fract(aSeed * 13.7)) / -mv.z;
    vAlpha = mix(0.16, 0.7, g) * (0.45 + 0.55 * fract(aSeed * 91.3));
    vSeed = aSeed;
  }`;
const frag = /* glsl */ `
  uniform vec3 uInk, uAccent;
  varying float vAlpha; varying float vSeed;
  void main() {
    vec2 c = gl_PointCoord - 0.5;
    float d = dot(c, c);
    if (d > 0.25) discard;
    vec3 col = mix(uInk, uAccent, step(0.9, vSeed));
    gl_FragColor = vec4(col, vAlpha * smoothstep(0.25, 0.05, d));
  }`;

function Cloud({ dark, loading }: { dark: boolean; loading: boolean }) {
  const { size, gl } = useThree();
  const count = size.width < 700 ? 30000 : 60000;
  const data = useMemo(() => makePoints(count), [count]);
  const gather = useRef(0);
  const mat = useRef<THREE.ShaderMaterial>(null);
  const group = useRef<THREE.Group>(null);
  const uniforms = useMemo(() => ({
    uTime: { value: 0 }, uGather: { value: 0 }, uSize: { value: 2.6 }, uPixel: { value: gl.getPixelRatio() },
    uInk: { value: new THREE.Color() }, uAccent: { value: new THREE.Color() },
  }), [gl]);
  useEffect(() => {
    uniforms.uInk.value.set(dark ? "#f2f5f8" : "#0e141b");
    uniforms.uAccent.value.set(dark ? "#5cc8e6" : "#1673b5");
  }, [dark, uniforms]);

  useFrame((state, dt) => {
    const u = mat.current?.uniforms;
    if (!u || !group.current) return;
    u.uTime.value = state.clock.elapsedTime;
    const goal = loading ? 1 : 0;
    gather.current += Math.sign(goal - gather.current) * Math.min(Math.abs(goal - gather.current), dt / (loading ? 2.4 : 1.2));
    u.uGather.value = gather.current;
    if (!reduced) group.current.rotation.y += dt * (0.05 + 0.35 * gather.current);
  });

  return (
    <group ref={group}>
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

export default function Hero({ dark, loading }: { dark: boolean; loading: boolean }) {
  return (
    <div className="absolute inset-0 select-none">
      <Canvas dpr={[1, 2]} camera={{ position: [0, 0.2, 3.4], fov: 38 }} gl={{ antialias: true, alpha: true }}
        aria-label={loading ? "Working on the design" : "Empty viewport"}>
        <Cloud dark={dark} loading={loading} />
      </Canvas>
      <p className="pointer-events-none absolute inset-x-0 bottom-4 text-center text-xs text-muted-foreground/70">
        {loading ? "Designing…" : "Describe anything above"}
      </p>
    </div>
  );
}
