# Submission sheet

Copy for the hackathon form. The README is the full write-up.

**Name:** Margin

**Tagline:** Create anything.

**Elevator pitch (under 200 characters):**
Describe anything. AI designs it, verified physics simulations test it against
the real world, and it's improved until every test passes with margin to spare.

**Repository:** https://github.com/print-EddyMa/Simulate-Anything

**Built with (tags):** claude, anthropic, claude-api, python, numpy, scipy,
react, typescript, vite, tailwindcss, three.js, react-three-fiber, shadcn-ui,
magic-ui, react-flow, motion, zustand

**Track:** General

## Demo video script (about 2 minutes)

1. **The problem (0:00-0:15).** "Ask an AI to design something physical and
   you get confident numbers nobody checked. Engineers can't use that."
2. **The request (0:15-0:30).** Open Margin, click *Passive radar to spot
   drones around a base*. The request appears at the top of the process panel.
3. **Design (0:30-0:55).** The brief with its requirements, the system
   diagram drawing itself, the node building in 3D part by part.
4. **The real world (0:55-1:20).** Five tests resolve. Point at the radar
   test: 0.75 km against 1.5 km, and why: leaked TV signal raises the noise
   floor by 44 dB, so a bigger antenna wouldn't help.
5. **The fix (1:20-1:40).** The mesh screen appears; detection jumps to
   2.2 km. The optimiser paints the enclosure white, grows the battery,
   stiffens the mast, trims material.
6. **Trust (1:40-1:55).** Open a test: margin of safety, the verification
   checklist, the *Reproduce* command. Switch to *Site* to show the coverage
   map with drones lighting up.
7. **Close (1:55-2:00).** "Margin. Create anything."

Record at 1440 x 900 or larger with a real GPU (the 3D site view is heavy for
software rendering). `margin serve`, then open http://127.0.0.1:8000.

## Before submitting

- [ ] Set `main` as the default branch (GitHub, Settings, General, Default branch).
- [ ] Add a repository description and topics (GitHub, About, the gear icon).
- [ ] Record the demo video and add its link to the README and the form.
- [ ] Confirm the CI badge is green on the Actions tab.
