import { useState, useRef, useEffect } from 'react'
import { Link, NavLink, useNavigate } from 'react-router-dom'
import './NavBar.css'

/* Text-only nav. The underline grows from the centre on hover, which is the
   only motion in the header - links are the one thing here you act on. */

export default function NavBar() {
  const [open, setOpen] = useState(false)
  const wrapRef = useRef(null)
  const navigate = useNavigate()

  useEffect(() => {
    const close = (e) => { if (wrapRef.current && !wrapRef.current.contains(e.target)) setOpen(false) }
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [])

  const go = (mode) => { setOpen(false); navigate(`/simulate?mode=${mode}`) }

  return (
    <header className="nav">
      <div className="shell nav-inner">
        <Link to="/" className="nav-brand">
          <span className="nav-bars" aria-hidden="true">
            {[9, 17, 13, 21, 11].map((h, i) => <i key={i} style={{ height: `${h}px` }} />)}
          </span>
          Spectral Canvas
        </Link>

        <nav className="nav-links" aria-label="Main">
          <div
            className="nav-drop" ref={wrapRef}
            onMouseEnter={() => setOpen(true)}
            onMouseLeave={() => setOpen(false)}
          >
            <button
              type="button" className="nav-link"
              aria-expanded={open} aria-haspopup="menu"
              onClick={() => setOpen((o) => !o)}
            >
              Simulation
            </button>

            <div className={`nav-menu ${open ? 'is-open' : ''}`} role="menu">
              <button role="menuitem" type="button" onClick={() => go('image')}>
                <b>Send an image</b><span>Photo, logo or any picture file</span>
              </button>
              <button role="menuitem" type="button" onClick={() => go('text')}>
                <b>Send text</b><span>Type it or upload a .txt</span>
              </button>
              <button role="menuitem" type="button" onClick={() => go('doodle')}>
                <b>Live doodle</b><span>Draw it yourself on the canvas</span>
              </button>
            </div>
          </div>

          <NavLink to="/receive" className="nav-link">Receive</NavLink>
          <a className="nav-link" href="#how">How it works</a>
        </nav>
      </div>
    </header>
  )
}
