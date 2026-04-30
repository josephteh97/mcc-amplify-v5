import React from 'react';
import { Upload }         from './components/Upload.jsx';
import { Progress }       from './components/Progress.jsx';
import { Classification } from './components/Classification.jsx';
import { Manifest }       from './components/Manifest.jsx';
import { useJob }         from './hooks/useJob.js';

export default function App() {
  const job = useJob();
  const busy = job.status === 'uploading' || job.status === 'running';

  return (
    <main>
      <h1>MCC Amplify v5.3</h1>
      <p className="lead">
        Drop the consultant PDF set — overall plans, enlargements, elevations, sections.
        The classifier discards anything outside those four classes; downstream stages
        emit a Revit 2023 RVT and GLTF per storey.
      </p>

      <Upload
        files={job.files}
        setFiles={job.setFiles}
        onUpload={job.upload}
        disabled={busy}
      />

      {job.jobId && (
        <>
          <h2>Job {job.jobId.slice(0, 8)}…</h2>
          <Progress status={job.status} events={job.events} />
          {job.error          && <div className="error">{job.error}</div>}
          {job.classification && <Classification classification={job.classification} />}
          {job.manifest       && <Manifest manifest={job.manifest} />}
          <div className="row">
            <button className="secondary" onClick={job.reset}>New job</button>
          </div>
        </>
      )}
    </main>
  );
}
