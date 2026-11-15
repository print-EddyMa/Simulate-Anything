export function fmtMass(g: number) {
  return g >= 1000 ? `${(g / 1000).toFixed(g >= 10000 ? 1 : 2)} kg` : `${g.toFixed(g >= 10 ? 0 : 1)} g`;
}
