import { useState, useEffect, useRef, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { drawText, textWidth } from './dmgFont'
import './DMG01.css'

/* The DMG-01: the landing page's object, and a miniature of the whole product.
 *
 * Draw a glyph on the pad, press ENCODE and the rows become frequency lanes,
 * press LOCK and a key scatters them, press OPEN and they come back. That is
 * the entire Spectral Canvas pipeline in three button presses and no backend
 * call at all.
 *
 * The screen is a real 160x144 panel with four tones and nothing else. It is
 * drawn a pixel at a time, in a bitmap face written for it, because a screen
 * that renders at browser resolution is a picture of a console rather than a
 * console. What the canvas draws is a pure function of state, and nothing is
 * layered over it: a panel that is partly covered while an entrance animation
 * runs is a panel a visitor cannot read in the first viewport. The scanline
 * arrival this world is built on lives where it means something instead - on
 * Receive, where the decoder really is resolving the picture a slice at a
 * time, and in the idle scope waiting for a file. shuffleGrid mirrors security.py: two independent permutations,
 * rows and columns, from one seed. It is a visual analogue of the real
 * cipher, not the cipher, but it scatters the same way.
 */

const N = 8
const W = 160
const H = 144

/* The four tones. Nothing on this panel may be any other colour. */
const T0 = '#A8B78A'
const T1 = '#7F9166'
const T2 = '#4E6144'
const T3 = '#23301F'

const BANDS = [
  { label: '1-8K', top: 8, step: 1 },
  { label: '1.5-12K', top: 12, step: 1.5 },
  { label: '0.7-5.4K', top: 5.4, step: 0.67 },
]

const SEED_GLYPH = [
  [0, 0, 1, 1, 1, 1, 0, 0],
  [0, 1, 0, 0, 0, 0, 1, 0],
  [1, 0, 1, 0, 0, 1, 0, 1],
  [1, 0, 0, 0, 0, 0, 0, 1],
  [1, 0, 1, 1, 1, 1, 0, 1],
  [1, 0, 0, 0, 0, 0, 0, 1],
  [0, 1, 0, 0, 0, 0, 1, 0],
  [0, 0, 1, 1, 1, 1, 0, 0],
]

/* The moulded arrow on a d-pad face. One authored shape, rotated per
   direction by CSS, so all four are identical by construction. */
const ARROW = (
  <svg viewBox="0 0 12 12" width="12" height="12" aria-hidden="true" focusable="false">
    <path d="M6 2.6 10.2 9.4H1.8Z" fill="#14170F" />
  </svg>
)

const clone = (g) => g.map((r) => [...r])
const blank = () => Array.from({ length: N }, () => Array(N).fill(0))

function shuffleGrid(grid, seed) {
  let s = seed
  const rand = () => (s = (s * 1664525 + 1013904223) % 4294967296) / 4294967296
  const perm = (n) => {
    const a = [...Array(n).keys()]
    for (let i = n - 1; i > 0; i--) {
      const j = Math.floor(rand() * (i + 1))
      ;[a[i], a[j]] = [a[j], a[i]]
    }
    return a
  }
  const rp = perm(N)
  const cp = perm(N)
  return rp.map((r) => cp.map((c) => grid[r][c]))
}

/* --- screen geometry, in panel pixels ----------------------------------- */
const PAD_X = 17
const PAD_Y = 13
const CELL_W = 16
const CELL_H = 13
const GAP_X = 1
const GAP_Y = 2
const cellX = (c) => PAD_X + c * (CELL_W + GAP_X)
const cellY = (r) => PAD_Y + r * (CELL_H + GAP_Y)

export default function DMG01() {
  const reduced =
    typeof window !== 'undefined' &&
    window.matchMedia?.('(prefers-reduced-motion: reduce)').matches

  const [grid, setGrid] = useState(SEED_GLYPH)
  const [stage, setStage] = useState('glyph') // glyph | spectrum | locked | recovered
  const [band, setBand] = useState(0)
  const [cursor, setCursor] = useState([3, 3])
  const [power, setPower] = useState(true)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('READY. DRAW ON THE PAD')
  const [focused, setFocused] = useState(false)

  const canvasRef = useRef(null)
  const frameRef = useRef(0)
  const timers = useRef([])
  const navigate = useNavigate()

  const after = useCallback((fn, ms) => {
    timers.current.push(setTimeout(fn, ms))
  }, [])

  /* The panel rests lit. Its waking is the CSS wipe over the glass, not a timer
     that leaves a dead screen in the first viewport; the switch stays real, so
     a visitor can still turn the thing off and watch it go dark. */
  useEffect(() => () => timers.current.forEach(clearTimeout), [])

  /* ---- the panel ------------------------------------------------------- */
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d', { alpha: false })
    ctx.imageSmoothingEnabled = false
    let raf

    const scattered = stage === 'locked' ? shuffleGrid(grid, 20240917) : null
    const shown = stage === 'locked' ? scattered : grid

    const render = () => {
      const f = (frameRef.current += 1)

      /* the panel's own ground */
      ctx.fillStyle = T0
      ctx.fillRect(0, 0, W, H)

      if (!power) {
        /* unlit: the panel is still there, just dark and even */
        ctx.fillStyle = T2
        ctx.fillRect(0, 0, W, H)
        raf = requestAnimationFrame(render)
        return
      }

      /* --- status bar --- */
      drawText(ctx, 'SC-01', 3, 1, T3)
      const mode = stage === 'glyph' ? 'GLYPH'
        : stage === 'spectrum' ? 'SPECTRUM'
        : stage === 'locked' ? 'LOCKED' : 'RECOVERED'
      drawText(ctx, mode, W - 3 - textWidth(mode), 1, T3)
      ctx.fillStyle = T2
      ctx.fillRect(0, 10, W, 1)

      /* --- frequency lanes: a row IS a pitch, so the labels sit beside the
             glyph too, not only beside the spectrum --- */
      const b = BANDS[band]
      for (let r = 0; r < N; r++) {
        const hz = b.top - r * b.step
        const label = hz >= 10 ? `${Math.round(hz)}K` : `${hz.toFixed(1)}K`.replace('.0K', 'K')
        drawText(ctx, label.padStart(2, ' ').slice(0, 4), 1, cellY(r) + 3, T2)
      }

      /* --- the pad --- */
      for (let r = 0; r < N; r++) {
        for (let c = 0; c < N; c++) {
          const x = cellX(c)
          const y = cellY(r)
          const on = shown[r][c]

          if (!on) {
            ctx.fillStyle = T1
            ctx.fillRect(x + (CELL_W >> 1), y + (CELL_H >> 1), 1, 1)
            continue
          }

          if (stage === 'glyph') {
            ctx.fillStyle = T3
            ctx.fillRect(x, y, CELL_W, CELL_H)
          } else if (stage === 'spectrum') {
            /* a lane carrying energy: body plus a bright cap */
            ctx.fillStyle = T2
            ctx.fillRect(x, y + 2, CELL_W, CELL_H - 2)
            ctx.fillStyle = T3
            ctx.fillRect(x, y, CELL_W, 2)
          } else if (stage === 'locked') {
            /* four tones cannot show "wrong", so scrambled reads as dither */
            ctx.fillStyle = T3
            for (let yy = 0; yy < CELL_H; yy++) {
              for (let xx = (yy & 1); xx < CELL_W; xx += 2) {
                ctx.fillRect(x + xx, y + yy, 1, 1)
              }
            }
          } else {
            ctx.fillStyle = T3
            ctx.fillRect(x, y, CELL_W, CELL_H)
            ctx.fillStyle = T0
            ctx.fillRect(x + 1, y + 1, 2, 2)
          }
        }
      }

      /* --- the sweep: columns are moments, so a playhead crosses them --- */
      if (stage === 'spectrum' && !reduced) {
        const col = Math.floor((f / 9) % N)
        const x = cellX(col)
        ctx.fillStyle = T3
        ctx.fillRect(x - 1, PAD_Y - 2, 1, N * (CELL_H + GAP_Y))
      }

      /* --- cursor --- */
      if (stage === 'glyph' && (reduced || focused || (f >> 4) % 2 === 0)) {
        const [cr, cc] = cursor
        const x = cellX(cc)
        const y = cellY(cr)
        ctx.fillStyle = grid[cr][cc] ? T0 : T3
        ctx.fillRect(x, y, CELL_W, 1)
        ctx.fillRect(x, y + CELL_H - 1, CELL_W, 1)
        ctx.fillRect(x, y, 1, CELL_H)
        ctx.fillRect(x + CELL_W - 1, y, 1, CELL_H)
      }

      /* --- message line --- */
      ctx.fillStyle = T2
      ctx.fillRect(0, 132, W, 1)
      drawText(ctx, message.slice(0, 26), 3, 135, T3)

      raf = requestAnimationFrame(render)
    }

    raf = requestAnimationFrame(render)
    return () => cancelAnimationFrame(raf)
  }, [grid, stage, band, cursor, power, message, focused, reduced])

  /* ---- actions --------------------------------------------------------- */
  const toggle = useCallback(() => {
    if (busy || !power) return
    const [r, c] = cursor
    setGrid((g) => {
      const n = clone(g)
      n[r][c] = n[r][c] ? 0 : 1
      return n
    })
    setStage('glyph')
    setMessage(`R${r + 1}C${c + 1} ${grid[r][c] ? 'CLEARED' : 'SET'}`)
  }, [busy, power, cursor, grid])

  const advance = useCallback(() => {
    if (busy || !power) return
    setBusy(true)
    if (stage === 'glyph') {
      setStage('spectrum')
      setMessage('MAPPING ROWS TO TONES')
      after(() => {
        setMessage(`${N} TONES ${N} FRAMES ${BANDS[band].label}`)
        setBusy(false)
      }, 900)
    } else if (stage === 'spectrum') {
      setStage('locked')
      setMessage('KEY APPLIED. ROWS SCATTERED')
      after(() => {
        setMessage('LOCKED. WRONG KEY GIVES NOISE')
        setBusy(false)
      }, 900)
    } else if (stage === 'locked') {
      setStage('recovered')
      setMessage('PERMUTATION REVERSED')
      after(() => {
        setMessage('RECOVERED. NOTHING WAS LOST')
        setBusy(false)
      }, 900)
    } else {
      setStage('glyph')
      setMessage('READY. DRAW ON THE PAD')
      setBusy(false)
    }
  }, [busy, power, stage, band, after])

  const clear = useCallback(() => {
    if (busy || !power) return
    setGrid(blank())
    setStage('glyph')
    setMessage('PAD CLEARED')
  }, [busy, power])

  const move = useCallback((dr, dc) => {
    if (!power) return
    setStage('glyph')
    setCursor(([r, c]) => [(r + dr + N) % N, (c + dc + N) % N])
  }, [power])

  const send = useCallback(() => {
    navigate('/simulate?mode=doodle')
  }, [navigate])

  const cycleBand = useCallback(() => {
    if (busy || !power) return
    setBand((b) => {
      const n = (b + 1) % BANDS.length
      setMessage(`BAND ${BANDS[n].label}`)
      return n
    })
  }, [busy, power])

  const onKeyDown = (e) => {
    const k = e.key
    const map = {
      ArrowUp: () => move(-1, 0),
      ArrowDown: () => move(1, 0),
      ArrowLeft: () => move(0, -1),
      ArrowRight: () => move(0, 1),
      ' ': toggle,
      z: toggle,
      Z: toggle,
      x: advance,
      X: advance,
      Enter: send,
      Backspace: clear,
    }
    if (map[k]) {
      e.preventDefault()
      map[k]()
    }
  }

  const stageLabel = {
    glyph: 'a picture',
    spectrum: 'the same data read as frequency over time',
    locked: 'permuted by the key',
    recovered: 'rebuilt, nothing lost',
  }[stage]

  return (
    <div className={`dmg ${power ? 'is-on' : ''}`}>
      <button
        type="button"
        className={`dmg-power ${power ? 'is-on' : ''}`}
        onClick={() => setPower((p) => !p)}
        aria-label={power ? 'Switch the console off' : 'Switch the console on'}
      >
        <i aria-hidden="true" />
      </button>
      <p className="dmg-top" aria-hidden="true">&#9668; OFF &middot; ON &#9658;</p>

      <div className="dmg-bezel">
        <p className="dmg-bezel-head" aria-hidden="true">
          <i /> DOT MATRIX WITH SPECTRAL SOUND <i />
        </p>
        <span className="dmg-battery" aria-hidden="true">
          <i className={power ? (stage === 'locked' ? 'is-locked' : 'is-lit') : ''} />
          <em>{stage === 'locked' ? 'LOCK' : 'PWR'}</em>
        </span>

        <div
          className="dmg-screen"
          role="application"
          tabIndex={0}
          aria-label="SC-01 console. Arrow keys move the cursor on the eight by eight pad, Z sets or clears a cell, X advances the pipeline, Enter opens the send page."
          onKeyDown={onKeyDown}
          onFocus={() => setFocused(true)}
          onBlur={() => setFocused(false)}
        >
          <canvas ref={canvasRef} width={W} height={H} />
          <span className="dmg-matrix" aria-hidden="true" />
          <span className="dmg-glare" aria-hidden="true" />
        </div>
      </div>

      <p className="dmg-brand" aria-hidden="true">
        <strong>SPECTRAL</strong> <span>canvas</span> <sup>01</sup>
      </p>

      <div className="dmg-controls">
        <div className="dmg-dpad-well">
          <div className="dmg-dpad">
            <button type="button" className="dmg-up" onClick={() => move(-1, 0)} aria-label="Move cursor up">{ARROW}</button>
            <button type="button" className="dmg-left" onClick={() => move(0, -1)} aria-label="Move cursor left">{ARROW}</button>
            <span className="dmg-hub" aria-hidden="true"><i /></span>
            <button type="button" className="dmg-right" onClick={() => move(0, 1)} aria-label="Move cursor right">{ARROW}</button>
            <button type="button" className="dmg-down" onClick={() => move(1, 0)} aria-label="Move cursor down">{ARROW}</button>
          </div>
        </div>

        <div className="dmg-ab">
          <span className="dmg-ab-slot">
            <button type="button" className="dmg-round" onClick={advance} aria-label="B. Advance the pipeline one stage" />
            <em>B</em>
          </span>
          <span className="dmg-ab-slot">
            <button type="button" className="dmg-round" onClick={toggle} aria-label="A. Set or clear the cell under the cursor" />
            <em>A</em>
          </span>
        </div>
      </div>

      <div className="dmg-pills">
        <button type="button" className="dmg-pill" onClick={clear}><i />SELECT</button>
        <button type="button" className="dmg-pill" onClick={send}><i />START</button>
      </div>

      <p className="dmg-legend" aria-hidden="true">
        <span>A</span> draw <span>B</span> encode
      </p>

      <button type="button" className="dmg-speaker" onClick={cycleBand} aria-label={`Band ${BANDS[band].label}. Change the frequency band.`}>
        {[0, 1, 2, 3, 4, 5].map((i) => <i key={i} />)}
      </button>

      <p className="sr-only" aria-live="polite">
        {`Cursor row ${cursor[0] + 1}, column ${cursor[1] + 1}. ` +
         `Stage: ${stage}, ${stageLabel}. ${message.toLowerCase()}.`}
      </p>
    </div>
  )
}
