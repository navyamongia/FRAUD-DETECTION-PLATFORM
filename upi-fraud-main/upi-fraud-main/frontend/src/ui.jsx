export const inr = (n) =>
  n == null || n === '' ? '–' : '₹' + Number(n).toLocaleString('en-IN', { maximumFractionDigits: 0 })

export const num = (n, d = 2) =>
  n == null || Number.isNaN(Number(n))
    ? '–'
    : Number(n).toLocaleString('en-IN', { maximumFractionDigits: d })

export const timeOf = (ts) => (ts ? String(ts).slice(11, 19) : '')

export function ActionBadge({ action }) {
  return <span className={`badge ${action}`}>{action}</span>
}

export function Card({ title, right, children, className = '' }) {
  return (
    <section className={`card ${className}`}>
      {(title || right) && (
        <header className="card-h">
          <h3>{title}</h3>
          <div>{right}</div>
        </header>
      )}
      {children}
    </section>
  )
}

export function Kpi({ label, value, sub, tone = '', hint }) {
  return (
    <div className={`kpi ${tone}`} title={hint}>
      <div className="kpi-v">{value}</div>
      <div className="kpi-l">{label}</div>
      {sub && <div className="kpi-s">{sub}</div>}
    </div>
  )
}

// Demo-mode badge: compares the real label with what the system decided.
export function Truth({ truth, action }) {
  if (!truth) return <span className="muted">–</span>
  const flagged = action === 'warn' || action === 'hold'
  if (truth.fraud)
    return (
      <span className={`badge ${flagged ? 'ok' : 'bad'}`}>
        {flagged ? 'caught' : 'missed'}: {truth.type}
      </span>
    )
  return (
    <span className={`badge ${flagged ? 'bad' : 'ok'}`}>
      {flagged ? 'false alarm' : 'genuine'}
      {truth.hard ? ` (${truth.hard})` : ''}
    </span>
  )
}