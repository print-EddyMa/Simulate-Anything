# Credits: models, libraries, data and methods

## AI model

| | Used for |
|---|---|
| **Claude Opus 5.5** (Anthropic, via the Claude API) | The designer agent: turns a request, photo or imported part into a design, writes the tests, reads the results, fixes conceptual failures. Model id `claude-opus-5-5`, adaptive thinking, tool use. The physics is never done by the model. |
| **Claude Code** (Anthropic) | Used to build the project. |

## Engine (Python)

| Library | License | Used for |
|---|---|---|
| [NumPy](https://numpy.org) | BSD-3-Clause | Arrays and linear algebra in every solver |
| [SciPy](https://scipy.org) | BSD-3-Clause | Sparse solvers, eigenproblems, root finding, interpolation |
| [Anthropic Python SDK](https://github.com/anthropics/anthropic-sdk-python) | MIT | Calling Claude (optional extra) |
| [pytest](https://pytest.org) | MIT | Test suite |
| [fontTools](https://github.com/fonttools/fonttools) | MIT | Outlining the wordmark in `docs/brand/make_brand.py` |

Everything else (the finite-volume, finite-element, Navier-Stokes, FDTD and
thermal-network solvers, the unit system and the verification checks) is
written from scratch in this repository.

## Interface (web)

| Library | License | Used for |
|---|---|---|
| [React](https://react.dev) 19 | MIT | Interface |
| [TypeScript](https://www.typescriptlang.org) | Apache-2.0 | Language |
| [Vite](https://vite.dev) | MIT | Build |
| [Tailwind CSS](https://tailwindcss.com) 4 | MIT | Styling |
| [three.js](https://threejs.org), [React Three Fiber](https://github.com/pmndrs/react-three-fiber), [drei](https://github.com/pmndrs/drei), [postprocessing](https://github.com/pmndrs/postprocessing) | MIT | 3D model, site view, ambient occlusion and bloom |
| [shadcn/ui](https://ui.shadcn.com) on [Radix UI](https://www.radix-ui.com) | MIT | Interface primitives |
| [Magic UI](https://magicui.design) | MIT | Border beam, shine border, number ticker, shiny text |
| [React Flow (xyflow)](https://reactflow.dev) and [dagre](https://github.com/dagrejs/dagre) | MIT | System diagram and its layout |
| [Motion](https://motion.dev) | MIT | Animation |
| [Zustand](https://github.com/pmndrs/zustand) | MIT | State |
| [Lucide](https://lucide.dev) | ISC | Icons |
| [react-markdown](https://github.com/remarkjs/react-markdown), [remark-gfm](https://github.com/remarkjs/remark-gfm) | MIT | Rendering the designer's summary |
| [Inter](https://rsms.me/inter/) and [JetBrains Mono](https://www.jetbrains.com/lp/mono/) via Fontsource | SIL OFL 1.1 | Typefaces (bundled, no CDN) |

## Demo video

| Tool | Used for |
|---|---|
| [Remotion](https://www.remotion.dev) | Composing and rendering the video in React (Remotion license; free for individuals and small teams) |
| [Playwright](https://playwright.dev) | Frame-exact capture of the real app on a virtual clock |
| [FFmpeg](https://ffmpeg.org) | Encoding and loudness normalisation |
| [Higgsfield](https://higgsfield.ai): Seedance 2.5 | The cinematic shots (perimeter at dusk, storm, heat, winter, sunrise, wireframe) |
| Higgsfield: ElevenLabs text-to-speech (voice "Callum") | Narration |
| Higgsfield: Mirelo text-to-audio | Sound effects |
| Music | A synthesised score generated in code (`video/scripts/music.py`) |
| Field footage | Shot by the team during the outdoor test of the radar node |

Every app shot is a screen capture of the real app. The radar run is the app's
built-in offline replay of a recorded run (its simulations run live); the five
other designs were made live by the designer agent and tested in the app.

## Data

| Data | Source | Where |
|---|---|---|
| Lid-driven cavity centreline velocities, Re = 100, 400, 1000 | U. Ghia, K. N. Ghia, C. T. Shin, "High-Re solutions for incompressible flow using the Navier-Stokes equations and a multigrid method", *J. Comput. Phys.* 48 (1982) 387-411 | `lid_driven_cavity` known-solution check |
| Material properties (density, stiffness, strength, conductivity, heat capacity, service and ignition temperatures, resistivity) | Typical engineering-handbook values for common grades | `create_anything/materials.py`; replace with your supplier's datasheet |
| TV channel band (US ATSC UHF, 6 MHz channels) | Public broadcast standard | Passive radar example |

No proprietary or personal data is used.

## Methods

The simulations implement published physics; each is checked against the
exact or benchmark solution listed in its report.

| Area | Methods and references |
|---|---|
| Verification | Richardson extrapolation for grid convergence; method of manufactured solutions |
| Heat | Fourier conduction, fin equation, semi-infinite solid (erfc), Stefan-Boltzmann radiation, R-networks, Pennes bioheat equation, Graetz problem (Nu = 7.541 / 8.235), Sutton-Graves stagnation heating |
| Structures | Euler-Bernoulli beam finite elements, Timoshenko beam correction, Rayleigh quotient, energy method for impact |
| Fluids | Projection method on a staggered (MAC) grid, Hagen-Poiseuille, Colebrook equation with Haaland and Swamee-Jain cross-checks (Moody chart) |
| Electromagnetics | Electrostatics and magnetostatics by finite volumes, Yee-grid FDTD, Fresnel slab transfer matrix, Hankel-function line sources, Gauss and Ampere laws, Poynting energy balance |
| Radar | Bistatic radar equation with direct-signal leakage after cancellation, effective aperture, radio horizon (4/3 earth) |
| Energy and orbit | Kepler period, eclipse geometry, NOCT solar-cell estimate, solar and battery energy balance |
| Chemistry and earth | Danckwerts boundary conditions and the Wehner-Wilhelm solution, Fisher-KPP fronts with the Bramson correction, Thiem drawdown |

## Earlier work in this repository

A parallel version of the designer (named Plimsoll) was merged and then
replaced. Its load metric, catalogue-simulation tests, design editing and a
solver tolerance fix were carried over; see the git history.
