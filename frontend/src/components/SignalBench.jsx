import { useState, useEffect, useRef, useCallback } from 'react'
import './SignalBench.css'

/* The landing page centrepiece: a miniature of the whole product.
   Draw a glyph on the 8x8 pad -> watch it become frequency lanes -> lock it
   with a PIN and watch the rows scatter -> open it and watch it come back.
   That is the entire Spectral Canvas pipeline in four button presses. */

const N = 8
const BAND = [
  ['8.0k', '7.0k', '6.0k', '5.0k', '4.0k', '3.0k', '2.0k', '1.0k'],
  ['12k', '10.5k', '9.0k', '7.5k', '6.0k', '4.5k', '3.0k', '1.5k'],
  ['5.4k', '4.7k', '4.0k', '3.4k', '2.7k', '2.0k', '1.4k', '700'],
]

const SEED_GLYPH = [
  [0,0,1,1,1,1,0,0],
  [0,1,0,0,0,0,1,0],
  [1,0,1,0,0,1,0,1],
  [1,0,0,0,0,0,0,1],
  [1,0,1,1,1,1,0,1],
  [1,0,0,0,0,0,0,1],
  [0,1,0,0,0,0,1,0],
  [0,0,1,1,1,1,0,0],
]

const empty = () => Array.from({ length: N }, () => Array(N).fill(0))
const clone = (g) => g.map((r) => [...r])

function shuffleGrid(grid, seed) {
  // mirrors security.py: independent row and column permutations from one key
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
  const rp = perm(N), cp = perm(N)
  return rp.map((r) => cp.map((c) => grid[r][c]))
}

export default function SignalBench() {
  const [grid, setGrid] = useState(SEED_GLYPH)
  const [view, setView] = useState('glyph')   // glyph | spectrum | locked | recovered
  const [busy, setBusy] = useState(false)
  const [band, setBand] = useState(0)
  const [cursor, setCursor] = useState([3, 3])
  const [booted, setBooted] = useState(false)
  const [readout, setReadout] = useState('ready. draw on the pad')
  const benchRef = useRef(null)
  const timers = useRef([])

  useEffect(() => {
    const id = setTimeout(() => setBooted(true), 220)
    return () => { clearTimeout(id); timers.current.forEach(clearTimeout) }
  }, [])

  const schedule = (fn, ms) => { timers.current.push(setTimeout(fn, ms)) }

  const toggle = (r, c) => {
    if (busy) return
    setGrid((g) => { const n = clone(g); n[r][c] = n[r][c] ? 0 : 1; return n })
    setView('glyph')
    setReadout(`cell r${r + 1} c${c + 1} toggled`)
  }

  const runEncode = useCallback(() => {
    if (busy) return
    setBusy(true); setView('spectrum')
    setReadout('mapping rows to tones...')
    schedule(() => { setReadout(`${N} tones - ${N} frames - ${BAND[band][0]} top row`); setBusy(false) }, 1100)
  }, [busy, band])

  const runLock = useCallback(() => {
    if (busy) return
    setBusy(true); setView('locked')
    setReadout('scrambling rows and columns with the PIN...')
    schedule(() => { setReadout('locked. the picture is still in there'); setBusy(false) }, 1100)
  }, [busy])

  const runOpen = useCallback(() => {
    if (busy) return
    setBusy(true); setView('recovered')
    setReadout('reading magnitudes back...')
    schedule(() => { setReadout('rebuilt. zero pixels lost'); setBusy(false) }, 1100)
  }, [busy])

  const runClear = useCallback(() => {
    if (busy) return
    setGrid(empty()); setView('glyph'); setReadout('pad cleared')
  }, [busy])

  // keyboard: the bench is fully playable without a mouse
  const onKeyDown = (e) => {
    const [r, c] = cursor
    const move = (dr, dc) => {
      e.preventDefault()
      setCursor([(r + dr + N) % N, (c + dc + N) % N])
    }
    if (e.key === 'ArrowUp') move(-1, 0)
    else if (e.key === 'ArrowDown') move(1, 0)
    else if (e.key === 'ArrowLeft') move(0, -1)
    else if (e.key === 'ArrowRight') move(0, 1)
    else if (e.key === ' ' || e.key === 'Enter') { e.preventDefault(); toggle(r, c) }
    else if (e.key.toLowerCase() === 'e') runEncode()
    else if (e.key.toLowerCase() === 'l') runLock()
    else if (e.key.toLowerCase() === 'o') runOpen()
    else if (e.key.toLowerCase() === 'c') runClear()
  }

  const shown = view === 'locked' ? shuffleGrid(grid, 9301 + band) : grid
  const labels = BAND[band]

  return (
    <div className={`bench ${booted ? 'is-on' : ''}`} ref={benchRef}>
      <div className="bench-lip">
        <span className="bench-id mono">SC&#8209;01</span>
        <span className="bench-sub mono">TIME / FREQUENCY BENCH</span>
        <span className={`bench-led ${view === 'locked' ? 'led-locked' : ''}`} aria-hidden="true" />
      </div>

      <div className="bench-body">
        {/* ---------------- screen ---------------- */}
        <div className="screen" role="img"
             aria-label={`Instrument screen showing ${view} view`}>
          <div className="screen-glass">
            <div className="screen-head mono">
              <span>{view === 'spectrum' ? 'SPECTRUM' : view === 'locked' ? 'LOCKED' : view === 'recovered' ? 'REBUILT' : 'GLYPH'}</span>
              <span>{labels[0]} &ndash; {labels[N - 1]}</span>
            </div>

            <div className="screen-stage">
              {/* frequency lane labels sit alongside both views, because the
                  whole point is that a row IS a frequency */}
              <div className="lanes mono" aria-hidden="true">
                {labels.map((f) => <span key={f}>{f}</span>)}
              </div>

              <div className={`plot plot-${view}`}>
                {shown.map((row, r) => (
                  <div className="plot-row" key={r}>
                    {row.map((v, c) => (
                      <span
                        key={c}
                        className={`plot-cell ${v ? 'on' : ''}`}
                        style={{ transitionDelay: `${(view === 'glyph' ? 0 : c * 42 + r * 9)}ms` }}
                      />
                    ))}
                  </div>
                ))}
                <span className="sweep" aria-hidden="true" />
              </div>
            </div>

            <div className="screen-foot mono">{readout}</div>
          </div>
        </div>

        {/* ---------------- controls ---------------- */}
        <div className="controls">
          <div
            className="pad"
            role="grid"
            tabIndex={0}
            aria-label="Eight by eight drawing pad. Arrow keys move, space toggles."
            onKeyDown={onKeyDown}
          >
            {grid.map((row, r) => row.map((v, c) => (
              <button
                key={`${r}-${c}`}
                type="button"
                tabIndex={-1}
                aria-label={`row ${r + 1} column ${c + 1}, ${v ? 'on' : 'off'}`}
                className={`pad-cell ${v ? 'on' : ''} ${cursor[0] === r && cursor[1] === c ? 'cursor' : ''}`}
                onClick={() => { setCursor([r, c]); toggle(r, c) }}
              />
            )))}
          </div>

          <div className="dial-wrap">
            <button
              type="button"
              className="dial"
              style={{ '--turn': `${band * 62 - 62}deg` }}
              aria-label={`Frequency band, currently ${labels[N - 1]} to ${labels[0]}`}
              onClick={() => { setBand((b) => (b + 1) % BAND.length); setReadout('band changed') }}
            >
              <span className="dial-mark" />
            </button>
            <span className="dial-label mono">BAND</span>
          </div>
        </div>
      </div>

      <div className="bench-keys">
        <button type="button" className="key key-go" onClick={runEncode} disabled={busy}>Encode</button>
        <button type="button" className="key key-lock" onClick={runLock} disabled={busy}>Lock</button>
        <button type="button" className="key" onClick={runOpen} disabled={busy}>Open</button>
        <button type="button" className="key" onClick={runClear} disabled={busy}>Clear</button>
      </div>

      <p className="bench-hint">
        Click the pad to draw, then press <b>Encode</b>. Keyboard works too&nbsp;&mdash; arrows, space,
        then <span className="mono">E</span> <span className="mono">L</span> <span className="mono">O</span>.
      </p>
    </div>
  )
}
