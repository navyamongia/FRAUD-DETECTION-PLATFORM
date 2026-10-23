async function req(path, { method = 'GET', body } = {}) {
  const res = await fetch(path, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`
    try {
      const j = await res.json()
      if (j.detail) msg = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail)
    } catch {
      /* ignore */
    }
    throw new Error(msg)
  }
  return res.json()
}

export const api = {
  scenarios: () => req('/api/scenarios'),
  score: (body) => req('/api/score', { method: 'POST', body }),
  respond: (txnId, response) =>
    req(`/api/payments/${txnId}/respond`, { method: 'POST', body: { response } }),
  alerts: (query = 'limit=100') => req(`/api/alerts?${query}`),
  trace: (txnId) => req(`/api/trace/${txnId}`),
  stats: () => req('/api/stats'),
  replay: () => req('/api/replay'),
  setReplay: (body) => req('/api/replay', { method: 'POST', body }),
  simulate: (warn, hold) => req(`/api/simulate?warn_pct=${warn}&hold_pct=${hold}`),
  curve: () => req('/api/curve'),
  config: () => req('/api/config'),
  setConfig: (body) => req('/api/config', { method: 'POST', body }),
  evaluation: () => req('/api/evaluation'),
  graph: (id, hops = 2) => req(`/api/graph/${encodeURIComponent(id)}?hops=${hops}`),
  cases: (status) => req(`/api/cases${status ? `?status=${status}` : ''}`),
  getCase: (id) => req(`/api/cases/${id}`),
  createCase: (body) => req('/api/cases', { method: 'POST', body }),
  patchCase: (id, body) => req(`/api/cases/${id}`, { method: 'PATCH', body }),
    // ---- demo (payment terminal + phone) ----
  demoInfo: () => req('/api/demo/info'),
  demoPresets: () => req('/api/demo/presets'),
  demoCalibrate: () => req('/api/demo/calibrate', { method: 'POST' }),
  demoStart: (preset) => req('/api/demo/sessions', { method: 'POST', body: { preset } }),
  demoSessions: () => req('/api/demo/sessions?limit=50'),
  demoReset: () => req('/api/demo/reset', { method: 'POST' }),
}