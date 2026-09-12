import { useRef, useState, useEffect, useCallback } from 'react'
import './DoodleCanvas.css'

/* A small paint surface. Three brushes that genuinely differ (pencil is hard
   and thin, paint is round and opaque, highlighter is wide and multiplies),
   plus shapes, an eraser, undo, and stroke smoothing.

   Smoothing matters more here than in a normal paint app: every stroke becomes
   a column of tones, so a jittery line becomes noisy audio. */

const W = 720, H = 540
/* The canvas ground stays white because brightness is what becomes loudness:
   a dark ground would encode as a wall of sound. The swatches sit in the
   site's world but still span hue, because colour mode sends three real
   passes and the user needs separable channels to see that happen. */
const SWATCHES = ['#23301F', '#5E7346', '#A8B78A', '#8C2F39', '#C08A2E', '#2E6A70', '#3A4F7A', '#FFFFFF']
const TOOLS = [
  { id: 'paint', label: 'Paint' },
  { id: 'pencil', label: 'Pencil' },
  { id: 'marker', label: 'Highlighter' },
  { id: 'line', label: 'Line' },
  { id: 'rect', label: 'Box' },
  { id: 'ellipse', label: 'Circle' },
  { id: 'eraser', label: 'Eraser' },
]

export default function DoodleCanvas({ onCommit, committed }) {
  const canvasRef = useRef(null)
  const ctxRef = useRef(null)
  const drawing = useRef(false)
  const points = useRef([])
  const snapshot = useRef(null)
  const history = useRef([])

  const [tool, setTool] = useState('paint')
  const [color, setColor] = useState('#23301F')
  const [weight, setWeight] = useState(8)
  const [smooth, setSmooth] = useState(true)
  const [saveNote, setSaveNote] = useState('')

  // ---- setup ----
  useEffect(() => {
    const canvas = canvasRef.current
    const dpr = window.devicePixelRatio || 1
    canvas.width = W * dpr
    canvas.height = H * dpr
    const ctx = canvas.getContext('2d', { willReadFrequently: true })
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    ctx.fillStyle = '#FFFFFF'
    ctx.fillRect(0, 0, W, H)
    ctx.lineCap = 'round'
    ctx.lineJoin = 'round'
    ctxRef.current = ctx
    pushHistory()
  }, [])

  const pushHistory = () => {
    const canvas = canvasRef.current
    if (!canvas) return
    history.current.push(canvas.toDataURL('image/png'))
    if (history.current.length > 24) history.current.shift()
  }

  const restore = (dataUrl) => new Promise((resolve) => {
    const image = new Image()
    image.onload = () => {
      const ctx = ctxRef.current
      ctx.save()
      ctx.globalCompositeOperation = 'source-over'
      ctx.globalAlpha = 1
      ctx.clearRect(0, 0, W, H)
      ctx.drawImage(image, 0, 0, W, H)
      ctx.restore()
      resolve()
    }
    image.src = dataUrl
  })

  const undo = async () => {
    if (history.current.length < 2) return
    history.current.pop()
    await restore(history.current[history.current.length - 1])
  }

  const clear = () => {
    const ctx = ctxRef.current
    ctx.save()
    ctx.globalCompositeOperation = 'source-over'
    ctx.globalAlpha = 1
    ctx.fillStyle = '#FFFFFF'
    ctx.fillRect(0, 0, W, H)
    ctx.restore()
    pushHistory()
  }

  // ---- brush setup ----
  const applyBrush = (ctx) => {
    ctx.globalCompositeOperation = 'source-over'
    ctx.globalAlpha = 1
    ctx.strokeStyle = color
    ctx.fillStyle = color
    ctx.lineWidth = weight

    if (tool === 'pencil') {
      ctx.lineWidth = Math.max(1, weight * 0.34)
      ctx.lineCap = 'butt'
      ctx.globalAlpha = 0.92
    } else if (tool === 'marker') {
      ctx.lineWidth = weight * 2.4
      ctx.lineCap = 'square'
      ctx.globalAlpha = 0.30
    } else if (tool === 'eraser') {
      ctx.strokeStyle = '#FFFFFF'
      ctx.fillStyle = '#FFFFFF'
      ctx.lineWidth = weight * 1.7
      ctx.lineCap = 'round'
    } else {
      ctx.lineCap = 'round'
    }
  }

  const pos = (e) => {
    const rect = canvasRef.current.getBoundingClientRect()
    const src = e.touches?.[0] || e
    return {
      x: (src.clientX - rect.left) * (W / rect.width),
      y: (src.clientY - rect.top) * (H / rect.height),
    }
  }

  const start = (e) => {
    e.preventDefault()
    drawing.current = true
    points.current = [pos(e)]
    snapshot.current = canvasRef.current.toDataURL('image/png')
    const ctx = ctxRef.current
    applyBrush(ctx)
    if (['paint', 'pencil', 'marker', 'eraser'].includes(tool)) {
      ctx.beginPath()
      ctx.moveTo(points.current[0].x, points.current[0].y)
    }
  }

  const move = (e) => {
    if (!drawing.current) return
    e.preventDefault()
    const p = pos(e)
    const ctx = ctxRef.current

    if (['line', 'rect', 'ellipse'].includes(tool)) {
      // shapes preview live: repaint the pre-stroke snapshot each frame
      const image = new Image()
      image.onload = () => {
        ctx.save()
        ctx.globalCompositeOperation = 'source-over'
        ctx.globalAlpha = 1
        ctx.clearRect(0, 0, W, H)
        ctx.drawImage(image, 0, 0, W, H)
        ctx.restore()
        applyBrush(ctx)
        drawShape(ctx, points.current[0], p)
      }
      image.src = snapshot.current
      return
    }

    points.current.push(p)
    const pts = points.current

    if (smooth && pts.length > 2) {
      // quadratic midpoint smoothing - cheap, and it makes the encoded audio
      // noticeably cleaner than raw polyline segments
      const a = pts[pts.length - 3], b = pts[pts.length - 2], c = pts[pts.length - 1]
      const m1 = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 }
      const m2 = { x: (b.x + c.x) / 2, y: (b.y + c.y) / 2 }
      ctx.beginPath()
      ctx.moveTo(m1.x, m1.y)
      ctx.quadraticCurveTo(b.x, b.y, m2.x, m2.y)
      ctx.stroke()
    } else {
      ctx.lineTo(p.x, p.y)
      ctx.stroke()
    }
  }

  const drawShape = (ctx, a, b) => {
    ctx.beginPath()
    if (tool === 'line') { ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y) }
    else if (tool === 'rect') ctx.rect(a.x, a.y, b.x - a.x, b.y - a.y)
    else {
      ctx.ellipse((a.x + b.x) / 2, (a.y + b.y) / 2,
        Math.abs(b.x - a.x) / 2, Math.abs(b.y - a.y) / 2, 0, 0, Math.PI * 2)
    }
    ctx.stroke()
  }

  const end = () => {
    if (!drawing.current) return
    drawing.current = false
    ctxRef.current.closePath()
    pushHistory()
  }

  // ---- autosave every minute ----
  const commit = useCallback((silent = false) => {
    const dataUrl = canvasRef.current.toDataURL('image/png')
    onCommit?.(dataUrl)
    const time = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    setSaveNote(silent ? `autosaved ${time}` : `saved ${time}`)
  }, [onCommit])

  useEffect(() => {
    const id = setInterval(() => commit(true), 60000)
    return () => clearInterval(id)
  }, [commit])

  const download = () => {
    const anchor = document.createElement('a')
    anchor.download = 'doodle.png'
    anchor.href = canvasRef.current.toDataURL('image/png')
    anchor.click()
  }

  return (
    <div className="doodle">
      <div className="doodle-bar">
        <div className="doodle-group">
          {TOOLS.map((t) => (
            <button
              key={t.id} type="button"
              className={`doodle-tool ${tool === t.id ? 'is-on' : ''}`}
              onClick={() => setTool(t.id)}
            >{t.label}</button>
          ))}
        </div>

        <div className="doodle-group">
          {SWATCHES.map((c) => (
            <button
              key={c} type="button"
              className={`doodle-swatch ${color === c ? 'is-on' : ''}`}
              style={{ background: c }}
              onClick={() => setColor(c)}
              aria-label={`Colour ${c}`}
            />
          ))}
          <input type="color" className="doodle-picker" value={color}
                 onChange={(e) => setColor(e.target.value)} aria-label="Custom colour" />
        </div>

        <div className="doodle-group doodle-weight">
          <span className="mono">{weight}px</span>
          <input type="range" min="1" max="42" value={weight}
                 onChange={(e) => setWeight(+e.target.value)} aria-label="Brush size" />
        </div>

        <div className="doodle-group">
          <label className="doodle-check">
            <input type="checkbox" checked={smooth} onChange={(e) => setSmooth(e.target.checked)} />
            Smoothing
          </label>
          <button type="button" className="doodle-tool" onClick={undo}>Undo</button>
          <button type="button" className="doodle-tool" onClick={clear}>Clear</button>
        </div>
      </div>

      <div className="doodle-stage">
        <canvas
          ref={canvasRef}
          className="doodle-canvas"
          style={{ aspectRatio: `${W} / ${H}` }}
          onMouseDown={start} onMouseMove={move} onMouseUp={end} onMouseLeave={end}
          onTouchStart={start} onTouchMove={move} onTouchEnd={end}
        />
      </div>

      <div className="doodle-foot">
        <button type="button" className="btn btn-primary" onClick={() => commit(false)}>
          Save drawing
        </button>
        <button type="button" className="btn btn-ghost" onClick={download}>Download PNG</button>
        <span className="doodle-note">
          {committed ? 'Drawing locked in and ready to send.' : 'Save to lock it in before sending.'}
          {saveNote && <b className="mono"> {saveNote}</b>}
        </span>
      </div>
    </div>
  )
}
