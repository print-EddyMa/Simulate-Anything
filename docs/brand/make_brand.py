"""Generate the Plimsoll mark and wordmark SVGs (run from the repo root).

The mark is the Plimsoll line itself: a load line through a ring, with water
below it.  The wordmark is a monoline lowercase "plimsoll" whose "o" is the
same ring and line, so the one object appears in the name as well.
"""
import re

SEA, OCEAN, SIGNAL = "#3464a0", "#1aa1c9", "#fe8c2c"
BONE, INK = "#eeefe9", "#12171a"
SW = 5.4                                   # stroke width of the letters


def num(v):
    return f"{v:.2f}".rstrip("0").rstrip(".")


def mark(ring):
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" role="img" aria-label="Plimsoll">
  <title>Plimsoll</title>
  <defs>
    <clipPath id="pm-in"><circle cx="32" cy="32" r="19.4"/></clipPath>
    <linearGradient id="pm-water" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="{OCEAN}"/><stop offset="1" stop-color="{SEA}"/>
    </linearGradient>
  </defs>
  <g clip-path="url(#pm-in)">
    <path d="M6 40.5 C13 37.6 20 37.6 27 40.5 S41 43.4 48 40.5 S62 37.6 69 40.5 V60 H6 Z" fill="url(#pm-water)"/>
  </g>
  <circle cx="32" cy="32" r="21.4" fill="none" stroke="{ring}" stroke-width="5"/>
  <path d="M4.5 32 H59.5" stroke="{SIGNAL}" stroke-width="5" stroke-linecap="round"/>
</svg>
'''


def icon():
    """App icon / favicon: the mark on a graphite tile, readable on any tab bar."""
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" role="img" aria-label="Plimsoll">
  <title>Plimsoll</title>
  <defs>
    <clipPath id="pi-in"><circle cx="32" cy="32" r="15.2"/></clipPath>
    <linearGradient id="pi-water" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="{OCEAN}"/><stop offset="1" stop-color="{SEA}"/>
    </linearGradient>
  </defs>
  <rect width="64" height="64" rx="15" fill="#0b0e0e"/>
  <g clip-path="url(#pi-in)">
    <path d="M10 38.6 C15.5 36.4 21 36.4 26.5 38.6 S37.5 40.8 43 38.6 S54 36.4 59.5 38.6 V54 H10 Z" fill="url(#pi-water)"/>
  </g>
  <circle cx="32" cy="32" r="17" fill="none" stroke="{BONE}" stroke-width="4.2"/>
  <path d="M9.5 32 H54.5" stroke="{SIGNAL}" stroke-width="4.2" stroke-linecap="round"/>
</svg>
'''


def wordmark(ink):
    # baseline 44, x-height 26, ascender 13, descender 56.5
    x, out = 0.0, []

    def path(d):
        out.append(f'<path d="{d}"/>')
    path(f"M{num(x + 3)} 35 V56.5")                                   # p
    out.append(f'<circle cx="{num(x + 12)}" cy="35" r="9"/>')
    x += 32
    path(f"M{num(x)} 13 V44")                                         # l
    x += 10.5
    path(f"M{num(x)} 27.5 V44")                                       # i
    out.append(f'<circle cx="{num(x)}" cy="17.2" r="3.3" fill="{ink}" stroke="none"/>')
    x += 10.5
    path(f"M{num(x)} 33.6 V44 M{num(x)} 33.6 a7.2 7.2 0 0 1 14.4 0 V44 "  # m
         f"M{num(x + 14.4)} 33.6 a7.2 7.2 0 0 1 14.4 0 V44")
    x += 28.8 + 10.5
    s = x                                                             # s
    path(f"M{num(s + 13.2)} 29.6 C{num(s + 11.6)} 26.4 {num(s + 2.4)} 25.6 {num(s + 1.2)} 30.6 "
         f"C{num(s + 0.2)} 34.9 {num(s + 13.6)} 33.8 {num(s + 13.7)} 39.1 "
         f"C{num(s + 13.8)} 44.6 {num(s + 3.2)} 45.2 {num(s + 0.6)} 40.6")
    x += 14 + 16
    cx = x + 9                                                        # o: ring + load line
    out.append(f'<circle cx="{num(cx)}" cy="35" r="9"/>')
    out.append(f'<path d="M{num(cx - 14)} 35 H{num(cx + 14)}" stroke="{SIGNAL}"/>')
    x += 18 + 16
    path(f"M{num(x)} 13 V44")                                         # l
    x += 10.5
    path(f"M{num(x)} 13 V44")                                         # l
    return "\n    ".join(out), x + 4


def logo(ink):
    body, w = wordmark(ink)
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="-4 6 {num(w + 4)} 56" role="img" aria-label="plimsoll">
  <title>plimsoll</title>
  <g fill="none" stroke="{ink}" stroke-width="{SW}" stroke-linecap="round" stroke-linejoin="round">
    {body}
  </g>
</svg>
'''


if __name__ == "__main__":
    for name, text in {"mark.svg": mark(INK), "mark-dark.svg": mark(BONE), "icon.svg": icon(),
                       "logo-light.svg": logo(INK), "logo-dark.svg": logo(BONE)}.items():
        with open(f"docs/brand/{name}", "w", encoding="utf-8") as fh:
            fh.write(text)
    print("written")
