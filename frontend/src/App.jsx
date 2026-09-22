import { BrowserRouter, Routes, Route } from 'react-router-dom'
import NavBar from './components/NavBar'
import Landing from './pages/Landing'
import Simulate from './pages/Simulate'
import Receive from './pages/Receive'
import Call from './pages/Call'
import Experiments from './pages/Experiments'

export default function App() {
  return (
    <BrowserRouter>
      <div className="app-frame">
        <NavBar />
        <Routes>
          <Route path="/" element={<Landing />} />
          <Route path="/simulate" element={<Simulate />} />
          <Route path="/receive" element={<Receive />} />
          <Route path="/call" element={<Call />} />
          <Route path="/experiments" element={<Experiments />} />
        </Routes>
        <footer className="site-foot">
          <div className="shell">
            <span className="foot-mark">Spectral Canvas &mdash; Signals and Systems coursework</span>
            <span className="foot-line">rows are pitches &middot; columns are moments</span>
          </div>
        </footer>
      </div>
    </BrowserRouter>
  )
}
