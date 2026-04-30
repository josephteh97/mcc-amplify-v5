import React, { useState } from 'react';

function fmtSize(bytes) {
  if (bytes < 1024)        return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export function Upload({ files, setFiles, onUpload, disabled }) {
  const [dragOver, setDragOver] = useState(false);

  const addFiles = (incoming) => {
    const pdfs = [...incoming].filter(
      (f) => f.name && f.name.toLowerCase().endsWith('.pdf'),
    );
    setFiles([...files, ...pdfs]);
  };

  return (
    <div>
      <div
        className={`dropzone${dragOver ? ' over' : ''}`}
        onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragOver(false);
          addFiles(e.dataTransfer.files);
        }}
      >
        <p>Drop PDFs here, or pick a directory / files below.</p>
        <input
          type="file"
          multiple
          accept=".pdf"
          onChange={(e) => addFiles(e.target.files)}
        />
      </div>

      {files.length > 0 && (
        <ul className="file-list">
          {files.map((f, i) => (
            <li key={`${f.name}-${i}`}>
              <span className="name">{f.name}</span>
              <span className="size">{fmtSize(f.size)}</span>
              <button
                className="remove"
                aria-label={`Remove ${f.name}`}
                onClick={() => setFiles(files.filter((_, j) => j !== i))}
              >
                ×
              </button>
            </li>
          ))}
        </ul>
      )}

      <div className="row">
        <button
          className="primary"
          disabled={disabled || files.length === 0}
          onClick={() => onUpload(files)}
        >
          Upload {files.length} file{files.length !== 1 ? 's' : ''}
        </button>
        {files.length > 0 && !disabled && (
          <button className="secondary" onClick={() => setFiles([])}>
            Clear
          </button>
        )}
      </div>
    </div>
  );
}
