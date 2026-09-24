import { useState, useEffect, useRef } from "react";
import { useSearchParams } from "react-router-dom";
import WaveformScope from "../components/WaveformScope";
import { downloadUrl } from "../api/client";
import * as tel from "../api/telClient";
import "./Simulate.css";
import "./Receive.css";
import "./Call.css";

/* Over a call. The same picture-to-sound-and-back idea as Send and Receive,
   rebuilt for an 8 kHz voice channel, in two generations you choose between:

     A  parallel multitone with pilot tones. The pixel is in how loud a tone
        is, which is the one thing a speech codec throws away - so it arrives
        damaged, on purpose. Cheap on the wire, so it carries the detail.
     B  16-FSK. The pixel is in which tone plays, which a codec keeps - so it
        arrives exact, at a fraction of the resolution.

   Generation C (WebP + Reed-Solomon) was cut: byte-exact or nothing, so
   there was no graded loss to measure or learn from. */

const MODES = [
  { id: "send", label: "Send" },
  { id: "receive", label: "Receive" },
];

const SEND_STAGES = ["Picture", "Encode", "Call", "Rebuilt"];

/* Every dial state that means the call has not finished yet. The backend
   walks registering -> ringing -> answered -> playing before it is done. */
const DIALLING = [
  "dialling",
  "registering",
  "ringing",
  "waiting",
  "answered",
  "playing",
];
const RECEIVE_STAGES = ["File", "Inspect", "Key", "Rebuilt"];

const FALLBACK_GENERATIONS = [
  {
    id: "A",
    label: "Generation A",
    tagline: "Amplitude carries the pixel",
    summary:
      "The same parallel multitone scheme the WAV track uses, narrowed to 700-3000 Hz with two pilot tones. A voice codec models each frame with eight poles and cannot hold that many tone levels, so the picture arrives damaged - about three quarters of pixels exact. It is fast on the wire, so it carries the most detail.",
    sizes: [16, 24, 32, 48, 64],
    default_size: 48,
    levels: [2, 4, 8, 16],
    default_levels: 16,
    lossy: true,
  },
  {
    id: "B",
    label: "Generation B",
    tagline: "Which tone carries the pixel",
    summary:
      "One tone at a time out of sixteen. The decoder takes an argmax and never compares loudness, so a codec that destroys amplitude cannot touch it - the picture arrives exact. The cost is airtime, which caps the grid at about 32 x 32.",
    sizes: [16, 24, 32, 64],
    default_size: 32,
    levels: [2, 4, 16],
    default_levels: 4,
    lossy: false,
  },
];

const LOSSES = [0, 0.01, 0.02, 0.05, 0.1];
const NOISES = [-60, -45, -35, -25];

const digits = (v) => v.replace(/\D/g, "");

export default function Call() {
  const [params, setParams] = useSearchParams();
  const mode = params.get("mode") === "receive" ? "receive" : "send";
  const [info, setInfo] = useState(null);

  useEffect(() => {
    tel
      .info()
      .then(setInfo)
      .catch(() => setInfo(null));
  }, []);

  const generations = info?.generations?.length
    ? info.generations
    : FALLBACK_GENERATIONS;

  /* The Send tab's last transmission. A recording of a real call carries no
     header, so the Receive tab needs the sender's settings to rebuild it. */
  const [reference, setReference] = useState(null);

  return (
    <main className="call">
      <div className="shell">
        <p className="slug">
          <span>Call</span>
          <span>{mode}</span>
          <span>Gen A &middot; multitone</span>
          <span>Gen B &middot; 16-FSK</span>
          <span>
            {info
              ? info.gsm_available
                ? "GSM codec ready"
                : "No GSM codec"
              : "—"}
          </span>
          <span className="slug-sep" />
          <b>8 kHz &middot; 0.7&ndash;3.2 kHz</b>
        </p>
      </div>

      <div className="shell">
        <header className="sim-head">
          <div className="sim-head-copy">
            <h1 className="sim-title">Send it over a phone call</h1>
            <p className="sim-sub">
              A voice codec keeps which tone is playing and throws away how loud
              it is. Generation B puts the picture where the codec cannot reach
              it and arrives exact. Generation A leaves it where the codec does
              the damage, and arrives broken in a way that is worth measuring.
              Simulate the call to study it, or place a real one over SIP and
              rebuild the picture from what the phone recorded.
            </p>
            <div
              className="modeswitch call-modes"
              role="tablist"
              aria-label="Send or receive"
            >
              {MODES.map((m) => (
                <button
                  key={m.id}
                  role="tab"
                  type="button"
                  aria-selected={mode === m.id}
                  className={`modeswitch-b ${mode === m.id ? "is-on" : ""}`}
                  onClick={() =>
                    setParams(m.id === "send" ? {} : { mode: m.id }, {
                      replace: true,
                    })
                  }
                >
                  {m.label}
                </button>
              ))}
            </div>
          </div>

          <dl className="head-readout">
            <dt>Sample rate</dt>
            <dd>{info?.sample_rate ?? 8000} Hz</dd>
            <dt>Generations</dt>
            <dd>{generations.map((g) => g.id).join(" / ")}</dd>
            <dt>Gen A</dt>
            <dd>lossy</dd>
            <dt>Gen B</dt>
            <dd>exact</dd>
            <dt>Codec</dt>
            <dd>{info?.gsm_available ? "GSM" : "none"}</dd>
          </dl>
        </header>

        {/* both stay mounted so switching tabs never throws work away */}
        <div hidden={mode !== "send"}>
          <CallSend
            info={info}
            generations={generations}
            onSent={setReference}
          />
        </div>
        <div hidden={mode !== "receive"}>
          <CallReceive reference={reference} model={info?.model} />
        </div>
      </div>
    </main>
  );
}

/* ------------------------------------------------------------------------ */

function StageRail({ stages, stage }) {
  return (
    <ol className="stagerail" aria-label="Progress">
      {stages.map((s, i) => (
        <li
          key={s}
          className={i < stage ? "is-done" : i === stage ? "is-now" : ""}
        >
          <span className="led" aria-hidden="true" />
          <span className="stagerail-k">{s}</span>
        </li>
      ))}
    </ol>
  );
}

function LockFields({
  id,
  caller,
  receiver,
  pin,
  onCaller,
  onReceiver,
  onPin,
}) {
  return (
    <div className="lockfields">
      <div>
        <label className="field-label" htmlFor={`${id}-caller`}>
          Caller number
        </label>
        <input
          id={`${id}-caller`}
          className="input mono"
          inputMode="numeric"
          maxLength={11}
          placeholder="11 digits"
          value={caller}
          onChange={(e) => onCaller(digits(e.target.value))}
        />
      </div>
      <div>
        <label className="field-label" htmlFor={`${id}-receiver`}>
          Receiver number
        </label>
        <input
          id={`${id}-receiver`}
          className="input mono"
          inputMode="numeric"
          maxLength={11}
          placeholder="11 digits"
          value={receiver}
          onChange={(e) => onReceiver(digits(e.target.value))}
        />
      </div>
      <div>
        <label className="field-label" htmlFor={`${id}-pin`}>
          PIN
        </label>
        <input
          id={`${id}-pin`}
          className="input mono"
          inputMode="numeric"
          maxLength={8}
          placeholder="4 to 8 digits"
          value={pin}
          onChange={(e) => onPin(digits(e.target.value))}
        />
      </div>
    </div>
  );
}

function WavCard({ name, stats, locked, selected, onSelect, detail }) {
  return (
    <button
      type="button"
      className={`filecard ${selected ? "is-on" : ""}`}
      onClick={onSelect}
    >
      <span className="filecard-icon mono">WAV</span>
      <span className="filecard-body">
        <b className="mono">{name}</b>
        <span className="mono">
          {stats.duration}s &middot; {stats.sample_rate} Hz
          {detail ? ` · ${detail}` : ""}
        </span>
      </span>
      {locked !== undefined && (
        <span className={`filecard-tag ${locked ? "locked" : ""}`}>
          <span
            className={`led ${locked ? "is-locked" : "is-open"}`}
            aria-hidden="true"
          />
          {locked ? "Locked" : "Open"}
        </span>
      )}
    </button>
  );
}

function Scope({ selected, wave, audioSrc, title, accent, empty }) {
  return (
    <div className="module scope-col">
      {selected && wave ? (
        <WaveformScope
          data={wave}
          audioSrc={audioSrc}
          title={title}
          accent={accent}
        />
      ) : (
        <p className="scope-empty">{empty}</p>
      )}
    </div>
  );
}

/* What came back. On success it sits beside what was sent; on failure it is
   the received bytes painted as static, which is what a wrong PIN looks like. */
function Rebuilt({ result, sentUrl, model }) {
  const [enhanced, setEnhanced] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  /* A new rebuild makes any earlier guess stale, so drop it. */
  useEffect(() => {
    setEnhanced(null);
    setError("");
  }, [result?.url]);

  const enhance = async () => {
    setBusy(true);
    setError("");
    try {
      const response = await tel.enhance({ session_id: result.session_id });
      setEnhanced(fresh(response));
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  if (!result) {
    return (
      <p className="scope-empty call-rebuilt-empty">
        The rebuilt picture will appear here.
      </p>
    );
  }

  /* Both generations always produce a picture, so there is no "did not open"
     case any more - what a wrong PIN produces is a real picture of the wrong
     thing. The number that tells them apart is how much of it is right. */
  const exact = result.match?.exact_fraction;
  const meta = [
    `${result.rows} × ${result.columns}`,
    `gen ${result.generation}`,
    exact === undefined
      ? null
      : exact === 1
        ? "pixel-identical"
        : `${(exact * 100).toFixed(1)}% of pixels exact`,
  ].filter(Boolean);

  return (
    <div className="module recovered">
      <div className="module-head">
        <h2>Rebuilt picture</h2>
        <span className="mono recovered-meta">{meta.join(" · ")}</span>
      </div>

      <div className="module-body">
        <div
          className={`call-compare ${
            sentUrl && enhanced
              ? "is-trio"
              : sentUrl || enhanced
                ? "is-pair"
                : ""
          }`}
        >
          {sentUrl && (
            <figure className="call-fig">
              <div className="recovered-frame call-frame">
                <img
                  src={sentUrl}
                  alt="The picture as it went on the wire"
                  className="call-img"
                />
              </div>
              <figcaption>Sent</figcaption>
            </figure>
          )}
          <figure className="call-fig">
            <div className="recovered-frame call-frame">
              <img
                key={result.url}
                src={result.url}
                className="recovered-img call-img"
                alt="The picture rebuilt from the audio"
              />
            </div>
            <figcaption>Rebuilt from the audio</figcaption>
          </figure>
          {enhanced && (
            <figure className="call-fig">
              <div className="recovered-frame call-frame">
                <img
                  key={enhanced.url}
                  src={enhanced.url}
                  className="recovered-img call-img"
                  alt="The rebuilt picture after the model"
                />
              </div>
              <figcaption>Model&rsquo;s guess</figcaption>
            </figure>
          )}
        </div>

        {result.match && (
          <div
            className={`alert call-alert ${exact > 0.9 ? "alert-ok" : "alert-error"}`}
          >
            <p>
              {result.match.identical
                ? "Identical to what was sent, pixel for pixel."
                : exact > 0.9
                  ? `Close to what was sent: ${(exact * 100).toFixed(1)}% of pixels exact, PSNR ${result.match.psnr} dB.`
                  : `Only ${(exact * 100).toFixed(1)}% of pixels are right, at PSNR ${result.match.psnr} dB. On Generation A that is what a voice codec does to amplitudes. If the transmission was locked, it is also what a wrong PIN looks like — the two are not distinguishable from the picture alone.`}
            </p>
          </div>
        )}

        <div className="recovered-foot">
          <p className="field-note">
            {result.generation === "A"
              ? `Pilot tones aligned the grid ${result.offset_seconds}s into the audio. Each frame's row amplitudes were divided by its own pilots, so gain and AGC cancel.`
              : `Preamble found ${result.offset_seconds}s into the audio. Each 40 ms slice was read as whichever of sixteen tones was strongest.`}
            {result.truncated
              ? " The audio stopped before the transmission ended, so the tail is missing."
              : ""}
          </p>
          <button
            type="button"
            className="btn btn-ghost"
            onClick={() => downloadUrl(result.url, "received.png")}
          >
            Download picture
          </button>
        </div>

        <Model
          result={result}
          model={model}
          enhanced={enhanced}
          busy={busy}
          error={error}
          onRun={enhance}
        />
      </div>
    </div>
  );
}

/* The learned upscaler, kept visually apart from the rebuilt picture because
   what it adds was never transmitted. */
function Model({ result, model, enhanced, busy, error, onRun }) {
  const ready = model?.ready;
  /* Gen B is what it was trained for: bits arrive exact, so the only loss is
     the shrinking and the 4 levels done before the call. */
  const trained = result.generation === "B";

  return (
    <div className="call-model">
      <div className="call-model-head">
        <span
          className={`led ${ready ? "is-open" : "is-locked"}`}
          aria-hidden="true"
        />
        <b>Restoration model</b>
        <span className="exp-tag mono">upscale + dequantise</span>
      </div>

      <p className="field-note">
        {trained
          ? `Generation B arrives bit-exact, so nothing here is repairing the call.
             What the model guesses back is the detail and the shades thrown away
             before it: ${result.rows}×${result.columns} at a few levels, up to 128×128.`
          : `This model was trained on Generation B pictures. On Generation A it is
             working outside what it learned, so treat the result as a sketch.`}
      </p>

      {!ready && model?.message && (
        <div className="alert alert-error">
          <p>{model.message}</p>
        </div>
      )}

      {enhanced && (
        <dl className="call-readout">
          <dt>Size</dt>
          <dd>
            {enhanced.from_size} → {enhanced.size} px
          </dd>
          <dt>Shades</dt>
          <dd>
            {enhanced.compare.levels_before} → {enhanced.compare.levels_after}
          </dd>
          <dt>Mean change</dt>
          <dd>{enhanced.compare.mean_change} / 255</dd>
        </dl>
      )}

      <div className="call-model-foot">
        <button
          type="button"
          className="btn btn-primary"
          onClick={onRun}
          disabled={!ready || busy}
        >
          {busy
            ? "Running the model…"
            : enhanced
              ? "Run again"
              : "Enhance with the model"}
        </button>
        {enhanced && (
          <button
            type="button"
            className="btn btn-ghost"
            onClick={() => downloadUrl(enhanced.url, "enhanced.png")}
          >
            Download the guess
          </button>
        )}
      </div>

      {error && (
        <div className="alert alert-error">
          <p>{error}</p>
        </div>
      )}

      {enhanced && (
        <p className="field-note">
          Those extra shades were invented by the model, not received. It is a
          plausible picture, not a more accurate one &mdash; for anything you
          intend to measure, use the rebuilt picture.
        </p>
      )}
    </div>
  );
}

const fresh = (response) => ({
  ...response,
  url: `${response.image_url}?t=${Date.now()}`,
});

/* ------------------------------------------------------------------------ */

function CallSend({ info, generations, onSent }) {
  const [file, setFile] = useState(null);
  const [preview, setPreview] = useState(null);
  const [staged, setStaged] = useState(null);
  const [staging, setStaging] = useState(false);
  const [genId, setGenId] = useState("A");
  const [size, setSize] = useState(48);
  const [levels, setLevels] = useState(16);
  const [autocontrast, setAutocontrast] = useState(true);
  const [colour, setColour] = useState(false);
  const [plan, setPlan] = useState(null);

  const [secure, setSecure] = useState(false);
  const [caller, setCaller] = useState("");
  const [receiver, setReceiver] = useState("");
  const [pin, setPin] = useState("");

  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const [tx, setTx] = useState(null);
  const [txWave, setTxWave] = useState(null);
  const [txOpen, setTxOpen] = useState(false);

  const [loss, setLoss] = useState(0.02);
  const [noise, setNoise] = useState(-45);
  const [seed, setSeed] = useState("0");
  const [calling, setCalling] = useState(false);
  const [callError, setCallError] = useState("");
  const [rx, setRx] = useState(null);
  const [rxWave, setRxWave] = useState(null);
  const [rxOpen, setRxOpen] = useState(false);

  const [sip, setSip] = useState(() => localStorage.getItem("sc.sip") || "");
  const [dialInfo, setDialInfo] = useState(null);
  const [dialling, setDialling] = useState(null);
  const [dialError, setDialError] = useState("");

  const [from, setFrom] = useState("rx");
  const [keyOn, setKeyOn] = useState(false);
  const [kCaller, setKCaller] = useState("");
  const [kReceiver, setKReceiver] = useState("");
  const [kPin, setKPin] = useState("");
  const [rebuilding, setRebuilding] = useState(false);
  const [rebuildError, setRebuildError] = useState("");
  const [rebuilt, setRebuilt] = useState(null);

  const dropRef = useRef(null);
  const planTick = useRef(0);

  useEffect(() => {
    if (!file) {
      setPreview(null);
      return;
    }
    const url = URL.createObjectURL(file);
    setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);

  useEffect(() => {
    tel
      .dialStatus()
      .then(setDialInfo)
      .catch(() => setDialInfo(null));
  }, []);

  /* Poll while pjsua is on the line. The call lasts as long as the
     transmission does, so this is the only way to know it finished. */
  useEffect(() => {
    if (!dialling || !DIALLING.includes(dialling.state)) return;
    const timer = setInterval(async () => {
      try {
        setDialling(await tel.dialProgress(dialling.call_id));
      } catch {
        clearInterval(timer);
      }
    }, 1500);
    return () => clearInterval(timer);
  }, [dialling]);

  const waitForCall = async () => {
    setDialError("");
    try {
      setDialling(await tel.answerCall({ session_id: tx.session_id }));
    } catch (e) {
      setDialError(e.message);
    }
  };

  const placeRealCall = async () => {
    setDialError("");
    try {
      localStorage.setItem("sc.sip", sip);
    } catch {
      /* private window */
    }
    try {
      setDialling(await tel.dial({ session_id: tx.session_id, target: sip }));
    } catch (e) {
      setDialError(e.message);
    }
  };

  const pick = async (f) => {
    if (!f) return;
    setFile(f);
    setStaged(null);
    setPlan(null);
    setError("");
    setStaging(true);
    try {
      setStaged(await tel.stage(f));
    } catch (e) {
      setError(e.message);
    } finally {
      setStaging(false);
    }
  };

  /* re-plan whenever the picture or its settings change; a slower answer to an
     older question is dropped rather than painted over a newer one */
  const gen = generations.find((g) => g.id === genId) || generations[0];

  /* Each generation carries its own grid sizes and level counts. Moving to one
     that cannot hold the current choice snaps to that generation's default
     rather than sending something the backend would refuse. */
  const switchGeneration = (id) => {
    const next = generations.find((g) => g.id === id);
    if (!next) return;
    setGenId(id);
    setError("");
    if (!next.sizes.includes(size)) setSize(next.default_size);
    if (!next.levels.includes(levels)) setLevels(next.default_levels);
  };

  useEffect(() => {
    if (!staged) return;
    const tick = ++planTick.current;
    tel
      .plan(genId, size, levels, colour)
      .then((p) => {
        if (tick === planTick.current) setPlan(p);
      })
      .catch((e) => {
        if (tick === planTick.current) setError(e.message);
      });
  }, [staged, genId, size, levels, colour]);

  const encode = async () => {
    setError("");
    setSending(true);
    setTx(null);
    setTxWave(null);
    setTxOpen(false);
    setRx(null);
    setRxWave(null);
    setRxOpen(false);
    setCallError("");
    setRebuilt(null);
    setRebuildError("");
    try {
      const response = await tel.send({
        image_id: staged.image_id,
        generation: genId,
        size,
        levels,
        colour,
        autocontrast,
        security_enabled: secure,
        caller: secure ? caller : null,
        receiver: secure ? receiver : null,
        pin: secure ? pin : null,
      });
      setTx(response);
      setFrom("tx");
      // the Receive tab needs these settings to rebuild a recording of this call
      onSent?.({ ...response, filename: file?.name });
      // the receiver starts from what the sender used; change it to try a wrong PIN
      setKeyOn(secure);
      setKCaller(caller);
      setKReceiver(receiver);
      setKPin(pin);
      setTxWave(await tel.waveform(response.session_id));
    } catch (e) {
      setError(e.message);
    } finally {
      setSending(false);
    }
  };

  const placeCall = async () => {
    setCallError("");
    setCalling(true);
    setRx(null);
    setRxWave(null);
    setRxOpen(false);
    setRebuilt(null);
    try {
      const response = await tel.call({
        session_id: tx.session_id,
        loss,
        noise_db: noise,
        seed: Number(seed) || 0,
      });
      setRx(response);
      setFrom("rx");
      setRxWave(await tel.waveform(response.session_id));
    } catch (e) {
      setCallError(e.message);
    } finally {
      setCalling(false);
    }
  };

  const rebuild = async () => {
    setRebuildError("");
    setRebuilding(true);
    try {
      const source = from === "rx" && rx ? rx : tx;
      const response = await tel.receive({
        session_id: source.session_id,
        reference_id: tx.session_id,
        security_enabled: keyOn,
        caller: keyOn ? kCaller : null,
        receiver: keyOn ? kReceiver : null,
        pin: keyOn ? kPin : null,
      });
      setRebuilt(fresh(response));
    } catch (e) {
      setRebuildError(e.message);
    } finally {
      setRebuilding(false);
    }
  };

  const gsm = info?.gsm_available;
  const stage = rebuilt ? 4 : rx ? 3 : tx ? 2 : staged ? 1 : 0;
  const source = from === "rx" && rx ? "rx.wav" : "tx.wav";

  return (
    <>
      <StageRail stages={SEND_STAGES} stage={stage} />

      <div className="sim-grid">
        <section className="module">
          <div className="module-head">
            <h2>Picture</h2>
          </div>
          <p className="module-hint">
            The picture is shrunk to the grid below before it goes on the call.
          </p>

          <div className="module-body">
            <div
              ref={dropRef}
              className="drop"
              onDragOver={(e) => {
                e.preventDefault();
                dropRef.current?.classList.add("is-over");
              }}
              onDragLeave={() => dropRef.current?.classList.remove("is-over")}
              onDrop={(e) => {
                e.preventDefault();
                dropRef.current?.classList.remove("is-over");
                pick(e.dataTransfer.files?.[0]);
              }}
            >
              {preview ? (
                <>
                  <img
                    src={preview}
                    alt="The picture you selected"
                    className="drop-preview"
                  />
                  <p className="drop-name mono">
                    {file?.name}
                    {staged ? ` · ${staged.width} × ${staged.height}` : ""}
                  </p>
                  <label className="btn btn-ghost drop-btn">
                    Choose a different picture
                    <input
                      type="file"
                      accept="image/*"
                      hidden
                      onChange={(e) => pick(e.target.files?.[0])}
                    />
                  </label>
                </>
              ) : (
                <>
                  <p className="drop-title">Drop an image here</p>
                  <p className="drop-hint">
                    PNG, JPG or WebP, up to 12&nbsp;MB. Simple, bold pictures
                    compress smallest and make the shortest calls.
                  </p>
                  <label className="btn btn-primary drop-btn">
                    Browse files
                    <input
                      type="file"
                      accept="image/*"
                      hidden
                      onChange={(e) => pick(e.target.files?.[0])}
                    />
                  </label>
                </>
              )}
            </div>
            {staging && <p className="field-note">Reading the picture…</p>}
          </div>
        </section>

        <aside className="module">
          <div className="module-head">
            <h2>Settings</h2>
          </div>

          <div className="module-body">
            <div className="field">
              <label className="field-label" htmlFor="tel-gen">
                Generation
              </label>
              <select
                id="tel-gen"
                className="input"
                value={genId}
                onChange={(e) => switchGeneration(e.target.value)}
              >
                {generations.map((g) => (
                  <option key={g.id} value={g.id}>
                    {g.id} — {g.tagline}
                  </option>
                ))}
              </select>
              <p className="field-note">{gen.summary}</p>
            </div>

            <div className="call-pair">
              <div className="field">
                <label className="field-label" htmlFor="tel-size">
                  Grid
                </label>
                <select
                  id="tel-size"
                  className="input"
                  value={size}
                  onChange={(e) => setSize(+e.target.value)}
                >
                  {gen.sizes.map((v) => (
                    <option key={v} value={v}>
                      {v} × {v}
                    </option>
                  ))}
                </select>
              </div>
              <div className="field">
                <label className="field-label" htmlFor="tel-levels">
                  Gray levels
                </label>
                <select
                  id="tel-levels"
                  className="input"
                  value={levels}
                  onChange={(e) => setLevels(+e.target.value)}
                >
                  {gen.levels.map((v) => (
                    <option key={v} value={v}>
                      {v}
                    </option>
                  ))}
                </select>
              </div>
            </div>

            <label className="switch">
              <input
                type="checkbox"
                checked={colour}
                onChange={(e) => setColour(e.target.checked)}
              />
              <span className="switch-box" aria-hidden="true" />
              <span className="switch-text">Send in colour</span>
            </label>
            <p className="field-note">
              Colour sends three passes, so the call runs three times as long.
            </p>

            <label className="switch">
              <input
                type="checkbox"
                checked={autocontrast}
                onChange={(e) => setAutocontrast(e.target.checked)}
              />
              <span className="switch-box" aria-hidden="true" />
              <span className="switch-text">Stretch the contrast</span>
            </label>
            <p className="field-note">
              A photograph rarely uses the whole black-to-white range, and a
              call carries very few levels &mdash; so spending them on range the
              picture never touches is what makes a cat come out as a smear.
              Costs nothing and changes no airtime. Turn it off to see the
              difference.
            </p>

            <dl className="call-readout">
              <dt>On the wire</dt>
              <dd>{plan ? `${plan.rows} × ${plan.columns}` : "—"}</dd>
              <dt>Passes</dt>
              <dd>{plan ? plan.channels : "—"}</dd>
              <dt>Call length</dt>
              <dd>{plan ? `${plan.seconds.toFixed(1)} s` : "—"}</dd>
              <dt>Survives GSM</dt>
              <dd>{gen.lossy ? "No, by design" : "Yes"}</dd>
            </dl>
            <p className="field-note">
              {gen.lossy
                ? "Generation A is the one the codec damages. That damage is the point: it is what the restoration model is trained to undo."
                : "Generation B decodes by argmax over sixteen tones, so a codec that flattens loudness leaves it untouched."}
            </p>

            <hr className="module-rule" />

            <label className="switch">
              <input
                type="checkbox"
                checked={secure}
                onChange={(e) => setSecure(e.target.checked)}
              />
              <span className="switch-box" aria-hidden="true" />
              <span className="switch-text">Lock this transmission</span>
            </label>
            <p className="field-note">
              The numbers and PIN shuffle the rows and columns, upstream of the
              modem, so the scramble survives the call. Without them the
              receiver rebuilds static.
            </p>
            {secure && (
              <LockFields
                id="tel-send"
                caller={caller}
                receiver={receiver}
                pin={pin}
                onCaller={setCaller}
                onReceiver={setReceiver}
                onPin={setPin}
              />
            )}
          </div>

          <div className="module-foot">
            <button
              type="button"
              className="btn btn-primary sim-send"
              onClick={encode}
              disabled={!staged || sending}
            >
              {sending ? "Encoding…" : "Encode for a call"}
            </button>
            {!staged && <p className="field-note">Add a picture to send.</p>}
            {/* Generation B has no airtime cap - exactness is the whole reason
                to pick it - so a long one is a caution, not a refusal. The
                simulation itself is quick: it processes the audio offline
                rather than playing it. */}
            {plan?.long && (
              <p className="field-note">
                That is {(plan.seconds / 60).toFixed(1)} minutes of call time, and
                a {((plan.seconds * 16000) / 1e6).toFixed(0)}&nbsp;MB file. Generation{" "}
                {plan.generation} has no limit, and the simulated call still runs
                in about a second &mdash; the minutes are what a real call would
                have cost.
              </p>
            )}
            {error && (
              <div className="alert alert-error">
                <p>{error}</p>
              </div>
            )}
          </div>
        </aside>
      </div>

      {tx && (
        <section className="call-section">
          <div className="rule-head">
            <h2>On the wire</h2>
          </div>

          <div className="result-grid">
            <div className="file-col">
              <WavCard
                name="tx.wav"
                stats={tx.stats}
                locked={tx.locked}
                selected={txOpen}
                onSelect={() => setTxOpen(true)}
              />
              {!txOpen && (
                <p className="field-note">
                  Open the file to inspect its waveform.
                </p>
              )}

              <figure className="sent-preview">
                <img
                  src={tx.sent_url}
                  alt="What was sent, at transmission size"
                />
                <figcaption>
                  {tx.report.rows} &times; {tx.report.columns} &middot;{" "}
                  {tx.report.gray_levels} gray levels &middot;{" "}
                  {tx.report.channels === 3 ? "colour" : "grayscale"} &middot;{" "}
                  Generation {tx.report.generation}
                </figcaption>
              </figure>

              <div className="result-actions">
                <button
                  type="button"
                  className="btn btn-primary"
                  onClick={() => downloadUrl(tx.audio_url, "tx.wav")}
                >
                  Download tx.wav
                </button>
              </div>
              <p className="field-note">
                8&nbsp;kHz, 16-bit mono &mdash; what a voice line would actually
                carry. The call below is simulated: the same GSM&nbsp;06.10
                codec, packet loss and noise, without a phone.
              </p>
            </div>

            <Scope
              selected={txOpen}
              wave={txWave}
              audioSrc={tx.audio_url}
              title="tx.wav"
              empty="Open the file to inspect its waveform."
            />
          </div>
        </section>
      )}

      {tx && (
        <section className="call-section">
          <div className="rule-head">
            <h2>Through the call</h2>
          </div>

          <div className="result-grid">
            <div className="file-col">
              <div className="module">
                <div className="module-head">
                  <h2>Simulated GSM call</h2>
                </div>
                <div className="module-body">
                  <div className="call-pair">
                    <div className="field">
                      <label className="field-label" htmlFor="tel-loss">
                        Packet loss
                      </label>
                      <select
                        id="tel-loss"
                        className="input"
                        value={loss}
                        onChange={(e) => setLoss(+e.target.value)}
                      >
                        {LOSSES.map((l) => (
                          <option key={l} value={l}>
                            {Math.round(l * 100)}%
                          </option>
                        ))}
                      </select>
                    </div>
                    <div className="field">
                      <label className="field-label" htmlFor="tel-noise">
                        Noise floor
                      </label>
                      <select
                        id="tel-noise"
                        className="input"
                        value={noise}
                        onChange={(e) => setNoise(+e.target.value)}
                      >
                        {NOISES.map((n) => (
                          <option key={n} value={n}>
                            {n} dB
                          </option>
                        ))}
                      </select>
                    </div>
                  </div>
                  <div className="field">
                    <label className="field-label" htmlFor="tel-seed">
                      Seed
                    </label>
                    <input
                      id="tel-seed"
                      className="input mono"
                      inputMode="numeric"
                      maxLength={6}
                      value={seed}
                      onChange={(e) => setSeed(digits(e.target.value))}
                    />
                  </div>
                  <p className="field-note">
                    GSM 06.10 encoding, lost 20&nbsp;ms packets repeated, a
                    wandering level and a noise floor, with random silence
                    before it starts.
                  </p>
                </div>
                <div className="module-foot">
                  <button
                    type="button"
                    className="btn btn-primary sim-send"
                    onClick={placeCall}
                    disabled={calling || !gsm}
                  >
                    {calling
                      ? "Calling…"
                      : rx
                        ? "Call again"
                        : "Place the call"}
                  </button>
                  {info && !gsm && (
                    <p className="field-note">
                      The server has no GSM codec. Install it with brew install
                      libgsm, or rebuild straight from tx.wav below.
                    </p>
                  )}
                  {callError && (
                    <div className="alert alert-error">
                      <p>{callError}</p>
                    </div>
                  )}
                </div>
              </div>

              {rx && (
                <div className="call-rx">
                  <WavCard
                    name="rx.wav"
                    stats={rx.stats}
                    selected={rxOpen}
                    onSelect={() => setRxOpen(true)}
                    detail={`${Math.round(rx.loss * 100)}% loss`}
                  />
                  <div className="result-actions">
                    <button
                      type="button"
                      className="btn btn-ghost"
                      onClick={() => downloadUrl(rx.audio_url, "rx.wav")}
                    >
                      Download rx.wav
                    </button>
                  </div>
                </div>
              )}
            </div>

            <Scope
              selected={rxOpen}
              wave={rxWave}
              audioSrc={rx?.audio_url}
              title="rx.wav"
              accent="open"
              empty={
                rx
                  ? "Open rx.wav to hear what the call did to it."
                  : "Place the call to hear what comes out the far end."
              }
            />
          </div>
        </section>
      )}

      {tx && (
        <section className="call-section">
          <div className="rule-head">
            <h2>Rebuilt</h2>
          </div>

          <div className="result-grid">
            <div className="module">
              <div className="module-head">
                <h2>Receiver</h2>
              </div>
              <div className="module-body">
                {rx && (
                  <div className="field">
                    <span className="field-label">Rebuild from</span>
                    <div
                      className="toggle"
                      role="group"
                      aria-label="Which audio to rebuild from"
                    >
                      <button
                        type="button"
                        className={from === "rx" ? "is-on" : ""}
                        onClick={() => setFrom("rx")}
                      >
                        rx.wav, after the call
                      </button>
                      <button
                        type="button"
                        className={from === "tx" ? "is-on" : ""}
                        onClick={() => setFrom("tx")}
                      >
                        tx.wav, no call
                      </button>
                    </div>
                  </div>
                )}

                <label className="switch">
                  <input
                    type="checkbox"
                    checked={keyOn}
                    onChange={(e) => setKeyOn(e.target.checked)}
                  />
                  <span className="switch-box" aria-hidden="true" />
                  <span className="switch-text">Use numbers and PIN</span>
                </label>
                <p className="field-note">
                  Filled in with what the sender used. Change a digit to see
                  what a wrong PIN gets.
                </p>
                {keyOn && (
                  <LockFields
                    id="tel-key"
                    caller={kCaller}
                    receiver={kReceiver}
                    pin={kPin}
                    onCaller={setKCaller}
                    onReceiver={setKReceiver}
                    onPin={setKPin}
                  />
                )}
              </div>
              <div className="module-foot">
                <button
                  type="button"
                  className="btn btn-primary sim-send"
                  onClick={rebuild}
                  disabled={rebuilding}
                >
                  {rebuilding ? "Rebuilding…" : `Rebuild from ${source}`}
                </button>
                {rebuildError && (
                  <div className="alert alert-error">
                    <p>{rebuildError}</p>
                  </div>
                )}
              </div>
            </div>

            <RealCall
              info={dialInfo}
              sip={sip}
              onSip={setSip}
              onDial={placeRealCall}
              onWait={waitForCall}
              state={dialling}
              error={dialError}
              seconds={tx.report.seconds}
            />

            <Rebuilt
              result={rebuilt}
              sentUrl={tx.sent_url}
              model={info?.model}
            />
          </div>
        </section>
      )}
    </>
  );
}

/* ------------------------------------------------------------------------ */

function RealCall({ info, sip, onSip, onDial, onWait, state, error, seconds }) {
  /* The browser starts the call; pjsua places it. Linphone's own Python
     bindings are not on PyPI and have to be compiled from the SDK source, and
     none of that is needed: sip.linphone.org is an ordinary SIP registrar and
     the app on the phone answers any SIP client. */
  const ready = info?.pjsua && info?.configured;
  const busy = DIALLING.includes(state?.state);

  return (
    <div className="module call-dial">
      <div className="module-head">
        <h2>Place a real call</h2>
        {info && (
          <span className={`filecard-tag ${ready ? "" : "locked"}`}>
            <span
              className={`led ${ready ? "is-open" : "is-locked"}`}
              aria-hidden="true"
            />
            {ready ? "Ready" : "Not configured"}
          </span>
        )}
      </div>

      <div className="module-body">
        {!ready && info?.message && (
          <div className="alert alert-error">
            <p>{info.message}</p>
          </div>
        )}

        <div className="field">
          <label className="field-label" htmlFor="sip-target">
            Your phone&rsquo;s SIP address
          </label>
          <input
            id="sip-target"
            className="input mono"
            value={sip}
            placeholder="sip:yourphone@sip.linphone.org"
            onChange={(e) => onSip(e.target.value)}
            disabled={!ready || busy}
          />
          <p className="field-note">
            The second Linphone account, the one signed in on the phone &mdash;
            not the one this server calls from
            {info?.identity ? (
              <>
                {" "}
                (<span className="mono">{info.identity}</span>)
              </>
            ) : null}
            . This is SIP to SIP, so it never touches the phone network and
            costs nothing.
          </p>
        </div>

        <ol className="call-steps">
          <li>Answer on the phone.</li>
          <li>
            <b>Mute the microphone</b> &mdash; Linphone records both directions,
            so room noise would land on top of the tones.
          </li>
          <li>
            Press <b>Record</b> in the call.
          </li>
          <li>Start the call below and wait {Math.ceil(seconds)}&nbsp;s.</li>
          <li>Stop recording, share the file back, and open the Receive tab.</li>
        </ol>
        <p className="field-note">
          If the phone never rings, use{" "}
          <b>Or let the phone call this Mac</b> instead and dial{" "}
          <span className="mono">{info?.identity || "this account"}</span> from
          the handset. A phone placing a call needs no push notification, which
          is the usual reason an incoming one is only ever seen in the call
          history. Same account, same codec, same recording.
        </p>
        <p className="field-note">
          The recording has to be made on the phone: pjsua can only record its
          own inbound leg, which is your muted microphone, not the tones the
          phone received. No app can capture another app&rsquo;s call audio.
        </p>

        {state && (
          <dl className="call-readout">
            <dt>State</dt>
            <dd>{state.state}</dd>
            <dt>Target</dt>
            <dd className="mono">{state.target}</dd>
            <dt>Elapsed</dt>
            <dd>
              {state.elapsed ?? state.seconds ?? 0}&nbsp;s of{" "}
              {state.expected_seconds}&nbsp;s
            </dd>
            {state.negotiated && (
              <>
                <dt>Codec</dt>
                <dd className="mono">{state.negotiated}</dd>
              </>
            )}
          </dl>
        )}
        {state?.state === "waiting" && (
          <div className="alert alert-ok">
            <p>
              Waiting. On the phone, dial{" "}
              <b className="mono">{state.dial_this || info?.identity}</b> from
              the Linphone app now. It answers automatically.
            </p>
          </div>
        )}
        {state?.state === "answered" && (
          <div className="alert alert-ok">
            <p>
              Answered. <b>Mute the phone&rsquo;s microphone and press Record
              now</b> &mdash; the tones start in about{" "}
              {Math.round(state.grace_seconds ?? 8)}&nbsp;s.
            </p>
          </div>
        )}
        {state?.state === "playing" && (
          <div className="alert alert-ok">
            <p>Playing. Leave both alone until it finishes.</p>
          </div>
        )}
        {state?.state === "done" && (
          <p className="field-note">
            Check the codec line above. It should read GSM &mdash; that is the
            codec this end offers and the one the simulated call models, so the
            two are comparable. Anything else came through a gentler channel,
            and a clean decode over it proves less than it looks.
          </p>
        )}
        {state?.error && (
          <div className="alert alert-error">
            <p>{state.error}</p>
          </div>
        )}
      </div>

      <div className="module-foot">
        <button
          type="button"
          className="btn btn-primary"
          onClick={onDial}
          disabled={!ready || busy || !sip.trim()}
        >
          {busy ? "On the line…" : "Call and play"}
        </button>
        <button
          type="button"
          className="btn btn-ghost"
          onClick={onWait}
          disabled={!ready || busy}
        >
          {busy ? "…" : "Or let the phone call this Mac"}
        </button>
        {error && (
          <div className="alert alert-error">
            <p>{error}</p>
          </div>
        )}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------------ */

function CallReceive({ reference, model }) {
  const [file, setFile] = useState(null);
  const [found, setFound] = useState(null);
  const [wave, setWave] = useState(null);
  const [selected, setSelected] = useState(false);

  const [keyOn, setKeyOn] = useState(false);
  const [caller, setCaller] = useState("");
  const [receiver, setReceiver] = useState("");
  const [pin, setPin] = useState("");

  const [busy, setBusy] = useState(false);
  const [opening, setOpening] = useState(false);
  const [error, setError] = useState("");
  const [rebuilt, setRebuilt] = useState(null);
  const [audioSrc, setAudioSrc] = useState(null);
  const dropRef = useRef(null);

  useEffect(() => {
    if (!file) {
      setAudioSrc(null);
      return;
    }
    const url = URL.createObjectURL(file);
    setAudioSrc(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);

  const take = async (f) => {
    if (!f) return;
    setFile(f);
    setError("");
    setRebuilt(null);
    setFound(null);
    setWave(null);
    setSelected(false);
    setBusy(true);
    try {
      const uploaded = await tel.upload(f);
      /* A recording says what it is in its opening descriptor, and then
         needs nothing else. One from before descriptors carries no settings,
         so it is inspected against the Send tab's transmission if there is
         one; otherwise the upload's message says what to do. */
      const response = !uploaded.self_described && reference
        ? {
            ...uploaded,
            ...(await tel.inspect({
              session_id: uploaded.session_id,
              reference_id: reference.session_id,
            })),
          }
        : uploaded;
      setFound(response);
      // the descriptor says whether a lock was used, so ask for the numbers
      if (response.found && response.locked) setKeyOn(true);
      setWave(await tel.waveform(uploaded.session_id));
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const open = async () => {
    setError("");
    setOpening(true);
    try {
      const response = await tel.receive({
        session_id: found.session_id,
        /* still sent when the recording described itself: the backend then
           only uses it to score against, and only if it is the same picture */
        reference_id: reference?.session_id,
        security_enabled: keyOn,
        caller: keyOn ? caller : null,
        receiver: keyOn ? receiver : null,
        pin: keyOn ? pin : null,
      });
      setRebuilt(fresh(response));
    } catch (e) {
      setError(e.message);
    } finally {
      setOpening(false);
    }
  };

  const stage = rebuilt ? 4 : found ? (keyOn ? 2 : 3) : file ? 1 : 0;
  const alertKind = found?.found ? "alert-ok" : "alert-error";

  return (
    <>
      <StageRail stages={RECEIVE_STAGES} stage={stage} />

      <div className="rcv-grid">
        <section className="module">
          <div className="module-head">
            <h2>Incoming</h2>
          </div>

          <div className="module-body">
            <div
              ref={dropRef}
              className="drop"
              onDragOver={(e) => {
                e.preventDefault();
                dropRef.current?.classList.add("is-over");
              }}
              onDragLeave={() => dropRef.current?.classList.remove("is-over")}
              onDrop={(e) => {
                e.preventDefault();
                dropRef.current?.classList.remove("is-over");
                take(e.dataTransfer.files?.[0]);
              }}
            >
              {file ? (
                <>
                  <p className="drop-title mono">{file.name}</p>
                  <p className="drop-hint">
                    {(file.size / 1024).toFixed(0)} KB
                  </p>
                  <label className="btn btn-ghost drop-btn">
                    Load a different file
                    <input
                      type="file"
                      accept="audio/*,.wav,.mka,.mkv,.m4a,.caf,.mp4,.ogg,.opus,.3gp,.amr,.mp3"
                      hidden
                      onChange={(e) => take(e.target.files?.[0])}
                    />
                  </label>
                </>
              ) : (
                <>
                  <p className="drop-title">Drop a call recording here</p>
                  <p className="drop-hint">
                    A tx.wav from the Send tab, or a recording made off a real
                    call &mdash; .wav, and .mkv/.mka/.m4a/.caf/.opus where
                    ffmpeg is installed. Linphone on a phone writes Matroska,
                    so .mkv and .mka are the usual ones. Any sample rate works;
                    it is brought to 8&nbsp;kHz first.
                  </p>
                  <label className="btn btn-primary drop-btn">
                    Browse files
                    <input
                      type="file"
                      accept="audio/*,.wav,.mka,.mkv,.m4a,.caf,.mp4,.ogg,.opus,.3gp,.amr,.mp3"
                      hidden
                      onChange={(e) => take(e.target.files?.[0])}
                    />
                  </label>
                </>
              )}
            </div>

            {busy && <p className="field-note">Reading the recording…</p>}

            {found && (
              <div className="rcv-stack">
                <div className={`alert ${alertKind}`}>
                  <p>{found.message}</p>
                </div>

                <WavCard
                  name={file?.name}
                  stats={found.stats}
                  selected={selected}
                  onSelect={() => setSelected(true)}
                  locked={found.found ? Boolean(found.locked) : undefined}
                  detail={
                    found.resampled ? `from ${found.sample_rate_in} Hz` : null
                  }
                />
                {!selected && (
                  <p className="field-note">
                    Open the file to inspect its waveform.
                  </p>
                )}

                {found.found && (
                  <div className="unlock">
                    <dl className="call-readout">
                      <dt>Generation</dt>
                      <dd>{found.generation ?? "—"}</dd>
                      <dt>Picture</dt>
                      <dd>
                        {found.rows}&times;{found.columns},{" "}
                        {found.gray_levels} levels
                        {found.mode === "RGB" ? ", colour" : ""}
                      </dd>
                      <dt>Settings from</dt>
                      <dd>
                        {found.self_described ? "the recording" : "the Send tab"}
                      </dd>
                      <dt>Starts at</dt>
                      <dd>{found.offset_seconds} s</dd>
                      <dt>Preamble</dt>
                      <dd>
                        {found.preamble_score == null
                          ? "pilots"
                          : found.preamble_score}
                      </dd>
                      <dt>Complete</dt>
                      <dd>{found.truncated ? "Cut short" : "Yes"}</dd>
                      {found.symbol_error != null && (
                        <>
                          {/* Measured against the transmission's own
                              error-correcting code, so it needs no reference
                              picture: decode it, re-encode what came out, and
                              count the symbols that disagree. Unlike the tone
                              margin it does not move when the codec changes -
                              a perfect GSM call reads 0% here and 50x on the
                              margin, where a clean PCMU one reads 340x. */}
                          <dt>Symbols wrong</dt>
                          <dd>
                            {found.symbol_error === 0
                              ? "none"
                              : `${(found.symbol_error * 100).toFixed(1)}%`}
                          </dd>
                        </>
                      )}
                    </dl>

                    <label className="switch">
                      <input
                        type="checkbox"
                        checked={keyOn}
                        onChange={(e) => setKeyOn(e.target.checked)}
                      />
                      <span className="switch-box" aria-hidden="true" />
                      <span className="switch-text">Use numbers and PIN</span>
                    </label>
                    {keyOn && (
                      <LockFields
                        id="tel-rcv"
                        caller={caller}
                        receiver={receiver}
                        pin={pin}
                        onCaller={setCaller}
                        onReceiver={setReceiver}
                        onPin={setPin}
                      />
                    )}
                    <button
                      type="button"
                      className="btn btn-primary rcv-go"
                      onClick={open}
                      disabled={opening}
                    >
                      {opening
                        ? "Rebuilding…"
                        : keyOn
                          ? "Unlock and rebuild"
                          : "Rebuild picture"}
                    </button>
                  </div>
                )}
              </div>
            )}

            {error && (
              <div className="alert alert-error call-alert">
                <p>{error}</p>
              </div>
            )}
          </div>
        </section>

        <section className="rcv-right">
          <Scope
            selected={selected}
            wave={wave}
            audioSrc={audioSrc}
            title={file?.name || "rx.wav"}
            accent="open"
            empty="Load a file and open it to inspect the waveform."
          />
          {rebuilt && <Rebuilt result={rebuilt} model={model} />}
        </section>
      </div>
    </>
  );
}
