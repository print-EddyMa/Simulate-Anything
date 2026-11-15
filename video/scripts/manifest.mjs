// Lists the media in public/ (AI clips, voice, music, sfx, app captures) with
// durations, so the film can use what exists and fall back on the rest.
import fs from "node:fs";
import path from "node:path";
import { execFileSync } from "node:child_process";

const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), "..");
const pub = path.join(root, "public");
const dur = (f) => {
  try {
    return +execFileSync("ffprobe", ["-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", f]).toString().trim();
  } catch { return 0; }
};
const scan = (dir) => {
  const out = {};
  const d = path.join(pub, dir);
  if (!fs.existsSync(d)) return out;
  for (const f of fs.readdirSync(d).sort()) {
    if (!/\.(mp4|mov|webm|mp3|wav|m4a|aac|ogg)$/i.test(f)) continue;
    const id = f.replace(/\.[^.]+$/, "").split(/[_\s-]/)[0].toUpperCase();
    out[id] = { file: `${dir}/${f}`, dur: dur(path.join(d, f)) };
  }
  return out;
};
const marks = {};
for (const part of ["run", "site", "details", "sitefail", "things", "sensorcap"]) {
  const m = path.join(pub, "app", `${part}.marks.txt`);
  if (!fs.existsSync(m)) continue;
  marks[part] = Object.fromEntries(fs.readFileSync(m, "utf8").trim().split("\n").map((l) => {
    const [n, name] = l.split(" ");
    return [name, +n];
  }));
}
const app = scan("app");
const manifest = { ai: scan("ai"), vo: scan("vo"), sfx: scan("sfx"), music: scan("music"), app, marks };
fs.writeFileSync(path.join(root, "src", "assets.json"), JSON.stringify(manifest, null, 1));
console.log(JSON.stringify(manifest, null, 1));
