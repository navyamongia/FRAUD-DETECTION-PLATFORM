import { Link } from 'react-router-dom'
import { useLive } from '../useLive.jsx'
import { Card, inr } from '../ui.jsx'
import { LEVEL_BADGE, LEVEL_TEXT, demoTime, voiceIcon, voiceText, whatHappened } from '../demoText.js'

// Live list of payments made from the phone, in plain words.
export default function DemoFeed({ onTrace }) {
  const { demoSessions } = useLive()
  const rows = demoSessions.slice(0, 8)
  return (
    <Card
      title="Demo payments"
      right={
        <Link className="btn" to="/demo">
          Open Demo control →
        </Link>
      }
    >
      {rows.length === 0 ? (
        <p className="muted">No demo payments yet. Start one from Demo control and it appears here live.</p>
      ) : (
        <div className="scroll short">
          <table className="clickable">
            <thead>
              <tr>
                <th>Time</th>
                <th>What happened</th>
                <th>Amount</th>
                <th>Status</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {rows.map((s) => {
                const level = s.dashboard?.level || 'pending'
                return (
                  <tr key={s.id} onClick={() => s.txn_id != null && onTrace(s.txn_id, s.alert_id)}>
                    <td>{demoTime(s)}</td>
                    <td>
                      <b>{s.scenario_title}</b>
                      
                      <div className="muted small">{whatHappened(s)}</div>
                      {voiceText(s) && (
                        <div className="voice-line">
                          {voiceIcon(s)} {voiceText(s)}
                        </div>
                      )}
                    </td>
                    <td>{inr(s.amount)}</td>
                    <td>
                      <span className={`badge ${LEVEL_BADGE[level]}`}>{LEVEL_TEXT[level]}</span>
                      <div className="muted small">{s.dashboard?.label}</div>
                    </td>
                    <td>
                      {s.dashboard?.graph_account && (
                        <Link
                          className="btn"
                          to={`/investigate?account=${encodeURIComponent(s.dashboard.graph_account)}`}
                          onClick={(e) => e.stopPropagation()}
                        >
                          Mule network
                        </Link>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  )
}