# Demo video source

The 2-minute demo (`docs/media/margin-demo.mp4`) is a [Remotion](https://www.remotion.dev)
composition. Every app shot is a frame-exact capture of the real app.

## How it is made

1. **Capture the app.** Start the app (`margin serve --port 8765`), then record each
   part on a virtual clock, so the 3D view renders every frame however slow the machine:

   ```bash
   node capture/capture.js capture/cap/run run
   ```

   Parts: `run` (the radar request, typed and designed), `sitefail` (coverage while the
   radar test fails), `site` (coverage after the fix), `details` (a test opened), and
   `things` (designs loaded through Edit JSON; set `THINGS="file.json|request|zoom;;..."`).
   `vclock.js` replaces the page's clock and timers so time only moves when the
   script says so.
2. **Encode the captures:** `scripts/app.sh` (writes `public/app/*.mp4` and their marks).
3. **Add the generated media:** `scripts/fetch.sh assets.txt` with lines like
   `V1 https://...` (V = video, L = narration, M = music, S = sound effects). Field
   footage goes in `public/field/`. Without music, `scripts/music.py public/music/fallback.wav`
   writes the synthesised score.
4. **Render:** `npm install`, then `scripts/render.sh` (1920x1080, 30 fps, H.264 + AAC).

`scripts/manifest.mjs` lists what is in `public/` so the composition uses whatever
exists. The timeline, captions and motion graphics are in `src/Film.tsx`.
