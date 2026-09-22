// The voice-call page talks to /api/tel only. Kept apart from client.js so the
// Send and Receive pages' contract is untouched. The call is simulated here;
// nothing on this page places a real one.

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

export const receive = (payload) => postJson('/api/tel/receive', payload)

// The learned upscaler (Track 2). Guesses back the detail and the shades that
// were thrown away BEFORE the call, which is a guess, not received data - so
// it is a separate call and a separate picture on the page.
export const enhance = (payload) => postJson('/api/tel/enhance', payload)

export const waveform = async (sessionId, buckets = 2000) =>
  unwrap(await fetch(`/api/tel/waveform/${sessionId}?buckets=${buckets}`))
