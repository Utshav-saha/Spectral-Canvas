// All requests go to /api, which vite.config.js proxies to FastAPI.
// Nothing here hardcodes a host, so dev and build behave the same.

async function unwrap(response) {
  if (!response.ok) {
    let detail = `Request failed (${response.status})`
    try { detail = (await response.json()).detail || detail } catch { /* non-JSON error */ }
    throw new Error(detail)
  }
  return response.json()
}

export async function encode(params, file) {
  const body = new FormData()
  body.append('payload', JSON.stringify(params))
  if (file) body.append('file', file)
  return unwrap(await fetch('/api/encode', { method: 'POST', body }))
}

export async function inspect(file) {
  const body = new FormData()
  body.append('file', file)
  return unwrap(await fetch('/api/inspect', { method: 'POST', body }))
}

export async function decode(payload) {
  return unwrap(await fetch('/api/decode', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }))
}

export async function waveform(sessionId, buckets = 1000) {
  return unwrap(await fetch(`/api/waveform/${sessionId}?buckets=${buckets}`))
}

export const audioUrl     = (id) => `/api/audio/${id}`
export const previewUrl   = (id) => `/api/preview/${id}`
export const recoveredUrl = (id) => `/api/recovered/${id}`

export async function downloadUrl(url, filename) {
  const response = await fetch(url)
  if (!response.ok) throw new Error('Download failed.')
  const blob = await response.blob()
  const href = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = href
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  URL.revokeObjectURL(href)
}
