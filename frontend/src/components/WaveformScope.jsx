import { useEffect, useRef, useState, useCallback } from 'react'
import './WaveformScope.css'

/* A scope with a real timebase, not a thumbnail of the whole file.
 *
 * The old version squeezed every second of audio into one canvas width, which
 * made a 14 second transmission a grey smear with no readable detail. This one
 * holds a fixed window of time and runs the signal through it, past a reading
 * head pinned at the centre. That is what the subject actually is: material
 * crossing a head, one moment at a time. The window is selectable, so the same
 * component serves a glance at the whole file and a look at forty milliseconds
 * of it.
 *
 * Everything is drawn from the min/max/rms envelope the backend sends. Each
 * screen column aggregates whatever buckets fall inside it, so the trace stays
 * honest at every zoom instead of dropping samples between pixels.
 */

const PLAY_ICON = (
  <svg viewBox="0 0 14 14" width="12" height="12" aria-hidden="true" focusable="false">
    <path d="M4 2.6 11.4 7 4 11.4Z" fill="currentColor" />
  </svg>
)
const PAUSE_ICON = (
  <svg viewBox="0 0 14 14" width="12" height="12" aria-hidden="true" focusable="false">
    <path d="M3.6 2.8h2.5v8.4H3.6ZM7.9 2.8h2.5v8.4H7.9Z" fill="currentColor" />
  </svg>
)

/* Timebase, the way a bench scope offers it. null means the whole file.
 *
 * 2s is the tightest rung on purpose. The backend caps the envelope at 2000
 * buckets, so on a long transmission a half-second window would put roughly
 * sixteen screen pixels on every bucket and the trace would degrade into a bar
 * chart - a zoom that shows less than it appears to. 2s keeps at least one
 * bucket every few pixels, so every rung here renders a real waveform. */
const WINDOWS = [
  { label: '2s', sec: 2 },
  { label: '5s', sec: 5 },
  { label: '15s', sec: 15 },
  { label: 'All', sec: null },
]

const RULER_H = 24
const HEAD_AT = 0.5          // the reading head sits at the middle of the glass
const SNAP_PX = 14           // how close the pointer must be to lock onto the trace

const TICKS = [0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 30, 60]

const fmt = (s) => {
  if (!isFinite(s)) return '0.00'
  if (s >= 60) {
    const m = Math.floor(s / 60)
    return `${m}:${(s - m * 60).toFixed(2).padStart(5, '0')}`
  }
  return s.toFixed(2)
}

export default function WaveformScope({ data, audioSrc, title = 'output.wav', accent = 'signal' }) {
  const canvasRef = useRef(null)
  const wrapRef = useRef(null)
  const audioRef = useRef(null)
  const rafRef = useRef(0)

  const envelope = data?.envelope
  const buckets = data?.buckets || []
  const stats = data?.stats || {}
  const duration = Number(stats.duration) || 0

  const [time, setTime] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [volume, setVolume] = useState(0.8)
  const [cursor, setCursor] = useState(null)   // { x, y } in css px, or null
  const [size, setSize] = useState({ w: 0, h: 0 })
  const [windowSec, setWindowSec] = useState(() => (duration > 6 ? 5 : null))

  const span = windowSec == null ? Math.max(duration, 0.001) : Math.min(windowSec, Math.max(duration, 0.001))
  /* The window is held inside the transmission, so the glass is never half
     empty. The head runs out to the left edge at the start, rides the middle
     through the body of the file, and runs out to the right edge at the end -
     the material moves past a fixed head only where there is material on both
     sides of it to move. */
  const t0 = Math.max(0, Math.min(time - span * HEAD_AT, Math.max(duration - span, 0)))

  const tint = accent === 'open'
    ? { trace: '#A8B78A', body: 'rgba(168,183,138,.28)' }
    : { trace: '#7F9166', body: 'rgba(127,145,102,.32)' }

  /* ---- geometry helpers, shared by the renderer and the pointer ---------- */
  const timeAtX = useCallback((x) => t0 + (x / Math.max(size.w, 1)) * span, [t0, span, size.w])
  const xAtTime = useCallback((t) => ((t - t0) / span) * size.w, [t0, span, size.w])
  const bucketAt = useCallback((t) => {
    if (!buckets.length || duration <= 0) return null
    const i = Math.floor((t / duration) * buckets.length)
    return buckets[Math.max(0, Math.min(buckets.length - 1, i))] || null
  }, [buckets, duration, span])

  /* ---- the trace --------------------------------------------------------- */
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas || !size.w || !size.h) return
    const dpr = window.devicePixelRatio || 1
    canvas.width = size.w * dpr
    canvas.height = size.h * dpr
    const ctx = canvas.getContext('2d')
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)

    const W = size.w
    const H = size.h
    const traceH = H - RULER_H
    const mid = traceH / 2
    const amp = mid * 0.9

    ctx.clearRect(0, 0, W, H)

    /* the stretch of wire either side of the signal reads as empty, not black */
    const xStart = xAtTime(0)
    const xEnd = xAtTime(duration)
    ctx.fillStyle = 'rgba(10,18,14,.55)'
    if (xStart > 0) ctx.fillRect(0, 0, Math.min(xStart, W), traceH)
    if (xEnd < W) ctx.fillRect(Math.max(xEnd, 0), 0, W - Math.max(xEnd, 0), traceH)

    /* zero line */
    ctx.strokeStyle = 'rgba(126,145,102,.32)'
    ctx.lineWidth = 1
    ctx.beginPath(); ctx.moveTo(0, mid + 0.5); ctx.lineTo(W, mid + 0.5); ctx.stroke()

    const N = envelope?.max?.length || 0
    if (N && duration > 0) {
      const perPixel = []
      for (let px = 0; px < W; px++) {
        const ta = timeAtX(px)
        const tb = timeAtX(px + 1)
        if (tb < 0 || ta > duration) { perPixel.push(null); continue }
        let i0 = Math.floor((Math.max(ta, 0) / duration) * N)
        let i1 = Math.ceil((Math.min(tb, duration) / duration) * N)
        i0 = Math.max(0, Math.min(N - 1, i0))
        i1 = Math.max(i0 + 1, Math.min(N, i1))
        let lo = Infinity, hi = -Infinity, rms = 0
        for (let i = i0; i < i1; i++) {
          const a = envelope.min[i] ?? 0
          const b = envelope.max[i] ?? 0
          if (a < lo) lo = a
          if (b > hi) hi = b
          const r = envelope.rms?.[i] ?? 0
          if (r > rms) rms = r
        }
        perPixel.push({ lo, hi, rms })
      }

      /* rms body under the peak envelope: two readings of the same signal */
      ctx.fillStyle = tint.body
      for (let px = 0; px < W; px++) {
        const c = perPixel[px]
        if (!c) continue
        const r = c.rms * amp
        ctx.fillRect(px, mid - r, 1, Math.max(r * 2, 1))
      }

      ctx.fillStyle = tint.trace
      for (let px = 0; px < W; px++) {
        const c = perPixel[px]
        if (!c) continue
        const hi = mid - c.hi * amp
        const lo = mid - c.lo * amp
        ctx.fillRect(px, hi, 1, Math.max(lo - hi, 1))
      }
    }

    /* the ends of the transmission, marked rather than implied */
    ctx.strokeStyle = 'rgba(168,183,138,.5)'
    ctx.setLineDash([2, 3])
    for (const x of [xStart, xEnd]) {
      if (x > 0 && x < W) {
        ctx.beginPath(); ctx.moveTo(x + 0.5, 0); ctx.lineTo(x + 0.5, traceH); ctx.stroke()
      }
    }
    ctx.setLineDash([])

    /* ---- ruler ---------------------------------------------------------- */
    ctx.fillStyle = 'rgba(18,28,23,.9)'
    ctx.fillRect(0, traceH, W, RULER_H)
    ctx.strokeStyle = '#33453A'
    ctx.beginPath(); ctx.moveTo(0, traceH + 0.5); ctx.lineTo(W, traceH + 0.5); ctx.stroke()

    const target = span / 6
    const step = TICKS.find((t) => t >= target) || TICKS[TICKS.length - 1]
    ctx.font = '10px "Spline Sans Mono", ui-monospace, monospace'
    ctx.textBaseline = 'middle'
    const first = Math.ceil(t0 / step) * step
    for (let t = first; t <= t0 + span + 1e-9; t += step) {
      if (t < -1e-9 || t > duration + 1e-9) continue
      const x = Math.round(xAtTime(t)) + 0.5
      ctx.strokeStyle = '#33453A'
      ctx.beginPath(); ctx.moveTo(x, traceH); ctx.lineTo(x, traceH + 5); ctx.stroke()
      ctx.fillStyle = '#869A82'
      const label = step < 1 ? t.toFixed(2) : t.toFixed(step < 5 ? 1 : 0)
      const tw = ctx.measureText(label).width
      ctx.fillText(label, Math.min(Math.max(x - tw / 2, 2), W - tw - 2), traceH + 14)
    }

    /* ---- the reading head ------------------------------------------------ */
    const headX = Math.round(xAtTime(time)) + 0.5
    if (headX >= 0 && headX <= W) {
      ctx.strokeStyle = '#A63B46'
      ctx.lineWidth = 1.5
      ctx.beginPath(); ctx.moveTo(headX, 0); ctx.lineTo(headX, traceH); ctx.stroke()
      ctx.fillStyle = '#A63B46'
      ctx.beginPath(); ctx.moveTo(headX - 4, 0); ctx.lineTo(headX + 4, 0); ctx.lineTo(headX, 5); ctx.fill()
    }

    /* ---- crosshair, and the dot that locks it to the trace --------------- */
    if (cursor && cursor.x >= 0 && cursor.x <= W && cursor.y <= traceH) {
      const cx = Math.round(cursor.x) + 0.5
      const ct = timeAtX(cursor.x)
      const b = bucketAt(ct)
      const peak = b ? Number(b.peak) : 0
      const onSignal = ct >= 0 && ct <= duration
      const topY = mid - peak * amp
      const botY = mid + peak * amp
      const near = onSignal &&
        Math.min(Math.abs(cursor.y - topY), Math.abs(cursor.y - botY)) <= SNAP_PX
      const snapY = Math.abs(cursor.y - topY) <= Math.abs(cursor.y - botY) ? topY : botY

      ctx.strokeStyle = near ? 'rgba(168,183,138,.35)' : 'rgba(168,183,138,.55)'
      ctx.lineWidth = 1
      ctx.setLineDash([3, 4])
      ctx.beginPath(); ctx.moveTo(cx, 0); ctx.lineTo(cx, traceH); ctx.stroke()
      if (!near) {
        const cy = Math.round(cursor.y) + 0.5
        ctx.beginPath(); ctx.moveTo(0, cy); ctx.lineTo(W, cy); ctx.stroke()
      }
      ctx.setLineDash([])

      if (near) {
        /* locked onto the trace: the crosshair becomes a dot on the wave */
        ctx.beginPath()
        ctx.arc(cx, snapY, 6.5, 0, Math.PI * 2)
        ctx.strokeStyle = '#0C1310'
        ctx.lineWidth = 3
        ctx.stroke()
        ctx.beginPath()
        ctx.arc(cx, snapY, 6.5, 0, Math.PI * 2)
        ctx.strokeStyle = '#A8B78A'
        ctx.lineWidth = 1.5
        ctx.stroke()
        ctx.beginPath()
        ctx.arc(cx, snapY, 2.5, 0, Math.PI * 2)
        ctx.fillStyle = '#A8B78A'
        ctx.fill()
      } else if (onSignal) {
        /* free: small open marks where this instant meets the trace */
        ctx.strokeStyle = 'rgba(168,183,138,.75)'
        ctx.lineWidth = 1.25
        for (const y of [topY, botY]) {
          ctx.beginPath(); ctx.arc(cx, y, 3, 0, Math.PI * 2); ctx.stroke()
        }
      }
    }
  }, [envelope, size, time, cursor, span, t0, duration, accent, tint.body, tint.trace,
      timeAtX, xAtTime, bucketAt])

  /* ---- size ------------------------------------------------------------- */
  useEffect(() => {
    const el = wrapRef.current
    if (!el) return
    const measure = () => {
      const r = el.getBoundingClientRect()
      setSize({ w: Math.round(r.width), h: Math.round(r.height) })
    }
    measure()
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  /* ---- transport --------------------------------------------------------- */
  useEffect(() => { if (audioRef.current) audioRef.current.volume = volume }, [volume])

  /* timeupdate only fires a few times a second, which is far too coarse for a
     signal running past a head; the clock is read every frame instead. */
  useEffect(() => {
    if (!playing) return
    const tick = () => {
      const a = audioRef.current
      if (a) setTime(a.currentTime)
      rafRef.current = requestAnimationFrame(tick)
    }
    rafRef.current = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(rafRef.current)
  }, [playing])

  const toggle = () => {
    const a = audioRef.current
    if (!a) return
    if (a.paused) { a.play(); setPlaying(true) } else { a.pause(); setPlaying(false) }
  }

  const seekTo = (t) => {
    const clamped = Math.max(0, Math.min(duration, t))
    setTime(clamped)
    if (audioRef.current) audioRef.current.currentTime = clamped
  }

  /* ---- pointer ----------------------------------------------------------- */
  const onMove = (e) => {
    const r = e.currentTarget.getBoundingClientRect()
    setCursor({ x: e.clientX - r.left, y: e.clientY - r.top })
  }

  const onKeyDown = (e) => {
    const nudge = e.shiftKey ? span / 20 : span / 200
    if (e.key === 'ArrowRight') { e.preventDefault(); seekTo(time + nudge) }
    else if (e.key === 'ArrowLeft') { e.preventDefault(); seekTo(time - nudge) }
    else if (e.key === 'Home') { e.preventDefault(); seekTo(0) }
    else if (e.key === 'End') { e.preventDefault(); seekTo(duration) }
    else if (e.key === ' ') { e.preventDefault(); toggle() }
  }

  /* the readout follows the pointer when there is one, the head when there is not */
  const readAt = cursor && size.w ? timeAtX(cursor.x) : time
  const read = bucketAt(Math.max(0, Math.min(duration, readAt)))
  const readInRange = readAt >= 0 && readAt <= duration

  return (
    <div className="scope">
      <div className="scope-head">
        <span className="scope-file mono">{title}</span>
        <span className="scope-meta mono">
          {stats.duration ?? '--'}s &middot; {stats.sample_rate ?? '--'} Hz &middot; peak {stats.peak ?? '--'}
        </span>
      </div>

      <div
        ref={wrapRef}
        className={`scope-glass ${playing ? 'is-running' : ''}`}
        onMouseMove={onMove}
        onMouseLeave={() => setCursor(null)}
        onClick={(e) => {
          const r = e.currentTarget.getBoundingClientRect()
          seekTo(timeAtX(e.clientX - r.left))
        }}
        onKeyDown={onKeyDown}
        tabIndex={0}
        role="group"
        aria-label={`Waveform of ${title}. Arrow keys move the reading head, space plays and pauses.`}
      >
        <canvas ref={canvasRef} className="scope-canvas" />
        <span className="scope-matrix" aria-hidden="true" />
      </div>

      <div className="scope-readout mono" aria-live="off">
        <span className="scope-readout-k">{cursor ? 'Cursor' : 'Head'}</span>
        <span><b>{readInRange && read ? fmt(readAt) : '\u2014'}</b>s</span>
        <span>peak <b>{readInRange && read ? Number(read.peak).toFixed(4) : '\u2014'}</b></span>
        <span>rms <b>{readInRange && read ? Number(read.rms).toFixed(4) : '\u2014'}</b></span>
        <span><b>{readInRange && read ? Number(read.freq).toFixed(0) : '\u2014'}</b> Hz</span>
        <span className="scope-readout-hint">
          {playing ? 'pause to inspect' : 'move across the trace'}
        </span>
      </div>

      {audioSrc && (
        <>
          <div className="scope-transport">
            <button
              type="button"
              className="scope-play"
              onClick={toggle}
              aria-label={playing ? 'Pause' : 'Play'}
            >
              {playing ? PAUSE_ICON : PLAY_ICON}
            </button>

            <span className="scope-clock mono">{fmt(time)}</span>

            <input
              className="scope-seek"
              type="range"
              min="0"
              max={duration || 0}
              step="0.001"
              value={Math.min(time, duration || 0)}
              onChange={(e) => seekTo(parseFloat(e.target.value))}
              aria-label="Position in the transmission"
            />

            <span className="scope-clock scope-clock-total mono">{fmt(duration)}</span>
          </div>

          <div className="scope-controls">
            <div className="scope-zoom" role="group" aria-label="Timebase">
              <span className="scope-zoom-k">Window</span>
              {WINDOWS.map((w) => (
                <button
                  key={w.label}
                  type="button"
                  className={`scope-zoom-b ${windowSec === w.sec ? 'is-on' : ''}`}
                  aria-pressed={windowSec === w.sec}
                  onClick={() => setWindowSec(w.sec)}
                >
                  {w.label}
                </button>
              ))}
            </div>

            <div className="scope-vol">
              <span className="scope-zoom-k">Level</span>
              <input
                className="scope-volume"
                type="range" min="0" max="1" step="0.01"
                value={volume}
                onChange={(e) => setVolume(parseFloat(e.target.value))}
                aria-label="Volume"
              />
              <span className="scope-vol-read mono">{Math.round(volume * 100)}</span>
            </div>
          </div>

          <audio
            ref={audioRef}
            src={audioSrc}
            onEnded={() => { setPlaying(false); setTime(duration) }}
          />
        </>
      )}
    </div>
  )
}
