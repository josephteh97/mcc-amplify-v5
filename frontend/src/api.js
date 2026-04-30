// Thin client over the v5.3 FastAPI surface (PLAN.md §2).

async function getJson(url, label) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${label} fetch failed: ${r.status}`);
  return r.json();
}

export async function uploadFiles(files) {
  const form = new FormData();
  files.forEach((f) => form.append('files', f, f.name));
  const r = await fetch('/api/upload', { method: 'POST', body: form });
  if (!r.ok) {
    const detail = await r.text();
    throw new Error(`Upload failed (${r.status}): ${detail}`);
  }
  return r.json();
}

export const fetchJob            = (jobId) => getJson(`/api/jobs/${jobId}`,                   'Job');
export const fetchManifest       = (jobId) => getJson(`/api/jobs/${jobId}/manifest`,          'Manifest');
export const fetchClassification = (jobId) => getJson(`/api/jobs/${jobId}/classification`,    'Classification');
export const fetchReview         = (jobId) => getJson(`/api/jobs/${jobId}/review`,            'Review');
export const fetchStoreys        = (jobId) => getJson(`/api/jobs/${jobId}/storeys`,           'Storeys');

export function gltfUrl(jobId, storeyId) {
  return `/api/jobs/${jobId}/gltf/${encodeURIComponent(storeyId)}`;
}

export function openProgressSocket(jobId, onEvent, onError) {
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws';
  const url = `${proto}://${window.location.host}/api/ws/${jobId}`;
  const ws = new WebSocket(url);
  ws.onmessage = (msg) => {
    try { onEvent(JSON.parse(msg.data)); }
    catch (e) { onError?.(e); }
  };
  ws.onerror = (e) => onError?.(e);
  return ws;
}
