import { BrowserRouter, Routes, Route } from 'react-router-dom'
import NavBar from './components/NavBar'
import Landing from './pages/Landing'
import Simulate from './pages/Simulate'
import Receive from './pages/Receive'

export default function App() {
  return (
    <BrowserRouter>
      <NavBar />
      <Routes>
        <Route path="/" element={<Landing />} />
        <Route path="/simulate" element={<Simulate />} />
        <Route path="/receive" element={<Receive />} />
      </Routes>
      <footer className="site-foot">
        <div className="shell">
          <span>Spectral Canvas — Signals and Systems coursework</span>
          <span className="mono">rows are pitches · columns are moments</span>
        </div>
      </footer>
    </BrowserRouter>
  )
}
