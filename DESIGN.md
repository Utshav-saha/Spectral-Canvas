---
name: Spectral Canvas
description: A dark green room lit from one side, with a bone-ivory handheld on the desk and its four-tone screen the brightest thing present.
colors:
  void: "#0C1310"
  room: "#121C17"
  panel: "#1B2620"
  raised: "#2A3A2C"
  well: "#0F1814"
  line: "#33453A"
  line-soft: "#223129"
  bone: "#E8E4D6"
  bone-hi: "#F4F1E7"
  bone-lo: "#C4BEAA"
  bone-edge: "#A8A292"
  bone-ink: "#5C6152"
  lcd-0: "#A8B78A"
  lcd-1: "#7F9166"
  lcd-2: "#4E6144"
  lcd-3: "#23301F"
  oxide: "#8C2F39"
  oxide-hi: "#A63B46"
  oxide-lo: "#66202A"
  ink: "#E4EADD"
  ink-2: "#A9B8A4"
  ink-3: "#869A82"
  signal: "#7F9166"
  open: "#A8B78A"
  locked: "#8C2F39"
typography:
  hero:
    fontFamily: "Archivo, ui-sans-serif, system-ui, sans-serif"
    fontSize: "clamp(2.75rem, 5.5vw, 4.5rem)"
    fontWeight: 800
    lineHeight: 0.94
    letterSpacing: "-0.035em"
    fontVariation: "'wdth' 84"
  headline:
    fontFamily: "Archivo, ui-sans-serif, system-ui, sans-serif"
    fontSize: "clamp(1.75rem, 3.2vw, 2.5rem)"
    fontWeight: 700
    lineHeight: 1.05
    letterSpacing: "-0.022em"
    fontVariation: "'wdth' 88"
  title:
    fontFamily: "Archivo, ui-sans-serif, system-ui, sans-serif"
    fontSize: "1.0625rem"
    fontWeight: 700
    lineHeight: 1.35
    letterSpacing: "-0.01em"
    fontVariation: "'wdth' 92"
  body:
    fontFamily: "Archivo, ui-sans-serif, system-ui, sans-serif"
    fontSize: "1rem"
    fontWeight: 400
    lineHeight: 1.6
    letterSpacing: "normal"
    fontVariation: "'wdth' 100"
  readout:
    fontFamily: "Spline Sans Mono, ui-monospace, SFMono-Regular, monospace"
    fontSize: "0.75rem"
    fontWeight: 500
    lineHeight: 1.5
    letterSpacing: "0.08em"
    fontFeature: "'tnum' 1"
  slug:
    fontFamily: "Spline Sans Mono, ui-monospace, SFMono-Regular, monospace"
    fontSize: "0.6875rem"
    fontWeight: 400
    lineHeight: 1.5
    letterSpacing: "0.06em"
    fontFeature: "'tnum' 1"
  lcd:
    fontFamily: "dmgFont 5x7 bitmap (canvas-only)"
    fontSize: "7px"
    fontWeight: 400
    lineHeight: 1
    letterSpacing: "1px"
rounded:
  xs: "2px"
  sm: "4px"
  md: "8px"
  lg: "14px"
  shell: "23px 23px 94px 23px"
spacing:
  "1": "4px"
  "2": "8px"
  "3": "12px"
  "4": "14px"
  "5": "20px"
  "6": "26px"
  "7": "34px"
  "8": "52px"
  "9": "72px"
  "10": "96px"
components:
  button-primary:
    backgroundColor: "{colors.oxide}"
    textColor: "{colors.bone-hi}"
    typography: "{typography.body}"
    rounded: "{rounded.sm}"
    padding: "13px 24px"
  button-primary-disabled:
    backgroundColor: "{colors.raised}"
    textColor: "{colors.ink-3}"
    rounded: "{rounded.sm}"
    padding: "13px 24px"
  button-ghost:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.ink}"
    typography: "{typography.body}"
    rounded: "{rounded.sm}"
    padding: "13px 24px"
  input:
    backgroundColor: "{colors.well}"
    textColor: "{colors.ink}"
    typography: "{typography.readout}"
    rounded: "{rounded.sm}"
    padding: "12px 14px"
  field-label:
    textColor: "{colors.ink-3}"
    typography: "{typography.readout}"
  panel:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.ink}"
    rounded: "{rounded.lg}"
  module-head:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.ink-3}"
    typography: "{typography.readout}"
    padding: "15px 20px 14px"
  drop:
    backgroundColor: "{colors.well}"
    textColor: "{colors.ink-3}"
    rounded: "{rounded.md}"
    padding: "34px 26px"
    height: "320px"
  filecard:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
    padding: "14px 16px"
  led:
    backgroundColor: "{colors.raised}"
    size: "7px"
  nav:
    backgroundColor: "{colors.void}"
    textColor: "{colors.ink-2}"
    typography: "{typography.body}"
    height: "62px"
  alert-error:
    backgroundColor: "{colors.oxide}"
    textColor: "#EFC3C4"
    rounded: "{rounded.sm}"
    padding: "12px 15px"
  alert-ok:
    backgroundColor: "{colors.signal}"
    textColor: "{colors.lcd-0}"
    rounded: "{rounded.sm}"
    padding: "12px 15px"
---

# Design System: Spectral Canvas

## Overview

**Creative North Star: "The Wire Desk"**

A wirephoto press-transmission desk in a dark green room, lit from one side, with a bone-ivory handheld console sitting on it. Everything that is not the console is the room: recessed panels, hairline rules, tabular figures, silkscreen labels. The console's four-tone olive screen is the brightest surface on the site, and every other element is calibrated so it stays that way. The world is physical rather than graphic — things are wells cut into panels, plates extruded from a single upper-left light, and buttons that actually travel down when pressed.

The register is instrumentation, not consumer software. Every number is a measurement and is set in tabular mono; every heading is condensed news type; every state is said by a lit dot rather than a coloured pill. Density is high on the operating surfaces (Send, Receive) and asymmetric on Landing, where the claim reads down the left and the object sits over the right edge of the log the way a device sits on paper. The one saturated colour in the entire palette is oxide red, and it is rationed to things a hand can press.

The world declines the category's default arrangement: no centred gradient headline, no animated neon waveform hero, no glass feature cards. It equally declines the warm-cream-and-serif opposite. Ground is dark green, and it stays dark.

**Key Characteristics:**
- One light source, upper left, and all depth obeys it
- Oxide red exclusively on pressable things
- Every readout in tabular mono; every measurement right-aligned
- A four-tone LCD ramp treated as a closed material
- Hairline rules and dispatch slugs instead of cards and containers
- Wells are recessed, panels are lifted, and borders never fake either

## Colors

A dark green room, a bone-ivory object in it, one olive screen, and exactly one saturated accent.

### Primary
- **Oxide Red** (`{colors.oxide}`): The only saturated colour on the site. It appears on primary buttons, the transport play control, the console's A/B buttons and its bezel accent line — all pressable. It never colours a heading, a rule, a border or a decorative accent.
- **Oxide Bright** (`{colors.oxide-hi}`): The lit top of a pressed control's gradient, the alert-error dot, and the locked LED.
- **Oxide Deep** (`{colors.oxide-lo}`): The extrusion plate beneath a primary button — the visible thickness the button loses when it travels down on press.

### Secondary — the LCD ramp
- **Screen Highlight** (`{colors.lcd-0}`): The brightest tone. The focus ring, the recovered-state LED, active toggle text, the paint-line, and the caret. Off the console it is used as a signal of live/open state, never as a body text colour on a dark panel.
- **Screen Mid** (`{colors.lcd-1}`): The signal tone. Waveform trace, maker's-mark bars, range-input accent, drop-target hover ring.
- **Screen Shade** (`{colors.lcd-2}`): Selection background, completed-stage LED, active tool background, focus stroke on inputs.
- **Screen Ink** (`{colors.lcd-3}`): The darkest ramp tone — the ink of the panel, and the ground of the idle scope well.

### Tertiary — the shell
- **Bone** (`{colors.bone}`), **Bone Highlight** (`{colors.bone-hi}`), **Bone Underside** (`{colors.bone-lo}`), **Bone Edge** (`{colors.bone-edge}`): The console's ivory case, its lit face, its shaded underside, and the first extrusion plate. Bone Highlight is also the text colour on oxide buttons — the only place shell colour leaves the console.

### Neutral
- **Void** (`{colors.void}`): The page ground and the nav's translucent base.
- **Room** (`{colors.room}`): The next tone up; scope tooltip ground.
- **Panel** (`{colors.panel}`): Every lifted surface — modules, cards, menus.
- **Raised** (`{colors.raised}`): A control lifted off a panel — active rocker segments, tool chips, unlit LEDs, scrollbar thumbs.
- **Well** (`{colors.well}`): Every recessed surface — inputs, drop targets, canvas stages, switch tracks.
- **Line** (`{colors.line}`) and **Line Soft** (`{colors.line-soft}`): The two hairline weights. Line-soft rules structure; line strokes an interactive edge.
- **Ink / Ink-2 / Ink-3** (`{colors.ink}`, `{colors.ink-2}`, `{colors.ink-3}`): Primary text (13.9:1 on room), secondary prose (8.3:1), and labels/hints (5.8:1 on room, 5.2:1 on panel).

### Named Rules
**The Pressable Red Rule.** Oxide is worn only by things that can be pressed. A disabled primary control gives the colour back — it becomes a raised neutral plate with a hairline inset — rather than staying red and dimming.

**The Closed Ramp Rule.** Inside the LCD panel, only the four ramp tones exist. No fifth tone, no antialiased intermediate, no webfont. Everything drawn there is drawn at 160×144 and rendered `image-rendering: pixelated`.

**The Colour-Means-State Rule.** Colour states are inherited and fixed: signal is `{colors.signal}`, open/recovered is `{colors.open}`, locked/peak/alert is `{colors.locked}`. Learned once, they hold on the log, the file cards, the stage rails and the console's battery lamp.

## Typography

**Display Font:** Archivo (variable, width axis 62–125; with `ui-sans-serif, system-ui, sans-serif`)
**Body Font:** Archivo, width axis at 100
**Label/Mono Font:** Spline Sans Mono (with `ui-monospace, SFMono-Regular, monospace`)
**Screen Font:** a hand-authored 5×7 bitmap face, 6px advance, canvas-only

**Character:** A newsprint grotesque narrowed on its own width axis for headlines, against a monospace that exists purely to set measurements. The pairing reads as a wire desk: the copy is written by a person, the figures are produced by an instrument.

### Hierarchy
- **Hero** (800, `clamp(2.75rem, 5.5vw, 4.5rem)`, 0.94, `'wdth' 84`, `-0.035em`): The Landing claim, two lines maximum, balanced.
- **Headline** (700, `clamp(1.75rem, 3.2vw, 2.5rem)`, 1.05, `'wdth' 88`): Section titles inside `.rule-head`, always followed by a hairline that runs to the edge of the frame.
- **Title** (700, `1.0625rem`, `'wdth' 92`): Pipeline step names, module-level headings in prose.
- **Body** (400, `1rem`/`1.0625rem`, 1.6, `'wdth' 100`): Ledes and paragraphs. Measure held to 46–68ch (`--measure: 68ch`).
- **Readout** (500, `0.75rem`, `0.07–0.12em`, uppercase, tabular): Field labels, module heads, stage rails, head readouts. Mono, always.
- **Slug** (400, `0.6875rem`, `0.06em`, uppercase, tabular): The dispatch rail at the top of every page.
- **LCD** (5×7 bitmap, 6px advance, 26 characters per line): Inside the console screen only.

### Named Rules
**The Measurement Rule.** Anything that is a number, a filename, a rate, a dimension or a code is set in Spline Sans Mono with tabular figures and right-aligned in its column. Anything that is a sentence is set in Archivo.

**The Three Registers Rule.** Each face has one territory and never crosses: Archivo for display and UI, Spline Sans Mono for readouts, the bitmap face inside the LCD canvas. The site footer's slogan is prose and therefore gives the mono face back.

**The Width Axis Rule.** Display type is condensed on the variable axis (`'wdth' 84–92`), never faked with `transform: scaleX()` or a separate condensed family. Body sits at 100.

## Layout

A single centred frame (`--shell-w: 1240px`) with `28px` inline padding, over a fixed two-radial-gradient light wash on the page ground. Every page opens with the `.slug` dispatch rail — flex row, baseline-aligned, a flexible spacer pushing the last item to the right edge, closed by a hairline.

Landing's hero is a genuine 12-column grid (`repeat(12, minmax(0, 1fr))`, `0 20px` gap) in which the copy occupies columns 1–7 and the console 7–13, both pinned to `grid-row: 1` so they share column 7 and overlap. The overlap is load-bearing: it is what makes the console an object resting on the log rather than an illustration beside it. The wire log is held to `660px` with a `104px` right gutter on every readable cell so the shell's shadow falls across rules but never across a figure. At `≤1180px` the grid collapses to `1fr / 470px` and the gutter is released; at `≤980px` the copy becomes `display: contents` and the children reorder explicitly — claim, lede, console, actions, log — because a 390px screen should not lead with an uncaptioned device.

The Operate surfaces use asymmetric two-column module grids (`1.55fr / 1fr` on Send, `400px / 1fr` on Receive), both collapsing to one column at `1080px`. Vertical rhythm runs on a 4px base with a common set of steps: `14px` intra-component, `20px` module padding, `26px` grid gap, `34–52px` between blocks, `72–110px` page top and bottom.

Breakpoints in use: `1180px`, `1080px`, `980px`, `900px`, `700px`, `620px`, `480px`, `420px`.

**The Rail Rule.** The dispatch slug and the head readout both depend on a left run and a right anchor. When the row wraps, the anchor takes its own line rather than colliding with the tail of the run.

## Elevation & Depth

Depth is modelled, not suggested. There is one light source, upper left, and every shadow in the system is consistent with it. Surfaces are either lifted (panel tone, an inset white top-edge highlight, a soft cast below) or recessed (well tone, an inset dark top shadow, a faint bottom highlight). Borders never do elevation work: an interactive edge is drawn as `inset 0 0 0 1px`, which is a stroke, not a frame.

Interactive controls carry a solid, zero-blur plate as the *body* of an extrusion — the visible thickness of the button — and that plate is always accompanied by a soft cast in the same stack. Pressing translates the element down by exactly the plate's height and swaps the stack for an inset one, so the travel is real. A bare offset shadow with no soft cast underneath is not part of this vocabulary.

### Shadow Vocabulary
- **Lift 1** (`0 1px 0 rgba(255,255,255,0.04), 0 2px 6px rgba(0,0,0,0.30)`): Small lifted objects — file cards at rest.
- **Lift 2** (`0 1px 0 rgba(255,255,255,0.05), 0 6px 14px -6px rgba(0,0,0,0.45), 0 20px 44px -24px rgba(0,0,0,0.70)`): Panels, modules, dropdown menus.
- **Inset Well** (`inset 0 1px 3px rgba(0,0,0,0.55), inset 0 -1px 0 rgba(255,255,255,0.03)`): Every recessed surface — inputs, drop targets, switch tracks, canvas stages.
- **Button extrusion** (`inset 0 1px 0 rgba(255,255,255,0.18), inset 0 -2px 0 {oxide-lo}, 0 3px 0 {oxide-lo}, 0 6px 14px -6px rgba(0,0,0,0.7)`): Primary controls; press travels `3px`.
- **Shell extrusion** (`inset 3px 3px 4px rgba(255,255,255,0.85), inset -6px -4px 6px rgba(120,116,100,0.42), 9px 13px 0 -3px {bone-edge}, 14px 19px 0 -6px #8E8878, 18px 28px 26px -12px rgba(0,0,0,0.55), 34px 52px 74px -26px rgba(0,0,0,0.75)`): The console only. Two solid plates give the case its side wall, two soft casts put it on the desk.
- **Screen glow** (`0 0 36px -6px {colors.lcd-glow}`): Added only when the console is powered on.
- **Lit LED** (`0 0 6px rgba(168,183,138,0.75), inset 0 1px 1px rgba(255,255,255,0.4)`): The bloom of a lamp that is actually on.

### Named Rules
**The One Light Rule.** Every shadow falls down and to the right of a single upper-left source. No element carries a shadow that contradicts it, and no element uses a border to imply a raise.

**The Real Travel Rule.** A pressed control moves. The press state translates by the exact height of its extrusion plate and replaces the outer stack with an inset one, so the button appears to bottom out.

## Shapes

Radii are small and functional: `2px` on focus rings, `4px` on controls and inputs, `8px` on inner surfaces (drop targets, cards, menus, canvas stages), `14px` on modules and panels. Circles are reserved for things that are physically round — LED dots, the transport play button, colour swatches, the d-pad hub.

The one exception is the house shape: the console's asymmetric `23px 23px 94px 23px` shell, the DMG's swept bottom-right corner, echoed once by its bezel (`12px 12px 46px 12px`). It appears on the console and nowhere else, which is what keeps the object singular.

Rules are hairlines, one pixel, in `{colors.line-soft}` for structure and `{colors.line}` for an active edge. Section headings carry a rule that grows from the heading to the edge of the frame (`.rule-head::after`, `flex: 1`).

**The Singular Corner Rule.** The swept `94px` corner belongs to the console. Nothing else in the system may borrow it.

## Components

### Buttons
- **Shape:** Slightly softened rectangle (`{rounded.sm}` / 4px), `13px 24px` padding, Archivo 650 at `'wdth' 92` with `0.02em` tracking.
- **Primary:** A vertical oxide gradient (`oxide-hi → oxide`) with a `3px` oxide-deep plate under it and text in bone highlight. Hover lightens the gradient's top stop; active translates `3px` down and inverts to an inset stack.
- **Disabled primary:** Gives the colour back — raised neutral fill, ink-3 text, a hairline inset stroke, no plate, `cursor: not-allowed`.
- **Ghost:** Panel fill, a `{colors.line}` inset stroke, a `2px` well-tone plate. Hover raises to `{colors.raised}`; active travels `2px`. Disabled fades to `0.45` opacity.
- **Focus:** Global — `2px solid {colors.lcd-0}`, `3px` offset, `2px` radius. Never suppressed.

### Cards / Containers
- **Panel / Module** (`{rounded.lg}`): Panel fill, inset white top-edge highlight plus Lift 2. A module is a three-part object: a silkscreen head (mono uppercase, `0.12em`, hairline below), a `20px` body, and a foot rail (hairline above) holding the action.
- **File card** (`{rounded.md}`, `14px 16px`): Panel fill, hairline inset, Lift 1. Hover raises `1px` to Lift 2; selected swaps the stroke to `{colors.lcd-2}` with a `3px` translucent LCD ring. Carries a `42px` well-inset badge, a two-line body, and a state tag (LCD-tinted when open, oxide-tinted when locked).

### Inputs / Fields
- **Style:** Well fill, no border, `{rounded.sm}`, `12px 14px`, set in mono with tabular figures. Inset Well plus a hairline inset stroke.
- **Label:** A silkscreen strip above the field — mono, `0.75rem`, `0.08em`, uppercase, ink-3, `8px` clear.
- **Focus:** Stroke shifts to `{colors.lcd-2}` and a `3px` translucent LCD ring appears outside; the well shadow stays.
- **Select:** Native chevron removed and redrawn as two hairline gradient triangles in ink-3.
- **Switch:** A `38×21` well track with an olive-gradient thumb; checked tints the track with translucent signal and the thumb becomes the LCD gradient.
- **Rocker (`.modeswitch`, `.toggle`):** A well-inset trough holding mono uppercase segments; the active segment lifts to `{colors.raised}` with LCD-highlight text.

### Navigation
- Sticky, `62px` (`56px` on mobile), a translucent void ground with `blur(14px) saturate(1.2)` and a hairline underline. Brand is Archivo 700 at `'wdth' 88` beside an eight-lane bar mark drawn from the product's own structure. Links are ink-2, rising to ink; the active/hover underline is an LCD-highlight hairline that scales from `scaleX(0)` — a transform, never a width animation. The dropdown is a `268px` panel with an inset stroke and Lift 2, entering on opacity plus a `5px` rise.

### Status
- **LED dot** (`7px` circle): The single state vocabulary. Unlit is raised tone with an inset shadow; `is-open` is the open tone with an outward bloom; `is-locked` is oxide bright with its own bloom; a completed stage is flat LCD shade with no bloom. Used on the wire log, file cards, stage rails and the console's battery lamp.
- **Alert:** A translucent tinted block with a matching inset stroke and a `8px` glowing dot at the text's first line. Error is oxide-tinted with `#EFC3C4` text; ok is signal-tinted with LCD-highlight text.
- **Stage rail:** Mono uppercase steps divided by hairlines, each led by an LED; done is ink-2, current is LCD highlight.

### The Console (DMG-01)
The Landing page's object and a working miniature of the product. A `546px` bone-ivory shell tilted `-1.1deg`, extruded by the shell shadow stack, with case seams drawn as bordered pseudo-elements, a power switch straddling the top edge, and a separate dark bezel the screen is set into. The screen is a real `<canvas width="160" height="144">` drawn a pixel at a time in the four-tone ramp with the 5×7 bitmap face, overlaid by a `3px/1px` dual repeating-gradient dot matrix at `0.34` opacity and a single-source glass glare. Controls are physical: a d-pad in a dished well with one authored SVG arrow rotated four ways, oxide A/B buttons on a rotated pad, rotated rubber pills for START/SELECT, and raked speaker slots. Below `700px` the tilt is removed and the object gives back its side margins; below `420px` the speaker is dropped.

### The Scope
A well cut into a module (`200px`, Inset Well, `crosshair`) with a signal-tone trace inside. Hover produces a mono tooltip with tabular figures on a near-opaque room ground. Transport is a `42px` circular oxide button with the same extrusion and travel as a primary button, an LCD-accented range input, and a tabular volume readout. Idle, the scope is not a blank card: it is a powered panel — LCD-ink ground, the same dot matrix, and a `3.6s` linear scanline sweep.

## Do's and Don'ts

### Do:
- **Do** put every number, filename, rate and dimension in Spline Sans Mono with tabular figures, right-aligned in its column.
- **Do** condense display type on Archivo's variable width axis (`'wdth' 84–92`) and leave body at 100.
- **Do** build depth from a stacked shadow consistent with one upper-left light: an inset highlight on the lit edge, an inset shade on the shadowed one, and a soft cast below.
- **Do** recess anything a user types or draws into (`{colors.well}` plus Inset Well) and lift anything that groups content (`{colors.panel}` plus Lift 2).
- **Do** say state with the `.led` dot and the inherited state colours — signal, open, locked — everywhere state appears.
- **Do** make a press physically travel: translate by the plate height and swap to an inset stack.
- **Do** open every page with the dispatch slug on its hairline, carrying real transmission data.
- **Do** keep the LCD canvas to its four tones and its bitmap face, rendered pixelated at 160×144.
- **Do** give an outline back on focus — `2px solid {colors.lcd-0}` at `3px` offset — on every interactive element.
- **Do** honour `prefers-reduced-motion`: the sweep and the paint-line are removed outright, not merely shortened.

### Don't:
- **Don't** use oxide red on anything that cannot be pressed — not a heading, a rule, a border, a badge, or an icon that is not a control.
- **Don't** keep a disabled control red. It gives the colour back.
- **Don't** use a border to imply elevation. Strokes are `inset 0 0 0 1px`; lift comes from shadow.
- **Don't** ship a solid zero-blur offset shadow on its own. A hard plate is the body of an extrusion and must sit in a stack with a soft cast; a bare block shadow is not this world's device.
- **Don't** introduce a fifth tone, an antialiased webfont, or a high-resolution graphic inside the LCD panel.
- **Don't** reuse the `23px 23px 94px 23px` swept corner on anything but the console.
- **Don't** set a measurement in Archivo or a sentence in the mono face.
- **Don't** add scroll-triggered fade-ups. Entrance motion is limited to the console's arrival on Landing; after that, motion answers an action — Receive's `paint-down` reveal with its leading line, and the idle scope's sweep.
- **Don't** animate width, height or layout properties for state feedback; use `transform` and `opacity` (the nav underline scales, it does not grow).
- **Don't** let expression obscure task, state or affordance on Send and Receive — the module language carries the world there, not decoration.
- **Don't** change the import order on Receive: `Simulate.css` first, then `Receive.css`. `.module`, `.drop`, `.filecard`, `.lockfields` and `.switch` are defined in the former and shared.
