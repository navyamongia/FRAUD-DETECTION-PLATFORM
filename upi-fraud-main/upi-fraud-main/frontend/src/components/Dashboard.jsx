import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { CartesianGrid, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { api } from '../api.js'
import { useLive } from '../useLive.jsx'
import { Card, Kpi, Truth, inr, num, timeOf } from '../ui.jsx'
import TracePanel from './TracePanel.jsx'
import ThresholdPanel from './ThresholdPanel.jsx'
import AttentionBanner from './AttentionBanner.jsx'
import DemoFeed from './DemoFeed.jsx'

const DECISION = { allow: 'Allowed', warn: 'Warned', hold: 'Held' }
const FILTERS = [
  ['', 'All'],
  ['hold', 'Held'],
  ['warn', 'Warned'],
  ['demo', 'Demo payments'],
]

function Decision({ action }) {
  return <span className={`badge ${action}`}>{DECISION[action] || action}</span>
}

function ReplayControls() {
  const { replay, setReplay } = useLive()
  const [speed, setSpeed] = useState(600)
  const [err, setErr] = useState('')

  useEffect(() => { api.replay().then(setReplay).catch(() => {}) }, [])
  useEffect(() => { if (replay?.speed) setSpeed(replay.speed) }, [replay?.speed])

  const send = (action, sp) =>
    api.setReplay({ action, speed: sp }).then(setReplay).catch((e) => setErr(e.message))
  const running = replay?.running

  return (
    <div className="row">
      <button className="btn primary" onClick={() => send(running ? 'pause' : 'start', speed)}>
        {running ? '⏸ Pause background traffic' : '▶ Start background traffic'}
      </button>
      <select
        title="How fast the background traffic plays"
        value={speed}
        onChange={(e) => {
          const v = Number(e.target.value)
          setSpeed(v)
          if (running) send('speed', v)
        }}
      >
        {[60, 600, 2000, 5000, 20000].map((v) => <option key={v} value={v}>{v}× speed</option>)}
      </select>
      {replay && (
        <span className="muted">
          {replay.replay_ts?.replace('T', ' ')} · {num(replay.progress_pct, 1)}%{replay.finished ? ' · finished (restart the backend for a fresh run)' : ''}
        </span>
      )}
      {err && <span className="err">{err}</span>}
    </div>
  )
}

export default function Dashboard() {
  const { stats, latencyHist, txns, alerts, demoMode } = useLive()
  const [sp, setSp] = useSearchParams()
  const [filter, setFilter] = useState('')
  const [open, setOpen] = useState(() =>
    sp.get('trace')
      ? { txnId: Number(sp.get('trace')), alertId: sp.get('alert') ? Number(sp.get('alert')) : null }
      : null
  )

  const openTrace = (txnId, alertId) => setOpen({ txnId, alertId: alertId ?? null })
  const close = () => { setOpen(null); setSp({}) }
  const rows = alerts.filter((a) => (filter === '' ? true : filter === 'demo' ? a.source === 'simulator' : a.action === filter))
  const a = stats?.actions || {}
  const k = stats?.known_outcomes || {}
  const lat = stats?.latency_ms || {}

  return (
    <div className="page">
      <AttentionBanner onTrace={openTrace} />

      <DemoFeed onTrace={openTrace} />

      <Card title="Background activity" right={<ReplayControls />}>
        <p className="muted small mt-0">
          Simulated payments from the dataset keep this screen busy and give the mule network its history. Start them a minute
          before the demo.
        </p>
        <div className="kpis">
          <Kpi label="Payments checked" hint="Every payment is scored by the model before it settles" value={num(stats?.txns_scored, 0)} sub={`${num(stats?.txns_per_sec, 1)} per second`} />
          <Kpi label="Allowed" hint="Looked normal, no alert" value={num(a.allow, 0)} tone="allow" />
          <Kpi label="Warned" hint="Customer was shown an alert and could continue or cancel" value={num(a.warn, 0)} tone="warn" />
          <Kpi label="Held" hint="Stopped until the customer verifies" value={num(a.hold, 0)} tone="hold" />
          <Kpi label="Decision speed" hint="Typical (p50) and slowest 1% (p99) time to decide, against a 200 ms budget" value={`${num(lat.p50, 2)} / ${num(lat.p99, 2)} ms`} sub={`budget ${lat.budget ?? 200} ms`} />
          {demoMode && (
            <Kpi label="Fraud caught / missed" hint="Uses the true labels in the dataset" value={`${num(k.fraud_caught, 0)} / ${num(k.fraud_missed, 0)}`} sub={`${num(k.false_alarms, 0)} false alarms`} />
          )}
        </div>
      </Card>

      <Card
        title={`Alerts (${rows.length})`}
        right={
          <div className="chips">
            {FILTERS.map(([v, label]) => (
              <button key={v || 'all'} className={`chipbtn ${filter === v ? 'on' : ''}`} onClick={() => setFilter(v)}>
                {label}
              </button>
            ))}
          </div>
        }
      >
        <div className="scroll tall">
          <table className="clickable">
            <thead>
              <tr>
                <th>Time</th><th>Payment</th><th>Amount</th>
                <th title="0 = very normal, 100 = very unusual">Risk</th>
                <th>Decision</th><th>Why it was flagged</th>{demoMode && <th>Truth</th>}<th>Case</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((al) => (
                <tr
                  key={al.alert_id ?? `${al.txn_id}`}
                  className={al.source === 'simulator' ? 'demo-row' : ''}
                  onClick={() => openTrace(al.txn_id, al.alert_id)}
                >
                  <td>{timeOf(al.ts)}</td>
                  <td>
                    {al.sender} → {al.receiver} {al.source === 'simulator' && <span className="badge demo">demo</span>}
                  </td>
                  <td>{inr(al.amount)}</td>
                  <td>{num(al.risk, 0)}</td>
                  <td><Decision action={al.action} /></td>
                  <td>
                    {al.override && <span className="badge hold">{al.override}</span>}{' '}
                    {al.reasons?.[0]?.text?.en || al.reasons?.[0]?.id || ''}
                  </td>
                  {demoMode && <td><Truth truth={al.truth} action={al.action} /></td>}
                  <td>{al.case_id ? `#${al.case_id}` : ''}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {rows.length === 0 && (
            <p className="muted">
              No alerts here yet. They appear when a payment looks risky: run a demo scenario or start the background traffic.
            </p>
          )}
        </div>
      </Card>

      <Card title="Live payment feed">
        <div className="feed">
          {txns.slice(0, 14).map((t) => (
            <div key={`${t.txn_id}-${t.ts}`} className="feed-row">
              <span className="muted">{timeOf(t.ts)}</span>
              <span>{t.sender} → {t.receiver}</span>
              <span>{inr(t.amount)}</span>
              <span>{num(t.risk, 0)}</span>
              <Decision action={t.action} />
            </div>
          ))}
          {txns.length === 0 && <p className="muted">Nothing yet. Press “Start background traffic” above.</p>}
        </div>
      </Card>

      <details className="advanced">
        <summary>Advanced: decision speed chart and alert thresholds</summary>
        <div className="advanced-body">
          <Card title="Decision speed over time (ms, log scale)">
            <ResponsiveContainer width="100%" height={240}>
              <LineChart data={latencyHist} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="t" hide />
                <YAxis scale="log" domain={[0.1, 300]} allowDataOverflow ticks={[0.1, 1, 10, 100, 200]} />
                <Tooltip formatter={(v) => `${num(v, 2)} ms`} />
                <Legend />
                <ReferenceLine y={200} stroke="#B3261E" strokeDasharray="6 4" label={{ value: '200 ms budget', fill: '#B3261E', fontSize: 11 }} />
                <Line dataKey="p50" stroke="#1E7B4D" dot={false} isAnimationActive={false} />
                <Line dataKey="p95" stroke="#B7791F" dot={false} isAnimationActive={false} />
                <Line dataKey="p99" stroke="#B3261E" dot={false} isAnimationActive={false} />
              </LineChart>
            </ResponsiveContainer>
          </Card>
          <ThresholdPanel />
        </div>
      </details>

      {open && <TracePanel txnId={open.txnId} alertId={open.alertId} onClose={close} />}
    </div>
  )
}