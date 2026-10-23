import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Bar, BarChart, Cell, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { api } from '../api.js'
import { useLive } from '../useLive.jsx'
import { ActionBadge, Truth, inr, num } from '../ui.jsx'

const COLORS = { up: '#B3261E', down: '#1E7B4D', base: '#5F6B76', total: '#1B7B96' }
const hopsText = (h) => (h == null ? '–' : h === 0 ? 'is a known mule' : h >= 9 ? 'no link' : `${h} hop${h > 1 ? 's' : ''} away`)

function buildWaterfall(shap) {
  if (!shap) return []
  const rows = []
  let cur = shap.bias ?? 0
  const rng = (a, b) => [Math.min(a, b), Math.max(a, b)]
  rows.push({ name: 'base (average payment)', range: rng(0, cur), v: cur, kind: 'base' })
  for (const c of shap.contributions || []) {
    const next = cur + c.shap
    rows.push({ name: `${c.feature} = ${num(c.value, 2)}`, range: rng(cur, next), v: c.shap, kind: c.shap >= 0 ? 'up' : 'down' })
    cur = next
  }
  if (shap.raw_logodds != null) {
    const other = shap.raw_logodds - cur // features outside the top 15
    if (Math.abs(other) > 0.005) {
      rows.push({ name: 'all other features', range: rng(cur, cur + other), v: other, kind: other >= 0 ? 'up' : 'down' })
      cur += other
    }
  }
  rows.push({ name: 'final log-odds', range: rng(0, cur), v: cur, kind: 'total' })
  return rows
}

export default function TracePanel({ txnId, alertId, onClose }) {
  const nav = useNavigate()
  const { demoMode, refreshAlerts } = useLive()
  const [d, setD] = useState(null)
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')

  useEffect(() => {
    setD(null); setErr(''); setMsg('')
    api.trace(txnId).then(setD).catch((e) => setErr(e.message))
  }, [txnId])

  const wf = useMemo(() => buildWaterfall(d?.shap), [d])

  async function createCase() {
    try {
      const c = await api.createCase({
        title: `Alert on ${d.txn.receiver}`,
        alert_ids: alertId ? [alertId] : [],
        assignee: 'analyst1',
      })
      setMsg(`Case #${c.case_id} created`)
      refreshAlerts()
    } catch (e) {
      setMsg(e.message)
    }
  }

  const ms = (name) => d?.stages_ms?.find((s) => s.stage === name)?.ms
  const maxMs = Math.max(...(d?.stages_ms || []).map((s) => s.ms), 0.001)

  return (
    <>
      <div className="drawer-bg" onClick={onClose} />
      <aside className="drawer">
        <div className="drawer-h">
          <h2>Decision Trace · txn {txnId}</h2>
          <button className="btn" onClick={onClose}>✕ Close</button>
        </div>
        {err && <p className="err">{err}</p>}
        {!d && !err && <p className="muted">Loading…</p>}

        {d && (
          <>
            <div className="trace-sum">
              <div><b>{d.txn.sender}</b> → <b>{d.txn.receiver}</b> · {inr(d.txn.amount)} · {d.txn.channel}</div>
              <div className="muted">
                {d.txn.ts?.replace('T', ' ')} · device {d.txn.device_id || '–'}
                {d.txn.call_active ? ' · on a call' : ''}{d.txn.screen_share ? ' · screen sharing' : ''}
              </div>
              <div className="row">
                <ActionBadge action={d.decision.action} />
                {d.decision.override && <span className="badge hold">override: {d.decision.override}</span>}
                <span>risk <b>{num(d.decision.risk, 1)}</b></span>
                <span className="muted">source: {d.source}</span>
                {demoMode && d.ground_truth && <Truth truth={d.ground_truth} action={d.decision.action} />}
              </div>
            </div>

            <h4>Pipeline</h4>
            <div className="pipeline">
              <div className="step"><b>1 Features</b><span>{d.features.length} computed</span><em>{num(ms('features'), 2)} ms</em></div>
              <div className="arrow">→</div>
              <div className="step"><b>2 Network graph</b>
                <span>component of {d.graph?.component_size ?? '–'} · PageRank ×{num(d.graph?.pagerank_rel, 1)} · mule {hopsText(d.graph?.hops_to_known_mule)}</span>
                <em>(inside features)</em></div>
              <div className="arrow">→</div>
              <div className="step"><b>3 Model + SHAP</b><span>log-odds {num(d.shap?.raw_logodds, 2)}</span><em>{num(ms('model'), 2)} ms</em></div>
              <div className="arrow">→</div>
              <div className="step"><b>4 Thresholds</b><span>warn ≥ {num(d.decision.warn_t, 4)}<br />hold ≥ {num(d.decision.hold_t, 4)}</span><em>{num(ms('decision'), 3)} ms</em></div>
              <div className="arrow">→</div>
              <div className={`step act ${d.decision.action}`}><b>5 Action</b><span>{d.decision.action.toUpperCase()}</span><em>total {num(d.total_ms, 2)} ms</em></div>
            </div>

            <h4>Time per stage</h4>
            {d.stages_ms.map((s) => (
              <div key={s.stage} className="stagebar">
                <span>{s.stage}</span>
                <div className="bar"><div style={{ width: `${(s.ms / maxMs) * 100}%` }} /></div>
                <span>{num(s.ms, 3)} ms</span>
              </div>
            ))}
            <p className="muted small">“full_shap (trace)” is computed only for this view and is not part of the scoring time.</p>

            <h4>Why the model scored it this way (SHAP waterfall)</h4>
            <p className="muted small">Red pushes toward fraud, green pushes toward genuine. Top 15 features.</p>
            <ResponsiveContainer width="100%" height={wf.length * 26 + 30}>
              <BarChart data={wf} layout="vertical" margin={{ top: 4, right: 16, left: 8, bottom: 4 }}>
                <XAxis type="number" />
                <YAxis type="category" dataKey="name" width={200} tick={{ fontSize: 11 }} interval={0} />
                <ReferenceLine x={0} stroke="#9AA6B2" />
                <Tooltip content={({ active, payload }) =>
                  active && payload?.length
                    ? <div className="tip">{payload[0].payload.name}: {num(payload[0].payload.v, 2)}</div>
                    : null} />
                <Bar dataKey="range" isAnimationActive={false}>
                  {wf.map((r, i) => <Cell key={i} fill={COLORS[r.kind]} />)}
                </Bar>
              </BarChart>
            </ResponsiveContainer>

            {d.reasons?.length > 0 && (
              <>
                <h4>Reasons shown to the user</h4>
                <ul className="small-list">
                  {d.reasons.map((r) => <li key={r.id}><b>{r.id}</b>: {r.text?.en}</li>)}
                </ul>
              </>
            )}

            <h4>All features</h4>
            <div className="scroll tall">
              <table>
                <thead><tr><th>Feature</th><th>Group</th><th>Value</th><th>Normal</th><th>vs normal</th></tr></thead>
                <tbody>
                  {d.features.map((f) => {
                    const ratio = f.normal ? f.value / f.normal : null
                    return (
                      <tr key={f.name}>
                        <td>{f.name}</td>
                        <td><span className={`chip g-${f.group}`}>{f.group}</span></td>
                        <td>{num(f.value, 3)}</td>
                        <td>{num(f.normal, 3)}</td>
                        <td className={ratio != null && Math.abs(ratio) >= 5 ? 'hot' : ''}>
                          {ratio != null && Number.isFinite(ratio) ? `×${num(ratio, 1)}` : '–'}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>

            <div className="row mt">
              <button className="btn primary" onClick={() => nav(`/investigate?account=${encodeURIComponent(d.txn.receiver)}`)}>🔎 Investigate receiver</button>
              <button className="btn" onClick={createCase}>📁 Create case</button>
              {msg && <span className="ok-text">{msg}</span>}
            </div>
          </>
        )}
      </aside>
    </>
  )
}