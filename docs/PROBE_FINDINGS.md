# Step 3 — Probe Findings (PLAN.md §14.3)

Investigative output from running the 4 probe scripts against the reference
fixture (81-PDF canonical subset). These findings drive the regex/heuristic
design in Steps 4–7. Raw JSON reports under `data/probe_reports/` (gitignored).

Run any probe with `python scripts/probe_<name>.py`.

---

## §3A-1 — `STRUCT_PLAN_OVERALL` (14 pages)

| Metric | Value |
|---|---|
| Text items scanned | 23,197 |
| Grid-label candidates (1–2 char) | 1,967 hits / 77 uniques |
| Perimeter letters | 407 |
| Perimeter digits  | 383 |
| Interior false positives | 1,177 |

**Implications for Step 4 (grid detector):**

- Grid bubbles are present in the vector text layer — no need for OCR.
- ~56 bubbles per page on average (≈28 letter rows + ≈28 digit columns).
- A simple "1–2 char alphanumeric" filter generates **~60% false positives** from interior text. Step 4 must restrict to a perimeter band (~10% of page edge) **and** validate by row/column alignment.
- "SB" appears 60× as a top single-token match — that's a non-bubble annotation that the strict perimeter filter eliminates (interior, not edge).
- Grid digit ranges observed: 17–33+, suggesting 17+ axis lines per storey. Letter labels A–Z+ on the orthogonal axis.

---

## §3A-2 — `STRUCT_PLAN_ENLARGED` (56 pages)

| Metric | Value |
|---|---|
| Text items scanned | 40,300 |
| TYPE_CODE strict hits | 12,764 / 37 uniques |
| TYPE_CODE loose-only (regex misses) | 8 / 1 unique (`C1A`) |
| RECT_DIM strict hits | 4,792 / 22 uniques |
| RECT_DIM loose-only | 0 |
| DIA strict hits | 200 / 4 uniques |
| DIA loose-only | 33 / 1 unique |
| Numeric-only | 2,849 / 58 uniques |
| Label→dims pairs found via naive proximity | 32 unique labels |

**Top type codes:** `C2` (4132) · `SB1` (3760) · `SB2` (1968) · `NSP2` (448) · `SB4` (402) · `SB3` (389) · `H-C2` (344) · `RCB2` (154) · `C9` (122) · `C1` (122) · `C6` (111) · `H-C9` (78) · `RS1` (76) · `LSB3` (73) · `LSB1` (71)

**Top rect dims:** `800x800` (3441) · `600x600` (830) · `800x300` (135 — beam) · `1150x800` (95) · `1000x1000` (52) · `1100x1000` (40) · `1200x1000` (37) · `390x800` (36)

**Top diameters:** `1130 Ø` (99) · `1000 Ø` (55) · `1200 Ø` (24) · `800 Ø` (22)

**Rotation distribution (key):**

| Rotation | Spans |
|---|---|
| 90° (vertical, bottom-up) | 20,656 |
| 135° | 9,303 |
| -180° (upside-down) | 7,728 |
| 0° (horizontal) | 1,457 |
| 45° | 961 |

**Implications for Step 5 (label associator):**

- `TYPE_CODE_RE = ^(H-)?[A-Z]{1,3}\d+$` is mostly correct. **Widen to** `^(H-)?[A-Z]{1,3}\d+[A-Z]?$` to catch `C1A` variants.
- `RECT_DIM_RE` and `DIA_RE` are good. Only DIA needs investigation of the 33 loose-only hits (likely embedded in larger strings without the `^…$` anchor).
- **Rotation is critical**: ~50% of labels are 90° vertical and another ~25% are 135°/-180°/45°. The bbox-diagonal proximity search must operate in screen-space (rotated text has rotated bbox); PyMuPDF's `dict` mode returns rotation per span — use it.
- 12,764 type-code hits across only 37 uniques means the BIM model needs few unique types — Stage 5A's auto-duplicate path will run rarely once a starter family is loaded.

---

## §3B — `ELEVATION` (5 pages)

| Metric | Value |
|---|---|
| Text items scanned | 2,091 |
| `LEVEL_NAME_RE` strict hits | **0** |
| `RL_RE` strict hits | 755 / 47 uniques (most are dimension annotations, NOT level RLs) |
| RL loose-only candidates | 603 / 108 uniques |

**Top loose-only RL candidates** reveal what the consultant actually uses:
`BASEMENT 1` (29) · `FFL+3.50` (20) · `FFL+6.50` (19) · `FFL+15.50` (10) · `FFL+52.85` (10) · `FFL+9.50` (10) · `FFL-2.50` (10) · `BASEMENT 2` (10) · `FFL+57.95` (10) …

**Implications for Step 6 (elevation extractor) — major regex rework needed:**

- The PLAN.md §13 `LEVEL_NAME_RE = ^(B\d|L\d+|RF|UR|MEZZ|GF|GL)\b` matches **nothing** in this fixture's architectural elevations. The consultant uses:
  - `BASEMENT 1`, `BASEMENT 2`, `BASEMENT 3` (full words)
  - Storey names like `1ST STOREY`, etc. (TBC — probe didn't surface any here, but the ARCH §02 probe folder uses them in plan titles)
- The PLAN.md §13 `RL_RE = [+\-]?\d+(?:\.\d+)?\s*(?:mm|m)?` doesn't match the FFL-prefixed form.
- **Real RL form**: `FFL[+\-]\d+\.\d{1,2}` — values in **meters with decimal** (e.g. `+3.50` = 3500 mm). Step 6 must multiply by 1000.
- The strict-regex top hits (`8400`, `4500`, `4950`, `6000`) are **horizontal dimension annotations** (column spacings on the elevation drawing), not RLs. Strict `RL_RE` is too greedy and will produce false positives.
- Structural codes like `B1`/`L1`/`RF` appear ONLY in the structural-plan FILENAMES, never in the architectural elevation drawing text. A consultant-specific name mapping (`"BASEMENT 1" ↔ "B1"`, etc.) belongs in `meta.yaml`.

**Step 6 regex revisions (proposed):**

```
LEVEL_NAME_RE_V2 = r"^(?:B\d|BASEMENT\s+\d|L\d+|\d+(?:ST|ND|RD|TH)\s+STOREY|RF|UR|MEZZ|GF|GL)\b"
RL_FFL_RE        = r"FFL\s*([+\-])\s*(\d+(?:\.\d+)?)"   # capture sign + meters
```

`meta.yaml.levels` should accept either short codes (`B1`, `L1`) or long names (`BASEMENT 1`, `1ST STOREY`); a project-level alias map normalises them.

---

## §3C — `SECTION` (4 pages)

| Metric | Value |
|---|---|
| Text items scanned | 6,107 |
| Section-label `^SECTION X-Y$` hits | **0** |
| Slab/beam thickness annos | **0** |
| `\d+x\d+` dim pairs | **0** |
| Bare numerics | 445 / 41 uniques (mostly horizontal building dims, same as elevation: `8400`, `4500`, `4950`, …) |
| Level refs | 42 / 9 uniques |

**Top room labels (regex misses, but informative):**
`CORRIDOR` (297) · `ROOM` (209) · `STAFF` (191) · `CIRCULATION` (147) · `ENS` (120) · `PUBLIC` (75) · `PATIENT` (69) · `RISER` (61) · `LIFT LOBBY` (61) · `OFFICE` (55) · `TOILET` (48) · `STORE` (38) · `ENSUITE` (36) · `CLASS A` (36)

**Implications for Step 7 (section extractor) — the hardest extractor:**

- This fixture's architectural sections carry **no machine-readable slab/beam annotations**. The consultant has not labeled slab thicknesses on the section drawings; depth information is only encoded geometrically (cross-section hatch heights).
- Section IDs (`A-A`, `B-C`) are not in the page body — they live in the **filename** (`TD-A-120-0101_SECTION A_B.pdf`).
- The drawings DO show building interiors with ARCH room labels (CORRIDOR, OFFICE, TOILET, etc.), confirming these are A-prefixed architectural sections.
- **Step 7 strategy**:
  1. Parse `section_id` from filename (regex `_SECTION\s+([A-Z]_[A-Z])`).
  2. Attempt the existing thickness-text patterns; expect mostly empty results on this fixture.
  3. **Fall back to `meta.yaml.slabs.default_thickness_mm`** — this fixture is the §17 case where vector-text slab depth is unavailable. Flag every slab with `source: "meta.yaml fallback"` in the review queue.
  4. Image-based slab measurement (computer-vision on hatched cross-sections) is **out of scope for v5.3** per §17.

---

## §3A-2 (continued) — Column W×H orientation convention

**Scope clarified**: of the four column types (square / rectangular / round /
steel), only **rectangular** has the W-vs-L orientation problem. Square has
aspect 1, round has a single diameter, and steel (`H-` prefix on the type
code) is geometrically rectangular and resolved the same way. The 469
asymmetric annotations counted below ARE the rectangular cohort.

**Toolchain reminder**: YOLO `column-detect.pt @1280` is **single-class** —
it locates columns but does not classify shape. Shape is determined from
the OCR'd / vector-text annotation pattern (`NxN` / `N≠M` / `ØN`); YOLO
just provides the confident bbox we pair the text against.



The §3A-2 strategy says: for an unequal `W×H` annotation, the larger
annotation dim should map to the longer bbox axis. But the convention
direction (X×Y geometric vs L×S size-order) is consultant-specific.
The orientation probe (`probe_column_orientation.py`) tested both hypotheses.

**Setup:** for each asymmetric `W×H` text annotation on the 56 enlarged
plans, find the nearest small filled rect path on the same page (column
candidates: 8–100 pt filled rectangles, neither dim < 4 pt) within 3×
the text-bbox diagonal. Compute aspect agreement under each hypothesis
within `ASPECT_TOL = 0.15`.

| Metric | Value |
|---|---|
| Asymmetric annotations | 469 |
| `first > second` | 385 |
| `first < second` | **84** |
| Paired with a column-candidate path | 354 (75%) |
| X×Y consistent | 170 (48.0%) |
| L×S consistent | 182 (51.4%) |
| Both consistent | 170 (48.0%) |
| Only X×Y consistent | 0 |
| Only L×S consistent | 12 (3.4%) |
| Neither consistent | 172 (48.6%) |

**Conclusions:**

1. **The convention is NOT pure "longer × shorter".** 84 annotations
   (18% of all asymmetric) are written `first < second`. A strict L×S
   convention would have these all be `first > second`. The presence of
   `390x800`, `1200x2400`, `1200x2000` proves the convention is at least
   sometimes geometric (X×Y).

2. **Proximity-only pairing is unreliable.** 48.6% of the 354 attempted
   pairs disagree with both hypotheses — the fixture's enlarged plans are
   dense enough that a 3× text-diagonal radius often captures a wall stub
   or beam fragment instead of the actual column. We cannot derive the
   convention fixture-wide from this signal alone.

3. **The 0 cases of "X×Y only" + 12 cases of "L×S only" tell us very
   little** because almost every successful pair where the bbox itself
   was correctly identified has both hypotheses agreeing (170 "both
   consistent"). This is geometrically expected: when the paired path's
   long axis happens to align with the annotation's larger number, the
   two hypotheses converge.

**Implications for Step 5 (label associator):**

- Proximity is not a strong-enough signal on its own. Step 5 must use
  **YOLO column detection** (the v4 `column-detect.pt @1280` model)
  to obtain confident column bboxes, then pair text labels to YOLO
  bboxes (not to vector rect paths in isolation).
- Once we have a confident `(label, dim, bbox)` triple, the X×Y vs L×S
  question is a per-element check, not a fixture-wide conclusion.
  Algorithm:
  - If `bbox` aspect agrees with `(W, H)` interpretation within `ASPECT_TOL` → accept X×Y, record `dim_along_x_mm = W`, `dim_along_y_mm = H`.
  - Else if `bbox` aspect agrees with `(H, W)` (swapped) → accept swapped.
  - Else flag for the LLM checker (`signals.orientation_ambiguous = True`) and let the VLM look at the rendered page to resolve.
- The 84 `first < second` cases prove geometric (X×Y) is at least the
  primary convention for this consultant. Step 5 should default to X×Y
  and only swap when bbox geometry contradicts.
- **No fixture-wide convention flag in `meta.yaml`** — convention is
  decided per element, with the LLM checker as final arbiter for
  ambiguous cases. This matches PLAN.md §11 strict-mode: never coerce.

**Slab thickness (§3C) deferred per direction**

Vector-text slab/beam depth extraction returned 0 hits on this fixture
(see §3C above). For v5.3 we **defer Stage 3C slab/beam parsing** and
have the section extractor:
1. Capture `section_id` from the filename (`TD-A-120-0101_SECTION A_B.pdf` → `A-B`)
2. Emit an empty joints list
3. Stage 5B reads `meta.yaml.slabs.default_thickness_mm` and flags every
   slab with `source: meta.yaml.fallback` for the review queue

The "find column dimensions correctly, including W×H orientation" task
takes priority — it's the immediate v4 gap the v5 rewrite is meant to
close.

---

## Cross-cutting observations

1. **Naming-convention divergence between disciplines**: Structural sheets use short codes (`B1`, `L1`, `C2`); architectural sheets (which provide elevation/section input) use full English names (`BASEMENT 1`, `OFFICE`). Either the regex tier widens its vocabulary, or `meta.yaml` carries an alias map. Recommend `meta.yaml.aliases.levels` for explicit project-level normalisation.

2. **Strict numeric regexes over-match dimension annotations**: 4-digit numbers like `8400` appear as both *grid spacings* (on plans) and *level RLs* (on elevations) and *section dims* (on sections). Bare-number heuristics are unreliable; require a contextual cue (proximity to a level line, FFL prefix, etc.) before interpreting.

3. **Type code uniqueness is low**: 12,764 instances across 37 unique codes means Stage 5A's auto-duplicate path runs ≤37 times (once per unique `(label, dims)` combination), bounded by the starter-family inventory.

4. **Rotated text is the rule, not the exception**: ~95% of structural labels are non-horizontal. Step 5's bbox-diagonal proximity logic must be rotation-aware.

---

## Action items locked in for Steps 4–7

| Step | What changes vs the original PLAN.md regex |
|---|---|
| 4 (overall) | Restrict grid-bubble candidates to a 10%-edge perimeter band; validate by row/column alignment before accepting |
| 5 (enlarged) | Widen `TYPE_CODE_RE` → `^(H-)?[A-Z]{1,3}\d+[A-Z]?$`; investigate the 33 DIA loose-only hits; rotation-aware proximity; **YOLO column bbox is required for reliable label pairing**; per-element X×Y vs L×S orientation check with LLM checker fallback when both hypotheses disagree with bbox geometry |
| 6 (elevation) | Add `LEVEL_NAME_RE_V2` and `RL_FFL_RE`; convert meters→mm; project-level alias map in `meta.yaml.aliases.levels` |
| 7 (section) | **Deferred for v5.3**: parse `section_id` from filename; emit empty joints; Stage 5B falls back to `meta.yaml.slabs.default_thickness_mm` with review-queue flag. Text-based slab/beam depth not extractable on this fixture |
