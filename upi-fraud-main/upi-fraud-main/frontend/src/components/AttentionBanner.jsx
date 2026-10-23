import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useLive } from '../useLive.jsx'
import { inr } from '../ui.jsx'
import { bannerTitle, voiceIcon, voiceText } from '../demoText.js'

// Big, plain-language banner for the newest demo payment when something needs attention.
export default function AttentionBanner({ onTrace }) {
  const { demoSessions } = useLive()
  const [dismissed, setDismissed] = useState(null)
  const s = demoSessions[0]
  if (!s || s.id === dismissed) return null
  const level = s.dashboard?.level
  if (!['alert', 'held', 'blocked'].includes(level)) return null

  const icon = level === 'alert' ? '⚠️' : '⛔'
  const reasons = (s.reasons || []).slice(0, 3)
  const graph = s.dashboard?.graph_account
  return (
    <div className={`attn ${level}`} role="alert">
      <div className="attn-h">
        <div>
          <h3>
            {icon} {bannerTitle(s)}
          </h3>
          <div className="muted">
           {inr(s.amount)} → <b>{s.receiver}</b> · {s.scenario_title}
          </div>
          {voiceText(s) && (
            <div className="voice-line">
              {voiceIcon(s)} {voiceText(s)}
            </div>
          )}
        </div>
        <button className="x" onClick={() => setDismissed(s.id)} aria-label="Dismiss">
          ×
        </button>
      </div>

      {reasons.length > 0 && (
        <div>
          <b>Why it was flagged</b>
          <ul>
            {reasons.map((r) => (
              <li key={r.id}>{r.text?.en || r.id}</li>
            ))}
          </ul>
        </div>
      )}

      <div className="row">
        {s.txn_id != null && (
          <button className="btn" onClick={() => onTrace(s.txn_id, s.alert_id)}>
            See how the model decided
          </button>
        )}
        {graph && (
          <Link className="btn primary" to={`/investigate?account=${encodeURIComponent(graph)}`}>
            View mule network →
          </Link>
        )}
      </div>
    </div>
  )
}