import React from 'react';

function summarize(event) {
  if (event.file_count != null && event.page_count != null) {
    return `${event.file_count} files / ${event.page_count} pages`;
  }
  if (event.source) {
    const parts = String(event.source).split('/');
    return parts[parts.length - 1] || event.source;
  }
  return '';
}

export function Progress({ status, events }) {
  return (
    <div>
      <span className="status-badge" data-status={status}>{status}</span>
      <ul className="events">
        {events.length === 0 && <li><span className="type">waiting…</span></li>}
        {events.map((e, i) => (
          <li key={i}>
            <span className="type">{e.type}</span>
            {e.stage && <span className="stage">{e.stage}</span>}
            <span className="summary">{summarize(e)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
