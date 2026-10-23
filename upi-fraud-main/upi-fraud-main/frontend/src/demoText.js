// Plain-language wording for demo payments, shared by the dashboard pieces.

export const LEVEL_BADGE = { pending: '', clear: 'ok', alert: 'warn', held: 'hold', blocked: 'bad' }
export const LEVEL_TEXT = { pending: 'In progress', clear: 'Clean', alert: 'Alert', held: 'Held', blocked: 'Blocked' }

export const demoTime = (s) => (s.created ? new Date(s.created * 1000).toLocaleTimeString() : '')

// One sentence a non-technical viewer can read at a glance.
export function whatHappened(s) {
  const a = s.action
  switch (s.status) {
    case 'waiting_scan':
      return 'QR code shown on the terminal'
    case 'processing':
      return 'Customer is paying'
    case 'expired':
      return 'QR code replaced by a newer one'
    case 'awaiting_customer':
      return a === 'hold' ? 'Payment held; waiting for the customer to verify' : 'Alert shown; the customer is deciding'
    default:
  }
  if (a === 'allow') return 'Paid normally'
  if (a === 'warn') return s.choice === 'proceed' ? 'Alert shown; the customer paid anyway' : 'Alert shown; the customer cancelled'
  if (a === 'hold') {
    if (s.choice === 'verified') return 'Held; the customer verified and it was released'
    if (s.choice === 'failed_verification') return 'Held; wrong code, so the payment was blocked'
    return 'Held; the customer cancelled'
  }
  return '–'
}

// Headline for the attention banner.
export function bannerTitle(s) {
  const done = s.status === 'success' || s.status === 'failed'
  switch (s.dashboard?.level) {
    case 'alert':
      if (!done) return 'Alert raised: the customer is deciding'
      return s.status === 'success' ? 'Paid despite the warning: needs review' : 'Alert raised: the customer cancelled'
    case 'held':
      return done ? 'Hold released after verification: worth reviewing' : 'Payment held: the receiver looks like a mule account'
    case 'blocked':
      return 'Payment blocked: mule account stopped'
    default:
      return ''
  }
}
export const LANG_NAME = { en: 'English', hi: 'Hindi', ta: 'Tamil' }

// What happened to the spoken warning on the customer's phone (null until the phone reports).
export function voiceText(s) {
  const v = s.voice
  if (!v) return null
  switch (v.status) {
    case 'played':
      if (v.fallback) return `Spoken aloud in English (no ${LANG_NAME[v.chosen] || 'selected-language'} voice on the phone)`
      return `Spoken aloud in ${LANG_NAME[v.lang] || v.lang}` + (v.plays > 1 ? `, replayed ${v.plays - 1}×` : '')
    case 'muted':
      return 'Voice is muted on the phone'
    case 'blocked':
      return 'The phone blocked the voice: the customer must tap Listen again'
    case 'unavailable':
      return 'This phone cannot speak'
    default:
      return null
  }
}

export const voiceIcon = (s) => ({ played: '🔊', muted: '🔇', blocked: '⚠️', unavailable: '⚠️' }[s.voice?.status] || '')