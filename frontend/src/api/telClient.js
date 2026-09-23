// The voice-call page talks to /api/tel only. Kept apart from client.js so the
// Send and Receive pages' contract is untouched. A call here is either
// simulated offline or placed for real over SIP.

async function unwrap(response) {
  if (!response.ok) {
    let detail = `Request failed (${response.status})`
    try { detail = (await response.json()).detail || detail } catch { /* non-JSON error */ }
    throw new Error(detail)
  }
  return response.json()
}

const postJson = async (path, body) => unwrap(await fetch(path, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
}))

const postFile = async (path, file) => {
  const body = new FormData()
  body.append('file', file)
  return unwrap(await fetch(path, { method: 'POST', body }))
}

export const info = async () => unwrap(await fetch('/api/tel/info'))

export const stage = (file) => postFile('/api/tel/stage', file)

export const plan = async (generation, size, levels, colour) =>
  unwrap(await fetch(
    `/api/tel/plan?generation=${generation}&size=${size}&levels=${levels}&colour=${colour}`))

export const send = (payload) => postJson('/api/tel/send', payload)

export const call = (payload) => postJson('/api/tel/call', payload)

// a recording comes in through upload; inspect then matches it to a send
export const upload = (file) => postFile('/api/tel/upload', file)

export const inspect = (payload) => postJson('/api/tel/inspect', payload)

export const receive = (payload) => postJson('/api/tel/receive', payload)

// A real SIP call, placed by the backend with pjsua. The browser only starts
// it and polls; credentials live in the server's environment.
export const dialStatus = async () => unwrap(await fetch('/api/tel/dial/status'))

export const dial = (payload) => postJson('/api/tel/dial', payload)

// The other direction: we wait, the phone calls us. No push notification has
// to get through for a handset to place a call.
export const answerCall = (payload) => postJson('/api/tel/answer', payload)

export const dialProgress = async (callId) =>
  unwrap(await fetch(`/api/tel/dial/${callId}`))

// The learned upscaler (Track 2). Guesses back the detail and the shades that
// were thrown away BEFORE the call, which is a guess, not received data - so
// it is a separate call and a separate picture on the page.
export const enhance = (payload) => postJson('/api/tel/enhance', payload)

export const waveform = async (sessionId, buckets = 2000) =>
  unwrap(await fetch(`/api/tel/waveform/${sessionId}?buckets=${buckets}`))
