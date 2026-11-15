# Margin: Create anything

**Track:** Climate  
**Team:** Eddy Ma  
**Code:** https://github.com/print-EddyMa/margin  ·  **Demo video:** see the README

## The problem

A city that wants to measure its own air quality and climate impact needs hardware out in the street: solar-powered sensors on lamp posts that keep working through storms, summer heat and weeks of winter cloud. Small city and community teams rarely have an engineer to check that a design will survive. If they ask an AI chatbot to design one, they get an answer that sounds right, but nobody has checked the physics.

## What we built

Margin is an AI engineer that designs a physical thing from one sentence and then proves it works. A city's environment officer could type: *"Design a solar-powered air-quality sensor that mounts on a street lamp post and runs through a week of cloudy winter weather."* What comes out is a full 3D design, with every part, its material and size, and a list of real-world tests: wind, sun, heat, battery endurance, drops and loads. Every test reports a **margin of safety** (allowable / actual − 1). A design is marked ready to build only when every test passes on simulations that check their own work.

## What we added

This is our own idea. We wrote the physics engine (32 simulations), the real-world tests, the optimiser, the Claude agent and the live 3D interface during the hackathon.

## How it works

- **AI (the main job):** Claude Opus 5.5 with tool use designs the object, chooses the tests, reads the results and redesigns when a test fails. Claude never computes the physics.
- **Physics:** a Python engine (NumPy, SciPy). Every simulation run is checked for units, known exact solutions, conservation and stability. Each also runs a three-grid convergence study with Richardson extrapolation. A result that fails these checks never counts as a pass.
- **Optimiser:** it tries size and material changes and keeps only the ones the simulations prove. It never shrinks a box until its contents fall outside it.
- **Data:** no training data. Material properties are typical handbook values. Benchmarks are published solutions (for example Ghia et al., 1982, for fluid flow).

## Results

**Baseline:** the AI's first design, unchecked, which is what a chatbot would hand you. We counted how many of each design's own tests that first version passes, then how many the version Margin finished with passes.

| Design (one sentence each) | First design (baseline) | After Margin |
|---|---|---|
| Solar air-quality sensor for a lamp post | 4 / 5 | 5 / 5 |
| Passive radar node, off-grid | 1 / 5 | 5 / 5 |
| Survey drone | 1 / 6 | 6 / 6 |
| Desktop robot arm | 4 / 6 | 6 / 6 |
| Rugged smartwatch | 1 / 6 | 6 / 6 |
| Go-kart motor driver board | 2 / 5 | 3 / 3* |
| **Total** | **13 / 34 (38%)** | **all pass** |

\*We later redesigned the board as the bare board assembly, which has three board-level tests.

So more than 60% of the tests failed on the unchecked AI designs. The failures were real: a radar that saw only 0.75 km of the 1.5 km it needed, and a battery that fell to 10% charge (it needed 20%) after three cloudy winter days. Margin found each failure and fixed it. The radar reached 2.2 km after Margin added a mesh screen against signal leakage.

**Where it fails**

- The models are simplified: beam theory, lumped heat and free-space radio. Several tests flagged that the parts were too short and stubby for beam theory to be accurate, so those margins are approximate.
- A clean result means the equations were solved correctly, not that they are the right model of the real hardware. Nothing has been calibrated against measurements yet.
- The watch needed a second request before its drop test passed.
- Our first detailed designs were cut off mid-reply because they ran past the model's output limit. We fixed this by streaming with a larger limit, and the app now reports a cut-off instead of claiming it finished.

**Field test:** the radar's receiver and its data logging come from a project our team built before the hackathon. This weekend we used Margin on it and filmed the outdoor test with a drone. We report no detection results from that test, because the logs used a placeholder site location.

## What we'd do next

Calibrate the tests against measurements from a real deployed sensor, and import real CAD files (STEP) so a city can check designs it already has.

## Sources

- **AI:** [Claude Opus 5.5 via the Anthropic API](https://docs.anthropic.com) (the designer agent); [Claude Code](https://claude.com/claude-code) (coding assistant used to build the project).
- **Video:**
  - [Higgsfield](https://higgsfield.ai): Seedance 2.5 video, ElevenLabs voice, Mirelo sound effects.
  - [Remotion](https://www.remotion.dev), [Playwright](https://playwright.dev) and [FFmpeg](https://ffmpeg.org).
- **Libraries:**
  - [NumPy](https://numpy.org), [SciPy](https://scipy.org) and [pytest](https://pytest.org).
  - [React](https://react.dev), [three.js](https://threejs.org), [React Three Fiber](https://github.com/pmndrs/react-three-fiber), [Tailwind CSS](https://tailwindcss.com), [shadcn/ui](https://ui.shadcn.com), [React Flow](https://reactflow.dev) and [Vite](https://vite.dev).
- **Data:** Ghia, Ghia and Shin, *J. Comput. Phys.* 48 (1982) 387–411 (cavity-flow benchmark); handbook material properties; the public US ATSC TV channel plan.
- The full list of libraries, licences and methods is in [docs/CREDITS.md](https://github.com/print-EddyMa/margin/blob/main/docs/CREDITS.md).
