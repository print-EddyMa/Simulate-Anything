import { AbsoluteFill, Img, staticFile } from "remotion";
const C = { bg: "#07090c", ink: "#f2f5f8", dim: "#8a96a3", accent: "#5cc8e6", pass: "#4fd18b" };
const SANS = "Inter, system-ui, sans-serif";
export const Thumb = () => (
  <AbsoluteFill style={{ background: C.bg, fontFamily: SANS, overflow: "hidden" }}>
    <style>{`@font-face { font-family: Inter; src: url(${staticFile("fonts/inter.woff2")}) format("woff2"); font-weight: 100 900; }`}</style>
    {/* the board, big, bleeding off the right */}
    <Img src={staticFile("thumb/board.jpg")} style={{ position: "absolute", right: -120, top: 40, width: 980, transform: "scale(1.25)", transformOrigin: "70% 50%" }} />
    <AbsoluteFill style={{ background: "linear-gradient(90deg, #07090c 34%, rgba(7,9,12,0.55) 58%, rgba(7,9,12,0) 80%)" }} />
    <AbsoluteFill style={{ background: "radial-gradient(ellipse at 75% 55%, rgba(92,200,230,0.18), transparent 55%)" }} />
    <div style={{ position: "absolute", left: 64, top: 70, display: "flex", alignItems: "center", gap: 16 }}>
      <svg width="64" height="64" viewBox="0 0 64 64"><rect x="13" y="17" width="19" height="30" rx="3.5" fill={C.ink} /><rect x="34.5" y="17" width="9" height="30" rx="1.5" fill={C.accent} /><rect x="47" y="11" width="4" height="42" rx="2" fill={C.ink} /></svg>
      <div style={{ fontWeight: 600, fontSize: 52, color: C.ink, letterSpacing: -2 }}>margin</div>
    </div>
    <div style={{ position: "absolute", left: 64, top: 200, width: 640 }}>
      <div style={{ fontWeight: 800, fontSize: 96, lineHeight: 0.98, letterSpacing: -4, color: C.ink }}>AI designs it.</div>
      <div style={{ fontWeight: 800, fontSize: 96, lineHeight: 0.98, letterSpacing: -4, color: C.accent, marginTop: 6 }}>Physics<br />proves it.</div>
    </div>
    <div style={{ position: "absolute", left: 64, bottom: 64, display: "flex", alignItems: "center", gap: 14, padding: "12px 22px", borderRadius: 999, background: "rgba(79,209,139,0.12)", border: "2px solid rgba(79,209,139,0.6)" }}>
      <svg width="30" height="30" viewBox="0 0 24 24"><path d="M5 12.5l4.5 4.5L19 7.5" stroke={C.pass} strokeWidth="3" fill="none" strokeLinecap="round" strokeLinejoin="round" /></svg>
      <span style={{ fontWeight: 700, fontSize: 34, color: C.pass }}>Every test passes</span>
    </div>
  </AbsoluteFill>
);
