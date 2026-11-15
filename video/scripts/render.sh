#!/usr/bin/env bash
# Full render: 1920x1080, 30 fps, H.264 + AAC.
set -euo pipefail
cd "$(dirname "$0")/.."
node scripts/manifest.mjs > /dev/null
npx remotion render src/index.ts Margin out/margin-demo.mp4 --codec h264 --crf 17 --audio-codec aac --audio-bitrate 256k "$@"
