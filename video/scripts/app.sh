#!/usr/bin/env bash
# Encode the app captures (JPEG frames on a virtual clock, 30 fps) to video for the film.
set -euo pipefail
cd "$(dirname "$0")/.."
CAP=capture/cap
for p in run site details sitefail things sensorcap; do
  [[ -f $CAP/$p/f00000.jpg ]] || continue
  ffmpeg -v error -y -framerate 30 -i "$CAP/$p/f%05d.jpg" -c:v libx264 -preset medium -crf 14 -pix_fmt yuv420p -g 15 "public/app/$p.mp4"
  cp "$CAP/$p/marks.txt" "public/app/$p.marks.txt"
  echo "$p: $(ls $CAP/$p | grep -c jpg) frames"
done
node scripts/manifest.mjs > /dev/null
