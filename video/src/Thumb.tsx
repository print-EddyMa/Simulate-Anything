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
    <div style={{ position: "absolute", left: 72, top: 0, bottom: 0, display: "flex", flexDirection: "column", justifyContent: "center" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 26 }}>
        <svg width="132" height="132" viewBox="0 0 64 64"><rect x="13" y="17" width="19" height="30" rx="3.5" fill={C.ink} /><rect x="34.5" y="17" width="9" height="30" rx="1.5" fill={C.accent} /><rect x="47" y="11" width="4" height="42" rx="2" fill={C.ink} /></svg>
        <div style={{ fontWeight: 700, fontSize: 150, color: C.ink, letterSpacing: -7, lineHeight: 1 }}>margin</div>
      </div>
      <div style={{ fontWeight: 700, fontSize: 84, color: C.accent, letterSpacing: -3, marginTop: 18, marginLeft: 8 }}>Create anything</div>
    </div>
  </AbsoluteFill>
);
