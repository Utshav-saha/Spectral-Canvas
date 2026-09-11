import { useState, useEffect, useRef } from 'react'
import { useSearchParams } from 'react-router-dom'
import DoodleCanvas from '../components/DoodleCanvas'
import WaveformScope from '../components/WaveformScope'
import * as api from '../api/client'
import './Simulate.css'

const MODES = [
  { id: 'image', label: 'Send an image' },
  { id: 'text', label: 'Send text' },
  { id: 'doodle', label: 'Live doodle' },
]

export default function Simulate() {
  const [params] = useSearchParams()
  const [mode, setMode] = useState(params.get('mode') || 'image')

  const [file, setFile] = useState(null)
  const [filePreview, setFilePreview] = useState(null)
  const [textMode, setTextMode] = useState('type')      // type | upload
  const [text, setText] = useState('')
  const [doodle, setDoodle] = useState(null)

  const [secure, setSecure] = useState(false)
  const [caller, setCaller] = useState('')
  const [receiver, setReceiver] = useState('')
  const [pin, setPin] = useState('')

  const [colour, setColour] = useState(false)
  const [size, setSize] = useState(64)

  const [sending, setSending] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState(null)
  const [wave, setWave] = useState(null)
  const [selected, setSelected] = useState(false)
  const dropRef = useRef(null)

  useEffect(() => { const m = params.get('mode'); if (m) setMode(m) }, [params])

  const pickFile = (f) => {
    if (!f) return
    setFile(f)
    setFilePreview(URL.createObjectURL(f))
    setError('')
  }

  const send = async () => {
    setError(''); setSending(true); setResult(null); setWave(null); setSelected(false)
    try {
      const payload = {
        source_type: mode,
        text: mode === 'text' ? text : null,
        data_url: mode === 'doodle' ? doodle : null,
        target_width: size, target_height: size,
        mode: colour ? 'RGB' : 'L',
        security_enabled: secure,
        caller: secure ? caller : null,
        receiver: secure ? receiver : null,
        pin: secure ? pin : null,
      }
      const response = await api.encode(payload, mode === 'image' ? file : null)
      setResult(response)
      setWave(await api.waveform(response.session_id, 900))
    } catch (e) {
      setError(e.message)
    } finally {
      setSending(false)
    }
  }

  const ready =
    (mode === 'image' && file) ||
    (mode === 'text' && text.trim()) ||
    (mode === 'doodle' && doodle)

  return (
    <main className="sim shell">
      <header className="sim-head">
        <h1 className="sim-title">Send a transmission</h1>
        <p className="sim-sub">
          Pick what to send, lock it if you want to, then listen to what your picture sounds like.
        </p>
      </header>

      <div className="sim-modes" role="tablist">
        {MODES.map((m) => (
          <button
            key={m.id} role="tab" type="button"
            aria-selected={mode === m.id}
            className={`sim-mode ${mode === m.id ? 'is-on' : ''}`}
            onClick={() => { setMode(m.id); setError('') }}
          >{m.label}</button>
        ))}
      </div>

      <div className="sim-grid">
        {/* ----------------- source ----------------- */}
        <section className="panel sim-source">
          {mode === 'image' && (
            <div
              ref={dropRef}
              className="drop"
              onDragOver={(e) => { e.preventDefault(); dropRef.current?.classList.add('is-over') }}
              onDragLeave={() => dropRef.current?.classList.remove('is-over')}
              onDrop={(e) => {
                e.preventDefault()
                dropRef.current?.classList.remove('is-over')
                pickFile(e.dataTransfer.files?.[0])
              }}
            >
              {filePreview ? (
                <>
                  <img src={filePreview} alt="Selected" className="drop-preview" />
                  <p className="drop-name mono">{file?.name}</p>
                  <button type="button" className="btn btn-ghost"
                          onClick={() => { setFile(null); setFilePreview(null) }}>
                    Choose a different file
                  </button>
                </>
              ) : (
                <>
                  <p className="drop-title">Drop an image here</p>
                  <p className="drop-hint">PNG, JPG or BMP. High-contrast pictures come back sharpest.</p>
                  <label className="btn btn-primary drop-btn">
                    Browse files
                    <input type="file" accept="image/*" hidden
                           onChange={(e) => pickFile(e.target.files?.[0])} />
                  </label>
                </>
              )}
            </div>
          )}

          {mode === 'text' && (
            <div className="text-pane">
              <div className="toggle">
                <button type="button" className={textMode === 'type' ? 'is-on' : ''}
                        onClick={() => setTextMode('type')}>Type it</button>
                <button type="button" className={textMode === 'upload' ? 'is-on' : ''}
                        onClick={() => setTextMode('upload')}>Upload a file</button>
              </div>
              <p className="toggle-hint">
                {textMode === 'type'
                  ? 'Whatever you type is drawn as a picture, then sent as sound. Short lines read best.'
                  : 'Upload a .txt or .md file and its contents are drawn the same way.'}
              </p>

              {textMode === 'type' ? (
                <textarea
                  className="input text-area" rows={7} value={text} maxLength={2000}
                  placeholder="Type a short message"
                  onChange={(e) => setText(e.target.value)}
                />
              ) : (
                <label className="btn btn-ghost">
                  Choose a text file
                  <input type="file" accept=".txt,.md,text/plain" hidden
                         onChange={async (e) => {
                           const f = e.target.files?.[0]
                           if (f) { setText(await f.text()); setTextMode('type') }
                         }} />
                </label>
              )}
              <p className="text-count mono">{text.length} / 2000</p>
            </div>
          )}

          {mode === 'doodle' && (
            <DoodleCanvas onCommit={setDoodle} committed={!!doodle} />
          )}
        </section>

        {/* ----------------- settings ----------------- */}
        <aside className="panel sim-settings">
          <h2 className="sim-h2">Settings</h2>

          <label className="field-label" htmlFor="grid-size">Grid size</label>
          <select id="grid-size" className="input" value={size}
                  onChange={(e) => setSize(+e.target.value)}>
            <option value={32}>32 x 32 — quickest</option>
            <option value={48}>48 x 48</option>
            <option value={64}>64 x 64 — balanced</option>
            <option value={96}>96 x 96 — most detail, longest audio</option>
          </select>

          <label className="switch">
            <input type="checkbox" checked={colour} onChange={(e) => setColour(e.target.checked)} />
            <span>Send in colour</span>
          </label>
          <p className="switch-note">Colour sends three passes, so the audio runs three times as long.</p>

          <hr className="sim-rule" />

          <label className="switch">
            <input type="checkbox" checked={secure} onChange={(e) => setSecure(e.target.checked)} />
            <span>Lock this transmission</span>
          </label>
          <p className="switch-note">
            The numbers and PIN shuffle the picture and hide a keyed hiss in the audio.
            Anyone without them hears static.
          </p>

          {secure && (
            <div className="lockfields">
              <div>
                <label className="field-label" htmlFor="caller">Your number</label>
                <input id="caller" className="input mono" inputMode="numeric" maxLength={11}
                       placeholder="11 digits" value={caller}
                       onChange={(e) => setCaller(e.target.value.replace(/\D/g, ''))} />
              </div>
              <div>
                <label className="field-label" htmlFor="receiver">Their number</label>
                <input id="receiver" className="input mono" inputMode="numeric" maxLength={11}
                       placeholder="11 digits" value={receiver}
                       onChange={(e) => setReceiver(e.target.value.replace(/\D/g, ''))} />
              </div>
              <div>
                <label className="field-label" htmlFor="pin">PIN</label>
                <input id="pin" className="input mono" inputMode="numeric" maxLength={8}
                       placeholder="4 to 8 digits" value={pin}
                       onChange={(e) => setPin(e.target.value.replace(/\D/g, ''))} />
              </div>
            </div>
          )}

          <button type="button" className="btn btn-primary sim-send"
                  onClick={send} disabled={!ready || sending}>
            {sending ? 'Sending…' : 'Send'}
          </button>

          {!ready && <p className="switch-note">
            {mode === 'doodle' ? 'Save your drawing first.' : 'Add something to send.'}
          </p>}

          {error && <div className="alert alert-error">{error}</div>}
        </aside>
      </div>

      {/* ----------------- result ----------------- */}
      {result && (
        <section className="sim-result">
          <h2 className="sim-h2">On the wire</h2>

          <div className="result-grid">
            <div className="file-col">
              <button
                type="button"
                className={`filecard ${selected ? 'is-on' : ''}`}
                onClick={() => setSelected(true)}
              >
                <span className="filecard-icon mono">WAV</span>
                <span className="filecard-body">
                  <b className="mono">output.wav</b>
                  <span className="mono">{result.duration.toFixed(2)}s · {result.rows}×{result.columns}</span>
                </span>
                <span className={`filecard-tag ${result.encrypted ? 'locked' : ''}`}>
                  {result.encrypted ? 'Locked' : 'Open'}
                </span>
              </button>
              {!selected && <p className="switch-note">Click the file to open its waveform.</p>}

              <figure className="sent-preview">
                <img src={api.previewUrl(result.session_id)} alt="What was sent, at transmission size" />
                <figcaption>Sent at {result.rows} × {result.columns}</figcaption>
              </figure>

              <div className="result-actions">
                <button type="button" className="btn btn-ghost"
                        onClick={() => api.downloadUrl(api.audioUrl(result.session_id), 'output.wav')}>
                  Download audio
                </button>
                {mode === 'doodle' && doodle && (
                  <a className="btn btn-ghost" href={doodle} download="doodle.png">Download drawing</a>
                )}
              </div>
            </div>

            <div className="scope-col panel">
              {selected && wave
                ? <WaveformScope data={wave} audioSrc={api.audioUrl(result.session_id)} title="output.wav" />
                : <p className="scope-empty">Select the file to inspect its waveform.</p>}
            </div>
          </div>
        </section>
      )}
    </main>
  )
}
