import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api.js'
import { useLive } from '../useLive.jsx'
import { ActionBadge, Card, inr } from '../ui.jsx'
import { LANG_NAME, voiceIcon, voiceText } from '../demoText.js'
const FRIENDLY = { allow: 'No alert', warn: 'Alert', hold: 'Hold' }
const LEVEL_BADGE = { pending: '', clear: 'ok', alert: 'warn', held: 'hold', blocked: 'bad' }
const STEPS = ['QR on terminal', 'Customer pays', 'Customer decides', 'Result']

const timeStr = (t) => (t ? new Date(t * 1000).toLocaleTimeString() : '')

function piText(s) {
  return (
    {
      waiting_scan: 'Shows the QR code',
      processing: 'Processing…',
      awaiting_customer: 'Waiting for customer',
      success: '✓ Payment successful',
      failed: '✕ Payment failed',
      expired: 'Replaced by a newer QR',
    }[s.status] || '–'
  )
}

function phoneText(s) {
  switch (s.status) {
    case 'waiting_scan':
      return 'Waiting for the customer to scan and press Pay'
    case 'processing':
      return 'Processing…'
    case 'awaiting_customer':
      return s.action === 'hold'
        ? 'Payment held: the customer must verify or cancel'
        : 'Alert shown: the customer can continue or cancel'
    case 'success':
      return 'Payment successful'
    case 'failed':
      return s.outcome === 'rejected' ? 'Payment blocked' : 'Payment cancelled'
    default:
      return 'This QR is no longer active'
  }
}

function choiceText(s) {
  if (!s.choice) return '–'
  if (s.action === 'allow') return 'Paid'
  return (
    { proceed: 'Continued anyway', cancel: 'Cancelled', verified: 'Verified (code)', failed_verification: 'Wrong code' }[
      s.choice
    ] || s.choice
  )
}

// ------------------------------------------------------------------ scenario card
export function PresetCard({ p, busy, onStart, onRecalibrate }) {
  const state = !p.available ? 'missing' : p.ok ? 'ready' : 'mismatch'
  return (
    <div className="preset">
      <div className="row between">
        <h4>{p.title}</h4>
        <span className={`badge ${p.expected}`}>{FRIENDLY[p.expected]}</span>
      </div>
      <p className="story">{p.story}</p>

      <div className={`health ${state}`}>
        {state === 'ready' && <>✓ Ready · the model scores this as “{FRIENDLY[p.observed]}” ({inr(p.amount)})</>}
        {state === 'mismatch' && (
          <>
            ⚠ The model now says “{FRIENDLY[p.observed]}” but this scenario needs “{FRIENDLY[p.expected]}”.
            {p.id === 'normal' && ' Reset the thresholds under Advanced on the Live monitor.'}
          </>
        )}
        {state === 'missing' && <>⚠ No payment currently triggers an alert with the present thresholds.</>}
        {(p.id === 'risky' || p.id === 'mule') && state !== 'ready' && (
          <div className="mt-s">
            <button className="btn" disabled={busy} onClick={onRecalibrate}>
              Recalibrate
            </button>
          </div>
        )}
      </div>
      {p.graph_ok === false && (
        <div className="health mismatch">
          ⚠ The Investigation page has no network graph for {p.receiver} yet. Press “Start background traffic” on the Live
          monitor so its transactions load, or choose another account.
        </div>
      )}
      <button className="btn primary big-btn" disabled={busy || !p.available} onClick={() => onStart(p.id)}>
        Show QR on terminal
      </button>
    </div>
  )
}

// ------------------------------------------------------------------ progress strip
export function Progress({ s }) {
  const idx = { waiting_scan: 0, processing: 1, awaiting_customer: 2, success: 3, failed: 3 }[s.status] ?? -1
  const finished = s.status === 'success' || s.status === 'failed'
  return (
    <div className="progress">
      {STEPS.map((label, i) => {
        let cls = 'pending'
        let mark = String(i + 1)
        if (s.status === 'expired') {
          cls = 'skipped'
        } else if (i < idx) {
          cls = 'done'
          mark = '✓'
        } else if (i === idx && !finished) {
          cls = 'active'
        } else if (i === idx && finished) {
          cls = s.status === 'success' ? 'done' : 'fail'
          mark = s.status === 'success' ? '✓' : '✕'
        }
        if (i === 2 && s.action === 'allow' && finished) {
          cls = 'skipped'
          mark = '–'
        }
        return (
          <div key={label} className={`pstep ${cls}`}>
            <div className="n">{mark}</div>
            {label}
          </div>
        )
      })}
    </div>
  )
}

// ------------------------------------------------------------ the payment on the terminal now
export function NowPanel({ s }) {
  const [copied, setCopied] = useState(false)
  const copy = () => {
    try {
      navigator.clipboard.writeText(s.pay_url).then(() => {
        setCopied(true)
        setTimeout(() => setCopied(false), 1500)
      })
    } catch {
      /* clipboard not available on plain http: ignore */
    }
  }
  const dash = s.dashboard || {}
  const graph = dash.graph_account
  return (
    <div className="now-grid">
      <div>
        {s.status === 'waiting_scan' ? (
          <>
            <div className="qrbox">
              <img src={`/api/demo/qr/${s.id}.svg`} alt="Payment QR code" />
            </div>
            <p className="small muted link-line">{s.pay_url}</p>
            <button className="btn" onClick={copy}>
              {copied ? 'Copied' : 'Copy phone link'}
            </button>
          </>
        ) : (
          <div className={`bigmark ${s.status}`}>
            {s.status === 'success' ? '✓' : s.status === 'failed' ? '✕' : s.status === 'expired' ? '–' : '…'}
          </div>
        )}
      </div>

      <div className="stack">
        <div className="row between">
          <div>
            <b>{s.scenario_title}</b> · {s.merchant} · {inr(s.amount)}
          </div>
          {s.action && <ActionBadge action={s.action} />}
        </div>

        <Progress s={s} />

        <div className="screens">
          <div className="screen-row">
            <b>Pi terminal</b>
            <span>{piText(s)}</span>
          </div>
          {/* <div className="screen-row">
            <b>Customer phone</b>
            <span>{phoneText(s)}</span>
          </div> */}
          <div className="screen-row">
            <b>Customer phone</b>
            <span>
              {phoneText(s)}
              {voiceText(s) && (
                <div className="voice-line">
                  {voiceIcon(s)} {voiceText(s)}
                </div>
              )}
            </span>
          </div>
          <div className="screen-row">
            <b>Dashboard</b>
            <span>
              <span className={`badge ${LEVEL_BADGE[dash.level] || ''}`}>{dash.level || 'pending'}</span> {dash.label}
            </span>
          </div>
        </div>

        <div className="row">
          {s.txn_id != null && (
            <Link className="btn" to={`/dashboard?trace=${s.txn_id}${s.alert_id ? `&alert=${s.alert_id}` : ''}`}>
              See how the model decided →
            </Link>
          )}
          {graph && (
            <Link className="btn primary" to={`/investigate?account=${encodeURIComponent(graph)}`}>
              View mule network →
            </Link>
          )}
        </div>

        {s.events?.length > 0 && (
          <ul className="timeline">
            {s.events.map((e, i) => (
              <li key={i}>
                <span className="muted">{timeStr(e.t)}</span> {e.text}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}

// ------------------------------------------------------------------ history table
export function SessionTable({ rows }) {
  if (!rows.length) return <p className="muted">No demo payments yet.</p>
  return (
    <div className="scroll tall">
      <table>
        <thead>
          <tr>
            <th>Time</th>
            <th>Scenario</th>
            <th>Amount</th>
            <th>Model decided</th>
            <th>Customer</th>
            <th>Voice</th>
            <th>Pi showed</th>
            <th>Dashboard status</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((s) => (
            <tr key={s.id}>
              <td>{timeStr(s.created)}</td>
              <td>{s.scenario_title}</td>
              <td>{inr(s.amount)}</td>
              <td>{s.action ? <ActionBadge action={s.action} /> : '–'}</td>
              <td>{choiceText(s)}</td>
              <td>{s.voice ? `${voiceIcon(s)} ${s.voice.status === 'played' ? LANG_NAME[s.voice.lang] || s.voice.lang : s.voice.status}` : '–'}</td>
              <td>{s.status === 'success' ? '✓ success' : s.status === 'failed' ? '✕ failed' : s.status === 'expired' ? 'replaced' : '…'}</td>
              <td>
                <span className={`badge ${LEVEL_BADGE[s.dashboard?.level] || ''}`}>{s.dashboard?.level}</span>{' '}
                {s.dashboard?.label}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ------------------------------------------------------------------------- page
export default function DemoControl() {
  const { connected, config, demoSessions, setDemoSessions, addDemo } = useLive()
  const [presets, setPresets] = useState([])
  const [info, setInfo] = useState(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  const loadPresets = useCallback(
    () => api.demoPresets().then(setPresets).catch((e) => setErr(e.message)),
    []
  )

  useEffect(() => {
    api.demoInfo().then(setInfo).catch(() => {})
  }, [])

  // re-check the scenarios whenever the thresholds change
  useEffect(() => {
    loadPresets()
  }, [loadPresets, config?.warn_t, config?.hold_t, config?.overrides_enabled])

  const run = async (fn) => {
    setBusy(true)
    setErr('')
    try {
      await fn()
    } catch (e) {
      setErr(e.message)
    } finally {
      setBusy(false)
    }
  }

  const start = (id) => run(async () => addDemo(await api.demoStart(id)))
  const recalibrate = () => run(async () => setPresets(await api.demoCalibrate()))
  const reset = () =>
    run(async () => {
      await api.demoReset()
      setDemoSessions([])
    })

  const cur = demoSessions[0]
  const localOnly = info && /127\.0\.0\.1|localhost/.test(info.public_url)

  return (
    <div className="page">
      <Card
        title="Start a scenario"
        right={
          <button className="btn" disabled={busy} onClick={reset}>
            Reset demo
          </button>
        }
      >
        {!connected && <p className="err">The dashboard is not connected to the backend. Is it running?</p>}
        {localOnly && (
          <p className="err">
            The phone link uses {info.public_url}, which only works on this laptop. Restart the backend with PUBLIC_URL set
            to the laptop’s Wi-Fi address.
          </p>
        )}
        {info && !localOnly && (
          <p className="muted small">
            Phones and the Pi connect through <b>{info.public_url}</b>
          </p>
        )}
        <div className="demo-presets">
          {presets.map((p) => (
            <PresetCard key={p.id} p={p} busy={busy} onStart={start} onRecalibrate={recalibrate} />
          ))}
        </div>
        {presets.length === 0 && !err && <p className="muted">Loading scenarios…</p>}
        {err && <p className="err">{err}</p>}
      </Card>

      <Card title="Right now">
        {cur ? (
          <NowPanel s={cur} />
        ) : (
          <p className="muted">Nothing on the terminal yet. Pick a scenario above and the QR code appears on the Pi.</p>
        )}
      </Card>

      <Card title={`Recent demo payments (${demoSessions.length})`}>
        <SessionTable rows={demoSessions} />
      </Card>
    </div>
  )
}