## Inspiration

Ask an AI to design something physical, like a bracket, a drone or a circuit board, and you get an answer that sounds right. The numbers are confident, but nobody checked the physics. An engineer can't build from that, and a wrong answer in hardware costs real money or breaks in the field.

We wanted an AI you could trust with hardware: one that designs the thing *and* proves it works, the way an engineer would. It should show its work, test against the conditions the thing will actually face, and only call a design ready when every number has been checked.

## What it does

You describe anything in one sentence, or send a photo or a 3D file. **Margin**:

1. **Designs it.** It writes the brief, sketches the system, and builds the design part by part in 3D, with real materials and dimensions.
2. **Tests it against the real world.** It runs storms, heat, drops, loads, vibration, current, airflow, battery endurance and radio coverage. Each test is a physics simulation that checks its own work.
3. **Fixes it.** When a test fails for a design reason, Claude changes the design. When it fails on size, an optimiser tries changes and keeps only the ones the simulations prove.
4. **Reports a margin of safety** for every result, with the exact command that reproduces it.

$$
\text{Margin of safety} = \frac{\text{allowable}}{\text{actual}} - 1
$$

$+0.25$ means 25% to spare, and anything below $0$ fails. A design is **ready to build** only when every test passes on verified simulations.

**Example.** "Design a passive radar that spots small drones around a base: receive-only, off-grid, survives storms and summer heat, sees a small quadcopter 1.5 km out."

- **First design:** 1 of 5 tests pass. Detection reaches only **0.75 km**. The simulation shows that the TV signal leaking into the back of the antenna sets the noise floor, so a bigger antenna would not help.
- **The fix:** a wire-mesh screen. Range jumps to **2.2 km**.
- **The optimiser:** it paints the enclosure white, sizes the battery for three cloudy days and stiffens the mast for a 35 m/s gust. All 5 tests then pass.
- **The field test:** we built the screen frame from Margin's design and tested it outdoors with a drone.

Margin then designed a survey drone, a robot arm, a rugged smartwatch, a go-kart motor driver board and a solar air-quality sensor live. Each has 89 to 106 parts and passes every one of its tests.

## How we built it

- **A self-checking physics engine** (Python, NumPy, SciPy), written from scratch. It has 32 simulations covering finite volume, finite elements, Navier–Stokes, FDTD electromagnetics, thermal networks, bistatic radar and battery energy balance. Every run must pass a verification protocol:
  - units and dimensional analysis;
  - known exact or benchmark solutions;
  - residuals, conservation and stability limits;
  - a three-grid convergence study with Richardson extrapolation.

$$
p = \frac{\ln\left(|f_2 - f_1| / |f_3 - f_2|\right)}{\ln 2},
\qquad
f_\text{exact} \approx f_3 + \frac{f_3 - f_2}{2^{p} - 1}
$$

- **Real-world tests** (12 types) that turn a design into simulation inputs and pass limits. The radar test uses the public bistatic radar equation, with the leaked direct signal added to the noise:

$$
\text{SNR} = \frac{P_t\, G_t\, G_r\, \lambda^2\, \sigma\, T_i}{(4\pi)^3\, R_t^2\, R_r^2\, L\, \left(k T_0 F + N_\text{leak}\right)}
$$

- **The designer agent** is Claude Opus 5.5 with tool use: set the diagram, build and update the design, run tests, optimise. Claude never does the physics. It decides *what* to build and *which* tests matter, reads the results, and fixes failures for a reason it states.
- **An optimiser** moves tunable dimensions both ways. It enforces containment, so electronics stay inside their box, and trims material only while every test keeps at least +5% margin.
- **A live interface** (React, TypeScript, three.js with React Three Fiber, Tailwind, shadcn/ui, React Flow). It streams every step: reasoning, the system diagram drawing itself, parts appearing in 3D, tests resolving, a radar coverage map with drones, and the optimiser's before-and-after.
- **The demo video** was cut in Remotion from frame-exact captures of the real app, recorded on a virtual clock with Playwright.

## Challenges we ran into

- **Trust.** It's easy to make a simulation produce a number, and hard to know the number is right. We built the verification protocol first and made every result carry its own checks. Untrusted numbers never count as a pass.
- **The optimiser cheated.** Early on it "passed" the heat test by shrinking the enclosure until the electronics were outside it. We added containment rules, plus a minimum margin so it can't trim a part right to the edge.
- **Giving the AI a reason, not just a number.** The radar failure wasn't fixable by sizing. Claude had to read *why* the noise floor was high (leakage, not receiver noise) and change the geometry.
- **Large designs got cut off.** A 100-part design plus Claude's reasoning ran past the reply limit, and the run quietly ended as if it had finished. We switched to streaming with a larger limit, and made the app say clearly when a reply is cut off.
- **Recording the 3D app.** Software rendering ran at about 1 frame per second. We replaced the page's clock so time only advances when a frame is captured, which gives smooth 30 fps footage on any machine.

## Accomplishments that we're proud of

- Every number Margin shows is verified, reproducible and stated as a margin of safety.
- The radar went from 0.75 km to 2.2 km for a physical reason the simulation exposed, and we built it and took it outside.
- Five very different objects, each designed from one sentence, in detail, passing every test.
- 259 automated tests, and a free offline mode that runs every simulation live without an API key.

## What we learned

- An AI agent is most useful for hardware when it is paired with tools that can say *no*. The checks made Claude's designs better, not slower.
- A margin of safety reads clearly for engineers and non-engineers alike.
- The hardest bugs were honesty bugs: places where the system claimed more than it had shown.

## What's next

- Import real CAD (STEP) and export manufacturing-ready drawings and BOMs.
- More physics: fatigue, fluid–structure interaction, full 3D FEM.
- Calibrate the tests against measured hardware, starting with our field node.
- Team review and versioned design history for real projects.
