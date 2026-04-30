import React from 'react';

import { basename } from '../util.js';

export function Manifest({ manifest }) {
  return (
    <div className="manifest">
      <h3>Manifest</h3>
      <p className="summary">
        {manifest.file_count} file(s) · {manifest.page_count} page(s) ingested
      </p>
      <details>
        <summary>Per-file detail</summary>
        <ul className="files">
          {manifest.files.map((f, i) => (
            <li key={i}>
              <span className="file-name">{basename(f.pdf)}</span>
              <span className="file-meta">{f.n_pages}p</span>
              <code>{f.page_hashes[0]?.slice(0, 12)}…</code>
            </li>
          ))}
        </ul>
      </details>
    </div>
  );
}
