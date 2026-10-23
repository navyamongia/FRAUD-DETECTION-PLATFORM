import { inr, num } from '../ui.jsx'

// Facts about the selected account, taken straight from the graph data (nothing is guessed).
export function summarize(g) {
  if (!g) return null
  const c = g.nodes.find((n) => n.id === g.center)
  if (!c) return null
  const received = g.edges.filter((e) => e.target === g.center)
  const total = received.reduce((s, e) => s + (e.amount || 0), 0)
  const payments = received.reduce((s, e) => s + (e.count || 1), 0)
  const pattern = !c.is_known_mule && c.in_deg >= 5 && c.age_days <= 14
  return { c, total, payments, pattern }
}

export function GraphSummary({ g }) {
  const s = summarize(g)
  if (!s) return null
  const { c, total, payments, pattern } = s
  return (
    <div className="gsummary">
      <div className="gstats">
        <div>
          <b>{c.id}</b>
          <span>selected account</span>
        </div>
        <div>
          <b>{num(c.in_deg, 0)}</b>
          <span>different accounts paid it</span>
        </div>
        <div>
          <b>{num(c.age_days, 1)} days</b>
          <span>account age</span>
        </div>
        <div>
          <b>{inr(total)}</b>
          <span>received in this view ({num(payments, 0)} payments)</span>
        </div>
        <div>
          <b>{c.is_known_mule ? 'Yes' : 'No'}</b>
          <span>on the known-mule list</span>
        </div>
      </div>
      <p className={`gverdict ${c.is_known_mule || pattern ? 'bad' : ''}`}>
        {c.is_known_mule
          ? '⛔ This account is on the known-mule list.'
          : pattern
            ? '⚠ Many different senders paying one very new account: this matches the classic mule pattern.'
            : 'No strong mule pattern in this view.'}
      </p>
    </div>
  )
}

export function GraphLegend() {
  return (
    <div className="legend">
      <span>
        <i className="riskbar" /> Risk: low → high
      </span>
      <span>
        <i className="dot ring-black" /> Known mule
      </span>
      <span>
        <i className="dot ring-blue" /> Selected account
      </span>
      <span>
        <i className="dot sz" /> Bigger = more connections
      </span>
      <span>→ Direction of the money (red = into the selected account)</span>
    </div>
  )
}