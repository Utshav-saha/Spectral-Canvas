# Spectral Canvas — Frontend Guide

React + plain JavaScript + plain CSS, built with Vite. No Tailwind, no
TypeScript, no component library — the visual identity is the point, and a
utility framework would push it toward looking like everything else.

---

## 1. Tooling: what actually helps

**Claude Code** is the right tool for the rest of this build. It works across
your whole repo rather than one file at a time, which matters because a change
to `tokens.css` ripples through eight component stylesheets. Run it in the
`frontend/` folder and it can read the existing components before writing new
ones, so additions match what's already there.

**The `frontend-design` skill** is already active in this environment and is
what shaped the palette and type decisions below. Worth knowing it exists: if
you ask for "a nicer landing page" you'll get generic output, but if you ask for
"a landing page for a signals lab instrument, audience is a CS department demo"
the skill grounds the result in the subject.

**Modern Web Guidance plugin** (in your org catalog, card below) keeps React and
Vite patterns current — useful since a lot of React advice online is written for
class components and older hooks patterns.

**What I'd skip:** component libraries (MUI, Chakra, shadcn). They'd fight the
design system and make the project look like a template. The whole visual
argument here is that it *doesn't*.

---

## 2. Design system

### Palette

```css
--paper:      #EDF1F8   /* pale periwinkle — page ground */
--paper-deep: #E1E8F5   /* recessed wells, insets */
--surface:    #FBFCFE   /* raised panels */
--ink:        #14182F   /* deep navy — all primary text */
--slate:      #656D94   /* secondary text, axis labels */
--indigo:     #4B3FCF   /* primary action, signal trace */
--aqua:       #0E9C9C   /* open state, recovered image */
--coral:      #F06B47   /* locked state, peaks, playhead */
--lcd:        #D2DCEE   /* instrument screen */
```

**Why cool, not warm.** The reference site is cream + olive + crimson. That warm
palette is also the single most common look in AI-generated design right now, so
copying it would make the project read as templated twice over. Cool periwinkle
also does something the brief needs: it makes indigo and aqua traces pop as the
only saturated things on screen, which is exactly right for a page about signals.

**Colour carries state, consistently.** Indigo = the signal itself. Aqua = open
/ recovered / safe. Coral = locked / peak / alert. Once a user learns that on
the landing bench, it holds on every page.

### Type

| Role | Face | Why |
|---|---|---|
| Display + UI | **Bricolage Grotesque** 400/500/700/800 | Variable grotesque with real character. Technical without being cold. Deliberately not the serif the reference uses. |
| Data | **JetBrains Mono** | Frequencies, durations, file names, chassis silkscreen. Tabular numerals so readouts don't jitter. |
| Hover stats | **Caveat** | Reserved for *one* job: the waveform hover tooltip. It's a margin note on the trace, not interface chrome. |

Three faces is one more than usual, but the third is scoped to a single element,
which keeps it a deliberate accent rather than drift.

### Layout principles

- **Asymmetric hero**, not a centred one. The instrument sits on the right like
  an object on a desk you could reach over and touch.
- **Spend boldness in one place.** The bench is the memorable thing; everything
  else stays quiet and disciplined.
- **Motion answers actions.** One orchestrated page-load (the bench fades and
  rotates into place), and after that every animation responds to something the
  user did. No scroll-triggered fade-ups — that's the generic default.
- **Structural devices encode information.** The numbered pipeline on the
  landing page is numbered because it genuinely is a four-stage sequence in
  order. Nothing else gets numbers.

---

## 3. File structure

```
frontend/
├── index.html                 font links live here
├── vite.config.js             /api proxy to FastAPI
├── package.json
└── src/
    ├── main.jsx
    ├── App.jsx                routes + footer
    ├── styles/
    │   ├── tokens.css         every colour, size, font — change here first
    │   ├── global.css         resets, .btn, .panel, .input, .alert
    │   └── app.css            footer
    ├── api/client.js          every fetch call
    ├── components/
    │   ├── NavBar.jsx/.css
    │   ├── SignalBench.jsx/.css     the landing instrument
    │   ├── WaveformScope.jsx/.css   canvas + player + hover stats
    │   └── DoodleCanvas.jsx/.css    the paint surface
    └── pages/
        ├── Landing.jsx/.css
        ├── Simulate.jsx/.css
        └── Receive.jsx/.css
```

`Receive.jsx` imports `Simulate.css` **before** its own, because `.drop`,
`.filecard`, `.lockfields` and `.switch-note` are defined there and shared.
Keep that import order or the receive page loses its styling.

---

## 4. The SC-01 bench

The landing page centrepiece, and a miniature of the entire product. Draw a
glyph on the 8×8 pad → **Encode** turns rows into frequency lanes → **Lock**
scatters them → **Open** brings them back. That is the whole pipeline in four
button presses, with no backend call at all.

**Why an instrument and not a games console.** The reference borrows a Game Boy.
Keeping the *elements* (a live screen, a control cluster, a physical button row,
keyboard play) while changing the object to a bench analyser means the borrowed
structure works for us instead of reading as a copy — and a lab instrument is
what this subject actually looks like.

**What each state teaches:**

| State | Screen | Point being made |
|---|---|---|
| `glyph` | square dark pixels | this is a picture |
| `spectrum` | indigo bars, squashed vertically, lane labels 8.0k–1.0k | the same data read as frequency over time |
| `locked` | coral cells, scattered, LED turns coral | the picture is still in there, just permuted |
| `recovered` | aqua cells, back in place | nothing was lost |

The frequency labels (`8.0k … 1.0k`) sit beside *both* the glyph and the
spectrum on purpose. A row **is** a frequency — showing the labels in glyph mode
is what makes that land.

`shuffleGrid()` mirrors `security.py`: two independent permutations, rows and
columns, from one seed. It's a visual analogue, not the real cipher, but it
scatters the same way.

Keyboard: arrows move the cursor, space toggles, `E` / `L` / `O` / `C` fire the
buttons. The pad is one tab stop with `role="grid"`; individual cells are
`tabIndex={-1}` so you don't tab through 64 buttons.

---

## 5. WaveformScope

Draws the min/max envelope the backend sends, not raw samples. Three layers:

1. a translucent RMS body (how loud, on average)
2. the peak envelope over it (how loud, at most)
3. a coral playhead during playback

Click to seek. Hover reads that bucket's time / peak / RMS / dominant frequency
in Caveat. The canvas is sized by `devicePixelRatio` so it stays sharp on
retina displays.

`accent="aqua"` on the Receive page, indigo on Send — small thing, but it
reinforces which direction you're going.

---

## 6. DoodleCanvas

Three brushes that genuinely differ, not three names for the same stroke:

- **Pencil** — `lineWidth * 0.34`, butt cap, alpha 0.92. Hard and thin.
- **Paint** — full weight, round cap, opaque.
- **Highlighter** — `lineWidth * 2.4`, square cap, alpha 0.30. Overlaps build up.

Plus line / box / circle (live preview by repainting the pre-stroke snapshot
each frame), eraser, undo (24-step dataURL history), and colour swatches with a
custom picker.

**Smoothing matters more here than in a normal paint app.** Every stroke becomes
a column of tones, so a jittery line becomes noisy audio. The quadratic-midpoint
smoothing is cheap and audibly improves the result.

Autosave fires every 60 seconds via `setInterval`, and "Save drawing" commits
explicitly. Both call `onCommit(dataURL)`; the parent won't let you send until
something is committed.

---

## 7. API client

Everything goes through `/api`, which `vite.config.js` proxies to
`127.0.0.1:8000`. No host is hardcoded anywhere, so dev and production behave
identically and CORS never bites in development.

`unwrap()` pulls FastAPI's `detail` field out of error responses, which is why
backend error messages must be written for users — they're shown verbatim.

---

## 8. Accessibility floor

Built in rather than bolted on: visible `:focus-visible` rings everywhere,
`prefers-reduced-motion` honoured globally in `tokens.css`, ARIA labels on the
pad/dial/screen, real `<label>` elements on every input, and the bench fully
operable from the keyboard. Layout collapses to one column at 980px and the
bench un-rotates on mobile.

---

## 9. What's still to build

- **Channel panel** on the Simulate page — sliders for noise/echo/filters, then
  re-decode and show the damage. Needs `POST /api/channel` first. This is the
  highest-value addition for the demo.
- **Spectrogram view** — `spectral/analysis/waveform.py::spectrogram()` already
  returns a downsampled dB matrix ready to paint as a heatmap. Showing the
  picture *inside* the spectrogram is the project's best visual moment.
- **Side-by-side compare** on Receive: sent vs recovered vs difference.
- **Experiment plots** for the report.
