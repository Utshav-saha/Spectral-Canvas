import { useState, useEffect, useRef } from 'react'
import { Link } from 'react-router-dom'
import * as api from '../api/client'
import './Simulate.css'
import './Receive.css'
import './Experiments.css'

/* The channel bench. One image row is one frequency, so what an LTI channel
   does to the spectrum is directly visible as which rows of the picture it
   damages. The per-row error plot is the whole argument in one figure:
   a low-pass ramps at the top, a band-stop punches a contiguous hole, and
   clipping scatters, because intermodulation puts energy on rows that were
   never transmitted. (backend/spectral/channel/effects.py) */

const STAGES = ['Picture', 'Chain', 'Run', 'Measured']

const FALLBACK = { effects: [], presets: [], max_effects: 6, inversion: [], model: null }

function RowError({ values, rows }) {
  /* Mean absolute pixel error per image row, drawn top row first so it lines
     up with the picture beside it. Deliberately not oxide red: nothing here
     is pressable. */
  if (!values?.length) return null
  const peak = Math.max(...values, 1e-6)
  const w = 240
  const h = Math.max(120, Math.min(300, values.length * 3))
  const step = h / values.length

  return (
    <figure className="rowerr">
      <svg viewBox={`0 0 ${w} ${h}`} role="img" width="100%" height={h}
           aria-label={`Error per image row, peaking at row ${values.indexOf(peak)}`}>
        <rect x="0" y="0" width={w} height={h} className="rowerr-bg" />
        {values.map((v, i) => (
          <rect key={i} x="0" y={i * step} width={Math.max(0.6, (v / peak) * w)}
                height={Math.max(0.8, step - 0.4)} className="rowerr-bar" />
        ))}
      </svg>
      <figcaption>
        Error per row, top row first. {rows} rows, worst {peak.toFixed(1)} of 255.
      </figcaption>
    </figure>
  )
}

/* The three columns RESTORATION_PLAN Phase 5 asks for: damaged, then what the
   analytic inverse got back, then what the model added on top. Never averaged
   into one number, because the whole point is that they differ per effect -
   the inverse wins on LTI damage, the model only earns its place where there
   is no inverse at all. */
function Stages({ result }) {
  const damaged = result.metrics?.mae
  const inverted = result.undone?.metrics?.mae
  const restored = result.restored?.metrics?.mae

  const best = [damaged, inverted, restored].filter((v) => v != null)
  const lowest = best.length ? Math.min(...best) : null
  const drop = (v) => (v == null || damaged == null || !damaged) ? null
    : Math.round((1 - v / damaged) * 100)

  const columns = [
    { key: 'damaged', label: 'Damaged', value: damaged, note: 'straight off the channel' },
    { key: 'inverted', label: '+ LTI inverse', value: inverted, note: result.undone ? `eps ${result.undone.epsilon}` : null },
    { key: 'restored', label: '+ model', value: restored, note: result.restored ? `run on the ${result.restored.after === 'inverse' ? 'inverted' : 'damaged'} picture` : null },
  ].filter((c) => c.value != null)

  return (
    <div className="exp-inverse">
      <table className="exp-stages">
        <thead>
          <tr>
            <th>Stage</th><th>MAE</th><th>Error removed</th><th></th>
          </tr>
        </thead>
        <tbody>
          {columns.map((c) => (
            <tr key={c.key} className={c.value === lowest ? 'is-best' : ''}>
              <td>{c.label}</td>
              <td className="mono">{c.value}</td>
              <td className="mono">{c.key === 'damaged' ? '—' : `${drop(c.value)}%`}</td>
              <td className="exp-stage-note">{c.note}</td>
            </tr>
          ))}
        </tbody>
      </table>

      {result.undone && (
        <ul className="exp-verdicts">
          {result.undone.undone.map((id) => (
            <li key={id}><span className="led is-open" aria-hidden="true" />
              <b>{id}</b> undone &mdash; it was LTI, so dividing by H(f) brought it back
            </li>
          ))}
          {result.undone.attempted.map((id) => (
            <li key={id}><span className="led is-signal" aria-hidden="true" />
              <b>{id}</b> partly undone &mdash; the shoulders came back, the rows
              inside the stop band did not
            </li>
          ))}
          {result.undone.skipped.map((id) => (
            <li key={id}><span className="led is-locked" aria-hidden="true" />
              <b>{id}</b> has no inverse &mdash; nothing was faked
              {result.restored ? ', so this is what the model was asked to guess' : ''}
            </li>
          ))}
        </ul>
      )}

      {result.restored && restored != null && inverted != null && restored > inverted && (
        <p className="field-note">
          The model made this one worse. That is the expected answer when the
          inverse already did the job: it was trained on damage that has no
          inverse, and asking it to polish an already-correct picture only adds
          its own guesswork.
        </p>
      )}
    </div>
  )
}

/* The standing answer, independent of any run: which effects the analytic
   stage can undo and which need the model. Served from the backend so this
   table and the code that does the undoing cannot disagree. */
function InverseTable({ rows }) {
  if (!rows?.length) return null
  return (
    <div className="module exp-table">
      <div className="module-head"><h2>What an LTI inverse can undo</h2></div>
      <div className="module-body">
        <table className="exp-lti">
          <thead>
            <tr><th>Effect</th><th>Inverse?</th><th>Why</th></tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id}>
                <td className="mono">{r.id}</td>
                <td>
                  <span className={`led ${r.invertible ? 'is-open' : 'is-locked'}`}
                        aria-hidden="true" />
                  {r.invertible ? 'yes' : 'no'}
                </td>
                <td>{r.why}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="field-note">
          The &ldquo;no&rdquo; rows are exactly the jobs the learned model is for:
          clipping is nonlinear, a stop-band annihilates rows rather than
          attenuating them, aliasing folds two rows into one sum, and noise was
          added rather than convolved. Tick <b>Then run the model</b> to hand it
          those, and read the three stages side by side &mdash; it earns its place
          on clipping and heavy damage, and gets in the way where the inverse
          already worked.
        </p>
      </div>
    </div>
  )
}

function Metric({ label, value, unit }) {
  return (
    <>
      <dt>{label}</dt>
      <dd>{value === null || value === undefined ? 'exact' : `${value}${unit || ''}`}</dd>
    </>
  )
}

export default function Experiments() {
  const [cat, setCat] = useState(FALLBACK)
  const [file, setFile] = useState(null)
  const [preview, setPreview] = useState(null)
  const [size, setSize] = useState(64)
  const [sending, setSending] = useState(false)
  const [tx, setTx] = useState(null)

  const [chain, setChain] = useState([])
  const [running, setRunning] = useState(false)
  /* Run the LTI inverse over the damaged audio as well. On by default: the
     whole point of the page is which effects that division can undo. */
  const [undo, setUndo] = useState(true)
  /* And then hand what the inverse could not fix to the learned model. Off by
     default: it helps heavy, non-invertible damage and gets in the way of
     everything else, which the three columns below make visible. */
  const [restore, setRestore] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState('')
  const dropRef = useRef(null)

  useEffect(() => {
    api.channelEffects().then(setCat).catch(() => {})
  }, [])

  useEffect(() => {
    if (!file) { setPreview(null); return }
    const url = URL.createObjectURL(file)
    setPreview(url)
    return () => URL.revokeObjectURL(url)
  }, [file])

  const spec = (id) => cat.effects.find((e) => e.id === id)

  const addEffect = (id) => {
    const found = spec(id)
    if (!found || chain.length >= cat.max_effects) return
    const step = { type: id }
    found.params.forEach((p) => { step[p.id] = p.default })
    setChain([...chain, step])
    setError('')
  }

  const setParam = (index, key, value) => {
    setChain(chain.map((s, i) => (i === index ? { ...s, [key]: value } : s)))
  }

  const encode = async () => {
    setError(''); setSending(true); setResult(null); setTx(null)
    try {
      setTx(await api.encode({
        track: 'wav', source_type: 'image',
        target_width: size, target_height: size,
        mode: 'L', gray_levels: 16, security_enabled: false,
      }, file))
    } catch (e) {
      setError(e.message)
    } finally {
      setSending(false)
    }
  }

  const run = async () => {
    setError(''); setRunning(true)
    try {
      setResult(await api.channel({ session_id: tx.session_id, effects: chain, undo, restore }))
    } catch (e) {
      setError(e.message)
    } finally {
      setRunning(false)
    }
  }

  const stage = result ? 3 : tx ? (chain.length ? 2 : 1) : file ? 1 : 0

  return (
    <main className="exp">
      <div className="shell">
        <p className="slug">
          <span>Experiments</span>
          <span>{size}&times;{size}</span>
          <span>{chain.length} effect{chain.length === 1 ? '' : 's'}</span>
          <span>{result ? `MAE ${result.metrics?.mae ?? '—'}` : 'Not run'}</span>
          <span className="slug-sep" />
          <b>44.1 kHz &middot; 1&ndash;8 kHz</b>
        </p>
      </div>

      <div className="shell">
        <header className="sim-head">
          <div className="sim-head-copy">
            <h1 className="sim-title">Put it through a channel</h1>
            <p className="sim-sub">
              One image row is one frequency. So whatever a channel does to the
              spectrum, you can read straight off the picture: a low-pass fades
              the top rows, a band-stop deletes a stripe, and clipping invents
              rows that were never sent.
            </p>
          </div>

          <dl className="head-readout">
            <Metric label="MAE" value={result?.metrics?.mae} />
            <Metric label="MSE" value={result?.metrics?.mse} />
            <Metric label="PSNR" value={result?.metrics?.psnr} unit=" dB" />
            <dt>Effects</dt><dd>{chain.length}</dd>
            <dt>Rows</dt><dd>{result?.rows ?? size}</dd>
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

        <div className="sim-grid">
          {/* ------------------------- source ------------------------- */}
          <section className="module">
            <div className="module-head"><h2>Picture</h2></div>
            <p className="module-hint">
              Sent clean first, so the channel is the only thing that changes
              between the two images.
            </p>

            <div className="module-body">
              <div
                ref={dropRef}
                className="drop"
                onDragOver={(e) => { e.preventDefault(); dropRef.current?.classList.add('is-over') }}
                onDragLeave={() => dropRef.current?.classList.remove('is-over')}
                onDrop={(e) => {
                  e.preventDefault(); dropRef.current?.classList.remove('is-over')
                  const f = e.dataTransfer.files?.[0]
                  if (f) { setFile(f); setTx(null); setResult(null) }
                }}
              >
                {preview ? (
                  <>
                    <img src={preview} alt="The picture you selected" className="drop-preview" />
                    <p className="drop-name mono">{file?.name}</p>
                    <label className="btn btn-ghost drop-btn">
                      Choose a different picture
                      <input type="file" accept="image/*" hidden
                             onChange={(e) => { setFile(e.target.files?.[0]); setTx(null); setResult(null) }} />
                    </label>
                  </>
                ) : (
                  <>
                    <p className="drop-title">Drop an image here</p>
                    <p className="drop-hint">
                      High-contrast pictures make the damage easiest to read.
                    </p>
                    <label className="btn btn-primary drop-btn">
                      Browse files
                      <input type="file" accept="image/*" hidden
                             onChange={(e) => { setFile(e.target.files?.[0]); setTx(null); setResult(null) }} />
                    </label>
                  </>
                )}
              </div>

              <div className="field exp-size">
                <label className="field-label" htmlFor="exp-size">Grid size</label>
                <select id="exp-size" className="input" value={size}
                        onChange={(e) => { setSize(+e.target.value); setTx(null); setResult(null) }}>
                  {[32, 48, 64, 96, 128].map((n) => (
                    <option key={n} value={n}>{n} × {n}</option>
                  ))}
                </select>
                <p className="field-note">
                  More rows means finer frequency spacing, so a filter's edge
                  lands on a sharper row boundary.
                </p>
              </div>
            </div>

            <div className="module-foot">
              <button type="button" className="btn btn-primary sim-send"
                      onClick={encode} disabled={!file || sending}>
                {sending ? 'Encoding…' : tx ? 'Re-encode' : 'Encode clean'}
              </button>
              {tx && (
                <p className="field-note mono">
                  {tx.duration.toFixed(2)}s · {tx.rows}×{tx.columns} · ready
                </p>
              )}
            </div>
          </section>

          {/* -------------------------- chain -------------------------- */}
          <aside className="module">
            <div className="module-head"><h2>Channel</h2></div>
            <p className="module-hint">
              Applied in order, top to bottom. The first four have an inverse;
              the last three do not, which is exactly where a learned step
              would have to take over.
            </p>

            <div className="module-body">
              <div className="field">
                <label className="field-label" htmlFor="exp-preset">Start from</label>
                <select id="exp-preset" className="input" value=""
                        onChange={(e) => {
                          const p = cat.presets.find((x) => x.id === e.target.value)
                          if (p) { setChain(p.effects.map((s) => ({ ...s }))); setError('') }
                        }}>
                  <option value="">A preset chain…</option>
                  {cat.presets.map((p) => (
                    <option key={p.id} value={p.id}>{p.label} — {p.note}</option>
                  ))}
                </select>
              </div>

              <div className="exp-add" role="group" aria-label="Add an effect">
                {cat.effects.map((e) => (
                  <button key={e.id} type="button" className="btn btn-ghost exp-chip"
                          onClick={() => addEffect(e.id)}
                          disabled={chain.length >= cat.max_effects}
                          title={e.summary}>
                    + {e.label}
                  </button>
                ))}
              </div>

              {chain.length === 0 && (
                <p className="field-note">
                  Nothing in the chain yet. Add an effect, or pick a preset.
                </p>
              )}

              <ol className="exp-chain">
                {chain.map((step, i) => {
                  const s = spec(step.type)
                  if (!s) return null
                  return (
                    <li key={`${step.type}-${i}`} className="exp-step">
                      <div className="exp-step-head">
                        <span className={`led ${s.invertible ? 'is-open' : 'is-locked'}`}
                              aria-hidden="true" />
                        <b>{s.label}</b>
                        <span className="exp-tag mono">
                          {s.invertible ? 'has an inverse' : 'no inverse'}
                        </span>
                        <button type="button" className="btn btn-ghost exp-drop"
                                onClick={() => setChain(chain.filter((_, j) => j !== i))}
                                aria-label={`Remove ${s.label}`}>
                          Remove
                        </button>
                      </div>
                      <p className="field-note">{s.summary}</p>
                      <div className="exp-params">
                        {s.params.map((p) => (
                          <label key={p.id} className="exp-param">
                            <span className="field-label">
                              {p.label}{p.unit ? ` (${p.unit})` : ''}
                            </span>
                            {p.type === 'boolean' ? (
                              <span className="switch">
                                <input type="checkbox" checked={!!step[p.id]}
                                       onChange={(e) => setParam(i, p.id, e.target.checked)} />
                                <span className="switch-box" aria-hidden="true" />
                                <span className="switch-text">
                                  {step[p.id] ? 'On' : 'Off'}
                                </span>
                              </span>
                            ) : (
                              <input type="number" className="input mono"
                                     min={p.min} max={p.max} step={p.step}
                                     value={step[p.id]}
                                     onChange={(e) => setParam(i, p.id, e.target.value === '' ? '' : +e.target.value)} />
                            )}
                          </label>
                        ))}
                      </div>
                    </li>
                  )
                })}
              </ol>
            </div>

            <div className="module-foot">
              <label className="switch">
                <input type="checkbox" checked={undo}
                       onChange={(e) => setUndo(e.target.checked)} />
                <span className="switch-box" aria-hidden="true" />
                <span className="switch-text">Also try to undo it</span>
              </label>
              <p className="field-note">
                Divides the spectrum back by H(f), the LTI inverse. Effects with
                no inverse are left alone rather than faked.
              </p>

              <label className="switch">
                <input type="checkbox" checked={restore} disabled={!cat.model?.ready}
                       onChange={(e) => setRestore(e.target.checked)} />
                <span className="switch-box" aria-hidden="true" />
                <span className="switch-text">Then run the model</span>
              </label>
              <p className="field-note">
                {cat.model?.ready
                  ? 'The learned step, on whatever the inverse could not fix: clipping, killed rows, aliasing. All three results are shown, never merged.'
                  : (cat.model?.message || 'The restoration model is unavailable on this server.')}
              </p>
              <button type="button" className="btn btn-primary sim-send"
                      onClick={run} disabled={!tx || !chain.length || running}>
                {running ? 'Running…' : 'Run the channel'}
              </button>
              {!tx && <p className="field-note">Encode a picture first.</p>}
              {error && <div className="alert alert-error"><p>{error}</p></div>}
            </div>
          </aside>
        </div>

        <InverseTable rows={cat.inversion} />

        {/* -------------------------- result -------------------------- */}
        {result && (
          <section className="exp-result">
            <div className="rule-head"><h2>Measured</h2></div>

            <p className="exp-chainline mono">
              {result.description.join('  →  ')}
            </p>

            <div className="exp-grid">
              <figure className="exp-fig">
                <img src={api.previewUrl(result.session_id)} alt="What was sent" />
                <figcaption>Sent, {result.rows} × {result.columns}</figcaption>
              </figure>

              <figure className="exp-fig">
                <img src={result.image_url} alt="What came back through the channel" />
                <figcaption>Through the channel</figcaption>
              </figure>

              {result.undone && (
                <figure className="exp-fig">
                  <img src={result.undone.image_url} alt="After the LTI inverse" />
                  <figcaption>After the inverse</figcaption>
                </figure>
              )}

              {result.restored && (
                <figure className="exp-fig">
                  <img src={result.restored.image_url} alt="After the model" />
                  <figcaption>After the model</figcaption>
                </figure>
              )}

              <RowError values={result.row_error} rows={result.rows} />
            </div>

            {(result.undone || result.restored) && <Stages result={result} />}

            <div className="exp-foot">
              <dl className="call-readout">
                <Metric label="MAE" value={result.metrics?.mae} />
                <Metric label="MSE" value={result.metrics?.mse} />
                <Metric label="PSNR" value={result.metrics?.psnr} unit=" dB" />
                <dt>Peak level</dt><dd>{result.stats.dbfs} dBFS</dd>
                <dt>Clean peak</dt><dd>{result.clean_stats.dbfs} dBFS</dd>
              </dl>

              <div className="result-actions">
                <button type="button" className="btn btn-ghost"
                        onClick={() => api.downloadUrl(result.audio_url, 'channel.wav')}>
                  Download the degraded audio
                </button>
                <Link className="btn btn-ghost" to="/receive">
                  Open it on the Receive page
                </Link>
              </div>
            </div>

            <p className="exp-note">
              The green dots mark effects an LTI inverse can undo: each image
              row is a single frequency, so a linear channel can only scale it,
              and dividing that scale back out restores the row. The red dots
              mark the ones it cannot &mdash; a stop-band destroys rows rather
              than attenuating them, clipping is nonlinear, and aliasing folds
              two rows into one. Those three are what the model is asked to
              guess, and a guessed pixel looks exactly as convincing as a
              received one &mdash; which is why the stages stay in separate
              columns rather than being folded into a single number.
            </p>
          </section>
        )}
      </div>
    </main>
  )
}
