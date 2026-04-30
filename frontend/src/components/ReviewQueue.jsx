import React, { useState } from 'react';

// Surfaces the strict-mode review queue (PLAN.md §11) — every conflict,
// unresolved column, missing-coverage flag and DISCARD record the human
// must adjudicate before the RVTs can ship without caveat.

function basename(p) {
  return (p || '').split('/').pop() || '';
}

function Section({ title, count, children, defaultOpen = false }) {
  const [open, setOpen] = useState(defaultOpen);
  const tone = count === 0 ? 'review-section-empty' : 'review-section-active';
  return (
    <section className={`review-section ${tone}`}>
      <button
        type="button"
        className="review-section-header"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
      >
        <span className="review-section-toggle">{open ? '▾' : '▸'}</span>
        <span className="review-section-title">{title}</span>
        <span className={`review-section-count count-${count === 0 ? 'zero' : 'nonzero'}`}>
          {count}
        </span>
      </button>
      {open && <div className="review-section-body">{children}</div>}
    </section>
  );
}

function EmptyState({ message = 'Nothing to review.' }) {
  return <div className="review-empty">{message}</div>;
}

// ── Per-section renderers ───────────────────────────────────────────────────

function DiscardedFiles({ items }) {
  if (!items.length) return <EmptyState message="No DISCARD pages." />;
  return (
    <ul className="review-list">
      {items.map((it, i) => (
        <li key={i}>
          <span className="review-name">{basename(it.pdf)}</span>
          <span className="review-meta">page {it.page_index ?? 0}</span>
          <span className="review-meta">tier: {it.tier}</span>
          <span className="review-detail">{it.reason}</span>
        </li>
      ))}
    </ul>
  );
}

function UnresolvedClassifications({ items }) {
  if (!items.length) return <EmptyState message="All pages resolved by tiers 1–4." />;
  return (
    <ul className="review-list">
      {items.map((it, i) => (
        <li key={i}>
          <span className="review-name">{basename(it.pdf)}</span>
          <span className="review-meta">page {it.page_index ?? 0}</span>
          <span className="review-detail">{it.reason}</span>
        </li>
      ))}
    </ul>
  );
}

function ReconcileConflicts({ storeys }) {
  const flat = storeys.flatMap((s) =>
    s.conflicts.map((c) => ({ ...c, storey_id: s.storey_id })),
  );
  if (!flat.length) return <EmptyState message="No label conflicts across storeys." />;
  return (
    <ul className="review-list">
      {flat.map((c, i) => (
        <li key={i}>
          <span className="review-name">{c.storey_id} · col #{c.canonical_idx}</span>
          <span className="review-detail">
            {c.label_candidates.map((lc, j) => (
              <span key={j} className="review-chip">
                {lc.label}/{lc.shape}
                {lc.dim_along_x_mm != null && lc.dim_along_y_mm != null
                  ? ` ${lc.dim_along_x_mm}×${lc.dim_along_y_mm}`
                  : lc.diameter_mm != null ? ` Ø${lc.diameter_mm}` : ''}
                {' '}
                <span className="review-chip-meta">
                  {lc.source_pdf ? basename(lc.source_pdf) : ''}{lc.distance_mm != null ? ` · Δ${lc.distance_mm} mm` : ''}
                </span>
              </span>
            ))}
          </span>
        </li>
      ))}
    </ul>
  );
}

function ReconcileMissing({ storeys }) {
  // Aggregate per storey so the reviewer sees magnitude, not 12,000 rows.
  const rows = storeys
    .map((s) => ({ storey_id: s.storey_id, count: s.missing.length, summary: s.summary }))
    .filter((r) => r.count > 0);
  if (!rows.length) return <EmptyState message="Every canonical column carries a label." />;
  return (
    <ul className="review-list">
      {rows.map((r, i) => (
        <li key={i}>
          <span className="review-name">{r.storey_id}</span>
          <span className="review-meta">{r.count} unlabelled</span>
          <span className="review-detail">
            {r.summary?.canonical_total != null
              ? `${r.summary.labelled}/${r.summary.canonical_total} labelled`
              : ''}
            {r.summary?.label_inferred ? ` · ${r.summary.label_inferred} inferred from neighbours` : ''}
          </span>
        </li>
      ))}
    </ul>
  );
}

function ResolveRejects({ storeys }) {
  const flat = storeys.flatMap((s) =>
    (s.rejected || []).map((r) => ({ ...r, storey_id: s.storey_id })),
  );
  if (!flat.length) return <EmptyState message="No placements rejected by Stage 5A." />;
  return (
    <ul className="review-list">
      {flat.map((r, i) => (
        <li key={i}>
          <span className="review-name">{r.storey_id} · col #{r.canonical_idx}</span>
          <span className="review-meta">{r.reason}</span>
          <span className="review-detail">
            {r.label ? `label=${r.label} ` : ''}shape={r.shape}
            {r.dim_along_x_mm != null ? ` ${r.dim_along_x_mm}×${r.dim_along_y_mm}` : ''}
            {r.diameter_mm != null ? ` Ø${r.diameter_mm}` : ''}
            {' · '}{r.audit}
          </span>
        </li>
      ))}
    </ul>
  );
}

function EmitGates({ storeys }) {
  const failed = storeys.filter((s) => s.hard_failures && s.hard_failures.length > 0);
  const warned = storeys.filter((s) => s.warnings && s.warnings.length > 0);
  if (!failed.length && !warned.length) {
    return <EmptyState message="All storeys passed every gate." />;
  }
  return (
    <ul className="review-list">
      {failed.map((s, i) => (
        <li key={`f${i}`} className="review-row-hard">
          <span className="review-name">{s.storey_id}</span>
          <span className="review-meta">SKIPPED</span>
          <span className="review-detail">
            {s.hard_failures.map((g, j) => (
              <span key={j} className="review-chip review-chip-fail">{g.name}: {g.detail}</span>
            ))}
          </span>
        </li>
      ))}
      {warned.map((s, i) => (
        <li key={`w${i}`}>
          <span className="review-name">{s.storey_id}</span>
          <span className="review-meta">warn</span>
          <span className="review-detail">
            {s.warnings.map((g, j) => (
              <span key={j} className="review-chip review-chip-warn">{g.name}: {g.detail}</span>
            ))}
          </span>
        </li>
      ))}
    </ul>
  );
}

// ── Top-level component ─────────────────────────────────────────────────────

export function ReviewQueue({ review }) {
  if (!review) return null;
  const { classification, reconcile, resolve, emit } = review;
  const counts = {
    discarded:    classification.discarded.length,
    unresolved:   classification.unresolved.length,
    conflicts:    reconcile.storeys.reduce((n, s) => n + s.conflicts.length, 0),
    missing:      reconcile.storeys.reduce((n, s) => n + s.missing.length, 0),
    rejected:     resolve.storeys.reduce((n, s) => n + (s.rejected || []).length, 0),
    gateFailed:   emit.storeys.filter((s) => s.hard_failures.length > 0).length,
    gateWarned:   emit.storeys.filter((s) => s.warnings.length > 0).length,
  };
  const total =
    counts.discarded + counts.unresolved + counts.conflicts +
    counts.missing + counts.rejected + counts.gateFailed + counts.gateWarned;

  return (
    <section className="review-queue">
      <h2>
        Review queue
        <span className={`review-total review-total-${total === 0 ? 'clean' : 'dirty'}`}>
          {total === 0 ? 'all clear' : `${total} item(s) need attention`}
        </span>
      </h2>

      <Section title="Classifier — DISCARD log" count={counts.discarded}>
        <DiscardedFiles items={classification.discarded} />
      </Section>

      <Section title="Classifier — unresolved pages" count={counts.unresolved} defaultOpen>
        <UnresolvedClassifications items={classification.unresolved} />
      </Section>

      <Section title="Reconcile — label conflicts" count={counts.conflicts} defaultOpen>
        <ReconcileConflicts storeys={reconcile.storeys} />
      </Section>

      <Section title="Reconcile — missing coverage" count={counts.missing}>
        <ReconcileMissing storeys={reconcile.storeys} />
      </Section>

      <Section title="Type resolver — rejected placements" count={counts.rejected}>
        <ResolveRejects storeys={resolve.storeys} />
      </Section>

      <Section
        title="Geometry emitter — gate status"
        count={counts.gateFailed + counts.gateWarned}
        defaultOpen={counts.gateFailed > 0}
      >
        <EmitGates storeys={emit.storeys} />
      </Section>
    </section>
  );
}
