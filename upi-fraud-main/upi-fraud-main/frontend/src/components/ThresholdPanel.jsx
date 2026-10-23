import { useEffect, useRef, useState } from 'react'
import { CartesianGrid, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { api } from '../api.js'
import { useLive } from '../useLive.jsx'
import { Card, Kpi, inr, num } from '../ui.jsx'

const MIN = 0.01
const MAX = 5
// Sliders are logarithmic: the useful range is 0.01% to 5% of genuine payments.
const toPos = (p) => Math.min(100, Math.max(0, (100 * Math.log(p / MIN)) / Math.log(MAX / MIN)))
const fromPos = (s) => Number((MIN * Math.pow(MAX / MIN, s / 100)).toPrecision(2))

export default function ThresholdPanel() {
  const { config, setConfig } = useLive()
  const [warn, setWarn] = useState(null)
  const [hold, setHold] = useState(null)
  const [sim, setSim] = useState(null)
  const [curve, setCurve] = useState([])
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')
  const init = useRef(false)

  useEffect(() => {
    api.curve().then((c) => setCurve(c.points || [])).catch(() => {})
    api.config().then(setConfig).catch(() => {})
  }, [])

  useEffect(() => {
    if (config && !init.current && config.warn_pct != null) {
      init.current = true
      setWarn(config.warn_pct)
      setHold(config.hold_pct)
    }
  }, [config])

  // debounce ~100 ms, as the contract suggests
  useEffect(() => {
    if (warn == null || hold == null) return
    const id = setTimeout(() => {
      api.simulate(warn, hold).then((s) => { setSim(s); setErr('') }).catch((e) => setErr(e.message))
    }, 100)
    return () => clearTimeout(id)
  }, [warn, hold])

  const onWarn = (s) => {
    const w = fromPos(Number(s))
    setWarn(w)
    if (hold > w) setHold(w) // hold_pct must be <= warn_pct
  }
  const onHold = (s) => setHold(Math.min(fromPos(Number(s)), warn))

  const apply = () =>
    api.setConfig({ warn_pct: warn, hold_pct: hold })
      .then(() => api.config().then(setConfig))
      .then(() => { setMsg('Applied to the live system'); setTimeout(() => setMsg(''), 3000) })
      .catch((e) => setErr(e.message))

  const toggleOverrides = (on) =>
    api.setConfig({ overrides_enabled: on }).then(() => api.config().then(setConfig)).catch((e) => setErr(e.message))

  if (warn == null) return <Card title="Threshold slider"><p className="muted">Waiting for backend config…</p></Card>

  return (
    <Card
      title="Threshold slider: fraud caught vs. customers interrupted"
      right={
        <label className="check small">
          <input type="checkbox" checked={!!config?.overrides_enabled} onChange={(e) => toggleOverrides(e.target.checked)} />
          Override rules ON
        </label>
      }
    >
      <div className="grid2">
        <div>
          <div className="slider-row">
            <label>Warn at <b>{warn}%</b> of genuine payments</label>
            <input type="range" min="0" max="100" step="0.5" value={toPos(warn)} onChange={(e) => onWarn(e.target.value)} />
          </div>
          <div className="slider-row">
            <label>Hold at <b>{hold}%</b> of genuine payments</label>
            <input type="range" min="0" max="100" step="0.5" value={toPos(hold)} onChange={(e) => onHold(e.target.value)} />
          </div>
          <p className="muted">Lower numbers = fewer interruptions for genuine users, but more fraud slips through. Numbers come from the held-out test period.</p>
          <div className="row">
            <button className="btn primary" onClick={apply}>Apply to live system</button>
            {msg && <span className="ok-text">{msg}</span>}
            {err && <span className="err">{err}</span>}
          </div>

          {sim && (
            <div className="kpis mt">
              <Kpi label="Fraud caught (recall)" value={`${num(sim.recall * 100, 1)}%`} sub={`${num(sim.fraud_caught, 0)} of ${num(sim.fraud_total, 0)}`} tone="allow" />
              <Kpi label="Precision" value={`${num(sim.precision * 100, 1)}%`} />
              <Kpi label="Genuine users interrupted" value={`${num((sim.friction?.warned_pct ?? 0) + (sim.friction?.held_pct ?? 0), 2)}%`}
                sub={`warned ${num(sim.friction?.warned_pct)}% · held ${num(sim.friction?.held_pct)}%`} tone="warn" />
              <Kpi label="Fraud prevented" value={inr(sim.fraud_prevented_inr)} sub={`of ${inr(sim.fraud_total_inr)}`} />
            </div>
          )}
          {sim && (
            <p className="muted">
              ≈ {num(sim.friction?.genuine_per_1000_flagged, 1)} genuine payments flagged per 1,000.
              Assumes {num((sim.assumptions?.warn_heeded ?? 0) * 100, 0)}% heed a warning and {num((sim.assumptions?.hold_effective ?? 0) * 100, 0)}% of holds stop fraud. {sim.note}
            </p>
          )}
        </div>

        <ResponsiveContainer width="100%" height={280}>
          <LineChart data={curve} margin={{ top: 8, right: 16, left: 0, bottom: 8 }}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis dataKey="flag_pct" type="number" scale="log" domain={[0.01, 5]} ticks={[0.01, 0.1, 0.5, 1, 5]} allowDataOverflow
              label={{ value: '% of genuine payments flagged', position: 'insideBottom', offset: -2, fontSize: 11 }} />
            <YAxis domain={[0, 1]} />
            <Tooltip formatter={(v) => num(v, 3)} labelFormatter={(l) => `${l}% of genuine flagged`} />
            <Legend verticalAlign="top" />
            <Line dataKey="recall" stroke="#1E7B4D" dot={false} isAnimationActive={false} />
            <Line dataKey="precision" stroke="#1B7B96" dot={false} isAnimationActive={false} />
            <ReferenceLine x={warn} stroke="#B7791F" strokeWidth={2} label={{ value: 'warn', fontSize: 11 }} />
            <ReferenceLine x={hold} stroke="#B3261E" strokeWidth={2} label={{ value: 'hold', fontSize: 11 }} />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </Card>
  )
}