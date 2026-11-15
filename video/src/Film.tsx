import React from "react";
import {
  AbsoluteFill, Audio, Easing, Img, OffthreadVideo, Sequence, interpolate, random,
  staticFile, useCurrentFrame, useVideoConfig,
} from "remotion";
import assets from "./assets.json";

export const FPS = 30;
export const TOTAL = 120 * FPS;
const s = (sec: number) => Math.round(sec * FPS);

const C = {
  bg: "#07090c", panel: "#0d1117", ink: "#f2f5f8", dim: "#8a96a3", faint: "#3a4450",
  accent: "#5cc8e6", fail: "#f0706a", pass: "#4fd18b", warn: "#f2b84b",
};
const SANS = "Inter, system-ui, sans-serif";
const MONO = "'JetBrains Mono', ui-monospace, monospace";

type Media = { file: string; dur: number };
const A = assets as unknown as {
  ai: Record<string, Media>; vo: Record<string, Media>; sfx: Record<string, Media>;
  music: Record<string, Media>; app: Record<string, Media>; marks: Record<string, Record<string, number>>;
};

const clamp = { extrapolateLeft: "clamp", extrapolateRight: "clamp" } as const;
const ease = Easing.bezier(0.65, 0, 0.35, 1);
const out = Easing.bezier(0.16, 1, 0.3, 1);

const Fonts = () => (
  <style>{`
    @font-face { font-family: Inter; src: url(${staticFile("fonts/inter.woff2")}) format("woff2"); font-weight: 100 900; }
    @font-face { font-family: 'JetBrains Mono'; src: url(${staticFile("fonts/mono.woff2")}) format("woff2"); font-weight: 100 900; }
  `}</style>
);

/* ---------- building blocks ---------- */

/** Fades a section in and out, with a short zoom-blur on entry. */
const Shot: React.FC<{ dur: number; children: React.ReactNode; inF?: number; outF?: number; blur?: boolean }> = ({ dur, children, inF = 8, outF = 6, blur = true }) => {
  const f = useCurrentFrame();
  const o = Math.min(interpolate(f, [0, inF], [0, 1], clamp), interpolate(f, [dur - outF, dur], [1, 0], clamp));
  const k = interpolate(f, [0, inF + 4], [1, 0], { ...clamp, easing: out });
  return (
    <AbsoluteFill style={{ opacity: o, transform: `scale(${1 + 0.06 * k})`, filter: blur && k > 0.01 ? `blur(${10 * k}px)` : undefined }}>
      {children}
    </AbsoluteFill>
  );
};

/** A white flash for hard cuts. */
const Flash: React.FC<{ len?: number; color?: string; peak?: number }> = ({ len = 8, color = "#fff", peak = 0.85 }) => {
  const f = useCurrentFrame();
  return <AbsoluteFill style={{ background: color, opacity: interpolate(f, [0, 1, len], [0, peak, 0], clamp), pointerEvents: "none" }} />;
};

type Key = { t: number; x: number; y: number; z: number };
/** Interpolated camera over a full-frame layer: focus point (0..1) and zoom. */
function cam(keys: Key[], f: number) {
  if (f <= keys[0].t) return keys[0];
  for (let i = 1; i < keys.length; i++) {
    const a = keys[i - 1], b = keys[i];
    if (f <= b.t) {
      const e = ease((f - a.t) / Math.max(1, b.t - a.t));
      return { t: f, x: a.x + (b.x - a.x) * e, y: a.y + (b.y - a.y) * e, z: a.z + (b.z - a.z) * e };
    }
  }
  return keys[keys.length - 1];
}
const Camera: React.FC<{ keys: Key[]; children: React.ReactNode }> = ({ keys, children }) => {
  const f = useCurrentFrame();
  const k = cam(keys, f);
  // translate so the focus point sits at the frame centre (clamped to the edges)
  const W = 1920, H = 1080, z = k.z;
  const tx = Math.min(0, Math.max(W - W * z, W / 2 - k.x * W * z));
  const ty = Math.min(0, Math.max(H - H * z, H / 2 - k.y * H * z));
  return (
    <AbsoluteFill style={{ transformOrigin: "0 0", transform: `translate(${tx}px, ${ty}px) scale(${z})` }}>
      {children}
    </AbsoluteFill>
  );
};

/** Real app footage. `at` is the source frame to start on. */
const App: React.FC<{ part: string; at: number; rate?: number; keys?: Key[] }> = ({ part, at, rate = 1, keys }) => {
  const m = A.app[part.toUpperCase()] ?? A.app[part];
  const body = m ? (
    <OffthreadVideo src={staticFile(m.file)} trimBefore={Math.max(0, Math.round(at))} playbackRate={rate} muted style={{ width: 1920, height: 1080 }} />
  ) : (
    <AbsoluteFill style={{ background: C.panel, color: C.dim, fontFamily: MONO, fontSize: 28, alignItems: "center", justifyContent: "center" }}>app capture: {part}</AbsoluteFill>
  );
  return <AbsoluteFill style={{ background: C.bg }}>{keys ? <Camera keys={keys}>{body}</Camera> : body}</AbsoluteFill>;
};

/** An AI clip when it exists, otherwise the motion-graphics fallback. */
const AI: React.FC<{ id: string; dur: number; trim?: number; rate?: number; push?: number; fallback: React.ReactNode; grade?: string }> = ({ id, dur, trim = 0, rate = 1, push = 0.06, fallback, grade }) => {
  const f = useCurrentFrame();
  const m = A.ai[id];
  if (!m) return <>{fallback}</>;
  const z = 1 + push * (f / dur);
  return (
    <AbsoluteFill style={{ background: "#000", overflow: "hidden" }}>
      <OffthreadVideo src={staticFile(m.file)} trimBefore={trim} playbackRate={rate} muted style={{ width: 1920, height: 1080, objectFit: "cover", transform: `scale(${z})`, filter: grade }} />
    </AbsoluteFill>
  );
};

const Vignette: React.FC<{ strength?: number }> = ({ strength = 0.55 }) => (
  <AbsoluteFill style={{ background: `radial-gradient(ellipse at center, transparent 45%, rgba(0,0,0,${strength}) 100%)`, pointerEvents: "none" }} />
);

const Grain: React.FC = () => {
  const f = useCurrentFrame();
  return (
    <AbsoluteFill style={{ pointerEvents: "none", opacity: 0.07, mixBlendMode: "overlay" }}>
      <svg width="1920" height="1080">
        <filter id="g"><feTurbulence type="fractalNoise" baseFrequency="0.9" numOctaves="2" seed={f % 12} /></filter>
        <rect width="1920" height="1080" filter="url(#g)" />
      </svg>
    </AbsoluteFill>
  );
};

/* ---------- motion graphics ---------- */

const Mark: React.FC<{ size: number; t: number }> = ({ size, t }) => {
  // the logo: design bar, glowing margin, limit line (geometry from docs/brand)
  const a = interpolate(t, [0, 0.35], [0, 1], { ...clamp, easing: out });
  const b = interpolate(t, [0.25, 0.6], [0, 1], { ...clamp, easing: out });
  const c = interpolate(t, [0.5, 0.85], [0, 1], { ...clamp, easing: out });
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" style={{ overflow: "visible" }}>
      <defs><filter id="mg"><feGaussianBlur stdDeviation="2.2" /></filter></defs>
      <rect x="13" y={17 + 30 * (1 - a)} width="19" height={30 * a} rx="3.5" fill={C.ink} />
      <rect x={34.5 - 10 * (1 - b)} y="17" width="9" height="30" rx="1.5" fill={C.accent} opacity={0.9 * b} filter="url(#mg)" />
      <rect x={34.5 - 10 * (1 - b)} y="17" width="9" height="30" rx="1.5" fill={C.accent} opacity={0.9 * b} />
      <rect x="47" y={11 - 40 * (1 - c)} width="4" height="42" rx="2" fill={C.ink} opacity={c} />
    </svg>
  );
};

const GridBg: React.FC<{ opacity?: number }> = ({ opacity = 0.5 }) => (
  <AbsoluteFill style={{
    opacity,
    backgroundImage: `linear-gradient(rgba(242,245,248,0.05) 1px, transparent 1px), linear-gradient(90deg, rgba(242,245,248,0.05) 1px, transparent 1px)`,
    backgroundSize: "64px 64px",
    maskImage: "radial-gradient(ellipse at center, black 30%, transparent 75%)",
  }} />
);


/* ---------- type ---------- */

/** One centred line, like the opening captions. */
const Title: React.FC<{ dur: number; children: React.ReactNode; size?: number; y?: number; scrim?: boolean }> = ({ dur, children, size = 84, y = 0, scrim = true }) => {
  const f = useCurrentFrame();
  const a = interpolate(f, [0, 14], [0, 1], { ...clamp, easing: out });
  const b = interpolate(f, [dur - 10, dur], [1, 0], clamp);
  return (
    <AbsoluteFill style={{ alignItems: "center", justifyContent: "center", pointerEvents: "none", opacity: a * b }}>
      {scrim && <AbsoluteFill style={{ background: "radial-gradient(ellipse 60% 40% at 50% 50%, rgba(0,0,0,0.55), transparent 75%)" }} />}
      <div style={{ position: "relative", transform: `translateY(${y + (1 - a) * 22}px)`, fontFamily: SANS, fontWeight: 600, fontSize: size, letterSpacing: -2, color: C.ink, textAlign: "center", lineHeight: 1.1, textShadow: "0 2px 30px rgba(0,0,0,0.55)", maxWidth: 1500 }}>
        {children}
      </div>
    </AbsoluteFill>
  );
};

/** A label and one big number, centred. */
const Stat: React.FC<{ dur: number; label: string; value: string; color: string; from?: number; to?: number; unit?: string }> = ({ dur, label, value, color, from, to, unit = "" }) => {
  const f = useCurrentFrame();
  const a = interpolate(f, [0, 12], [0, 1], { ...clamp, easing: out });
  const b = interpolate(f, [dur - 8, dur], [1, 0], clamp);
  const pop = interpolate(f, [6, 16], [1.25, 1], { ...clamp, easing: out });
  const shown = from !== undefined && to !== undefined
    ? `${to % 1 ? interpolate(f, [8, 34], [from, to], { ...clamp, easing: out }).toFixed(2) : Math.round(interpolate(f, [8, 40], [from, to], { ...clamp, easing: out })).toLocaleString("en-US")}${unit}` : value;
  return (
    <AbsoluteFill style={{ alignItems: "center", justifyContent: "center", pointerEvents: "none", opacity: a * b }}>
      <AbsoluteFill style={{ background: "radial-gradient(ellipse 55% 45% at 50% 50%, rgba(0,0,0,0.6), transparent 75%)" }} />
      <div style={{ position: "relative", textAlign: "center" }}>
        <div style={{ fontFamily: SANS, fontWeight: 600, fontSize: 64, letterSpacing: -1.5, color: C.ink, textShadow: "0 2px 24px rgba(0,0,0,.6)" }}>{label}</div>
        <div style={{ fontFamily: SANS, fontWeight: 700, fontSize: 220, letterSpacing: -8, lineHeight: 1, color, transform: `scale(${pop})`, textShadow: "0 0 50px rgba(0,0,0,.45)" }}>{shown}</div>
      </div>
    </AbsoluteFill>
  );
};

const LogoBuild: React.FC<{ dur: number; final?: boolean }> = ({ final }) => {
  const f = useCurrentFrame();
  const t = interpolate(f, [4, 46], [0, 1], clamp);
  const word = "margin";
  const w = Math.floor(interpolate(f, [34, 58], [0, word.length], clamp));
  const cap = interpolate(f, [60, 76], [0, 1], { ...clamp, easing: out });
  const glow = interpolate(f, [24, 40, 90], [0, 1, 0.35], clamp);
  return (
    <AbsoluteFill style={{ background: C.bg, alignItems: "center", justifyContent: "center" }}>
      <AbsoluteFill style={{ background: `radial-gradient(circle at 50% 50%, rgba(92,200,230,${0.16 * glow}), transparent 45%)` }} />
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 34 }}>
          <Mark size={200} t={t} />
          <div style={{ fontFamily: SANS, fontWeight: 600, fontSize: 168, letterSpacing: -6, color: C.ink, lineHeight: 1 }}>
            {word.slice(0, w)}<span style={{ opacity: 0 }}>{word.slice(w)}</span>
          </div>
        </div>
        <div style={{ fontFamily: SANS, fontWeight: 500, fontSize: 54, color: C.dim, marginTop: 18, letterSpacing: -0.5, opacity: cap, transform: `translateY(${(1 - cap) * 14}px)` }}>Create anything</div>
      </div>
      {final && (
        <div style={{ position: "absolute", bottom: 70, display: "flex", alignItems: "center", gap: 12, fontFamily: SANS, fontWeight: 500, fontSize: 28, color: "#b9c3cc", opacity: interpolate(f, [50, 66], [0, 1], clamp), transform: `translateY(${interpolate(f, [50, 66], [10, 0], { ...clamp, easing: out })}px)` }}>
          <svg width="28" height="28" viewBox="0 0 16 16" fill="#b9c3cc"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z"/></svg>
          github.com/print-EddyMa/Simulate-Anything
        </div>
      )}
    </AbsoluteFill>
  );
};

/** The 3D viewport of an app capture, cropped to the model, as a tile. */
const Tile: React.FC<{ part: string; at: number; w: number; h: number; label: string; delay: number }> = ({ part, at, w, h, label, delay }) => {
  const f = useCurrentFrame();
  const a = interpolate(f, [delay, delay + 12], [0, 1], { ...clamp, easing: out });
  // the model sits in x 280..1110, y 150..700 of the 1920x1080 capture
  const cx = 690, cy = 425, cw = 860;
  const k = w / cw;
  const m = A.app[part.toUpperCase()] ?? A.app[part];
  return (
    <div style={{ width: w, opacity: a, transform: `translateY(${(1 - a) * 30}px)` }}>
      <div style={{ width: w, height: h, overflow: "hidden", borderRadius: 18, position: "relative", background: C.panel, boxShadow: "0 20px 60px rgba(0,0,0,.5)" }}>
        {m && <OffthreadVideo src={staticFile(m.file)} trimBefore={Math.max(0, Math.round(at))} muted
          style={{ position: "absolute", width: 1920 * k, height: 1080 * k, left: -(cx * k) + w / 2, top: -(cy * k) + h / 2 }} />}
      </div>
      <div style={{ fontFamily: SANS, fontWeight: 600, fontSize: 34, color: C.ink, marginTop: 16, textAlign: "center", letterSpacing: -0.5 }}>{label}</div>
    </div>
  );
};

/** A field photo with a slow push. */
const Photo: React.FC<{ src: string; dur: number; from?: number; to?: number; ox?: string }> = ({ src, dur, from = 1.04, to = 1.12, ox = "50% 50%" }) => {
  const f = useCurrentFrame();
  const z = interpolate(f, [0, dur], [from, to]);
  return (
    <AbsoluteFill style={{ background: "#000", overflow: "hidden" }}>
      <Img src={staticFile(src)} style={{ width: "100%", height: "100%", objectFit: "cover", transform: `scale(${z})`, transformOrigin: ox }} />
    </AbsoluteFill>
  );
};
const Clip: React.FC<{ src: string; dur: number; rate?: number; trim?: number }> = ({ src, dur, rate = 1, trim = 0 }) => {
  const f = useCurrentFrame();
  return (
    <AbsoluteFill style={{ background: "#000", overflow: "hidden" }}>
      <OffthreadVideo src={staticFile(src)} trimBefore={trim} playbackRate={rate} muted style={{ width: "100%", height: "100%", objectFit: "cover", transform: `scale(${1.02 + 0.05 * f / dur})` }} />
    </AbsoluteFill>
  );
};

/* ---------- audio ---------- */

const VO_AT: [string, number][] = [["L1", 0.4], ["L2", 8.3], ["L3", 15.3], ["L4", 24.8], ["L5", 36.1], ["L6SHORT", 44.3], ["L7", 51.3], ["L8", 61.8], ["L9", 75.8], ["L10", 114.6]];
const SFX_AT: [string, number, number][] = [
  ["S1", 7.4, 0.9], ["S5", 14.6, 0.4], ["S5", 17.0, 0.4], ["S5", 19.4, 0.4], ["S5", 21.8, 0.4], ["S2", 24.5, 0.5],
  ["S4", 36.0, 0.8], ["S2", 38.7, 0.35], ["S2", 41.4, 0.35], ["S6", 44.2, 0.7], ["S7", 54.8, 0.7],
  ["S2", 61.5, 0.45], ["S7", 72.6, 0.5], ["S2", 75.5, 0.45],
  ["S1", 83.3, 0.5], ["S7", 88.2, 0.45], ["S2", 91.0, 0.45], ["S2", 93.9, 0.45], ["S2", 96.8, 0.45], ["S2", 99.7, 0.45], ["S2", 102.6, 0.45], ["S3", 105.5, 0.5],
  ["S8", 110.6, 0.9], ["S1", 113.0, 0.6],
];
function voWindows() {
  return VO_AT.filter(([id]) => A.vo[id]).map(([id, at]) => [s(at), s(at + A.vo[id].dur)] as const);
}
const Sound: React.FC = () => {
  const win = voWindows();
  const music = A.music.M1 ?? A.music.FALLBACK;
  return (
    <>
      {music && (
        <Audio src={staticFile(music.file)} volume={(f) => {
          const duck = win.some(([a, b]) => f >= a - 6 && f <= b + 6);
          const fadeOut = interpolate(f, [TOTAL - s(3), TOTAL], [1, 0], clamp);
          const fadeIn = interpolate(f, [0, 20], [0, 1], clamp);
          return (duck ? 0.26 : 0.62) * fadeOut * fadeIn;
        }} />
      )}
      {VO_AT.map(([id, at]) => A.vo[id] && (
        <Sequence key={id} from={s(at)} durationInFrames={s(A.vo[id].dur) + 2}><Audio src={staticFile(A.vo[id].file)} volume={1} /></Sequence>
      ))}
      {SFX_AT.map(([id, at, v], i) => A.sfx[id] && (
        <Sequence key={i} from={s(at)} durationInFrames={s(A.sfx[id].dur) + 2}><Audio src={staticFile(A.sfx[id].file)} volume={v} /></Sequence>
      ))}
    </>
  );
};

/* ---------- the film ---------- */

const M = (part: string, name: string, dflt: number) => A.marks[part]?.[name] ?? dflt;
const BLACK = <AbsoluteFill style={{ background: C.bg }} />;
// a gentle camera: never more than ~15% in, so the whole product stays in view
const drift = (dur: number, x = 0.5, y = 0.5, z0 = 1.0, z1 = 1.06): Key[] => [{ t: 0, x, y, z: z0 }, { t: dur, x, y, z: z1 }];

const capOf = (id: string) => (id === "sensor" ? "sensorcap" : "things");
const THINGS: [string, string][] = [["drone", "Drones"], ["arm", "Robot arms"], ["watch", "Watches"], ["board", "Circuit boards"], ["sensor", "Sensors"]];

export const Film: React.FC = () => {
  const send = M("run", "send", 331) + 1;
  const stretch = (M("run", "done", 1544) - send) / s(39.55);
  const ev = (sec: number) => send + Math.round(s(sec) * stretch);
  const typing = M("run", "typing", 64);
  return (
    <AbsoluteFill style={{ background: "#000" }}>
      <Fonts />

      {/* 0:00 hook */}
      <Sequence from={0} durationInFrames={s(8)}>
        <Shot dur={s(8)} inF={14} blur={false}>
          <Sequence durationInFrames={s(5.5)}><AI id="V1" dur={s(5.5)} fallback={BLACK} /></Sequence>
          <Sequence from={s(5.5)} durationInFrames={s(2.5)}><AI id="V7" dur={s(2.5)} fallback={BLACK} /></Sequence>
          <Vignette />
          <Sequence from={s(0.5)} durationInFrames={s(3.7)}><Title dur={s(3.7)}>Ask an AI to design something physical.</Title></Sequence>
          <Sequence from={s(4.4)} durationInFrames={s(3.5)}><Title dur={s(3.5)} size={96}>Nobody checks <span style={{ color: C.accent }}>the physics.</span></Title></Sequence>
        </Shot>
      </Sequence>

      {/* 0:08 logo */}
      <Sequence from={s(8)} durationInFrames={s(6)}><Shot dur={s(6)}><LogoBuild dur={s(6)} /></Shot></Sequence>
      <Sequence from={s(8)} durationInFrames={10}><Flash /></Sequence>

      {/* 0:14 the request, typed */}
      <Sequence from={s(14)} durationInFrames={s(10.5)}>
        <Shot dur={s(10.5)} outF={4}>
          <App part="run" at={typing - 10} keys={drift(s(10.5), 0.35, 0.2, 1.12, 1.05)} />
        </Shot>
      </Sequence>

      {/* 0:24 loading, then the design builds (real timing) */}
      <Sequence from={s(24.5)} durationInFrames={s(11.5)}>
        <Shot dur={s(11.5)} inF={4}>
          <App part="run" at={ev(0)} keys={drift(s(11.5), 0.45, 0.4, 1.0, 1.08)} />
        </Shot>
      </Sequence>

      {/* 0:36 the real world */}
      <Sequence from={s(36)} durationInFrames={s(8)}>
        <Sequence durationInFrames={s(2.7)}>
          <AI id="V3" dur={s(2.7)} fallback={BLACK} />
          <Stat dur={s(2.7)} label="Storm gust" value="−88%" color={C.fail} />
        </Sequence>
        <Sequence from={s(2.7)} durationInFrames={s(2.7)}>
          <AI id="V4" dur={s(2.7)} fallback={BLACK} />
          <Stat dur={s(2.7)} label="Summer heat" value="−18%" color={C.fail} />
        </Sequence>
        <Sequence from={s(5.4)} durationInFrames={s(2.6)}>
          <AI id="V5" dur={s(2.6)} fallback={BLACK} />
          <Stat dur={s(2.6)} label="Cloudy winter" value="−50%" color={C.fail} />
        </Sequence>
        <Vignette strength={0.4} />
        <Sequence from={0} durationInFrames={6}><Flash /></Sequence>
        <Sequence from={s(2.7)} durationInFrames={6}><Flash peak={0.5} /></Sequence>
        <Sequence from={s(5.4)} durationInFrames={6}><Flash peak={0.5} /></Sequence>
      </Sequence>

      {/* 0:44 the problem: it can't see far enough */}
      <Sequence from={s(44)} durationInFrames={s(7)}>
        <Shot dur={s(7)} inF={5}>
          <App part="sitefail" at={M("sitefail", "site", 0)} keys={drift(s(7), 0.36, 0.4, 1.04, 1.12)} />
          <Sequence from={s(0.8)} durationInFrames={s(6.2)}><Stat dur={s(6.2)} label="Drone range" value="0.75 km" color={C.fail} /></Sequence>
        </Shot>
      </Sequence>

      {/* 0:51 the fix: a screen, and the range more than doubles */}
      <Sequence from={s(51)} durationInFrames={s(10.5)}>
        <Sequence durationInFrames={s(3.5)}>
          <Shot dur={s(3.5)} inF={5}>
            <App part="run" at={ev(21.8)} keys={drift(s(3.5), 0.4, 0.42, 1.08, 1.14)} />
          </Shot>
        </Sequence>
        <Sequence from={s(3.5)} durationInFrames={s(7)}>
          <Shot dur={s(7)} inF={6}>
            <App part="site" at={M("site", "site", 30) - 6} rate={0.9} keys={drift(s(7), 0.36, 0.4, 1.04, 1.12)} />
            <Sequence from={s(0.3)} durationInFrames={s(6.7)}><Stat dur={s(6.7)} label="Drone range" value="2.2 km" from={0.75} to={2.2} unit=" km" color={C.pass} /></Sequence>
          </Shot>
        </Sequence>
        <Sequence from={s(3.5)} durationInFrames={6}><Flash peak={0.5} /></Sequence>
      </Sequence>

      {/* 1:01 it sizes everything else, keeps only what the simulations prove */}
      <Sequence from={s(61.5)} durationInFrames={s(14)}>
        <Shot dur={s(14)}>
          <App part="run" at={ev(24.3)} keys={[{ t: 0, x: 0.6, y: 0.5, z: 1.1 }, { t: s(14), x: 0.5, y: 0.55, z: 1.02 }]} />
          <Sequence from={s(11)} durationInFrames={s(3)}><Stat dur={s(3)} label="Real-world tests" value="5 / 5" color={C.pass} /></Sequence>
        </Shot>
      </Sequence>

      {/* 1:15 every number checked */}
      <Sequence from={s(75.5)} durationInFrames={s(8)}>
        <Shot dur={s(8)}>
          <App part="details" at={M("details", "expanded", 46) - 8} rate={0.6} keys={drift(s(8), 0.45, 0.6, 1.05, 1.12)} />
        </Shot>
      </Sequence>

      {/* 1:23 real hardware: Margin designed it, the team built it and flew a drone at it */}
      <Sequence from={s(83.5)} durationInFrames={s(7.5)}>
        <Shot dur={s(7.5)} inF={6} outF={5}>
          <Sequence durationInFrames={s(1.6)}><Photo src="field/aerial1.jpg" dur={s(1.6)} /></Sequence>
          <Sequence from={s(1.6)} durationInFrames={s(1.4)}><Clip src="field/fly.mp4" dur={s(1.4)} /></Sequence>
          <Sequence from={s(3.0)} durationInFrames={s(1.4)}><Photo src="field/drone.jpg" dur={s(1.4)} /></Sequence>
          <Sequence from={s(4.4)} durationInFrames={s(3.1)}><Photo src="field/console.jpg" dur={s(3.1)} from={1.1} to={1.3} ox="48% 40%" /></Sequence>
          <Vignette strength={0.35} />
          <Sequence from={s(0.2)} durationInFrames={s(2.8)}><Title dur={s(2.8)} size={92}>Margin designed it. We built it.</Title></Sequence>
          <Sequence from={s(3.1)} durationInFrames={s(1.4)}><Title dur={s(1.4)} size={92}>Tested in the field.</Title></Sequence>
          <Sequence from={s(4.6)} durationInFrames={s(2.9)}><Stat dur={s(2.9)} label="Live radio samples captured, none dropped" value="1.09 billion" from={0} to={1.09} unit=" billion" color={C.accent} /></Sequence>
        </Shot>
        {[1.6, 3.0, 4.4].map((t) => <Sequence key={t} from={s(t)} durationInFrames={5}><Flash peak={0.35} len={5} /></Sequence>)}
      </Sequence>

      {/* 1:29 not just radars */}
      {THINGS.map(([id, label], i) => (
        <Sequence key={id} from={s(91 + i * 2.9)} durationInFrames={s(2.9)}>
          <Shot dur={s(2.9)} inF={6} outF={4}>
            <App part={capOf(id)} at={M(capOf(id), id, i * 120)} keys={drift(s(2.9), 0.36, 0.38, 1.1, 1.16)} />
            <Sequence from={s(0.2)} durationInFrames={s(2.7)}><Title dur={s(2.7)} size={110} y={300}>{label}</Title></Sequence>
          </Shot>
        </Sequence>
      ))}
      <Sequence from={s(105.5)} durationInFrames={s(5)}>
        <Shot dur={s(5)}>
          <AbsoluteFill style={{ background: C.bg, alignItems: "center", justifyContent: "center" }}>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 540px)", gap: "34px 40px" }}>
              {[...THINGS.map(([id, label], i) => [id, label, M(capOf(id), id, i * 120) + 40] as const), ["run", "Passive radar", M("run", "done", 1544) + 30] as const].map(([part, label, at], i) => (
                <Tile key={label} part={part === "run" ? "run" : capOf(part)} at={at} w={540} h={330} label={label} delay={i * 3} />
              ))}
            </div>
          </AbsoluteFill>
          <Sequence from={s(1.9)} durationInFrames={s(3.1)}><Title dur={s(3.1)} size={110}>Describe anything.</Title></Sequence>
        </Shot>
      </Sequence>

      {/* 1:50 close */}
      <Sequence from={s(110.5)} durationInFrames={s(9.5)}>
        <Sequence durationInFrames={s(4)}>
          <Shot dur={s(4)} outF={10} blur={false}><AI id="V6" dur={s(4)} trim={s(1.5)} fallback={BLACK} push={0.04} /><Vignette strength={0.4} /></Shot>
        </Sequence>
        <Sequence from={s(4)} durationInFrames={s(5.5)}><Shot dur={s(5.5)} outF={1} blur={false}><LogoBuild dur={s(5.5)} final /></Shot></Sequence>
      </Sequence>

      <Grain />
      <Sound />
    </AbsoluteFill>
  );
};
