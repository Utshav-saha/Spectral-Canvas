import { useState, useRef, useEffect } from 'react'
import { Link, NavLink, useNavigate } from 'react-router-dom'
import './NavBar.css'

/* Text-only rail. The mark is eight lanes because the product is eight
   frequency lanes; it is the structure, not an ornament. */

const LANES = [7, 13, 9, 17, 11, 20, 8, 14]

export default function NavBar() {
  const [open, setOpen] = useState(false)
  const wrapRef = useRef(null)
  const navigate = useNavigate()

  useEffect(() => {
    const close = (e) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target)) setOpen(false)
    }
    const esc = (e) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', close)
    document.addEventListener('keydown', esc)
    return () => {
      document.removeEventListener('mousedown', close)
      document.removeEventListener('keydown', esc)
    }
  }, [])

  const go = (mode) => { setOpen(false); navigate(`/simulate?mode=${mode}`) }

  return (
    <header className="nav">
      <div className="shell nav-inner">
        <Link to="/" className="nav-brand">
          <span className="nav-bars" aria-hidden="true">
            {LANES.map((h, i) => <i key={i} style={{ height: `${h}px` }} />)}
          </span>
          <span>Spectral Canvas</span>
        </Link>

        <nav className="nav-links" aria-label="Main">
          <div
            className="nav-drop"
            ref={wrapRef}
            onMouseEnter={() => setOpen(true)}
            onMouseLeave={() => setOpen(false)}
          >
            <button
              type="button"
              className="nav-link"
              aria-expanded={open}
              aria-haspopup="menu"
              onClick={() => setOpen((o) => !o)}
            >
              Send
            </button>

            <div className={`nav-menu ${open ? 'is-open' : ''}`} role="menu">
              <button role="menuitem" type="button" onClick={() => go('image')}>
                <b>Send an image</b><span>A photo, a logo, any picture file</span>
              </button>
              <button role="menuitem" type="button" onClick={() => go('text')}>
                <b>Send text</b><span>Type it, or upload a .txt</span>
              </button>
              <button role="menuitem" type="button" onClick={() => go('doodle')}>
                <b>Draw it live</b><span>Paint straight onto the canvas</span>
              </button>
            </div>
          </div>

          <NavLink to="/receive" className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}>
            Receive
          </NavLink>
          <NavLink to="/call" className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}>
            Call
          </NavLink>
          <a className="nav-link" href="/#how">How it works</a>
        </nav>
      </div>
    </header>
  )
}
