import { useState, useRef, useEffect } from 'react'
import WaveformScope from '../components/WaveformScope'
import * as api from '../api/client'
import './Simulate.css'
import './Receive.css'

/* Receive. The mirror of Send, and the page where the claim is settled: a
   file of audio goes in, a picture comes out. The rebuilt image paints in
   from the top the way the console's screen does, because that is what the
   decoder is actually doing — measuring one slice at a time. */

const STAGES = ['File', 'Inspect', 'Key', 'Rebuilt']

export default function Receive() {
  const [file, setFile] = useState(null)
  const [info, setInfo] = useState(null)
  const [wave, setWave] = useState(null)
  const [selected, setSelected] = useState(false)

  const [caller, setCaller] = useState('')
  const [receiver, setReceiver] = useState('')
  const [pin, setPin] = useState('')

  const [busy, setBusy] = useState(false)
  const [opening, setOpening] = useState(false)
  const [error, setError] = useState('')
  const [recovered, setRecovered] = useState(null)
  const dropRef = useRef(null)

  const take = async (f) => {
    if (!f) return
    setFile(f); setError(''); setRecovered(null); setInfo(null); setWave(null)
    setSelected(false); setBusy(true)
    try {
      const response = await api.inspect(f)
      setInfo(response)
      setWave(await api.waveform(response.session_id, 2000))
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  const open = async () => {
    setError(''); setOpening(true)
    try {
      const response = await api.decode({
        session_id: info.session_id,
        caller: info.encrypted ? caller : null,
        receiver: info.encrypted ? receiver : null,
        pin: info.encrypted ? pin : null,
      })
      // cache-bust so a second attempt with a different PIN actually repaints
      setRecovered({ ...response, url: `${response.image_url}?t=${Date.now()}` })
    } catch (e) {
      setError(e.message)
    } finally {
      setOpening(false)
    }
  }

  /* One object URL per file, released when the file changes. Minting it inline
     in the markup handed the audio element a fresh src on every render, which
     reset playback the moment anyone touched the PIN fields. */
  const [audioSrc, setAudioSrc] = useState(null)
  useEffect(() => {
    if (!file) { setAudioSrc(null); return }
    const url = URL.createObjectURL(file)
    setAudioSrc(url)
    return () => URL.revokeObjectURL(url)
  }, [file])

  const stage = recovered ? 3 : info ? 2 : file ? 1 : 0

  return (
    <main className="rcv">
      <div className="shell">
        <p className="slug">
          <span>Receive</span>
          <span>{file ? file.name : 'No file'}</span>
          {info && <span>{info.stats.duration}s</span>}
          {info && <span>{info.stats.sample_rate} Hz</span>}
          <span>{info ? (info.encrypted ? 'Locked' : 'Open') : '—'}</span>
          <span className="slug-sep" />
          <b>Decoder ready</b>
        </p>
      </div>

      <div className="shell">
        <header className="sim-head">
          <div className="sim-head-copy">
            <h1 className="sim-title">Receive a transmission</h1>
            <p className="sim-sub">
              Load an audio file and rebuild the picture hiding inside it. Nothing but
              the sound is used.
            </p>
          </div>

          <dl className="head-readout">
            <dt>Duration</dt><dd>{info ? `${info.stats.duration} s` : '—'}</dd>
            <dt>Sample rate</dt><dd>{info ? `${info.stats.sample_rate} Hz` : '—'}</dd>
            <dt>Metadata</dt><dd>{info ? (info.has_metadata ? 'Present' : 'None') : '—'}</dd>
            <dt>Key</dt><dd>{info ? (info.encrypted ? 'Required' : 'Not set') : '—'}</dd>
            <dt>Rebuilt</dt>
            <dd>{recovered ? `${recovered.rows} × ${recovered.columns}` : '—'}</dd>
          </dl>
        </header>

        <ol className="stagerail" aria-label="Progress">
          {STAGES.map((s, i) => (
            <li key={s} className={i < stage ? 'is-done' : i === stage ? 'is-now' : ''}>
              <span className="led" aria-hidden="true" />
              <span className="stagerail-k">{s}</span>
            </li>
          ))}
        </ol>

        <div className="rcv-grid">
          <section className="module rcv-left">
            <div className="module-head">
              <h2>Incoming</h2>
            </div>

            <div className="module-body">
              <div
                ref={dropRef}
                className="drop"
                onDragOver={(e) => { e.preventDefault(); dropRef.current?.classList.add('is-over') }}
                onDragLeave={() => dropRef.current?.classList.remove('is-over')}
                onDrop={(e) => {
                  e.preventDefault(); dropRef.current?.classList.remove('is-over')
                  take(e.dataTransfer.files?.[0])
                }}
              >
                {file ? (
                  <>
                    <p className="drop-title mono">{file.name}</p>
                    <p className="drop-hint">{(file.size / 1024).toFixed(0)} KB</p>
                    <label className="btn btn-ghost drop-btn">
                      Load a different file
                      <input type="file" accept="audio/wav,.wav" hidden
                             onChange={(e) => take(e.target.files?.[0])} />
                    </label>
                  </>
                ) : (
                  <>
                    <p className="drop-title">Drop a .wav here</p>
                    <p className="drop-hint">Any transmission made on the send page will work.</p>
                    <label className="btn btn-primary drop-btn">
                      Browse files
                      <input type="file" accept="audio/wav,.wav" hidden
                             onChange={(e) => take(e.target.files?.[0])} />
                    </label>
                  </>
                )}
              </div>

              {busy && <p className="field-note">Reading the file…</p>}

              {info && (
                <div className="rcv-stack">
                  <div className={`alert ${info.encrypted ? 'alert-error' : 'alert-ok'}`}>
                    <p>{info.message}</p>
                  </div>

                  <button
                    type="button"
                    className={`filecard ${selected ? 'is-on' : ''}`}
                    onClick={() => setSelected(true)}
                  >
                    <span className="filecard-icon mono">WAV</span>
                    <span className="filecard-body">
                      <b className="mono">{file?.name}</b>
                      <span className="mono">
                        {info.stats.duration}s · {info.stats.sample_rate} Hz
                      </span>
                    </span>
                    <span className={`filecard-tag ${info.encrypted ? 'locked' : ''}`}>
                      <span className={`led ${info.encrypted ? 'is-locked' : 'is-open'}`} aria-hidden="true" />
                      {info.encrypted ? 'Locked' : 'Open'}
                    </span>
                  </button>
                  {!selected && <p className="field-note">Open the file to inspect its waveform.</p>}

                  {info.has_metadata && (
                    <div className="unlock">
                      {info.encrypted && (
                        <div className="lockfields">
                          <div>
                            <label className="field-label" htmlFor="r-caller">Your number</label>
                            <input id="r-caller" className="input mono" inputMode="numeric" maxLength={11}
                                   placeholder="11 digits" value={caller}
                                   onChange={(e) => setCaller(e.target.value.replace(/\D/g, ''))} />
                          </div>
                          <div>
                            <label className="field-label" htmlFor="r-receiver">Their number</label>
                            <input id="r-receiver" className="input mono" inputMode="numeric" maxLength={11}
                                   placeholder="11 digits" value={receiver}
                                   onChange={(e) => setReceiver(e.target.value.replace(/\D/g, ''))} />
                          </div>
                          <div>
                            <label className="field-label" htmlFor="r-pin">PIN</label>
                            <input id="r-pin" className="input mono" inputMode="numeric" maxLength={8}
                                   placeholder="4 to 8 digits" value={pin}
                                   onChange={(e) => setPin(e.target.value.replace(/\D/g, ''))} />
                          </div>
                        </div>
                      )}
                      <button type="button" className="btn btn-primary rcv-go"
                              onClick={open} disabled={opening}>
                        {opening ? 'Rebuilding…' : info.encrypted ? 'Unlock and rebuild' : 'Rebuild picture'}
                      </button>
                    </div>
                  )}
                </div>
              )}

              {error && <div className="alert alert-error"><p>{error}</p></div>}
            </div>
          </section>

          <section className="rcv-right">
            <div className="module scope-col">
              {selected && wave
                ? <WaveformScope data={wave} audioSrc={audioSrc}
                                 title={file?.name || 'received.wav'} accent="open" />
                : <p className="scope-empty">Load a file and open it to inspect the waveform.</p>}
            </div>

            {recovered && (
              <div className="module recovered">
                <div className="module-head">
                  <h2>Rebuilt picture</h2>
                  <span className="mono recovered-meta">
                    {recovered.rows} × {recovered.columns} ·{' '}
                    {recovered.mode === 'RGB' ? 'colour' : 'grayscale'}
                    {recovered.metrics && recovered.metrics.psnr !== null
                      ? ` · PSNR ${recovered.metrics.psnr} dB`
                      : ''}
                  </span>
                </div>

                <div className="module-body">
                  <figure className="recovered-frame">
                    <img
                      key={recovered.url}
                      src={recovered.url}
                      alt="The picture rebuilt from the audio"
                      className="recovered-img"
                    />
                  </figure>

                  <div className="recovered-foot">
                    {recovered.decrypted && (
                      <p className="field-note">
                        If this looks like static, the numbers or PIN do not match the
                        ones it was sent with. The data is all still there — it is just
                        being unshuffled with the wrong permutation.
                      </p>
                    )}
                    <button type="button" className="btn btn-ghost"
                            onClick={() => api.downloadUrl(recovered.url, 'recovered.png')}>
                      Download picture
                    </button>
                  </div>
                </div>
              </div>
            )}
          </section>
        </div>
      </div>
    </main>
  )
}
