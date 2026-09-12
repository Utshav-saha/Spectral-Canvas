---
version: 1
slug: "frontend-src-pages-landing-jsx"
primary_target: "frontend/src/pages/Landing.jsx"
related_targets: ["frontend/src/pages/Simulate.jsx","frontend/src/pages/Receive.jsx"]
---

## Scope

Replacement visual world for Spectral Canvas, applied to the three existing routes: `/` (Landing), `/simulate`, `/receive`. The four committed-but-unbuilt pages in PRODUCT.md are explicitly out of scope for this run and inherit this world when built.

Visitor mode: **Persuade** on Landing. **Operate** on Simulate and Receive — expression may never obscure the task, state, or affordance there.

## Audience and job

Two co-primary audiences (PRODUCT.md): a marker verifying the DSP is real and defensible, and a portfolio visitor on a public URL deciding in under a minute. Landing must serve the visitor's first two seconds without costing the marker any rigour. Simulate and Receive are working surfaces where the marker actually drives the demonstration.

## User decisions binding this run

- Ground is **dark**: a deep green room, single light source. Not cream, not warm.
- The console is the **Landing page's object only**. Simulate and Receive get their own layouts in this same world, not boxed inside a handheld.
- The console screen renders at **authentic DMG resolution** — a real 160×144 dot grid with a four-tone ramp. No high-resolution graphics inside the screen.
- Reference for construction technique only: `github.com/gonzalo-cordova-pou/voice-ai-benchmarks`. Borrow the layered box-shadow extrusion, asymmetric shell radius, bezel construction and dot-matrix overlay. **Do not copy its palette.**
- Content, layout, type and alignment are all in scope for rewriting. Product facts in PRODUCT.md are not.

## Direction contract

**THESIS.** A picture crossing a wire, painting down a dot-matrix screen one scanline at a time and logged like a press dispatch. This surface refuses the arrangement its category always ships: the centred gradient headline over an animated neon waveform with glass feature cards below. It equally refuses the warm-cream-and-serif opposite. The claim is not "we turn images into audio" stated in a headline; the claim is a transmission visibly arriving.

**OWN-WORLD.** A dark green room lit from one side. Grounds: `#0C1310` void, `#1B2620` recessed panel, `#2A3A2C` raised. The console shell is bone ivory `#E8E4D6` with a `#C4BEAA` underside, extruded by stacked box-shadows — inset white highlight, two offset solid plates, one soft ambient — never a border. Shell radius is asymmetric, `23px 23px 94px 23px`, the DMG's swept bottom-right. The LCD is a four-tone olive ramp — `#A8B78A` `#7F9166` `#4E6144` `#23301F` — behind a dual repeating-linear-gradient dot matrix at 2px/3px and an inset shadow that makes it a well, not a rectangle. Oxide red `#8C2F39` is the only saturated colour and appears only on things that can be pressed. Type: a condensed grotesque for display, a true bitmap face inside the LCD only, tabular mono for all readouts. Wire-service furniture throughout: hairline rules, a dispatch slug above every page, right-aligned tabular figures. Component language is physical — bevelled wells for inputs, silkscreen labels above them, LED dots for state.

**STORY.** The visitor understands within one viewport that an image and a sound are the same object here. They believe it because they watch a glyph paint itself line by line on a screen they can operate, not because a paragraph told them. They press SEND and go to Simulate carrying the mental model the console just taught them.

**FIRST VIEWPORT.** Dark room, full bleed. A dispatch slug pinned to the top edge: date, mode, `64×64`, sample rate, right-aligned in tabular mono against a hairline rule. Left column, starting at the optical third: the headline in condensed grotesque at `clamp(2.75rem, 5.5vw, 4.5rem)`, tight leading, two lines maximum. A four-line lede beneath it. Then two controls — oxide-red `SEND` as a physical bevelled button, `RECEIVE` as a flat ghost — sitting at the same baseline. Below them, the wire log: three past transmissions as tabular rows, each with duration, dimensions and an open/locked LED. Right, three-quarters across and overlapping the log's right edge: the DMG-01 at 546px, tilted a few degrees, its ivory shell catching the single light, its screen already painting a glyph downward scanline by scanline on load. The screen is the brightest thing on the page. Primary action is the SEND button, left column, above the fold at every width.

**FORM.** The Wire Desk — wirephoto press-transmission desk fused with the brief-pinned DMG handheld. Candidate 4 of the grounded list; the brief pinned the object, the ground and the material, and the roll's assignment binds the ritual, topology, state vocabulary and typographic furniture. Seed key `720b0998`, kind `assigned`, mode `persuade`, code-led.

Raises taken from declined challengers, each named for its donor:
- *from the consumer-app canon* — oxide red appears only where something can actually be pressed; it never decorates a heading, rule or border.
- *from the accretion-disk threshold* — the irreversible point is drawn, not captioned: where a damaged transmission stops being recoverable is marked on the trace itself.
- *from the Miura sheet* — one control propagates visibly across the whole linked field; changing the band re-counts all 64 lanes in front of the user rather than silently re-rendering.

Signature interaction: the console is really operable — d-pad moves the cursor on the pad, A encodes, B locks, START sends the glyph to Simulate. The screen paints line by line at DMG resolution, and the same scanline-arrival motion is the loading state on Receive.

Motion grammar: one orchestrated page-load in which the console settles and the screen wakes. After that, motion only answers an action. No scroll-triggered fade-ups.

**FINISH.** unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance

## Translations from the incumbent world

PRODUCT.md records a state-colour contract from `FRONTEND_GUIDE.md`: indigo = signal, aqua = open/recovered/safe, coral = locked/peak/alert. The contract survives this redesign; the hues are retranslated into the new world. Signal becomes the mid LCD tone `#7F9166`. Open/recovered becomes the brightest ramp tone `#A8B78A`, flashed on recovery. Locked/peak/alert becomes oxide red `#8C2F39`. The rule that colour means state, learned once and holding everywhere, is unchanged.

## Memorable moment

A glyph painting itself down a 160×144 dot-matrix screen, scanline by scanline, on a console the visitor can pick up and drive.

## Unresolved

- No accessibility standard has been set (PRODUCT.md open decision). Building to the documented floor plus AA contrast on all text; the LCD's four-tone ramp needs checking against its own ground.
- Simulate and Receive compositions are not yet specified beyond inheriting this world; they are Operate surfaces and get resolved during the build.
