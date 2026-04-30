// Thin client over the v5.3 FastAPI surface (PLAN.md §2).

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

export async function fetchJob(jobId) {
  const r = await fetch(`/api/jobs/${jobId}`);
  if (!r.ok) throw new Error(`Job fetch failed: ${r.status}`);
  return r.json();
}

export async function fetchManifest(jobId) {
  const r = await fetch(`/api/jobs/${jobId}/manifest`);
  if (!r.ok) throw new Error(`Manifest fetch failed: ${r.status}`);
  return r.json();
}

export async function fetchClassification(jobId) {
  const r = await fetch(`/api/jobs/${jobId}/classification`);
  if (!r.ok) throw new Error(`Classification fetch failed: ${r.status}`);
  return r.json();
}

export async function fetchReview(jobId) {
  const r = await fetch(`/api/jobs/${jobId}/review`);
  if (!r.ok) throw new Error(`Review fetch failed: ${r.status}`);
  return r.json();
}

export async function fetchStoreys(jobId) {
  const r = await fetch(`/api/jobs/${jobId}/storeys`);
  if (!r.ok) throw new Error(`Storeys fetch failed: ${r.status}`);
  return r.json();
}

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
