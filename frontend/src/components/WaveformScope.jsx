import { useEffect, useRef, useState } from 'react'
import './WaveformScope.css'

/* Draws the min/max envelope the backend sends (not raw samples - a 6 second
   clip is 265k samples and the canvas only has ~900 columns anyway).
   Hovering reads out that bucket's stats in a handwritten face, because the
   numbers are an aside to the shape, not the main event. */

export default function WaveformScope({ data, audioSrc, title = 'output.wav', accent = 'indigo' }) {
  const canvasRef = useRef(null)
  const audioRef = useRef(null)
  const [hover, setHover] = useState(null)
  const [playing, setPlaying] = useState(false)
  const [volume, setVolume] = useState(0.8)
  const [progress, setProgress] = useState(0)

  const envelope = data?.envelope
  const buckets = data?.buckets || []
  const stats = data?.stats || {}

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas || !envelope?.max?.length) return

    const dpr = window.devicePixelRatio || 1
    const { width, height } = canvas.getBoundingClientRect()
    canvas.width = width * dpr
    canvas.height = height * dpr
    const ctx = canvas.getContext('2d')
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    ctx.clearRect(0, 0, width, height)

    const mid = height / 2
    const n = envelope.max.length
    const step = width / n
    const stroke = accent === 'aqua' ? '#0E9C9C' : '#4B3FCF'

    // zero line
    ctx.strokeStyle = 'rgba(101,109,148,.32)'
    ctx.lineWidth = 1
    ctx.beginPath(); ctx.moveTo(0, mid); ctx.lineTo(width, mid); ctx.stroke()

    // rms body first, peak envelope over it: two readings of the same signal
    ctx.fillStyle = accent === 'aqua' ? 'rgba(14,156,156,.22)' : 'rgba(75,63,207,.20)'
    for (let i = 0; i < n; i++) {
      const r = (envelope.rms?.[i] || 0) * mid * 0.95
      ctx.fillRect(i * step, mid - r, Math.max(step, 0.7), r * 2)
    }

    ctx.fillStyle = stroke
    for (let i = 0; i < n; i++) {
      const hi = (envelope.max[i] || 0) * mid * 0.95
      const lo = (envelope.min[i] || 0) * mid * 0.95
      ctx.fillRect(i * step, mid - hi, Math.max(step * 0.62, 0.6), Math.max(hi - lo, 0.8))
    }

    // playhead
    if (progress > 0) {
      ctx.strokeStyle = '#F06B47'
      ctx.lineWidth = 1.6
      ctx.beginPath()
      ctx.moveTo(progress * width, 4)
      ctx.lineTo(progress * width, height - 4)
      ctx.stroke()
    }
  }, [envelope, progress, accent])

  const onMove = (e) => {
    const rect = e.currentTarget.getBoundingClientRect()
    const x = e.clientX - rect.left
    const ratio = Math.max(0, Math.min(1, x / rect.width))
    const index = Math.min(buckets.length - 1, Math.floor(ratio * buckets.length))
    if (index >= 0 && buckets[index]) {
      setHover({ ...buckets[index], x, y: e.clientY - rect.top })
    }
  }

  const seek = (e) => {
    const audio = audioRef.current
    if (!audio || !audio.duration) return
    const rect = e.currentTarget.getBoundingClientRect()
    audio.currentTime = ((e.clientX - rect.left) / rect.width) * audio.duration
  }

  useEffect(() => { if (audioRef.current) audioRef.current.volume = volume }, [volume])

  return (
    <div className="scope">
      <div className="scope-head">
        <span className="scope-file mono">{title}</span>
        <span className="scope-meta mono">
          {stats.duration ?? '--'}s &middot; {stats.sample_rate ?? '--'}Hz &middot; peak {stats.peak ?? '--'}
        </span>
      </div>

      <div
        className="scope-canvas-wrap"
        onMouseMove={onMove}
        onMouseLeave={() => setHover(null)}
        onClick={seek}
      >
        <canvas ref={canvasRef} className="scope-canvas" />

        {hover && (
          <div
            className="scope-tip"
            style={{
              left: `${Math.min(Math.max(hover.x, 74), (canvasRef.current?.getBoundingClientRect().width || 400) - 74)}px`,
            }}
          >
            <span>{hover.t}s</span>
            <span>peak {hover.peak}</span>
            <span>rms {hover.rms}</span>
            <span>~{hover.freq} Hz</span>
          </div>
        )}

        {!hover && <span className="scope-nudge">hover the wave for the numbers</span>}
      </div>

      {audioSrc && (
        <div className="scope-transport">
          <button
            type="button"
            className="scope-play"
            onClick={() => {
              const audio = audioRef.current
              if (!audio) return
              if (audio.paused) { audio.play(); setPlaying(true) }
              else { audio.pause(); setPlaying(false) }
            }}
            aria-label={playing ? 'Pause' : 'Play'}
          >
            {playing ? '❚❚' : '▶'}
          </button>

          <input
            className="scope-volume"
            type="range" min="0" max="1" step="0.01"
            value={volume}
            onChange={(e) => setVolume(parseFloat(e.target.value))}
            aria-label="Volume"
          />
          <span className="scope-vol-read mono">{Math.round(volume * 100)}</span>

          <audio
            ref={audioRef}
            src={audioSrc}
            onEnded={() => { setPlaying(false); setProgress(0) }}
            onTimeUpdate={(e) => {
              const a = e.currentTarget
              if (a.duration) setProgress(a.currentTime / a.duration)
            }}
          />
        </div>
      )}
    </div>
  )
}
