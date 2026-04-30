import React from 'react';

import { basename } from '../util.js';

const CLASS_ORDER = [
  'STRUCT_PLAN_OVERALL',
  'STRUCT_PLAN_ENLARGED',
  'ELEVATION',
  'SECTION',
  'DISCARD',
  'UNKNOWN',
];

const TIER_ORDER = [
  'filename',
  'title_block',
  'content',
  'llm',
  'unresolved',
  'manual',
];

function ClassPill({ name, count }) {
  return (
    <span className={`class-pill class-${name}`}>
      <span className="class-name">{name}</span>
      <span className="class-count">{count}</span>
    </span>
  );
}

export function Classification({ classification }) {
  if (!classification) return null;
  const { summary, items } = classification;

  const unresolved = items.filter((i) => i.tier === 'unresolved');
  const llmItems   = items.filter((i) => i.tier === 'llm');

  return (
    <div className="classification">
      <h3>Classification (Stage 2)</h3>
      <p className="summary">{summary.total} pages classified</p>

      <div className="pills">
        {CLASS_ORDER.filter((c) => summary.by_class[c]).map((c) => (
          <ClassPill key={c} name={c} count={summary.by_class[c]} />
        ))}
      </div>

      <details>
        <summary>By tier</summary>
        <ul className="tier-list">
          {TIER_ORDER.filter((t) => summary.by_tier[t]).map((t) => (
            <li key={t}>
              <span className="tier-name">{t}</span>
              <span className="tier-count">{summary.by_tier[t]}</span>
            </li>
          ))}
        </ul>
      </details>

      {llmItems.length > 0 && (
        <details>
          <summary>{llmItems.length} decided by LLM (primary + checker)</summary>
          <ul className="llm-list">
            {llmItems.map((i, idx) => {
              const p = i.signals?.primary;
              const c = i.signals?.checker;
              const agreed = i.signals?.checker_agreed;
              return (
                <li key={idx}>
                  <div className="row">
                    <span className="file-name">{basename(i.pdf)}</span>
                    <span className="cls">{i.class}</span>
                    <span className="conf">{i.confidence?.toFixed(2)}</span>
                  </div>
                  <div className="verdicts">
                    {p && <span title={`primary: ${p.reason}`}>P:{p.class} ({p.confidence?.toFixed(2)})</span>}
                    {c && <span title={`checker: ${c.reason}`}>C:{c.class} ({c.confidence?.toFixed(2)})</span>}
                    <span className={agreed ? 'agreed' : 'disagreed'}>
                      {agreed === undefined ? '—' : agreed ? 'agree' : 'disagree'}
                    </span>
                  </div>
                </li>
              );
            })}
          </ul>
        </details>
      )}

      {unresolved.length > 0 && (
        <details open>
          <summary className="unresolved-header">
            {unresolved.length} unresolved — needs review
          </summary>
          <ul className="unresolved-list">
            {unresolved.map((i, idx) => (
              <li key={idx}>
                <div className="row">
                  <span className="file-name">{basename(i.pdf)}</span>
                </div>
                <div className="reason">{i.reason}</div>
                {i.signals?.primary && i.signals?.checker && (
                  <div className="verdicts">
                    <span>P:{i.signals.primary.class} ({i.signals.primary.confidence?.toFixed(2)})</span>
                    <span>C:{i.signals.checker.class} ({i.signals.checker.confidence?.toFixed(2)})</span>
                  </div>
                )}
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
