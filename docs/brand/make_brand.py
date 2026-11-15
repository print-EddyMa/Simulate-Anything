"""Draw the Margin logo: the mark, the wordmark and the lockups.

The mark is a margin of safety drawn literally: a solid bar (the design),
then a gap (the margin, in the brand colour), then a thin line (the limit).
The wordmark is Inter at weight 600, converted to outlines so the SVGs render
identically everywhere.  Run:  python docs/brand/make_brand.py
"""
from pathlib import Path

from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont

HERE = Path(__file__).parent
ROOT = HERE.parent.parent
FONT = ROOT / "web/node_modules/@fontsource-variable/inter/files/inter-latin-wght-normal.woff2"

INK = {"dark": "#F2F5F8", "light": "#0E141B"}        # bar and limit line
ACCENT = {"dark": "#5CC8E6", "light": "#1673B5"}     # the margin
TILE = "#0E141B"


def mark(theme="dark", tile=False, size=64):
    """The mark in a 64x64 box: design bar, margin, limit."""
    ink, acc = INK[theme], ACCENT[theme]
    bg = f'<rect width="64" height="64" rx="15" fill="{TILE}"/>' if tile else ""
    if tile:
        ink, acc = INK["dark"], ACCENT["dark"]
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" '
            f'viewBox="0 0 64 64">{bg}'
            f'<rect x="13" y="17" width="19" height="30" rx="3.5" fill="{ink}"/>'
            f'<rect x="34.5" y="17" width="9" height="30" rx="1.5" fill="{acc}" opacity="0.9"/>'
            f'<rect x="47" y="11" width="4" height="42" rx="2" fill="{ink}"/>'
            f'</svg>')


def word_path(text, x0, baseline, size):
    font = TTFont(str(FONT))
    font = instantiateVariableFont(font, {"wght": 600})
    gs, cmap = font.getGlyphSet(), font.getBestCmap()
    upm = font["head"].unitsPerEm
    scale = size / upm
    hmtx = font["hmtx"]
    pen = SVGPathPen(gs)
    x = 0.0
    tracking = -0.025 * upm
    for ch in text:
        g = cmap[ord(ch)]
        tp = TransformPen(pen, (scale, 0, 0, -scale, x0 + x * scale, baseline))
        gs[g].draw(tp)
        x += hmtx[g][0] + tracking
    return pen.getCommands(), (x - tracking) * scale


def lockup(theme, tagline=True):
    ink = INK[theme]
    muted = "#8A96A3" if theme == "dark" else "#5B6673"
    d, w = word_path("margin", 66, 47, 46)
    parts = [mark(theme).replace('<svg xmlns="http://www.w3.org/2000/svg" width="64" '
                                 'height="64" viewBox="0 0 64 64">',
                                 '<g transform="translate(-9 -6.4) scale(1.2)">').replace(
        "</svg>", "</g>")]
    parts.append(f'<path d="{d}" fill="{ink}"/>')
    width = 66 + w + 8
    height = 64
    if tagline:
        td, tw = word_path("Create anything", 68, 84, 15)
        parts.append(f'<path d="{td}" fill="{muted}"/>')
        width = max(width, 68 + tw + 8)
        height = 96
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" height="{height}" '
            f'viewBox="0 0 {width:.0f} {height}">' + "".join(parts) + "</svg>")


def main():
    (HERE / "mark.svg").write_text(mark("light"))
    (HERE / "mark-dark.svg").write_text(mark("dark"))
    (HERE / "icon.svg").write_text(mark(tile=True, size=512))
    (HERE / "logo-light.svg").write_text(lockup("light"))
    (HERE / "logo-dark.svg").write_text(lockup("dark"))
    (HERE / "wordmark-light.svg").write_text(lockup("light", tagline=False))
    (HERE / "wordmark-dark.svg").write_text(lockup("dark", tagline=False))
    (ROOT / "web/public/favicon.svg").write_text(mark(tile=True, size=64))
    print("brand files written")


if __name__ == "__main__":
    main()
