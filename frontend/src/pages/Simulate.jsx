import { useState, useEffect, useRef } from 'react'
import { useSearchParams } from 'react-router-dom'
import DoodleCanvas from '../components/DoodleCanvas'
import WaveformScope from '../components/WaveformScope'
import * as api from '../api/client'
import './Simulate.css'

const MODES = [
  { id: 'image', label: 'Image', hint: 'A photo, a logo, any picture file' },
  { id: 'text', label: 'Text', hint: 'Typed, or a .txt you upload' },
  { id: 'doodle', label: 'Doodle', hint: 'Painted here, right now' },
]

/* The four stages of a send, shown as a rail across the top. It is the same
   sequence the console teaches on the landing page, so a visitor arrives here
   already knowing where they are. */
const STAGES = ['Source', 'Settings', 'Encode', 'On the wire']

/* Text skips the picture entirely: each byte is split into two 4-bit symbols
   and each symbol is one of 16 tones (backend/spectral/text/text_codec.py). */
const TEXT_MAX_CHARS = 1400

export default function Simulate() {
  const [params] = useSearchParams()
  const [mode, setMode] = useState(params.get('mode') || 'image')

  const [file, setFile] = useState(null)
  const [filePreview, setFilePreview] = useState(null)
  const [textMode, setTextMode] = useState('type')
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
      const locked = secure && !isText
      const payload = {
        source_type: mode,
        text: isText ? text : null,
        data_url: mode === 'doodle' ? doodle : null,
        target_width: size, target_height: size,
        mode: colour ? 'RGB' : 'L',
        security_enabled: locked,
        caller: locked ? caller : null,
        receiver: locked ? receiver : null,
        pin: locked ? pin : null,
      }
      const response = await api.encode(payload, mode === 'image' ? file : null)
      setResult({ ...response, sentText: isText ? text : null })
      setWave(await api.waveform(response.session_id, 2000))
    } catch (e) {
      setError(e.message)
    } finally {
      setSending(false)
    }
  }

  const isText = mode === 'text'

  const ready =
    (mode === 'image' && file) ||
    (mode === 'text' && text.trim()) ||
    (mode === 'doodle' && doodle)

  const stage = result ? 3 : sending ? 2 : ready ? 1 : 0

  return (
    <main className="sim">
      <div className="shell">
        <p className="slug">
          <span>Send</span>
          <span>{mode}</span>
          {isText ? (
            <>
              <span>16-tone MFSK</span>
              <span>{text.length} chars</span>
              <span>Open</span>
              <span className="slug-sep" />
              <b>44.1 kHz &middot; 2&ndash;5 kHz</b>
            </>
          ) : (
            <>
              <span>{size}&times;{size}</span>
              <span>{colour ? 'RGB, 3 passes' : 'Grayscale'}</span>
              <span>{secure ? 'Locked' : 'Open'}</span>
              <span className="slug-sep" />
              <b>44.1 kHz &middot; 1&ndash;8 kHz</b>
            </>
          )}
        </p>
      </div>

      <div className="shell">
        <header className="sim-head">
          <div className="sim-head-copy">
            <h1 className="sim-title">Send a transmission</h1>
            <p className="sim-sub">
              Choose what to send, lock it if you want to, then listen to what your
              picture sounds like on the way out.
            </p>
          </div>

          {isText ? (
            <dl className="head-readout">
              <dt>Tones</dt><dd>16</dd>
              <dt>Symbols</dt><dd>{new TextEncoder().encode(text).length * 2}</dd>
              <dt>Band</dt><dd>2&ndash;5 kHz</dd>
              <dt>Symbol</dt><dd>0.05 s</dd>
              <dt>Bits</dt><dd>4 / tone</dd>
            </dl>
          ) : (
            <dl className="head-readout">
              <dt>Lanes</dt><dd>{size}</dd>
              <dt>Frames</dt><dd>{size}</dd>
              <dt>Band</dt><dd>1&ndash;8 kHz</dd>
              <dt>Frame</dt><dd>0.05 s</dd>
              <dt>Passes</dt><dd>{colour ? 3 : 1}</dd>
            </dl>
          )}
        </header>

        <ol className="stagerail" aria-label="Progress">
          {STAGES.map((s, i) => (
            <li key={s} className={i < stage ? 'is-done' : i === stage ? 'is-now' : ''}>
              <span className="led" aria-hidden="true" />
              <span className="stagerail-k">{s}</span>
            </li>
          ))}
        </ol>

        <div className="sim-grid">
          {/* ----------------- source ----------------- */}
          <section className="module sim-source">
            <div className="module-head">
              <h2>Source</h2>
              <div className="modeswitch" role="tablist" aria-label="What to send">
                {MODES.map((m) => (
                  <button
                    key={m.id} role="tab" type="button"
                    aria-selected={mode === m.id}
                    className={`modeswitch-b ${mode === m.id ? 'is-on' : ''}`}
                    onClick={() => { setMode(m.id); setError('') }}
                  >{m.label}</button>
                ))}
              </div>
            </div>

            <p className="module-hint">{MODES.find((m) => m.id === mode)?.hint}</p>

            <div className="module-body">
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
                      <img src={filePreview} alt="The picture you selected" className="drop-preview" />
                      <p className="drop-name mono">{file?.name}</p>
                      <button type="button" className="btn btn-ghost"
                              onClick={() => { setFile(null); setFilePreview(null) }}>
                        Choose a different file
                      </button>
                    </>
                  ) : (
                    <>
                      <p className="drop-title">Drop an image here</p>
                      <p className="drop-hint">PNG, JPG or BMP, up to 12&nbsp;MB. High-contrast pictures come back sharpest.</p>
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
                  <div className="toggle" role="group" aria-label="How to supply the text">
                    <button type="button" className={textMode === 'type' ? 'is-on' : ''}
                            onClick={() => setTextMode('type')}>Type it</button>
                    <button type="button" className={textMode === 'upload' ? 'is-on' : ''}
                            onClick={() => setTextMode('upload')}>Upload a file</button>
                  </div>
                  <p className="toggle-hint">
                    {textMode === 'type'
                      ? 'Every character becomes two tones, one after another, picked from sixteen. The receiver listens for which tone is playing and spells the message back out.'
                      : 'Upload a .txt or .md file and its contents are sent the same way.'}
                  </p>

                  {textMode === 'type' ? (
                    <>
                      <label className="sr-only" htmlFor="sim-text">Message to send</label>
                      <textarea
                        id="sim-text"
                        className="input text-area" rows={8} value={text} maxLength={TEXT_MAX_CHARS}
                        placeholder="Type a short message"
                        onChange={(e) => setText(e.target.value)}
                      />
                      <p className="text-count mono">
                        <span>{text.length}</span> / {TEXT_MAX_CHARS}
                      </p>
                    </>
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
                </div>
              )}

              {mode === 'doodle' && (
                <DoodleCanvas onCommit={setDoodle} committed={!!doodle} />
              )}
            </div>
          </section>

          {/* ----------------- settings ----------------- */}
          <aside className="module sim-settings">
            <div className="module-head">
              <h2>Settings</h2>
            </div>

            <div className="module-body">
              {isText ? (
                <p className="field-note">
                  Text is sent one tone at a time, so grid size and colour do not apply. Each
                  symbol lasts 0.05&nbsp;s, which makes a 100-character message about ten
                  seconds long. Locking is not available for text yet.
                </p>
              ) : (
              <>
              <div className="field">
                <label className="field-label" htmlFor="grid-size">Grid size</label>
                <select id="grid-size" className="input" value={size}
                        onChange={(e) => setSize(+e.target.value)}>
                  <option value={32}>32 × 32 — quickest</option>
                  <option value={48}>48 × 48</option>
                  <option value={64}>64 × 64 — balanced</option>
                  <option value={96}>96 × 96 — most detail, longest audio</option>
                </select>
                <p className="field-note">
                  The grid size is the number of frequency lanes. {size} rows means {size} tones in the air at once.
                </p>
              </div>

              <label className="switch">
                <input type="checkbox" checked={colour} onChange={(e) => setColour(e.target.checked)} />
                <span className="switch-box" aria-hidden="true" />
                <span className="switch-text">Send in colour</span>
              </label>
              <p className="field-note">Colour sends three passes, so the audio runs three times as long.</p>

              <hr className="module-rule" />

              <label className="switch">
                <input type="checkbox" checked={secure} onChange={(e) => setSecure(e.target.checked)} />
                <span className="switch-box" aria-hidden="true" />
                <span className="switch-text">Lock this transmission</span>
              </label>
              <p className="field-note">
                The numbers and PIN shuffle the rows and columns, and hide a keyed hiss under
                the audio. Anyone without them rebuilds static.
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
              </>
              )}
            </div>

            <div className="module-foot">
              <button type="button" className="btn btn-primary sim-send"
                      onClick={send} disabled={!ready || sending}>
                {sending ? 'Sending…' : 'Send'}
              </button>

              {!ready && (
                <p className="field-note">
                  {mode === 'doodle' ? 'Save your drawing first.' : 'Add something to send.'}
                </p>
              )}

              {error && <div className="alert alert-error">{error}</div>}
            </div>
          </aside>
        </div>

        {/* ----------------- result ----------------- */}
        {result && (
          <section className="sim-result">
            <div className="rule-head">
              <h2>On the wire</h2>
            </div>

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
                    <span className="mono">
                      {result.duration.toFixed(2)}s ·{' '}
                      {result.kind === 'text' ? `${result.columns} symbols` : `${result.rows}×${result.columns}`}
                    </span>
                  </span>
                  <span className={`filecard-tag ${result.encrypted ? 'locked' : ''}`}>
                    <span className={`led ${result.encrypted ? 'is-locked' : 'is-open'}`} aria-hidden="true" />
                    {result.encrypted ? 'Locked' : 'Open'}
                  </span>
                </button>
                {!selected && <p className="field-note">Open the file to inspect its waveform.</p>}

                {result.kind === 'text' ? (
                  <figure className="sent-preview">
                    <p className="message-well">{result.sentText}</p>
                    <figcaption>
                      {result.metadata.bytes} bytes · {result.columns} tones
                    </figcaption>
                  </figure>
                ) : (
                  <figure className="sent-preview">
                    <img src={api.previewUrl(result.session_id)} alt="What was sent, at transmission size" />
                    <figcaption>Sent at {result.rows} × {result.columns}</figcaption>
                  </figure>
                )}

                <div className="result-actions">
                  <button type="button" className="btn btn-primary"
                          onClick={() => api.downloadUrl(api.audioUrl(result.session_id), 'output.wav')}>
                    Download audio
                  </button>
                  {mode === 'doodle' && doodle && (
                    <a className="btn btn-ghost" href={doodle} download="doodle.png">Download drawing</a>
                  )}
                </div>
              </div>

              <div className="scope-col module">
                {selected && wave
                  ? <WaveformScope data={wave} audioSrc={api.audioUrl(result.session_id)} title="output.wav" />
                  : <p className="scope-empty">Open the file to inspect its waveform.</p>}
              </div>
            </div>
          </section>
        )}
      </div>
    </main>
  )
}
