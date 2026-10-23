import { useEffect, useState } from 'react'
import { NavLink, Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { LiveProvider, useLive } from './useLive.jsx'
import PhoneSimulator from './components/PhoneSimulator.jsx'
import DemoControl from './components/DemoControl.jsx'
import Dashboard from './components/Dashboard.jsx'
import Investigation from './components/Investigation.jsx'
import Evaluation from './components/Evaluation.jsx'

const link = ({ isActive }) => (isActive ? 'active' : '')

// Rarely needed pages live here so the main menu stays short.
const MORE_PATHS = ['/evaluation', '/simulator']

function MoreMenu() {
  const [open, setOpen] = useState(false)
  const { pathname } = useLocation()
  const inMore = MORE_PATHS.includes(pathname)

  useEffect(() => {
    if (!open) return
    const onKey = (e) => e.key === 'Escape' && setOpen(false)
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open])

  return (
    <div className="more">
      <button
        className={`more-btn ${inMore ? 'active' : ''}`}
        aria-haspopup="true"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        More <span aria-hidden="true" className="caret">▾</span>
      </button>
      {open && (
        <>
          <div className="more-bg" onClick={() => setOpen(false)} />
          <div className="more-menu" onClick={() => setOpen(false)}>
            <NavLink to="/evaluation" className={link}>Model evaluation</NavLink>
            <NavLink to="/simulator" className={link}>Phone simulator (old, in-browser)</NavLink>
          </div>
        </>
      )}
    </div>
  )
}

function ShieldIcon() {
  return (
    <svg className="brand-icon" width="32" height="32" viewBox="0 0 48 48" aria-hidden="true">
      <path d="M24 3.5 7.5 9.8v11.6c0 10.1 6.8 19 16.5 22.7 9.7-3.7 16.5-12.6 16.5-22.7V9.8L24 3.5Z" fill="#1B7B96" />
      <path d="M24 3.5v40.6C14.3 40.4 7.5 31.5 7.5 21.4V9.8L24 3.5Z" fill="#14627A" />
      <path d="M24 17 16 29M24 17l8 12M16 29h16" stroke="#fff" strokeWidth="2" strokeLinecap="round" fill="none" />
      <circle cx="24" cy="17" r="4.2" fill="#fff" />
      <circle cx="16" cy="29" r="3.4" fill="#fff" />
      <circle cx="32" cy="29" r="3.4" fill="#fff" />
      <circle cx="24" cy="17" r="7.4" fill="none" stroke="#ACDAFB" strokeWidth="1.6" />
    </svg>
  )
}

function Header() {
  const { connected, demoMode, setDemoMode } = useLive()
  return (
    <header className="top">
      <div className="brand"><ShieldIcon /><span>UPI Fraud Alert</span></div>
      <nav aria-label="Main">
        <NavLink to="/demo" className={link}>Demo control</NavLink>
        <NavLink to="/dashboard" className={link}>Live monitor</NavLink>
        <NavLink to="/investigate" className={link}>Investigation</NavLink>
        <MoreMenu />
      </nav>
      <div className="top-right">
        <label className="check small" title="Shows the true fraud labels from the dataset next to the model's decisions">
          <input type="checkbox" checked={demoMode} onChange={(e) => setDemoMode(e.target.checked)} />
          Show true fraud labels
        </label>
        <span className={`pill ${connected ? 'on' : 'off'}`} role="status">{connected ? '● Connected' : '● Offline'}</span>
      </div>
    </header>
  )
}

export default function App() {
  return (
    <LiveProvider>
      <Header />
      <main>
        <Routes>
          <Route path="/" element={<Navigate to="/demo" replace />} />
          <Route path="/demo" element={<DemoControl />} />
          <Route path="/simulator" element={<PhoneSimulator />} />
          <Route path="/dashboard" element={<Dashboard />} />
          <Route path="/investigate" element={<Investigation />} />
          <Route path="/evaluation" element={<Evaluation />} />
        </Routes>
      </main>
    </LiveProvider>
  )
}