import * as THREE from "three";
import type { Material } from "@/types";

// ---------- procedural textures (no network) ----------
const texCache = new Map<string, THREE.Texture>();

function canvasTexture(key: string, w: number, h: number, draw: (g: CanvasRenderingContext2D) => void) {
  const hit = texCache.get(key);
  if (hit) return hit;
  const c = document.createElement("canvas");
  c.width = w;
  c.height = h;
  draw(c.getContext("2d")!);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  t.anisotropy = 8;
  texCache.set(key, t);
  return t;
}

/** Monocrystalline cells: dark blue squares, silver bus bars, thin gaps. */
export function solarTexture(cols: number, rows: number) {
  return canvasTexture(`solar-${cols}x${rows}`, 64 * cols, 64 * rows, (g) => {
    g.fillStyle = "#c9ced6";
    g.fillRect(0, 0, 64 * cols, 64 * rows);
    for (let i = 0; i < cols; i++) {
      for (let j = 0; j < rows; j++) {
        const x = i * 64 + 2, y = j * 64 + 2;
        const grad = g.createLinearGradient(x, y, x + 60, y + 60);
        grad.addColorStop(0, "#1b2f5c");
        grad.addColorStop(1, "#0f1c3d");
        g.fillStyle = grad;
        g.beginPath();
        g.roundRect(x, y, 60, 60, 6);
        g.fill();
        g.strokeStyle = "rgba(200,210,230,0.45)";
        g.lineWidth = 1;
        for (const f of [0.33, 0.66]) {
          g.beginPath();
          g.moveTo(x + 60 * f, y);
          g.lineTo(x + 60 * f, y + 60);
          g.stroke();
        }
        g.strokeStyle = "rgba(160,175,205,0.18)";
        for (let k = 1; k < 10; k++) {
          g.beginPath();
          g.moveTo(x, y + 6 * k);
          g.lineTo(x + 60, y + 6 * k);
          g.stroke();
        }
      }
    }
  });
}

/** Solder mask with traces. */
export function pcbTexture() {
  return canvasTexture("pcb", 512, 512, (g) => {
    g.fillStyle = "#0f5132";
    g.fillRect(0, 0, 512, 512);
    const rnd = mulberry(7);
    g.strokeStyle = "rgba(170,210,150,0.35)";
    g.lineWidth = 3;
    for (let i = 0; i < 70; i++) {
      let x = rnd() * 512, y = rnd() * 512;
      g.beginPath();
      g.moveTo(x, y);
      for (let s = 0; s < 4; s++) {
        if (rnd() > 0.5) x += (rnd() - 0.5) * 160;
        else y += (rnd() - 0.5) * 160;
        g.lineTo(x, y);
      }
      g.stroke();
    }
    g.fillStyle = "#d4b25a";
    for (let i = 0; i < 90; i++) {
      g.beginPath();
      g.arc(rnd() * 512, rnd() * 512, 3.5, 0, Math.PI * 2);
      g.fill();
    }
  });
}

export function mulberry(seed: number) {
  let a = seed;
  return () => {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// ---------- materials ----------
const FALLBACK: Record<string, string> = {
  aluminium: "#c9ced3", steel: "#8d949b", stainless: "#b4b9be", copper: "#c98452", brass: "#c9a858",
  titanium: "#9aa1a8", polycarbonate: "#dfeaf0", abs: "#d9d9d6", pla: "#e8e4d8", petg: "#cfe3ee",
  nylon: "#f1efe6", acrylic: "#e6f2f5", plywood: "#d8b98a", pine: "#e2c79b", carbon_fibre: "#3b3f44",
};

export interface Finish {
  color: string;
  metalness: number;
  roughness: number;
  clearcoat: number;
  family: string;
}

export function finishFor(materialId: string, mats: Record<string, Material>, override?: string): Finish {
  const m = mats[materialId];
  const family = (m?.family as string) ?? (["aluminium", "steel", "stainless", "copper", "brass", "titanium"].includes(materialId) ? "metal" : "plastic");
  const color = override ?? (m?.color as string) ?? FALLBACK[materialId] ?? "#c8c8c8";
  if (family === "metal") {
    const rough = materialId === "steel" ? 0.45 : materialId === "aluminium" ? 0.36 : materialId === "titanium" ? 0.4 : 0.3;
    // slightly below 1 so the base colour still reads under studio lighting
    return { color, metalness: 0.82, roughness: rough, clearcoat: 0, family };
  }
  if (family === "wood") return { color, metalness: 0, roughness: 0.82, clearcoat: 0, family };
  if (family === "composite") return { color, metalness: 0.2, roughness: 0.35, clearcoat: 0.8, family };
  return { color, metalness: 0, roughness: 0.42, clearcoat: 0.25, family };
}

export const STATUS_COLOR = { pass: "#22c55e", fail: "#ef4444", pending: "#38bdf8" } as const;

/** Cell colour scale for coverage maps: dark below threshold, cyan to white above. */
export function snrColor(v: number, thr: number, lo: number, hi: number): [number, number, number, number] {
  if (!Number.isFinite(v)) return [0, 0, 0, 0];
  if (v < thr) {
    const t = Math.max(0, Math.min(1, (v - lo) / (thr - lo)));
    return [30 + 40 * t, 50 + 60 * t, 90 + 80 * t, Math.round(25 + 70 * t)];
  }
  const t = Math.max(0, Math.min(1, (v - thr) / Math.max(hi - thr, 1)));
  const stops: [number, number, number][] = [[24, 160, 220], [60, 220, 240], [200, 250, 255]];
  const p = t * (stops.length - 1);
  const i = Math.min(stops.length - 2, Math.floor(p));
  const f = p - i;
  const c = stops[i].map((a, k) => a + (stops[i + 1][k] - a) * f) as [number, number, number];
  return [c[0], c[1], c[2], Math.round(150 + 90 * t)];
}
