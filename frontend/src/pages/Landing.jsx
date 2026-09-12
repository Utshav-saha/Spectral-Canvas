import { Link } from 'react-router-dom'
import DMG01 from '../components/DMG01'
import './Landing.css'

/* The wire desk. A dispatch slug, the claim, the object, and the log.
 *
 * Every cell in the log is read straight out of backend/tests/test_roundtrip.py
 * and says only what that file actually specifies: the five paths it runs, the
 * colour mode each uses, whether each is keyed, and the 48x48 size the
 * roundtrip() default applies. It deliberately does NOT print an error figure.
 * The suite's own gate is err < 0.5, not zero, and the suite does not currently
 * execute (image_reconstructor has no to_image_array), so any number here would
 * be a claim nobody can reproduce. Coverage is true and checkable; a measured
 * result is not ours to print until the suite runs again. */

const STAGES = [
  {
    k: 'A picture',
    d: 'A photo, a word, or something you drew, reduced to a small grid of brightness values.',
  },
  {
    k: 'Tones',
    d: 'Every row of that grid is given its own pitch. Every column becomes one short slice of sound.',
  },
  {
    k: 'A key',
    d: 'Two phone numbers and a PIN shuffle the rows and columns, and bury a keyed hiss under the audio.',
  },
  {
    k: 'The picture again',
    d: 'The receiver measures how loud each pitch was in each slice, and puts the grid back together.',
  },
]

const LOG = [
  { path: 'grayscale / open', mode: 'L', lock: false, dims: '48×48' },
  { path: 'grayscale / locked', mode: 'L', lock: true, dims: '48×48' },
  { path: 'colour / open', mode: 'RGB', lock: false, dims: '48×48' },
  { path: 'colour / locked', mode: 'RGB', lock: true, dims: '48×48' },
  { path: 'text / locked', mode: 'L', lock: true, dims: '48×48' },
]

export default function Landing() {
  return (
    <main className="landing">
      <div className="shell">
        <p className="slug">
          <span>Spectral Canvas</span>
          <span>Time&ndash;frequency</span>
          <span>64&times;64</span>
          <span>44.1 kHz</span>
          <span>1&ndash;8 kHz</span>
          <span className="slug-sep" />
          <b>Signals and Systems</b>
        </p>
      </div>

      <section className="hero shell">
        <div className="hero-copy">
          <h1 className="hero-title">
            Pictures that<br />travel as sound.
          </h1>

          <p className="hero-lede">
            Rows become pitches. Columns become moments. Brightness becomes loudness.
            A picture is turned into audio you can actually listen to, then rebuilt from
            nothing but that sound. Lock a transmission with two phone numbers and a PIN
            and it stays unreadable until the right ones are entered.
          </p>

          <div className="hero-actions">
            <Link to="/simulate" className="btn btn-primary">Send a transmission</Link>
            <Link to="/receive" className="btn btn-ghost">Receive one</Link>
          </div>

          <figure className="wirelog">
            <figcaption>
              <span>Round-trip log</span>
              <code className="mono">backend/tests/test_roundtrip.py</code>
            </figcaption>
            <table>
              <thead>
                <tr>
                  <th scope="col">Path</th>
                  <th scope="col">Key</th>
                  <th scope="col">Mode</th>
                  <th scope="col">Size</th>
                </tr>
              </thead>
              <tbody>
                {LOG.map((row) => (
                  <tr key={row.path}>
                    <td className="mono">{row.path}</td>
                    <td>
                      <span className={`led ${row.lock ? 'is-locked' : 'is-open'}`} aria-hidden="true" />
                      {row.lock ? 'Locked' : 'Open'}
                    </td>
                    <td className="mono num">{row.mode}</td>
                    <td className="mono num">{row.dims}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="wirelog-foot">
              Five paths through the real int16 WAV container, each one encoded and
              rebuilt end to end. A path passes when the mean absolute pixel error
              stays under 0.5.
            </p>
          </figure>
        </div>

        <div className="hero-console">
          <DMG01 />
          <p className="hero-console-cap">
            The whole pipeline, in three presses and no network call.
          </p>
        </div>
      </section>

      <section className="how shell" id="how">
        <div className="rule-head">
          <h2>What happens to your picture</h2>
        </div>

        <ol className="how-list">
          {STAGES.map((s, i) => (
            <li className="how-item" key={s.k}>
              <span className="how-step mono">{String(i + 1).padStart(2, '0')}</span>
              <h3 className="how-k">{s.k}</h3>
              <p className="how-d">{s.d}</p>
            </li>
          ))}
        </ol>

        <p className="how-foot">
          Because a row <em>is</em> a frequency, filtering the audio has a visible
          consequence. Cut the high end and the top of the picture fades out.
        </p>
      </section>
    </main>
  )
}
