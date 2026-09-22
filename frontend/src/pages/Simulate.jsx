import { useState, useEffect, useRef } from "react";
import { useSearchParams } from "react-router-dom";
import DoodleCanvas from "../components/DoodleCanvas";
import WaveformScope from "../components/WaveformScope";
import * as api from "../api/client";
import "./Simulate.css";

const MODES = [
  { id: "image", label: "Image", hint: "A photo, a logo, any picture file" },
  { id: "text", label: "Text", hint: "Typed, or a .txt you upload" },
  { id: "doodle", label: "Doodle", hint: "Painted here, right now" },
];

/* The four stages of a send, shown as a rail across the top. It is the same
   sequence the console teaches on the landing page, so a visitor arrives here
   already knowing where they are. */
const STAGES = ["Source", "Settings", "Encode", "On the wire"];

/* Text skips the picture entirely: each byte is split into two 4-bit symbols
   and each symbol is one of 16 tones (backend/spectral/text/text_codec.py). */
const TEXT_MAX_CHARS = 1400;

/* The two tracks a transmission can go out on. /api/health is the real list;
   this copy of app/config.py's TRACKS is only what the first paint uses, so
   the selector is never empty while that request is in flight. A third track
   - the same parallel multitone scheme sent over a phone call - was cut,
   because a GSM codec throws away exactly the amplitudes it depends on. */
const FALLBACK_TRACKS = [
  {
    id: "wav",
    number: 1,
    label: "Direct WAV",
    tagline: "A clean file, no codec in the way",
    summary:
      "Every row is a tone and the pixel sets how loud it is. Highest detail, but it only survives a channel that keeps amplitudes intact.",
    sample_rate: 44100,
    band: [1000, 8000],
    sizes: [32, 48, 64, 96, 128],
    default_size: 64,
    gray_levels: 16,
    supports_colour: true,
    supports_lock: true,
    supports_text: true,
  },
  {
    id: "call",
    number: 2,
    label: "Phone call",
    tagline: "Survives an 8 kHz voice codec",
    summary:
      "One tone at a time out of sixteen, so the pixel is in which tone plays, never in how loud it is. A GSM call destroys loudness but keeps pitch, so this gets through.",
    sample_rate: 8000,
    band: [700, 3200],
    sizes: [16, 24, 32],
    default_size: 32,
    gray_levels: 4,
    supports_colour: true,
    supports_lock: true,
    supports_text: false,
  },
];

const SIZE_HINTS = {
  32: "quickest",
  64: "balanced",
  128: "extreme detail, very long audio",
};

/* Mirrors image_fsk.budget(): a call runs in real time, so the length is worth
   showing before anyone commits to it. */
const FSK_SYMBOL_SECONDS = 0.04;
const FSK_PREAMBLE_SYMBOLS = 8;

function callSeconds(size, levels, channels) {
  const bits = size * size * channels * Math.log2(levels);
  const symbols =
    Math.ceil((bits * 7) / 4 / 4) + FSK_PREAMBLE_SYMBOLS;
  return symbols * FSK_SYMBOL_SECONDS;
}

function clockFace(seconds) {
  const whole = Math.round(seconds);
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
}

export default function Simulate() {
  const [params] = useSearchParams();
  const [mode, setMode] = useState(params.get("mode") || "image");

  const [tracks, setTracks] = useState(FALLBACK_TRACKS);
  const [trackId, setTrackId] = useState("wav");

  const [file, setFile] = useState(null);
  const [filePreview, setFilePreview] = useState(null);
  const [textMode, setTextMode] = useState("type");
  const [text, setText] = useState("");
  const [doodle, setDoodle] = useState(null);

  const [secure, setSecure] = useState(false);
  const [caller, setCaller] = useState("");
  const [receiver, setReceiver] = useState("");
  const [pin, setPin] = useState("");

  const [colour, setColour] = useState(false);
  const [size, setSize] = useState(64);

  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);
  const [wave, setWave] = useState(null);
  const [selected, setSelected] = useState(false);
  const dropRef = useRef(null);

  useEffect(() => {
    const m = params.get("mode");
    if (m) setMode(m);
  }, [params]);

  /* The server decides which tracks exist. If it cannot be reached the
     built-in copy stands in, so the page still works offline. */
  useEffect(() => {
    let live = true;
    api
      .health()
      .then((h) => {
        if (live && h.tracks?.length) setTracks(h.tracks);
      })
      .catch(() => {});
    return () => {
      live = false;
    };
  }, []);

  const track = tracks.find((t) => t.id === trackId) || tracks[0];

  /* Each track has its own usable grid sizes. Switching to one that cannot
     carry the current size snaps to that track's default rather than sending
     something the backend would refuse. */
  const switchTrack = (id) => {
    const next = tracks.find((t) => t.id === id);
    if (!next) return;
    setTrackId(id);
    setError("");
    setResult(null);
    setWave(null);
    if (!next.sizes.includes(size)) setSize(next.default_size);
    if (!next.supports_colour) setColour(false);
    if (!next.supports_lock) setSecure(false);
    if (!next.supports_text && mode === "text") setMode("image");
  };

  const pickFile = (f) => {
    if (!f) return;
    setFile(f);
    setFilePreview(URL.createObjectURL(f));
    setError("");
  };

  const send = async () => {
    setError("");
    setSending(true);
    setResult(null);
    setWave(null);
    setSelected(false);
    try {
      const locked = secure && !isText;
      const payload = {
        // text always goes out on Track 1; the backend refuses it on a call
        track: isText ? "wav" : trackId,
        source_type: mode,
        text: isText ? text : null,
        data_url: mode === "doodle" ? doodle : null,
        target_width: size,
        target_height: size,
        mode: colour ? "RGB" : "L",
        gray_levels: track.gray_levels,
        security_enabled: locked,
        caller: locked ? caller : null,
        receiver: locked ? receiver : null,
        pin: locked ? pin : null,
      };
      const response = await api.encode(
        payload,
        mode === "image" ? file : null,
      );
      setResult({ ...response, sentText: isText ? text : null });
      setWave(await api.waveform(response.session_id, 2000));
    } catch (e) {
      setError(e.message);
    } finally {
      setSending(false);
    }
  };

  const isText = mode === "text";
  const isCall = trackId === "call" && !isText;
  const channels = colour ? 3 : 1;
  const estimate = isCall
    ? callSeconds(size, track.gray_levels, channels)
    : null;

  const ready =
    (mode === "image" && file) ||
    (mode === "text" && text.trim()) ||
    (mode === "doodle" && doodle);

  const stage = result ? 3 : sending ? 2 : ready ? 1 : 0;

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
              <span>
                Track {track.number} &middot; {track.label}
              </span>
              <span>
                {size}&times;{size}
              </span>
              <span>
                {colour
                  ? isCall
                    ? "RGB"
                    : "RGB, 3 passes"
                  : "Grayscale"}
              </span>
              <span>{secure ? "Locked" : "Open"}</span>
              <span className="slug-sep" />
              <b>
                {(track.sample_rate / 1000).toFixed(1)} kHz &middot;{" "}
                {track.band[0] / 1000}&ndash;{track.band[1] / 1000} kHz
              </b>
            </>
          )}
        </p>
      </div>

      <div className="shell">
        <header className="sim-head">
          <div className="sim-head-copy">
            <h1 className="sim-title">Send a transmission</h1>
            <p className="sim-sub">
              Choose what to send, lock it if you want to, then listen to what
              your picture sounds like on the way out.
            </p>
          </div>

          {isText ? (
            <dl className="head-readout">
              <dt>Tones</dt>
              <dd>16</dd>
              <dt>Symbols</dt>
              <dd>{new TextEncoder().encode(text).length * 2}</dd>
              <dt>Band</dt>
              <dd>2&ndash;5 kHz</dd>
              <dt>Symbol</dt>
              <dd>0.05 s</dd>
              <dt>Bits</dt>
              <dd>4 / tone</dd>
            </dl>
          ) : (
            <dl className="head-readout">
              <dt>Track</dt>
              <dd>{track.number}</dd>
              {isCall ? (
                <>
                  <dt>Grid</dt>
                  <dd>
                    {size}&times;{size}
                  </dd>
                  <dt>Tones</dt>
                  <dd>16</dd>
                  <dt>Symbol</dt>
                  <dd>0.04 s</dd>
                  <dt>Call time</dt>
                  <dd>{clockFace(estimate)}</dd>
                </>
              ) : (
                <>
                  <dt>Lanes</dt>
                  <dd>{size}</dd>
                  <dt>Frames</dt>
                  <dd>{size}</dd>
                  <dt>Band</dt>
                  <dd>1&ndash;8 kHz</dd>
                  <dt>Frame</dt>
                  <dd>0.05 s</dd>
                  <dt>Passes</dt>
                  <dd>{colour ? 3 : 1}</dd>
                </>
              )}
            </dl>
          )}
        </header>

        <ol className="stagerail" aria-label="Progress">
          {STAGES.map((s, i) => (
            <li
              key={s}
              className={i < stage ? "is-done" : i === stage ? "is-now" : ""}
            >
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
              <div
                className="modeswitch"
                role="tablist"
                aria-label="What to send"
              >
                {MODES.map((m) => {
                  const blocked = m.id === "text" && !track.supports_text;
                  return (
                    <button
                      key={m.id}
                      role="tab"
                      type="button"
                      aria-selected={mode === m.id}
                      disabled={blocked}
                      title={
                        blocked
                          ? `Text is not sent over ${track.label}. Switch to Track 1.`
                          : undefined
                      }
                      className={`modeswitch-b ${mode === m.id ? "is-on" : ""}`}
                      onClick={() => {
                        setMode(m.id);
                        setError("");
                      }}
                    >
                      {m.label}
                    </button>
                  );
                })}
              </div>
            </div>

            <p className="module-hint">
              {MODES.find((m) => m.id === mode)?.hint}
            </p>

            <div className="module-body">
              {mode === "image" && (
                <div
                  ref={dropRef}
                  className="drop"
                  onDragOver={(e) => {
                    e.preventDefault();
                    dropRef.current?.classList.add("is-over");
                  }}
                  onDragLeave={() =>
                    dropRef.current?.classList.remove("is-over")
                  }
                  onDrop={(e) => {
                    e.preventDefault();
                    dropRef.current?.classList.remove("is-over");
                    pickFile(e.dataTransfer.files?.[0]);
                  }}
                >
                  {filePreview ? (
                    <>
                      <img
                        src={filePreview}
                        alt="The picture you selected"
                        className="drop-preview"
                      />
                      <p className="drop-name mono">{file?.name}</p>
                      <button
                        type="button"
                        className="btn btn-ghost"
                        onClick={() => {
                          setFile(null);
                          setFilePreview(null);
                        }}
                      >
                        Choose a different file
                      </button>
                    </>
                  ) : (
                    <>
                      <p className="drop-title">Drop an image here</p>
                      <p className="drop-hint">
                        PNG, JPG or BMP, up to 12&nbsp;MB. High-contrast
                        pictures come back sharpest.
                      </p>
                      <label className="btn btn-primary drop-btn">
                        Browse files
                        <input
                          type="file"
                          accept="image/*"
                          hidden
                          onChange={(e) => pickFile(e.target.files?.[0])}
                        />
                      </label>
                    </>
                  )}
                </div>
              )}

              {mode === "text" && (
                <div className="text-pane">
                  <div
                    className="toggle"
                    role="group"
                    aria-label="How to supply the text"
                  >
                    <button
                      type="button"
                      className={textMode === "type" ? "is-on" : ""}
                      onClick={() => setTextMode("type")}
                    >
                      Type it
                    </button>
                    <button
                      type="button"
                      className={textMode === "upload" ? "is-on" : ""}
                      onClick={() => setTextMode("upload")}
                    >
                      Upload a file
                    </button>
                  </div>
                  <p className="toggle-hint">
                    {textMode === "type"
                      ? "Every character becomes two tones, one after another, picked from sixteen. The receiver listens for which tone is playing and spells the message back out."
                      : "Upload a .txt or .md file and its contents are sent the same way."}
                  </p>

                  {textMode === "type" ? (
                    <>
                      <label className="sr-only" htmlFor="sim-text">
                        Message to send
                      </label>
                      <textarea
                        id="sim-text"
                        className="input text-area"
                        rows={8}
                        value={text}
                        maxLength={TEXT_MAX_CHARS}
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
                      <input
                        type="file"
                        accept=".txt,.md,text/plain"
                        hidden
                        onChange={async (e) => {
                          const f = e.target.files?.[0];
                          if (f) {
                            setText(await f.text());
                            setTextMode("type");
                          }
                        }}
                      />
                    </label>
                  )}
                </div>
              )}

              {mode === "doodle" && (
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
              <div className="field">
                <label className="field-label" htmlFor="track">
                  Track
                </label>
                <select
                  id="track"
                  className="input"
                  value={trackId}
                  onChange={(e) => switchTrack(e.target.value)}
                  disabled={isText}
                >
                  {tracks.map((t) => (
                    <option key={t.id} value={t.id}>
                      {t.number}. {t.label} — {t.tagline}
                    </option>
                  ))}
                </select>
                <p className="field-note">
                  {isText
                    ? "Text always goes out as a clean 44.1 kHz file."
                    : track.summary}
                </p>
              </div>

              <hr className="module-rule" />

              {isText ? (
                <p className="field-note">
                  Text is sent one tone at a time, so grid size and colour do
                  not apply. Each symbol lasts 0.05&nbsp;s, which makes a
                  100-character message about ten seconds long. Locking is not
                  available for text yet.
                </p>
              ) : (
                <>
                  <div className="field">
                    <label className="field-label" htmlFor="grid-size">
                      Grid size
                    </label>
                    <select
                      id="grid-size"
                      className="input"
                      value={size}
                      onChange={(e) => setSize(+e.target.value)}
                    >
                      {track.sizes.map((n) => (
                        <option key={n} value={n}>
                          {n} × {n}
                          {SIZE_HINTS[n] ? ` — ${SIZE_HINTS[n]}` : ""}
                        </option>
                      ))}
                    </select>
                    <p className="field-note">
                      {isCall
                        ? `A call carries about 57 bits a second, so ${size} × ${size} at ${track.gray_levels} levels takes ${clockFace(estimate)} of talking.`
                        : `The grid size is the number of frequency lanes. ${size} rows means ${size} tones in the air at once.`}
                    </p>
                  </div>

                  {track.supports_colour && (
                    <>
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
                        {isCall
                          ? "Colour sends three times the bits, so the call runs three times as long."
                          : "Colour sends three passes, so the audio runs three times as long."}
                      </p>
                    </>
                  )}

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
                    {isCall
                      ? "The numbers and PIN shuffle the rows and columns. The keyed hiss is left off here: cancelling it needs sample-exact alignment, and a phone call never gives that. Anyone without them still rebuilds static."
                      : "The numbers and PIN shuffle the rows and columns, and hide a keyed hiss under the audio. Anyone without them rebuilds static."}
                  </p>

                  {secure && (
                    <div className="lockfields">
                      <div>
                        <label className="field-label" htmlFor="caller">
                          Your number
                        </label>
                        <input
                          id="caller"
                          className="input mono"
                          inputMode="numeric"
                          maxLength={11}
                          placeholder="11 digits"
                          value={caller}
                          onChange={(e) =>
                            setCaller(e.target.value.replace(/\D/g, ""))
                          }
                        />
                      </div>
                      <div>
                        <label className="field-label" htmlFor="receiver">
                          Their number
                        </label>
                        <input
                          id="receiver"
                          className="input mono"
                          inputMode="numeric"
                          maxLength={11}
                          placeholder="11 digits"
                          value={receiver}
                          onChange={(e) =>
                            setReceiver(e.target.value.replace(/\D/g, ""))
                          }
                        />
                      </div>
                      <div>
                        <label className="field-label" htmlFor="pin">
                          PIN
                        </label>
                        <input
                          id="pin"
                          className="input mono"
                          inputMode="numeric"
                          maxLength={8}
                          placeholder="4 to 8 digits"
                          value={pin}
                          onChange={(e) =>
                            setPin(e.target.value.replace(/\D/g, ""))
                          }
                        />
                      </div>
                    </div>
                  )}
                </>
              )}
            </div>

            <div className="module-foot">
              <button
                type="button"
                className="btn btn-primary sim-send"
                onClick={send}
                disabled={!ready || sending}
              >
                {sending ? "Sending…" : "Send"}
              </button>

              {!ready && (
                <p className="field-note">
                  {mode === "doodle"
                    ? "Save your drawing first."
                    : "Add something to send."}
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
                  className={`filecard ${selected ? "is-on" : ""}`}
                  onClick={() => setSelected(true)}
                >
                  <span className="filecard-icon mono">WAV</span>
                  <span className="filecard-body">
                    <b className="mono">output.wav</b>
                    <span className="mono">
                      {result.duration.toFixed(2)}s ·{" "}
                      {result.kind === "text"
                        ? `${result.columns} symbols`
                        : `${result.rows}×${result.columns}`}
                      {" · "}
                      {(tracks.find((t) => t.id === result.track) || track).label}
                    </span>
                  </span>
                  <span
                    className={`filecard-tag ${result.encrypted ? "locked" : ""}`}
                  >
                    <span
                      className={`led ${result.encrypted ? "is-locked" : "is-open"}`}
                      aria-hidden="true"
                    />
                    {result.encrypted ? "Locked" : "Open"}
                  </span>
                </button>
                {!selected && (
                  <p className="field-note">
                    Open the file to inspect its waveform.
                  </p>
                )}

                {result.kind === "text" ? (
                  <figure className="sent-preview">
                    <p className="message-well">{result.sentText}</p>
                    <figcaption>
                      {result.metadata.bytes} bytes · {result.columns} tones
                    </figcaption>
                  </figure>
                ) : (
                  <figure className="sent-preview">
                    <img
                      src={api.previewUrl(result.session_id)}
                      alt="What was sent, at transmission size"
                    />
                    <figcaption>
                      Sent at {result.rows} × {result.columns}
                    </figcaption>
                  </figure>
                )}

                <div className="result-actions">
                  <button
                    type="button"
                    className="btn btn-primary"
                    onClick={() =>
                      api.downloadUrl(
                        api.audioUrl(result.session_id),
                        "output.wav",
                      )
                    }
                  >
                    Download audio
                  </button>
                  {mode === "doodle" && doodle && (
                    <a
                      className="btn btn-ghost"
                      href={doodle}
                      download="doodle.png"
                    >
                      Download drawing
                    </a>
                  )}
                </div>
              </div>

              <div className="scope-col module">
                {selected && wave ? (
                  <WaveformScope
                    data={wave}
                    audioSrc={api.audioUrl(result.session_id)}
                    title="output.wav"
                  />
                ) : (
                  <p className="scope-empty">
                    Open the file to inspect its waveform.
                  </p>
                )}
              </div>
            </div>
          </section>
        )}
      </div>
    </main>
  );
}
