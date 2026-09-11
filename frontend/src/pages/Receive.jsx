import { useState, useRef } from 'react'
import WaveformScope from '../components/WaveformScope'
import * as api from '../api/client'
import './Simulate.css'
import './Receive.css'

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
      setWave(await api.waveform(response.session_id, 900))
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

  return (
    <main className="rcv shell">
      <header className="sim-head">
        <h1 className="sim-title">Receive a transmission</h1>
        <p className="sim-sub">Load an audio file and rebuild the picture hiding inside it.</p>
      </header>

      <div className="rcv-grid">
        <section className="panel rcv-left">
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

          {busy && <p className="switch-note">Reading the file…</p>}

          {info && (
            <>
              <div className={`status ${info.encrypted ? 'status-locked' : 'status-open'}`}>
                <span className="status-dot" aria-hidden="true" />
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
                    {info.stats.duration}s · {info.stats.sample_rate}Hz
                  </span>
                </span>
                <span className={`filecard-tag ${info.encrypted ? 'locked' : ''}`}>
                  {info.encrypted ? 'Locked' : 'Open'}
                </span>
              </button>
              {!selected && <p className="switch-note">Click the file to open its waveform.</p>}

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
                  <button type="button" className="btn btn-aqua rcv-go"
                          onClick={open} disabled={opening}>
                    {opening ? 'Rebuilding…' : info.encrypted ? 'Unlock and rebuild' : 'Rebuild picture'}
                  </button>
                </div>
              )}
            </>
          )}

          {error && <div className="alert alert-error">{error}</div>}
        </section>

        <section className="rcv-right">
          <div className="panel scope-col">
            {selected && wave
              ? <WaveformScope data={wave} audioSrc={file ? URL.createObjectURL(file) : null}
                               title={file?.name || 'received.wav'} accent="aqua" />
              : <p className="scope-empty">Load a file and select it to inspect the waveform.</p>}
          </div>

          {recovered && (
            <div className="panel recovered">
              <h2 className="sim-h2">Rebuilt picture</h2>
              <img src={recovered.url} alt="The picture rebuilt from the audio" className="recovered-img" />
              <div className="recovered-foot">
                <span className="mono">
                  {recovered.rows} × {recovered.columns} · {recovered.mode === 'RGB' ? 'colour' : 'grayscale'}
                  {recovered.metrics ? ` · ${recovered.metrics.psnr === null ? '' : `PSNR ${recovered.metrics.psnr} dB`}` : ''}
                </span>
                <button type="button" className="btn btn-ghost"
                        onClick={() => api.downloadUrl(recovered.url, 'recovered.png')}>
                  Download picture
                </button>
              </div>
              {recovered.decrypted && (
                <p className="switch-note">
                  If this looks like static, the numbers or PIN do not match the ones it was sent with.
                </p>
              )}
            </div>
          )}
        </section>
      </div>
    </main>
  )
}
