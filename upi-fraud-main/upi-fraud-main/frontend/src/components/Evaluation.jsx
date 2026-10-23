import { useEffect, useState } from 'react'
import { api } from '../api.js'
import { Card } from '../ui.jsx'

const label = (k) => String(k).replace(/_/g, ' ')
const fmt = (v) =>
  typeof v === 'number' ? (Number.isInteger(v) ? v.toLocaleString('en-IN') : v.toFixed(3))
  : v == null ? '–'
  : typeof v === 'object' ? JSON.stringify(v)
  : String(v)

function Table({ rows }) {
  const cols = Object.keys(rows[0])
  return (
    <div className="scroll">
      <table>
        <thead><tr>{cols.map((c) => <th key={c}>{label(c)}</th>)}</tr></thead>
        <tbody>{rows.map((r, i) => <tr key={i}>{cols.map((c) => <td key={c}>{fmt(r[c])}</td>)}</tr>)}</tbody>
      </table>
    </div>
  )
}

function Any({ v }) {
  if (Array.isArray(v)) {
    if (v.length && typeof v[0] === 'object' && v[0] !== null) return <Table rows={v} />
    return <span>{v.map(fmt).join(', ')}</span>
  }
  if (v && typeof v === 'object') {
    const flat = Object.values(v).every((x) => x === null || typeof x !== 'object')
    return (
      <dl className={`kv${flat ? ' metrics' : ''}`}>
        {Object.entries(v).map(([k, x]) => (
          <div key={k}><dt>{label(k)}</dt><dd><Any v={x} /></dd></div>
        ))}
      </dl>
    )
  }
  return <span>{fmt(v)}</span>
}

const TITLES = {
  ablation: 'Ablation: what each layer adds (A behavioural, B + graph, C + known mules)',
  per_scenario: 'Recall per fraud scenario',
  hard_genuine: 'False positives on tricky genuine payments',
  decision_layer: 'Decision layer and value prevented',
  latency: 'Latency',
  shap_top_by_scenario: 'Top SHAP features per scenario',
}

export default function Evaluation() {
  const [data, setData] = useState(null)
  const [err, setErr] = useState('')
  useEffect(() => { api.evaluation().then(setData).catch((e) => setErr(e.message)) }, [])

  return (
    <div className="page">
      {err && <p className="err">{err}</p>}
      {!data && !err && <p className="muted">Loading…</p>}
      {data && Object.entries(data).map(([k, v]) => (
        <Card key={k} title={TITLES[k] || k}><Any v={v} /></Card>
      ))}
    </div>
  )
}