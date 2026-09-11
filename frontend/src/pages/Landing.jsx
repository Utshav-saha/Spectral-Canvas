import { Link } from 'react-router-dom'
import SignalBench from '../components/SignalBench'
import './Landing.css'

const STAGES = [
  { k: 'Picture', d: 'A photo, a word or something you drew, shrunk to a small grid of brightness values.' },
  { k: 'Tones', d: 'Every row of that grid is given its own pitch. Every column becomes one short slice of sound.' },
  { k: 'Lock', d: 'A phone number and PIN shuffle the rows and columns, and bury a keyed hiss in the audio.' },
  { k: 'Picture again', d: 'The receiver measures how loud each pitch was in each slice, and puts the grid back together.' },
]

export default function Landing() {
  return (
    <main className="landing">
      <section className="hero shell">
        <div className="hero-copy">
          <p className="hero-kicker">Signals and Systems &middot; time&ndash;frequency coursework</p>

          <h1 className="hero-title">
            Pictures that
            <span className="hero-line">travel as sound.</span>
          </h1>

          <p className="hero-lede">
            Spectral Canvas turns an image into an audio signal you can actually listen to,
            then rebuilds the image from nothing but that sound. Lock a transmission with a
            PIN and it stays unreadable until the right one is entered.
          </p>

          <div className="hero-actions">
            <Link to="/simulate" className="btn btn-primary">Send files</Link>
            <Link to="/receive" className="btn btn-ghost">Receive files</Link>
          </div>

          <dl className="hero-facts">
            <div><dt className="mono">0.00</dt><dd>pixel error on a clean transmission</dd></div>
            <div><dt className="mono">64</dt><dd>tones in the air at once</dd></div>
            <div><dt className="mono">RGB</dt><dd>colour sent as three passes</dd></div>
          </dl>
        </div>

        <div className="hero-bench">
          <SignalBench />
        </div>
      </section>

      <section className="how shell" id="how">
        <h2 className="how-title">What happens to your picture</h2>
        <ol className="how-list">
          {STAGES.map((s, i) => (
            <li className="how-item" key={s.k}>
              <span className="how-step mono">{i + 1}</span>
              <h3 className="how-k">{s.k}</h3>
              <p className="how-d">{s.d}</p>
            </li>
          ))}
        </ol>
        <p className="how-foot">
          Because a row is a frequency, filtering the audio has a visible consequence:
          cut the high end and the top of the picture fades out.
        </p>
      </section>
    </main>
  )
}
