import { useCallback, useRef, useState } from 'react';
import { uploadFiles, fetchManifest, openProgressSocket } from '../api.js';

// Job lifecycle states match the backend's JobRecord.status field, plus a
// frontend-only 'idle' (no job yet) and 'uploading' (POST in flight).
const IDLE = 'idle';

export function useJob() {
  const [files,    setFiles]    = useState([]);
  const [jobId,    setJobId]    = useState(null);
  const [status,   setStatus]   = useState(IDLE);
  const [events,   setEvents]   = useState([]);
  const [manifest, setManifest] = useState(null);
  const [error,    setError]    = useState(null);
  const wsRef = useRef(null);

  const closeSocket = () => {
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }
  };

  const reset = useCallback(() => {
    closeSocket();
    setFiles([]);
    setJobId(null);
    setStatus(IDLE);
    setEvents([]);
    setManifest(null);
    setError(null);
  }, []);

  const upload = useCallback(async (filesToUpload) => {
    setStatus('uploading');
    setEvents([]);
    setManifest(null);
    setError(null);

    try {
      const { job_id } = await uploadFiles(filesToUpload);
      setJobId(job_id);
      setStatus('running');

      const ws = openProgressSocket(
        job_id,
        async (event) => {
          setEvents((prev) => [...prev, event]);
          if (event.type === 'job_completed') {
            setStatus('completed');
            try {
              const m = await fetchManifest(job_id);
              setManifest(m);
            } catch (e) {
              setError(e.message);
            }
            closeSocket();
          } else if (event.type === 'error') {
            setStatus('failed');
            setError(event.message || 'pipeline error');
            closeSocket();
          }
        },
        (e) => {
          setError('WebSocket error — see console');
          // Don't auto-fail the job; the HTTP /jobs/{id} endpoint can still
          // be polled if the WS bridge dropped, and the pipeline itself may
          // still complete on the server side.
        },
      );
      wsRef.current = ws;
    } catch (e) {
      setStatus('failed');
      setError(e.message);
    }
  }, []);

  return { files, setFiles, jobId, status, events, manifest, error, upload, reset };
}
