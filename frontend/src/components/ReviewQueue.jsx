import React, { useMemo, useState } from 'react';

import { basename } from '../util.js';

// Surfaces the strict-mode review queue (PLAN.md §11) — every conflict,
// unresolved column, missing-coverage flag and DISCARD record the human
// must adjudicate before the RVTs can ship without caveat.

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

// Each review row is a {name, meta, detail} triple. ReviewList renders
// the empty state when items=[] so callers don't repeat that branch.
function ReviewList({ items, empty, render }) {
  if (!items.length) return <div className="review-empty">{empty}</div>;
  return (
    <ul className="review-list">
      {items.map((it, i) => {
        const { name, meta, detail, rowClass } = render(it, i);
        return (
          <li key={i} className={rowClass}>
            <span className="review-name">{name}</span>
            <span className="review-meta">{meta}</span>
            <span className="review-detail">{detail}</span>
          </li>
        );
      })}
    </ul>
  );
}

// Render a column's dimension signature regardless of shape.
function formatDims(d) {
  if (d.diameter_mm != null) return `Ø${d.diameter_mm}`;
  if (d.dim_along_x_mm != null && d.dim_along_y_mm != null) {
    return `${d.dim_along_x_mm}×${d.dim_along_y_mm}`;
  }
  return '';
}

// ── Per-section renderers ───────────────────────────────────────────────────

function DiscardedFiles({ items }) {
  return (
    <ReviewList
      items={items}
      empty="No DISCARD pages."
      render={(it) => ({
        name:   basename(it.pdf),
        meta:   <>page {it.page_index ?? 0} · tier: {it.tier}</>,
        detail: it.reason,
      })}
    />
  );
}

function UnresolvedClassifications({ items }) {
  return (
    <ReviewList
      items={items}
      empty="All pages resolved by tiers 1–4."
      render={(it) => ({
        name:   basename(it.pdf),
        meta:   `page ${it.page_index ?? 0}`,
        detail: it.reason,
      })}
    />
  );
}

function ReconcileConflicts({ storeys }) {
  const flat = storeys.flatMap((s) =>
    s.conflicts.map((c) => ({ ...c, storey_id: s.storey_id })),
  );
  return (
    <ReviewList
      items={flat}
      empty="No label conflicts across storeys."
      render={(c) => ({
        name: `${c.storey_id} · col #${c.canonical_idx}`,
        meta: '',
        detail: c.label_candidates.map((lc, j) => (
          <span key={j} className="review-chip">
            {lc.label}/{lc.shape} {formatDims(lc)}
            {' '}
            <span className="review-chip-meta">
              {lc.source_pdf ? basename(lc.source_pdf) : ''}
              {lc.distance_mm != null ? ` · Δ${lc.distance_mm} mm` : ''}
            </span>
          </span>
        )),
      })}
    />
  );
}

function ReconcileMissing({ storeys }) {
  // Aggregate per storey so the reviewer sees magnitude, not 12,000 rows.
  const rows = storeys
    .map((s) => ({ storey_id: s.storey_id, count: s.missing.length, summary: s.summary }))
    .filter((r) => r.count > 0);
  return (
    <ReviewList
      items={rows}
      empty="Every canonical column carries a label."
      render={(r) => {
        const s = r.summary || {};
        const inferred = s.label_inferred ? ` · ${s.label_inferred} inferred from neighbours` : '';
        const detail = s.canonical_total != null
          ? `${s.labelled}/${s.canonical_total} labelled${inferred}`
          : '';
        return {
          name:   r.storey_id,
          meta:   `${r.count} unlabelled`,
          detail,
        };
      }}
    />
  );
}

function ResolveRejects({ storeys }) {
  const flat = storeys.flatMap((s) =>
    (s.rejected || []).map((r) => ({ ...r, storey_id: s.storey_id })),
  );
  return (
    <ReviewList
      items={flat}
      empty="No placements rejected by Stage 5A."
      render={(r) => ({
        name:   `${r.storey_id} · col #${r.canonical_idx}`,
        meta:   r.reason,
        detail: <>
          {r.label ? `label=${r.label} ` : ''}shape={r.shape} {formatDims(r)}
          {' · '}{r.audit}
        </>,
      })}
    />
  );
}

function EmitGates({ storeys }) {
  const failed = storeys.filter((s) => s.hard_failures.length > 0);
  const warned = storeys.filter((s) => s.warnings.length > 0);
  if (!failed.length && !warned.length) {
    return <div className="review-empty">All storeys passed every gate.</div>;
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
  const counts = useMemo(() => {
    const sumLen = (storeys, key) =>
      storeys.reduce((n, s) => n + (s[key]?.length || 0), 0);
    return {
      discarded:    classification.discarded.length,
      unresolved:   classification.unresolved.length,
      conflicts:    sumLen(reconcile.storeys, 'conflicts'),
      missing:      sumLen(reconcile.storeys, 'missing'),
      rejected:     sumLen(resolve.storeys,   'rejected'),
      gateFailed:   emit.storeys.filter((s) => s.hard_failures.length > 0).length,
      gateWarned:   emit.storeys.filter((s) => s.warnings.length > 0).length,
    };
  }, [classification, reconcile, resolve, emit]);
  const total = Object.values(counts).reduce((a, b) => a + b, 0);

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
