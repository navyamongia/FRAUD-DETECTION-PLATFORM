import { createContext, useContext, useEffect, useRef, useState } from 'react'
import { api } from './api.js'

const Ctx = createContext(null)
// newest first, one entry per demo session id
const upsertDemo = (list, s) =>
  [s, ...list.filter((x) => x.id !== s.id)].sort((a, b) => b.created - a.created).slice(0, 50)
export const useLive = () => useContext(Ctx)

export function LiveProvider({ children }) {
  const [connected, setConnected] = useState(false)
  const [config, setConfig] = useState(null)
  const [replay, setReplay] = useState(null)
  const [stats, setStats] = useState(null)
  const [latencyHist, setLatencyHist] = useState([])
  const [txns, setTxns] = useState([])
  const [alerts, setAlerts] = useState([])
  const [caseTick, setCaseTick] = useState(0)
  const [demoMode, setDemoMode] = useState(true)
  const [demoSessions, setDemoSessions] = useState([])
  const buf = useRef({ txns: [], alerts: [] })
  const tick = useRef(0)

  const refreshAlerts = () =>
    api.alerts('limit=100').then(setAlerts).catch(() => {})

  useEffect(() => {
    refreshAlerts()
    api.demoSessions().then(setDemoSessions).catch(() => {})
    let ws
    let timer
    let closed = false

    const connect = () => {
      const proto = location.protocol === 'https:' ? 'wss' : 'ws'
      ws = new WebSocket(`${proto}://${location.host}/ws/stream`)
      ws.onopen = () => setConnected(true)
      ws.onclose = () => {
        setConnected(false)
        if (!closed) timer = setTimeout(connect, 2000)
      }
      ws.onmessage = (e) => {
        let msg
        try {
          msg = JSON.parse(e.data)
        } catch {
          return
        }
        const { type, data } = msg
        switch (type) {
          case 'txn_scored':
            buf.current.txns.push(data)
            break
          case 'alert':
            buf.current.alerts.push(data)
            break
          case 'stats':
            setStats(data)
            tick.current += 1
            setLatencyHist((h) =>
              [
                ...h,
                {
                  t: tick.current,
                  p50: data.latency_ms?.p50,
                  p95: data.latency_ms?.p95,
                  p99: data.latency_ms?.p99,
                },
              ].slice(-60)
            )
            break
          case 'config_changed':
            setConfig(data)
            break
          case 'replay_state':
            setReplay(data)
            break
          case 'demo_session':
            setDemoSessions((p) => upsertDemo(p, data))
            break
          case 'demo_reset':
            setDemoSessions([])
            break
          case 'case_update':
            setCaseTick((n) => n + 1)
            refreshAlerts()
            break
          default:
            break
        }
      }
    }
    connect()

    const flush = setInterval(() => {
      const b = buf.current
      if (b.txns.length) {
        const batch = b.txns
        b.txns = []
        setTxns((p) => [...batch.reverse(), ...p].slice(0, 200))
      }
      if (b.alerts.length) {
        const batch = b.alerts
        b.alerts = []
        setAlerts((p) => {
          const seen = new Set(p.map((a) => a.alert_id))
          const fresh = batch.filter((a) => a.alert_id == null || !seen.has(a.alert_id)).reverse()
          return [...fresh, ...p].slice(0, 200)
        })
      }
    }, 250)

    return () => {
      closed = true
      clearTimeout(timer)
      clearInterval(flush)
      if (ws) ws.close()
    }
  }, [])

  const value = {
    connected, config, setConfig, replay, setReplay, stats, latencyHist,
    txns, alerts, refreshAlerts, caseTick, demoMode, setDemoMode,
    demoSessions, setDemoSessions, addDemo: (s) => setDemoSessions((p) => upsertDemo(p, s)),
  }
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}