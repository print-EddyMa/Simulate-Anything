#!/usr/bin/env bash
# Usage: scripts/fetch.sh assets.txt   (lines: "V1 https://..."; also L1.., M1, S1..)
# Puts each file where the film looks for it, then refreshes src/assets.json.
set -euo pipefail
cd "$(dirname "$0")/.."
while read -r id url; do
  [[ -z "${id:-}" || "$id" == \#* ]] && continue
  case "$id" in V*) dir=ai;; L*) dir=vo;; M*) dir=music;; S*) dir=sfx;; *) echo "skip $id"; continue;; esac
  ext="${url%%\?*}"; ext="${ext##*.}"; [[ ${#ext} -gt 4 ]] && ext=mp4
  mkdir -p "public/$dir"
  curl -sSL --retry 3 -o "public/$dir/$id.$ext" "$url" && echo "$id -> public/$dir/$id.$ext"
done < "$1"
node scripts/manifest.mjs > /dev/null && echo "manifest updated"
