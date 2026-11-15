import { useEffect, useMemo, useRef, useState } from "react";
import type { ImageField, LineField } from "@/types";
import { useIsDarkDom } from "./useIsDark";

// categorical slots (light / dark), in fixed order
const SERIES_LIGHT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"];
const SERIES_DARK = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#0ca30c", "#9085e9", "#e66767"];

export function fmt(v: number | null | undefined) {
  if (v === null || v === undefined || !Number.isFinite(v)) return "–";
  const a = Math.abs(v);
  if (a === 0) return "0";
  if (a >= 1e5 || a < 1e-3) return v.toExponential(2).replace(/\.?0+e/, "e");
  return String(parseFloat(v.toPrecision(4)));
}

export const prettyUnit = (u: string) =>
  (u || "").replace(/degC/g, "°C").replace(/\^2/g, "²").replace(/\^3/g, "³").replace(/\*/g, "·");

function ticks(lo: number, hi: number, n: number) {
  if (lo === hi) { lo -= 1; hi += 1; }
  const raw = (hi - lo) / n, mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 5, 10].map((m) => m * mag).find((s) => s >= raw)!;
  const out: number[] = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + step * 1e-9; v += step) out.push(Math.abs(v) < step * 1e-9 ? 0 : v);
  return out;
}

function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [w, setW] = useState(480);
  useEffect(() => {
    if (!ref.current) return;
    const ro = new ResizeObserver(([e]) => setW(Math.max(260, e.contentRect.width)));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);
  return [ref, w] as const;
}

export function LineChart({ f }: { f: LineField }) {
  const dark = useIsDarkDom();
  const palette = dark ? SERIES_DARK : SERIES_LIGHT;
  const [ref, W] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const names = Object.keys(f.series).slice(0, 8);
  const x = f.x.map((v) => v ?? NaN);
  const H = 200, m = { l: 48, r: 12, t: 8, b: 32 };
  const all = names.flatMap((n) => f.series[n]).filter((v): v is number => v !== null && Number.isFinite(v));
  let ylo = Math.min(...all), yhi = Math.max(...all);
  const pad = (yhi - ylo || Math.abs(yhi) || 1) * 0.08;
  ylo -= pad; yhi += pad;
  const xlo = Math.min(...x.filter(Number.isFinite)), xhi = Math.max(...x.filter(Number.isFinite));
  const X = (v: number) => m.l + ((v - xlo) / (xhi - xlo || 1)) * (W - m.l - m.r);
  const Y = (v: number) => H - m.b - ((v - ylo) / (yhi - ylo)) * (H - m.t - m.b);
  const paths = useMemo(() => names.map((n) => {
    let d = "", pen = false;
    f.series[n].forEach((v, i) => {
      if (v === null || !Number.isFinite(v) || !Number.isFinite(x[i])) { pen = false; return; }
      d += `${pen ? "L" : "M"}${X(x[i]).toFixed(1)},${Y(v).toFixed(1)}`;
      pen = true;
    });
    return d;
  }), [f, W]); // eslint-disable-line react-hooks/exhaustive-deps

  const onMove = (e: React.PointerEvent<SVGRectElement>) => {
    const px = e.clientX - e.currentTarget.getBoundingClientRect().left + m.l;
    let best = 0;
    x.forEach((v, i) => { if (Math.abs(X(v) - px) < Math.abs(X(x[best]) - px)) best = i; });
    setHover(best);
  };
  return (
    <figure ref={ref} className="relative">
      <figcaption className="mb-1 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
        <span className="font-medium">{f.name}</span>
        {names.length > 1 && names.map((n, k) => (
          <span key={n} className="flex items-center gap-1.5 text-muted-foreground"><i className="inline-block h-0.5 w-3.5 rounded" style={{ background: palette[k] }} />{prettyUnit(n)}</span>
        ))}
      </figcaption>
      <svg width={W} height={H} className="block overflow-visible text-muted-foreground" role="img" aria-label={`${f.name} chart`}>
        {ticks(ylo, yhi, 4).map((t) => (
          <g key={t}>
            <line x1={m.l} x2={W - m.r} y1={Y(t)} y2={Y(t)} stroke="currentColor" strokeOpacity={0.15} />
            <text x={m.l - 6} y={Y(t) + 3.5} textAnchor="end" fontSize={10} fill="currentColor">{fmt(t)}</text>
          </g>
        ))}
        {ticks(xlo, xhi, Math.max(3, Math.floor(W / 90))).map((t) => (
          <text key={t} x={X(t)} y={H - m.b + 14} textAnchor="middle" fontSize={10} fill="currentColor">{fmt(t)}</text>
        ))}
        <text x={(m.l + W - m.r) / 2} y={H - 3} textAnchor="middle" fontSize={10} fill="currentColor">{prettyUnit(f.xlabel)}</text>
        {paths.map((d, k) => (
          <path key={k} d={d} fill="none" stroke={palette[k]} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
        ))}
        {hover !== null && (
          <g>
            <line x1={X(x[hover])} x2={X(x[hover])} y1={m.t} y2={H - m.b} stroke="currentColor" strokeOpacity={0.5} />
            {names.map((n, k) => {
              const v = f.series[n][hover];
              return v === null || !Number.isFinite(v) ? null : (
                <circle key={n} cx={X(x[hover])} cy={Y(v)} r={4} fill={palette[k]} stroke="var(--card)" strokeWidth={2} />
              );
            })}
          </g>
        )}
        <rect x={m.l} y={m.t} width={Math.max(0, W - m.l - m.r)} height={H - m.t - m.b} fill="transparent"
          onPointerMove={onMove} onPointerLeave={() => setHover(null)} />
      </svg>
      {hover !== null && (
        <div className="pointer-events-none absolute z-10 rounded-md border bg-popover px-2.5 py-1.5 text-xs shadow-md"
          style={{ left: Math.min(X(x[hover]) + 10, W - 160), top: 24 }}>
          <div className="text-muted-foreground">{prettyUnit(f.xlabel)} {fmt(x[hover])}</div>
          {names.map((n, k) => {
            const v = f.series[n][hover];
            return (
              <div key={n} className="flex items-center gap-2">
                <i className="inline-block h-0.5 w-3 rounded" style={{ background: palette[k] }} />
                <b className="font-semibold tabular-nums">{fmt(v)} {prettyUnit(f.unit)}</b>
                {names.length > 1 && <span className="text-muted-foreground">{prettyUnit(n)}</span>}
              </div>
            );
          })}
        </div>
      )}
    </figure>
  );
}

const BLUES = ["#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7", "#3987e5", "#2a78d6", "#256abf", "#1c5cab", "#184f95", "#104281", "#0d366b"];
const hex = (h: string) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
function ramp(stops: string[]) {
  const rgb = stops.map(hex);
  return (t: number) => {
    t = Math.min(1, Math.max(0, t)) * (rgb.length - 1);
    const i = Math.min(rgb.length - 2, Math.floor(t)), f = t - i;
    return rgb[i].map((c, k) => Math.round(c + (rgb[i + 1][k] - c) * f));
  };
}

export function Heatmap({ f }: { f: ImageField }) {
  const dark = useIsDarkDom();
  const canvas = useRef<HTMLCanvasElement>(null);
  const bar = useRef<HTMLCanvasElement>(null);
  const [tip, setTip] = useState<{ x: number; y: number; i: number; j: number } | null>(null);
  const nx = f.x.length, ny = f.y.length;
  const flat = f.values.flat().filter((v): v is number => v !== null && Number.isFinite(v));
  const lo = Math.min(...flat), hi = Math.max(...flat);
  const scale = useMemo(() => ramp(dark ? [...BLUES].reverse() : BLUES), [dark]);
  useEffect(() => {
    const c = canvas.current!, ctx = c.getContext("2d")!;
    const img = ctx.createImageData(nx, ny);
    for (let j = 0; j < ny; j++) for (let i = 0; i < nx; i++) {
      const v = f.values[i][j];
      const o = ((ny - 1 - j) * nx + i) * 4;
      const col = v === null ? [128, 128, 128] : scale((v - lo) / (hi - lo || 1));
      img.data.set([col[0], col[1], col[2], 255], o);
    }
    ctx.putImageData(img, 0, 0);
    const b = bar.current!, bctx = b.getContext("2d")!;
    const bi = bctx.createImageData(1, 128);
    for (let k = 0; k < 128; k++) bi.data.set([...scale(1 - k / 127), 255], k * 4);
    bctx.putImageData(bi, 0, 0);
  }, [f, scale, lo, hi, nx, ny]);
  const x = f.x as number[], y = f.y as number[];
  const aspect = Math.min(1.2, Math.max(0.2, Math.abs((y[ny - 1] - y[0]) / (x[nx - 1] - x[0] || 1))));
  return (
    <figure className="relative">
      <figcaption className="mb-1 text-xs font-medium">{f.name}{f.unit ? ` [${prettyUnit(f.unit)}]` : ""}</figcaption>
      <div className="flex gap-3">
        <div className="min-w-0 flex-1">
          <canvas
            ref={canvas} width={nx} height={ny} role="img"
            aria-label={`${f.name} from ${fmt(lo)} to ${fmt(hi)} ${f.unit}`}
            className="block w-full max-w-xl cursor-crosshair rounded"
            style={{ aspectRatio: `1 / ${aspect}`, imageRendering: "auto" }}
            onPointerMove={(e) => {
              const r = e.currentTarget.getBoundingClientRect();
              const i = Math.min(nx - 1, Math.max(0, Math.floor(((e.clientX - r.left) / r.width) * nx)));
              const j = Math.min(ny - 1, Math.max(0, ny - 1 - Math.floor(((e.clientY - r.top) / r.height) * ny)));
              setTip({ x: e.clientX - r.left, y: e.clientY - r.top, i, j });
            }}
            onPointerLeave={() => setTip(null)}
          />
          <div className="mt-1 flex max-w-xl justify-between text-[10px] text-muted-foreground">
            <span>{fmt(x[0])}</span><span>{prettyUnit(f.xlabel)}</span><span>{fmt(x[nx - 1])}</span>
          </div>
        </div>
        <div className="flex w-14 flex-col text-[10px] text-muted-foreground">
          <span>{fmt(hi)}</span>
          <canvas ref={bar} width={1} height={128} className="my-1 w-3 flex-1 rounded" />
          <span>{fmt(lo)}</span>
        </div>
      </div>
      {tip && (
        <div className="pointer-events-none absolute z-10 rounded-md border bg-popover px-2 py-1 text-xs shadow" style={{ left: tip.x + 12, top: tip.y + 20 }}>
          <b className="tabular-nums">{fmt(f.values[tip.i][tip.j])} {prettyUnit(f.unit)}</b>
          <div className="text-muted-foreground">{fmt(x[tip.i])}, {fmt(y[tip.j])}</div>
        </div>
      )}
    </figure>
  );
}
