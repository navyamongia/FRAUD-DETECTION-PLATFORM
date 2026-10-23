import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import ForceGraph2D from 'react-force-graph-2d'
import { api } from '../api.js'
import { useLive } from '../useLive.jsx'
import { Card, inr, num } from '../ui.jsx'
import { GraphLegend, GraphSummary } from './GraphInfo.jsx'

const STATUSES = ['open', 'investigating', 'confirmed_fraud', 'false_positive', 'closed']
const STATUS_LABEL = { open: 'Open', investigating: 'Investigating', confirmed_fraud: 'Confirmed fraud', false_positive: 'False alarm', closed: 'Closed' }
const idOf = (x) => (x && typeof x === 'object' ? x.id : x)
const riskColor = (r) => (r == null ? '#94a3b8' : `hsl(${120 - 120 * Math.min(1, Math.max(0, r))}, 75%, 45%)`)

function GraphPane({ account, setAccount }) {
  const [input, setInput] = useState(account || '')
  const [hops, setHops] = useState(2)
  const [g, setG] = useState(null)
  const [err, setErr] = useState('')
  const [loading, setLoading] = useState(false)
  const [muleId, setMuleId] = useState('')
  const [w, setW] = useState(600)
  const box = useRef(null)
  const fg = useRef(null)
  const fitted = useRef('')

  useEffect(() => {
    if (!box.current) return
    const ro = new ResizeObserver(([e]) => setW(e.contentRect.width))
    ro.observe(box.current)
    return () => ro.disconnect()
  }, [])

  // shortcut for an empty page: the demo's mule account
  useEffect(() => {
    api.demoPresets().then((ps) => setMuleId(ps.find((p) => p.id === 'mule')?.receiver || '')).catch(() => {})
  }, [])

  useEffect(() => {
    setInput(account || '')
    if (!account) { setG(null); return }
    setErr('')
    setLoading(true)
    api.graph(account, hops)
      .then((x) => { setG(x); fitted.current = '' })
      .catch((e) => { setG(null); setErr(e.message) })
      .finally(() => setLoading(false))
  }, [account, hops])

  // the graph library mutates its input, so give it copies
  const data = useMemo(
    () => (g ? { nodes: g.nodes.map((n) => ({ ...n })), links: g.edges.map((e) => ({ ...e })) } : { nodes: [], links: [] }),
    [g]
  )

  const radius = (n) => 4 + Math.min(8, Math.sqrt((n.in_deg || 0) + (n.out_deg || 0)))
  const paint = (n, ctx, scale) => {
    const r = radius(n)
    const isCenter = g && n.id === g.center
    ctx.beginPath(); ctx.arc(n.x, n.y, r, 0, 2 * Math.PI)
    ctx.fillStyle = riskColor(n.risk); ctx.fill()
    if (n.is_known_mule) { ctx.lineWidth = 3 / scale; ctx.strokeStyle = '#111827'; ctx.stroke() }
    if (isCenter) {
      ctx.beginPath(); ctx.arc(n.x, n.y, r + 4, 0, 2 * Math.PI)
      ctx.lineWidth = 3 / scale; ctx.strokeStyle = '#1B7B96'; ctx.stroke()
    }
    // label only what matters, or everything once the viewer zooms in
    if (isCenter || n.is_known_mule || n.risk >= 0.6 || scale > 1.6) {
      ctx.font = `${isCenter ? 'bold ' : ''}${(isCenter ? 12 : 10) / scale}px 'Source Sans 3', Arial, sans-serif`
      ctx.textAlign = 'center'
      const tw = ctx.measureText(n.id).width
      const y = n.y + r + (isCenter ? 14 : 10) / scale
      ctx.fillStyle = 'rgba(255,255,255,.85)'
      ctx.fillRect(n.x - tw / 2 - 2 / scale, y - 9 / scale, tw + 4 / scale, 12 / scale)
      ctx.fillStyle = '#2B3339'
      ctx.fillText(n.id, n.x, y)
    }
  }

  const noHistory = /no transactions/i.test(err)

  return (
    <Card
      title="Account network: who paid whom"
      right={
        <div className="row">
          <input placeholder="account id, e.g. Z001977" value={input} onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && input.trim() && setAccount(input.trim())} />
          <select value={hops} onChange={(e) => setHops(Number(e.target.value))} title="How far from the account to look">
            <option value={1}>Direct connections</option>
            <option value={2}>Two steps away</option>
            <option value={3}>Three steps away</option>
          </select>
          <button className="btn primary" onClick={() => input.trim() && setAccount(input.trim())}>Load</button>
        </div>
      }
    >
      <GraphSummary g={g} />
      <div ref={box} className="graph-box">
        {loading && <p className="muted graph-note">Loading network…</p>}
        {err && (
          <div className="graph-note">
            <p className="err">{noHistory ? 'We have no payment history for this account yet.' : err}</p>
            {noHistory && (
              <p className="muted">
                Press “Start background traffic” on the <Link to="/dashboard">Live monitor</Link> and try again in a minute.
              </p>
            )}
          </div>
        )}
        {!account && !err && (
          <div className="graph-note">
            <p className="muted">Enter an account id, or open a payment from an alert.</p>
            {muleId && (
              <button className="btn primary" onClick={() => setAccount(muleId)}>
                Show the demo mule account {muleId}
              </button>
            )}
          </div>
        )}
        <ForceGraph2D
          ref={fg}
          graphData={data} width={w} height={520}
          nodeCanvasObjectMode={() => 'replace'}
          nodeCanvasObject={paint}
          nodePointerAreaPaint={(n, color, ctx) => { ctx.fillStyle = color; ctx.beginPath(); ctx.arc(n.x, n.y, radius(n) + 2, 0, 2 * Math.PI); ctx.fill() }}
          nodeLabel={(n) => `${n.id}${n.kind === 'merchant' ? ' (shop)' : ''}<br/>account age ${num(n.age_days, 1)} days<br/>${n.in_deg} paid it · it paid ${n.out_deg}<br/>risk ${num(n.risk, 2)}${n.is_known_mule ? '<br/><b>KNOWN MULE</b>' : ''}`}
          linkLabel={(l) => `${inr(l.amount)} × ${l.count ?? 1}`}
          linkColor={(l) => (g && idOf(l.target) === g.center ? 'rgba(220,38,38,.6)' : '#C3CCD5')}
          linkWidth={(l) => 0.6 + Math.min(3, Math.max(0, Math.log10(Math.max(10, l.amount || 10)) - 1))}
          linkDirectionalArrowLength={5} linkDirectionalArrowRelPos={1}
          onEngineStop={() => {
            if (g && fitted.current !== g.center && fg.current) { fitted.current = g.center; fg.current.zoomToFit(400, 50) }
          }}
          onNodeClick={(n) => setAccount(n.id)}
        />
      </div>
      <GraphLegend />
      <p className="muted small">Click any account to re-centre the picture on it.</p>
    </Card>
  )
}

function CasesPane({ account, setAccount }) {
  const { alerts, caseTick, refreshAlerts } = useLive()
  const [filter, setFilter] = useState('')
  const [cases, setCases] = useState([])
  const [sel, setSel] = useState(null)
  const [title, setTitle] = useState('')
  const [picked, setPicked] = useState([])
  const [note, setNote] = useState('')
  const [err, setErr] = useState('')

  const autoTitle = useRef('')

  // opening an account from an alert or the graph pre-fills a case for it: one click to create
  useEffect(() => {
    if (!account) return
    setPicked(alerts.filter((a) => !a.case_id && a.receiver === account).map((a) => a.alert_id))
    const next = `Suspected mule account ${account}`
    setTitle((t) => (t === '' || t === autoTitle.current ? next : t))
    autoTitle.current = next
  }, [account])

  const load = () => api.cases(filter || undefined).then(setCases).catch((e) => setErr(e.message))
  useEffect(() => { load() }, [filter, caseTick])
  useEffect(() => { if (sel) api.getCase(sel.case_id).then(setSel).catch(() => {}) }, [caseTick])

  const openAlerts = alerts
    .filter((a) => !a.case_id)
    .sort((x, y) => (y.receiver === account) - (x.receiver === account))
    .slice(0, 8)
  const toggle = (id) => setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]))

  async function create() {
    setErr('')
    try {
      const c = await api.createCase({ title: title || 'Untitled case', alert_ids: picked, assignee: 'analyst1' })
      setTitle(''); setPicked([]); setSel(c); load(); refreshAlerts()
    } catch (e) { setErr(e.message) }
  }
  async function patch(body) {
    setErr('')
    try {
      const c = await api.patchCase(sel.case_id, body)
      setSel(c); setNote(''); load()
    } catch (e) { setErr(e.message) }
  }

  return (
    <Card title="Cases: follow-up work" right={
      <select value={filter} onChange={(e) => setFilter(e.target.value)}>
        <option value="">All cases</option>
        {STATUSES.map((s) => <option key={s} value={s}>{STATUS_LABEL[s]}</option>)}
      </select>
    }>
      {err && <p className="err">{err}</p>}

      <div className="newcase">
        <input placeholder="Case title" value={title} onChange={(e) => setTitle(e.target.value)} />
        <div className="small-list">
          {openAlerts.map((a) => (
            <label key={a.alert_id} className="check small">
              <input type="checkbox" checked={picked.includes(a.alert_id)} onChange={() => toggle(a.alert_id)} />
              #{a.alert_id} · {a.action} · {a.receiver} · {inr(a.amount)}
            </label>
          ))}
          {openAlerts.length === 0 && <span className="muted">No open alerts to attach.</span>}
        </div>
        <button className="btn primary" onClick={create}>Create case</button>
      </div>

      <div className="scroll short">
        <table className="clickable compact">
          <thead><tr><th>#</th><th>Title</th><th>Status</th><th>Alerts</th></tr></thead>
          <tbody>
            {cases.map((c) => (
              <tr key={c.case_id} className={sel?.case_id === c.case_id ? 'selrow' : ''} onClick={() => setSel(c)}>
                <td>{c.case_id}</td><td>{c.title}</td><td>{STATUS_LABEL[c.status] || c.status}</td><td>{c.alert_ids?.length ?? 0}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {cases.length === 0 && <p className="muted">No cases yet.</p>}
      </div>

      {sel && (
        <div className="casebox">
          <h4>Case #{sel.case_id}: {sel.title}</h4>
          <div className="row">
            <select value={sel.status} onChange={(e) => patch({ status: e.target.value })}>
              {STATUSES.map((s) => <option key={s} value={s}>{STATUS_LABEL[s]}</option>)}
            </select>
            <span className="muted">assignee: {sel.assignee || '–'}</span>
          </div>
          <div className="row wrap">
            {(sel.alert_ids || []).map((id) => {
              const a = alerts.find((x) => x.alert_id === id)
              return (
                <button key={id} className="chip" onClick={() => a && setAccount(a.receiver)}>
                  #{id}{a ? ` → ${a.receiver}` : ''}
                </button>
              )
            })}
          </div>
          <div className="row">
            <input placeholder="Add a note…" value={note} onChange={(e) => setNote(e.target.value)} />
            <button className="btn" disabled={!note.trim()} onClick={() => patch({ note })}>Add note</button>
          </div>
          <ul className="small-list">
            {(sel.events || []).slice().reverse().map((e, i) => (
              <li key={i}><span className="muted">{String(e.ts).replace('T', ' ').slice(0, 16)}</span> · {e.by}: {e.note}{e.status ? ` [${e.status}]` : ''}</li>
            ))}
          </ul>
        </div>
      )}
    </Card>
  )
}

export default function Investigation() {
  const [sp, setSp] = useSearchParams()
  const account = sp.get('account') || ''
  const setAccount = (id) => setSp({ account: id })
  return (
    <div className="page grid-inv">
      <GraphPane account={account} setAccount={setAccount} />
      <CasesPane account={account} setAccount={setAccount} />
    </div>
  )
}