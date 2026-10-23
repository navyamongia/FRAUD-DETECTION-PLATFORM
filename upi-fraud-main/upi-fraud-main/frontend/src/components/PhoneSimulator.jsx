import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api.js'
import { LANGS, T } from '../i18n.js'
import { ActionBadge, Card, inr, num } from '../ui.jsx'

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const blank = { sender: '', receiver: '', amount: '', channel: 'p2p', call_active: false, screen_share: false }

export default function PhoneSimulator() {
  const [lang, setLang] = useState('en')
  const [scenarios, setScenarios] = useState([])
  const [form, setForm] = useState(blank)
  const [scenarioId, setScenarioId] = useState(null)
  const [phase, setPhase] = useState('form') // form | processing | warn | hold | otp | done
  const [res, setRes] = useState(null)
  const [outcome, setOutcome] = useState(null)
  const [otp, setOtp] = useState('')
  const [err, setErr] = useState('')
  const t = T[lang]

  function pick(s) {
    const p = s.payment || {}
    setScenarioId(s.id)
    setForm({
      sender: p.sender || '', receiver: p.receiver || '', amount: p.amount ?? '',
      channel: p.channel || 'p2p', call_active: !!p.call_active, screen_share: !!p.screen_share,
    })
  }

  useEffect(() => {
    api.scenarios()
      .then((list) => {
        setScenarios(list)
        if (list[0]) pick(list[0])
      })
      .catch((e) => setErr(e.message))
  }, [])

  // Editing any field turns the preset into a custom payment.
  const setField = (k, v) => {
    setForm((f) => ({ ...f, [k]: v }))
    setScenarioId(null)
  }

  async function pay() {
    setErr('')
    setPhase('processing')
    const body = scenarioId
      ? { scenario_id: scenarioId, lang }
      : {
          sender: form.sender.trim(), receiver: form.receiver.trim(), amount: Number(form.amount),
          channel: form.channel, call_active: form.call_active, screen_share: form.screen_share, lang,
        }
    try {
      const r = await api.score(body)
      setRes(r)
      if (r.action === 'allow') {
        await sleep(700)
        setOutcome({ ok: true, message: t.success })
        setPhase('done')
      } else {
        setPhase(r.action) // 'warn' | 'hold'
      }
    } catch (e) {
      setErr(e.message)
      setPhase('form')
    }
  }

  async function respond(response) {
    setErr('')
    try {
      const o = await api.respond(res.txn_id, response)
      setOutcome({ ok: o.status === 'settled' || o.status === 'released', message: o.message })
      setPhase('done')
    } catch (e) {
      setErr(e.message)
    }
  }

  const reset = () => { setPhase('form'); setOutcome(null); setErr('') }
  const reasonText = (r) => r.text?.[lang] || r.text?.en || r.id
  const reasons = res?.reasons || []

  return (
    <div className="page sim-wrap">
      <div className="phone">
        <div className="phone-top">
          {LANGS.map((l) => (
            <button key={l.code} className={lang === l.code ? 'on' : ''} onClick={() => setLang(l.code)}>
              {l.label}
            </button>
          ))}
        </div>

        <div className="phone-body">
          {phase === 'form' && (
            <div className="form">
              <label>{t.from}<input value={form.sender} onChange={(e) => setField('sender', e.target.value)} /></label>
              <label>{t.payTo}<input value={form.receiver} onChange={(e) => setField('receiver', e.target.value)} /></label>
              <label>{t.amount}<input type="number" value={form.amount} onChange={(e) => setField('amount', e.target.value)} /></label>
              <label>{t.channel}
                <select value={form.channel} onChange={(e) => setField('channel', e.target.value)}>
                  <option value="p2p">{t.p2p}</option>
                  <option value="qr">{t.qr}</option>
                  <option value="collect">{t.collect}</option>
                </select>
              </label>
              <label className="check"><input type="checkbox" checked={form.call_active} onChange={(e) => setField('call_active', e.target.checked)} /> {t.onCall}</label>
              <label className="check"><input type="checkbox" checked={form.screen_share} onChange={(e) => setField('screen_share', e.target.checked)} /> {t.screenShare}</label>
              {err && <p className="err">{err}</p>}
              <button className="big primary" onClick={pay} disabled={!form.sender || !form.receiver || !form.amount}>
                {t.pay} {form.amount ? inr(form.amount) : ''}
              </button>
            </div>
          )}

          {phase === 'processing' && (
            <div className="done"><div className="spinner" /><h2>{t.processing}</h2></div>
          )}

          {(phase === 'warn' || phase === 'hold') && res && (
            <div className={`alertcard ${phase}`}>
              <div className="icon">{phase === 'warn' ? '⚠️' : '⛔'}</div>
              <h2>{phase === 'warn' ? t.warnTitle : t.holdTitle}</h2>
              <p className="amt">{inr(res.amount)} → {res.receiver}</p>
              {reasons.length > 0 && (
                <>
                  <h4>{t.why}</h4>
                  <ul>{reasons.map((r) => <li key={r.id}>{reasonText(r)}</li>)}</ul>
                </>
              )}
              {phase === 'warn' ? (
                <>
                  <button className="big primary" onClick={() => respond('cancel')}>{t.cancel}</button>
                  <button className="big ghost" onClick={() => respond('proceed')}>{t.proceed}</button>
                </>
              ) : (
                <>
                  <button className="big primary" onClick={() => { setOtp(''); setPhase('otp') }}>{t.verify}</button>
                  <button className="big ghost" onClick={() => respond('cancel')}>{t.cancel}</button>
                </>
              )}
              {err && <p className="err">{err}</p>}
            </div>
          )}

          {phase === 'otp' && (
            <div className="form">
              <h2>{t.otpTitle}</h2>
              <input className="otp" maxLength={4} inputMode="numeric" value={otp} onChange={(e) => setOtp(e.target.value)} />
              <p className="muted">{t.otpHint}</p>
              <button className="big primary" disabled={otp.length < 4}
                onClick={() => respond(otp === '1234' ? 'verified' : 'failed_verification')}>
                {t.confirm}
              </button>
              <button className="big ghost" onClick={() => setPhase('hold')}>{t.back}</button>
              {err && <p className="err">{err}</p>}
            </div>
          )}

          {phase === 'done' && outcome && (
            <div className="done">
              <div className={`tick ${outcome.ok ? 'ok' : 'bad'}`}>{outcome.ok ? '✓' : '✕'}</div>
              <h2>{outcome.message}</h2>
              <button className="big primary" onClick={reset}>{t.newPayment}</button>
            </div>
          )}
        </div>
      </div>

      <div className="side">
        <Card title="Demo scenarios">
          <div className="scn-list">
            {scenarios.map((s) => (
              <button key={s.id} className={`scn ${scenarioId === s.id ? 'sel' : ''}`}
                onClick={() => { pick(s); reset() }}>
                <span>{s.title}</span>
                <span>
                  <small>expected</small> <ActionBadge action={s.expected} />{' '}
                  <small>model</small> <ActionBadge action={s.observed} />
                </span>
              </button>
            ))}
          </div>
          <p className="muted">Pick a scenario, then press Pay on the phone. Editing any field makes it a custom payment.</p>
        </Card>

        {res && (
          <Card title="What the backend decided">
            <div className="kv-row"><span>Action</span><ActionBadge action={res.action} /></div>
            <div className="kv-row"><span>Risk (0–100)</span><b>{num(res.risk, 1)}</b></div>
            <div className="kv-row"><span>Share of genuine payments scoring this high</span><b>{num(res.flag_pct, 3)}%</b></div>
            <div className="kv-row"><span>Scored in</span><b>{num(res.latency_ms?.total, 2)} ms</b> <small>(budget 200 ms)</small></div>
            {res.override && <div className="kv-row"><span>Override rule</span><span className="badge hold">{res.override}</span></div>}
            <div className="kv-row"><span>Next step</span><b>{res.next_step}</b></div>
            {reasons.length > 0 && <ul className="small-list">{reasons.map((r) => <li key={r.id}><b>{r.id}</b>: {r.text?.en}</li>)}</ul>}
            <Link className="btn" to={`/dashboard?trace=${res.txn_id}${res.alert_id ? `&alert=${res.alert_id}` : ''}`}>
              Open Decision Trace →
            </Link>
          </Card>
        )}
        {err && phase !== 'form' && <p className="err">{err}</p>}
      </div>
    </div>
  )
}