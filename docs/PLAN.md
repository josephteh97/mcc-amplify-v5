# Plan v5.3 — Three-Input Single-Shot BIM Pipeline (canonical)

A direct renovation of v4. The user uploads all relevant PDFs in one go via the UI. The system keeps four drawing classes (overall plan + enlarged plan + elevation + section), discards the rest with a logged reason, and emits a Revit 2023 RVT plus GLTF per storey. Strict-mode and fail-loud throughout. No zip handling, no batched deliveries, no persistent workspace across uploads.

---

## 1. Vision

v4 produced storey-level structural geometry from a structural-plan PDF set but had no answer for type/dimension resolution (labels unreadable on the overall plan), no integration of elevation or section data, and assumed clean inputs. v5.3:

- Accepts a messy multi-file upload, classifies each PDF page into four kept buckets, discards the rest with a logged reason.
- Carries grid + canonical column positions from `-00` overall plans (v4's strength) and adds type/dimension labels from `-01..04` enlargements (v4's gap).
- Adds floor-to-floor heights from elevations and slab/beam depths from sections.
- Resolves Revit types deterministically: exact-match → label-match → auto-duplicate → reject. Never coerces, never silently rounds.
- Targets Autodesk Revit 2023 explicitly.
- Single-shot: one upload, one job, one output set. Rerun = full reprocess.

A normal BIM modeler can take the output and continue without redoing the structural shell.

---

## 2. Top-level architecture

```
upload many PDFs (loose, drag-drop)
        ↓
┌──────────────────────────────────────────────────────────────────┐
│ 1. Ingest         walk uploaded list, page-fingerprint           │
│ 2. Classify       4 kept buckets + DISCARD                       │
│ 3. Extract                                                       │
│    3A-1 plan_overall     grid + canonical column/beam positions  │
│    3A-2 plan_enlarged    type/dim/shape labels (readable text)   │
│    3B   elevation        levels: [{name, rl_mm}]                 │
│    3C   section          joints: [{grid_xy, level, slab/beam mm}]│
│ 4. Reconcile      grid-mm merge, cross-link overall ↔ enlarged   │
│ 5A. Type Resolver match / auto-duplicate Revit types             │
│ 5B. Emit          Revit 2023 RVT + GLTF per storey               │
└──────────────────────────────────────────────────────────────────┘
        ↓
output/<storey>.rvt + .gltf + _typing.json + _review.json
output/_classification_report.json   (every uploaded PDF and its disposition)
```

One job = one upload = one output. Rerun = full reprocess. The job's working directory is wiped on rerun.

---

## 3. Inputs (four kept classes, plus DISCARD)

| Class                  | Role                                                                                                  | Sample filenames                                  |
|------------------------|-------------------------------------------------------------------------------------------------------|---------------------------------------------------|
| STRUCT_PLAN_OVERALL    | global grid (bubble labels + intersections), canonical column/beam position list, storey scope       | `TGCH-TD-S-200-L3-00.pdf`                         |
| STRUCT_PLAN_ENLARGED   | readable type/dim labels at 4× zoom of the four quadrants                                             | `TGCH-TD-S-200-L3-01..04.pdf`                     |
| ELEVATION              | level names + RL → floor-to-floor heights                                                             | `TD-A-130-01-01_ELEVATION 1_2.pdf`                |
| SECTION                | slab thickness + beam depth at joints                                                                 | `TD-A-120-0101_SECTION A_B.pdf`                   |
| (DISCARD)              | logged in `_classification_report.json` and ignored                                                   | perspectives, schedules, ARCH plans, MEP, details |

`-00` and `-01..04` are gated on drawing kind, not discipline letter. ELEVATION/SECTION accept both A- and S-prefixed sheets — sample data shows the consultant ships these as A-series.

---

## 3.1 Reference sample upload

A canonical sample upload lives at:

```
~/Documents/sample_uploaded_documents/
```

This is what real consultant input looks like for the TGCH project. The build agent must use this as the day-one fixture for the classifier, every probe script, and the Milestone A acceptance test. Copy or symlink it into `tests/fixtures/sample_uploaded_documents/` in the new repo.

Directory tree (as shipped):

```
~/Documents/sample_uploaded_documents/
├── 03 120 - BLDG SECTIONS/                       4 PDFs
│   ├── TD-A-120-0101_SECTION A_B.pdf
│   ├── TD-A-120-0102_SECTION C_D.pdf
│   ├── TD-A-120-0103_SECTION E_F.pdf
│   └── TD-A-120-0104_SECTION G.pdf
│
├── 04 130 - BLDG ELEVATIONS/                     7 PDFs
│   ├── TD-A-130-0001_PERSPECTIVES 1.pdf          ← DISCARD (rendering)
│   ├── TD-A-130-0002_PERSPECTIVES 2.pdf          ← DISCARD (rendering)
│   ├── TD-A-130-01-01_ELEVATION 1_2.pdf
│   ├── TD-A-130-01-02_ELEVATION 3_4.pdf
│   ├── TD-A-130-01-03_ELEVATION 5_6.pdf
│   ├── TD-A-130-01-04_ELEVATION 7_8.pdf
│   └── TD-A-130-01-05_ELEVATION 9_10.pdf
│
└── FLOOR FRAMING PLANS/                          70 PDFs
    └── TGCH-TD-S-200-{B3,B2,B1,L1..L9,RF,UR}-{00..04}.pdf
        # 14 storeys × 5 pages each
        # -00 = STRUCT_PLAN_OVERALL
        # -01..04 = STRUCT_PLAN_ENLARGED
                                              ───
                                       Total: 81 PDFs
```

Storey list present in the sample:

```
B3, B2, B1, L1, L2, L3, L4, L5, L6, L7, L8, L9, RF, UR
```

14 storeys. No foundation plans (S-100 series absent — see §17 Out of scope).

Filename conventions in this sample:

| Series prefix              | Discipline                    | Class                                                       |
|----------------------------|-------------------------------|-------------------------------------------------------------|
| `TGCH-TD-S-200-`           | S = structural framing        | STRUCT_PLAN_OVERALL / STRUCT_PLAN_ENLARGED (suffix decides) |
| `TD-A-120-`                | A = architectural sections    | SECTION                                                     |
| `TD-A-130-…_ELEVATION_…`   | A = architectural elevations  | ELEVATION                                                   |
| `TD-A-130-…_PERSPECTIVES_…`| renderings                    | DISCARD                                                     |

Two corner cases the build agent must handle correctly:

1. Sections and elevations are A-prefixed, not S-prefixed. The classifier gates on drawing kind (the SECTION / ELEVATION keyword), not on the discipline letter. See §5.
2. Perspectives share the elevation prefix (`TD-A-130-…`). The PERSPECTIVE rule must precede the ELEVATION rule in `classifier_rules` (§10), or perspectives leak into ELEVATION and break Stage 3B.

Real-world deviations the agent should expect:

The sample is grouped into folders, but the build agent must not rely on folder structure. In production the user may drag-drop a flat list, or the consultant may ship a different folder layout. All classification decisions come from filename + title-block + content; folder names are advisory only. Stage 1 Ingest flattens the list before classification (§4).

---

## 4. Stage 1 — Ingest

- Accept N loose PDFs from the UI (drag-drop / multi-file). No zip handling.
- Walk the flat list; do not assume folder grouping (sample shows three folders, real uploads may be flat).
- Page-fingerprint each PDF page (sha256 of page bytes) so re-uploads of the same sheet dedupe automatically.
- Output: `[(pdf_path, n_pages, page_hashes), …]` to the orchestrator.

No archiving, no version history.

---

## 5. Stage 2 — Classify

For each PDF page, decide one of:

```
STRUCT_PLAN_OVERALL | STRUCT_PLAN_ENLARGED | ELEVATION | SECTION | DISCARD
```

Classifier signals, evaluated in order — first confident hit wins:

1. **Filename keyword** (per-project rule cache, project-overridable):
   - `…-S-200-…-00$` → STRUCT_PLAN_OVERALL
   - `…-S-200-…-0[1-4]$` → STRUCT_PLAN_ENLARGED
   - filename contains `PERSPECTIVE` → DISCARD (must precede ELEVATION rule — perspective files match the elevation prefix)
   - filename contains `SECTION` → SECTION
   - filename contains `ELEVATION` → ELEVATION
   - everything else → fall through to title-block parse
2. **Title-block parse** — PyMuPDF text in the bottom-right region; same keywords with broader sweep (catches filenames that don't follow the convention).
3. **Content heuristic**:
   - vertical level lines spanning ≥60% of page width → ELEVATION
   - cut-line label pattern (`SECTION A-A`) detected → SECTION
   - horizontal floor outline + grid bubbles around perimeter → STRUCT_PLAN_OVERALL
4. **LLM judge** (Ollama, two VLMs — primary + checker) — the smart brain. Catches anything heuristics couldn't decide and is the primary line of defence against junk files (ARCH plans, MEP, schedules, details, foundations, partial-discipline sheets) leaking into the four kept buckets.
   - **Primary VLM**: `aisingapore/Gemma-SEA-LION-v4-4B-VL:latest` (default; override via `CLASSIFIER_LLM_PRIMARY_MODEL`). Makes the classification call.
   - **Checker VLM**: `qwen3-vl:latest` (default; different architecture so the two don't share blind spots). Independently classifies the same image. Disable via `CLASSIFIER_LLM_CHECKER_DISABLED=true` if you want a single-model run.
   - **Combination rule**:
     - both pick same class → accept, `confidence = max(primary, checker)`, `signals.checker_agreed=True`
     - disagree → UNRESOLVED, both verdicts preserved in `signals.{primary,checker}` for the UI prompt
     - primary fails (Ollama down / unparseable / unknown class) → UNRESOLVED with checker's verdict captured
     - checker fails / disabled → accept primary alone if `confidence ≥ CLASSIFIER_LLM_CONF_MIN`, else UNRESOLVED
   - Input: rendered 512 px page thumbnail; both models receive the identical prompt.
   - Prompt: explicit anti-cue ("grid bubbles appear in BOTH structural and architectural plans — do NOT use them as the deciding factor"); room-label rule ("OFFICE/TOILET/LIFT/STAIR/BEDROOM in a plan view → DISCARD"). Plain-text reply format `CLASS: / CONFIDENCE: / REASON:` — NOT `format=json` (VLMs that emit a hidden "thinking" channel starve the JSON output budget).
   - Cache: SQLite keyed by `(page_hash, model)` at `data/classifier_cache.sqlite`. Each model's verdict is stored independently so swapping primary↔checker reuses prior work.
5. **UNKNOWN** → UI prompt; the user's decision is saved as a new filename rule for this project.

Per-project rule cache lives in `meta.yaml.classifier_rules`.

Day-one test cases (must pass on the sample upload):

- `TGCH-TD-S-200-L3-00.pdf` → STRUCT_PLAN_OVERALL
- `TGCH-TD-S-200-L3-01.pdf` → STRUCT_PLAN_ENLARGED
- `TD-A-120-0101_SECTION A_B.pdf` → SECTION
- `TD-A-130-01-01_ELEVATION 1_2.pdf` → ELEVATION
- `TD-A-130-0001_PERSPECTIVES 1.pdf` → DISCARD (critical edge case — must not leak into ELEVATION)

Expected classification of the reference sample (`~/Documents/sample_uploaded_documents/`, 81-PDF canonical subset):

- 14 STRUCT_PLAN_OVERALL — `TGCH-TD-S-200-{B3..UR}-00.pdf`
- 56 STRUCT_PLAN_ENLARGED — `TGCH-TD-S-200-{B3..UR}-{01..04}.pdf`
- 5 ELEVATION — `TD-A-130-01-{01..05}_ELEVATION *.pdf`
- 4 SECTION — `TD-A-120-{0101..0104}_SECTION *.pdf`
- 2 DISCARD — `TD-A-130-{0001,0002}_PERSPECTIVES *.pdf`

A classifier that produces any other counts on this fixture is broken; this is the day-one regression test.

Output: `_classification_report.json` listing every uploaded PDF, its decision, the signal that decided it, and the confidence.

---

## 6. Stage 3 — Extract

All extractors emit data in building grid-mm so downstream stages don't care which view it came from.

### 3A-1. STRUCT_PLAN_OVERALL (per `-00` page)

The renovation of v4's grid pipeline. **Authoritative for position, not type.**

```
detect grid bubble labels (A, B, …, 1, 2, …) at page perimeter
detect grid-line intersections inside the drawing area
solve 2D pixel→grid-mm affine; reject if residual > 1 px
YOLO column detection (column model imgsz=1280)
YOLO framing detection (framing model imgsz=640)
slab boundary detection (closed polygons of structural fill)
emit:
  extracted/<storey>.overall.json = {
    grid: {x_axes: [{label, mm}, ...], y_axes: [...]},
    columns_canonical: [{bbox_grid_mm, aspect}, ...],
    beams_canonical:   [{polyline_grid_mm}, ...],
    slabs_canonical:   [{polygon_grid_mm}, ...],
    affine_residual_px: <float>
  }
```

Vector text on `-00` is not parsed for type codes — at overall scale they are unreadable, overlapping, or absent. `-00` carries position truth only.

### 3A-2. STRUCT_PLAN_ENLARGED (per `-01..04` page)

The renovation gap-fill. **Authoritative for type, dimension, shape.**

```
PAGE_REGION_MAP = {
  01: "upper-left",
  02: "upper-right",
  03: "lower-left",
  04: "lower-right",
}

detect grid bubbles (subset, only the quadrant covered)
solve per-page pixel→grid-mm affine into the SAME global grid-mm as -00
YOLO column detection (high-res, redundant with -00 but bbox-precise)
vector text extraction: page.get_text("dict") → [{text, bbox_px, rotation}, ...]

shape-aware label associator (for each column bbox):
  search window = 2.0 × bbox_diagonal
  type-code regex:  ^(H-)?[A-Z]{1,3}\d+$            # C2, H-C9
  rect/sq dim:      ^\d{3,4}\s*[xX]\s*\d{3,4}$       # 800x800, 1150x800
  diameter:         ^[ØøD]\s*\d{3,4}$
                  | ^\d{3,4}\s*(?:DIA|dia|Ø|ø)$
  L/T 4-number:     flag + skip (deferred)
  pair type-code ↔ dim within PAIR_PROXIMITY_MM = 50

  column type from text pattern (NOT from bbox shape — YOLO is single-class):
    \d+x\d+ equal       → square(s_mm)
    \d+x\d+ unequal     → rectangular(dim_x_mm, dim_y_mm)  ← only this needs orientation work
    diameter (Ø / D)    → round(d_mm)
    H-prefix label      → steel (treated as rectangular for v5.3 geometry;
                                  family-resolver in Stage 5A picks the steel family)

  rectangular orientation — per-element X×Y vs swap (PROBE_FINDINGS.md §3A-2 cont):
    yolo_bbox = (dx_pt, dy_pt)
    annotation = (a_mm, b_mm), a ≠ b
    Try X×Y:  |dx/dy − a/b| / max(dx/dy, a/b) ≤ ASPECT_TOL → dim_along_x_mm = a, dim_along_y_mm = b
    Try swap: |dx/dy − b/a| / max(dx/dy, b/a) ≤ ASPECT_TOL → dim_along_x_mm = b, dim_along_y_mm = a
    Neither fits: signals.orientation_ambiguous = True; defer to LLM checker
                  ("looking at the rendered page, is this column's longer axis horizontal
                    or vertical?"). NEVER coerce — wrong dimension on a structural column
                  is an unacceptable failure mode (§11 strict-mode).

  bbox sanity by shape:
    round       — bbox aspect ∈ [ROUND_ASPECT_LO, ROUND_ASPECT_HI] = [0.85, 1.15]
    square      — bbox aspect ≈ 1.0 within ASPECT_TOL
    rectangular — at least one of {X×Y, swap} agrees with bbox aspect within ASPECT_TOL = 0.15;
                  if neither, defer to LLM checker. Do not coerce.

  unlabeled column → emit label=None, shape=unknown, dims=None, flag for review

emit per page:
  {
    type:           "column",
    label:          "C2" | "H-C9" | None,
    shape:          "rectangular" | "square" | "round" | "unknown",
    dim_along_x_mm: float | None,
    dim_along_y_mm: float | None,
    diameter_mm:    float | None,
    bbox_grid_mm:   [...],
    grid_mm_xy:     [x, y],
    page_id:        int,
    page_region:    "upper-left" | ...,
    flags:          [...]
  }
```

Same machinery for beams: `RCB\d+`, `H-RCB\d+`, etc.

Recipe sanitizer ported from v4.

### 3B. ELEVATION

Reduced scope — RL only. Column continuity is deferred.

```
detect long horizontal lines spanning ≥ 60% of drawing area
for each level line:
  search nearest text within ±LEVEL_TEXT_PX (vertical band, both sides)
    name regex:  ^(B\d|L\d+|RF|UR|MEZZ|GF|GL)\b
    rl   regex:  signed number, optional units
                 (+9.500, -3000, +12.500 SFL, +12500mm)
  emit {name, rl_mm}
sort by rl_mm
floor_to_floor[i] = rl[i+1] - rl[i]

emit: extracted/<pdf>.elev.json = {levels: [{name, rl_mm, source_pdf}, ...]}
```

When several elevation PDFs cover different facades, the level set must agree. Strict-mode flags any disagreement (e.g. front elevation says `L2: 4500`, side elevation says `L2: 4490`) outside `LEVEL_AGREEMENT_TOL_MM`.

### 3C. SECTION

```
locate section cut symbol; match section_id (e.g. "A-A") to plan
detect slab cross-sections at each level line in the section
detect beam cross-sections at column lines
extract slab thickness (mm) and beam depth (mm)
attach (grid_xy, level_name) where extractable

emit: extracted/<pdf>.section.json = {
  section_id: "A-A",
  joints: [
    {grid_xy_mm, level_name, slab_thk_mm, beam_depth_mm, source_pdf},
    ...
  ]
}
```

If a section view doesn't carry grid labels, fall back to applying its slab/beam values globally for the level (with a warning flag, recorded in the review queue).

---

## 7. Stage 4 — Reconcile

Combine outputs into one storey + project model.

### Per storey — cross-link OVERALL ↔ ENLARGED

```
for each canonical_column in <storey>.overall.json:
  candidates = enlarged_detections within DEDUPE_TOL_MM = 50 grid-mm
  if any candidate has a label:
    attach (label, shape, dim_x_mm, dim_y_mm, diameter_mm, flags)
    if multiple labelled candidates disagree (boundary overlap of -01/-02 etc.):
      strict — keep all distinct (label, shape, dims) tuples, flag conflict
  else:
    emit canonical column with label=None, flag "label missing"
    list the missing region in the review queue
```

Beams reconciled the same way (polyline endpoints in grid-mm).

`-00` defines truth-of-existence. `-01..04` define truth-of-type. Both transforms resolve into the same global grid-mm; correctness is verifiable by spot-checking columns visible in both `-00` and an enlarged page.

### Per project

- levels from elevation extractor → drives floor-to-floor heights.
- slabs from section extractor → drives slab thickness per zone.
- Override precedence (highest first): manual `meta.yaml` > extracted from drawings > fail.

---

## 8. Stage 5A — Type Resolver + Revit Family Manager

Goal: for every column/beam in the reconciled storey model, pick a Revit family type. If no existing type fits, duplicate one and set its dimensions. Strict on dimensions, tolerant on label.

### Family inventory (one-time per session)

```
for each loaded family in the Revit doc:
  for each Type:
    parse type name → (shape, dims) using project rules
    record {family_name, type_name, shape, dims, type_id}
build index keyed by (shape, dims) and by label
```

### Matching algorithm (per column, first hit wins)

1. **Exact dims match** — same shape + dims within ±`TYPE_DIM_TOL_MM` (5 mm) → use that type.
2. **Label-only match** — a Revit type with the same label code exists AND its dims agree within ±`TYPE_DIM_TOL_MM` → use it.
3. **Auto-duplicate** — no match. Create a new type:
   - pick the family by shape (`Concrete-Rectangular-Column`, `Concrete-Round-Column`)
   - duplicate any existing type as base
   - canonical new type name: `<label>_<shape_code>_<dims>` → `C2_R_1150x800`, `C5_RD_800`
   - set the family's dimension parameters (`b`, `h` for rect; `d` for round)
   - register in inventory cache so subsequent same-(shape, dims) reuse it
4. **Reject** — shape unknown, dims None, or shape is L/T (deferred). Skip placement, add to review queue.

### Tolerance rules

- Dimensions: ±5 mm strict. `1150x800 ≠ 800x1150`; orientation already resolved in Stage 3A-2.
- Label: case-insensitive, whitespace-stripped. `H-C9 ≠ C9`.
- Shape: exact only. Round never auto-substitutes for square.

### No-fuzzy-match rule

- Never silently round 1150 → 1200 to fit an existing type.
- Never substitute `C2 800x800` with `C3 800x800`.
- Either match exactly, duplicate-and-create, or reject. No middle ground.

### Per-column placement payload

```
{
  grid_mm_xy:    [x, y],
  type_id:       <Revit type id>,
  type_name:     "C2_R_1150x800",
  rotation_deg:  0 | 90 | …,           # from bbox orientation
  comments:      "C2",                  # consultant label → instance Comments
  source_label:  "C2",
  source_dims:   {x: 1150, y: 800} | {d: 800},
  flags:         [...]
}
```

### Audit trail (per column)

One of:

- `MATCHED_EXACT(family, type)`
- `MATCHED_LABEL(family, type, dim_delta_mm)`
- `CREATED(family, new_type)`
- `REJECTED(reason)`

Output: `output/<storey>_typing.json`.

### Execution context

Stage 5A produces a placement plan as JSON. A pyRevit / Revit API script consumes the plan inside an open Revit 2023 document, performs Edit Type / Duplicate operations, and places instances. Stage 5A itself does not require Revit; it can run headlessly given a previously-exported family inventory.

---

## 9. Stage 5B — Geometry Emitter (Revit 2023)

Consumes Stage 5A placement payloads.

- Place each instance at `grid_mm_xy` with the resolved `type_id`, applying `rotation_deg`.
- Write comments to the instance.
- Vertical extents from `meta.yaml.levels` (base level → top level for the storey).
- Slab thickness per zone from `meta.yaml.slabs`.
- Same flow for beams (after the beam typer is built).

### Hard-required gates (fail loud if missing)

- structural plan `-00` for the storey
- at least one structural plan `-0[1-4]` covering each canonical column from `-00`
- floor-to-floor height for the storey (from ELEVATION or `meta.yaml`)
- slab thickness (from SECTION or `meta.yaml`)
- starter Revit family per shape encountered, in Revit 2023 format

If any gate fails, emit a structured error naming exactly what's missing (e.g. `"L3 lower-left has no -03 page; columns at grid C/4..G/4 cannot be typed"`).

### Outputs per storey

- `output/<storey>.rvt` (Revit 2023)
- `output/<storey>.gltf`
- `output/<storey>_typing.json` (audit trail)
- `output/<storey>_review.json` (unresolved columns, conflicts, missing-coverage flags)

---

## 10. meta.yaml

Single source of truth for human-overridable values. Auto-populated by extractors; user edits override.

```yaml
project:
  id: TGCH
  classifier_rules:
    - { pattern: "^TGCH-TD-S-200-.*-00\\.pdf$",     class: STRUCT_PLAN_OVERALL }
    - { pattern: "^TGCH-TD-S-200-.*-0[1-4]\\.pdf$", class: STRUCT_PLAN_ENLARGED }
    - { pattern: "PERSPECTIVE",                       class: DISCARD }
    - { pattern: "SECTION",                           class: SECTION }
    - { pattern: "ELEVATION",                         class: ELEVATION }

target:
  revit_version: 2023

families:
  column:
    rectangular: "Concrete-Rectangular-Column"
    square:      "Concrete-Rectangular-Column"   # square = rect with b == h
    round:       "Concrete-Round-Column"
  beam:
    rectangular: "Concrete-Rectangular-Beam"

levels:                # filled by 3B; user can override
  B3: { rl_mm: -9000, source: manual }
  B2: { rl_mm: -6000, source: manual }
  B1: { rl_mm: -3000, source: manual }
  L1: { rl_mm:  0,    source: manual }
  L2: { rl_mm:  4500, source: "elev:TD-A-130-01-01_ELEVATION 1_2.pdf" }
  L3: { rl_mm:  8100, source: "elev:TD-A-130-01-01_ELEVATION 1_2.pdf" }
  # …

slabs:
  default_thickness_mm: 200
  zones:
    L3_default: { thickness_mm: 250, source: "section:A-A" }

review:
  unresolved_columns: []
  conflicts: []
```

---

## 11. Strict-mode policy

| Situation                                          | Action                                                              |
|----------------------------------------------------|---------------------------------------------------------------------|
| Required input missing                             | Fail with actionable message naming the missing class + storey      |
| Two extractions disagree                           | Keep all candidates, flag conflict, emit both as distinct types     |
| Annotation ambiguous (e.g. dim order w×h vs h×w)   | Resolve via bbox aspect; flag if disagreement > `ASPECT_TOL`        |
| File can't be classified                           | UI prompt; remember user's decision as a new rule                   |
| Elevation/section absent                           | Use `meta.yaml`; if absent there too, fail                          |
| Plan dims don't match any loaded Revit type        | Auto-duplicate, name canonically                                    |
| Plan dims close-but-not-equal to existing type     | Auto-duplicate, do not snap to existing                             |
| Starter family for a shape is missing              | Fail with message `"load <family.rfa>"`                             |
| Loaded family compiled in newer Revit version      | Fail with version-mismatch message                                  |
| Column has shape unknown / L-T / no dims           | Reject placement, add to review queue                               |
| Column from `-00` not covered by any `-0[1-4]`     | Emit unlabeled, list missing region                                 |
| Multiple elevations disagree on level RL           | Flag conflict, do not average                                       |

Never coerce. Never silently default.

---

## 12. Repo layout

```
mcc-amplify-v5/
├── backend/
│   ├── ingest/                 # Stage 1 (no unzip, multi-file)
│   ├── classify/               # Stage 2 (4-class + DISCARD)
│   ├── extract/
│   │   ├── plan_overall/       # 3A-1 — grid + canonical positions
│   │   ├── plan_enlarged/      # 3A-2 — labels + dims + shape
│   │   ├── elevation/          # 3B   — RL only
│   │   └── section/            # 3C   — slab/beam depth
│   ├── reconcile/              # Stage 4 — cross-link overall ↔ enlarged
│   ├── resolve/                # Stage 5A — type resolver + family manager
│   ├── emit/
│   │   ├── revit/              # Stage 5B — Revit 2023 RVT
│   │   └── gltf/               # Stage 5B — GLTF
│   └── core/
│       ├── grid_mm.py          # canonical coordinate space
│       ├── meta_yaml.py
│       ├── workspace.py        # job-scoped, ephemeral
│       └── orchestrator.py
├── revit_scripts/              # pyRevit consumer of Stage 5A placement plan
├── ml/weights/                 # YOLO models (column imgsz=1280, framing 640)
├── frontend/                   # multi-file upload, classifier review, type-resolver audit
├── tests/
│   └── fixtures/
│       └── sample_uploaded_documents/   # mirror of ~/Documents/sample_uploaded_documents/
│                                        # (copy or symlink at repo init)
└── docs/
    ├── PLAN.md                 # this document
    └── CLASSIFIER_RULES.md
```

---

## 13. Project-wide constants

```
PAGE_REGION_MAP = {
  01: "upper-left",
  02: "upper-right",
  03: "lower-left",
  04: "lower-right",
}

LABEL_SEARCH_PX        = 2.0 × bbox_diagonal
PAIR_PROXIMITY_MM      = 50
DEDUPE_TOL_MM          = 50
ASPECT_TOL             = 0.15
ROUND_ASPECT_LO        = 0.85
ROUND_ASPECT_HI        = 1.15
TYPE_DIM_TOL_MM        = 5            # type resolver match tolerance
LEVEL_AGREEMENT_TOL_MM = 25           # cross-elevation level agreement

CLASSIFIER_LLM_PRIMARY_MODEL = "aisingapore/Gemma-SEA-LION-v4-4B-VL:latest"  # §5.4 primary
CLASSIFIER_LLM_CHECKER_MODEL = "qwen3-vl:latest"                              # §5.4 checker
CLASSIFIER_LLM_THUMBPX       = 512    # rendered thumbnail size for vision input
CLASSIFIER_LLM_CONF_MIN      = 0.7    # accept threshold (single-model fallback only;
                                      # primary+checker uses agreement, not threshold)

TYPE_CODE_RE      = r"^(H-)?[A-Z]{1,3}\d+$"
SECTION_RE        = r"^\d{3,4}\s*[xX]\s*\d{3,4}$"
DIA_RE            = r"^[ØøD]\s*\d{3,4}$|^\d{3,4}\s*(?:DIA|dia|Ø|ø)$"
LEVEL_NAME_RE     = r"^(B\d|L\d+|RF|UR|MEZZ|GF|GL)\b"
RL_RE             = r"[+\-]?\d+(?:\.\d+)?\s*(?:mm|m)?"
```

---

## 14. Implementation order

1. **Skeleton** — repo scaffold, `meta.yaml` schema, multi-file upload UI, ingest with page-fingerprinting.
2. **Classifier** — filename + title-block + content-heuristic; manual-rule UI; per-project rule cache. Verify on full 81-file sample (must produce 14 OVERALL, 56 ENLARGED, 5 ELEVATION, 4 SECTION, 2 DISCARD; perspectives must not leak).
3. **Probe scripts** — one per extractor, before any parser code. Walk `~/Documents/sample_uploaded_documents/` (§3.1), enumerate label/dim forms across all 56 STRUCT_PLAN_ENLARGED pages, all 5 ELEVATION pages, all 4 SECTION pages. Validate regex coverage. Produce raw catalog `{label → set of dim strings}`.
4. **Extractor 3A-1 STRUCT_PLAN_OVERALL** — grid + canonical positions (renovation of v4's grid pipeline).
5. **Extractor 3A-2 STRUCT_PLAN_ENLARGED** — shape-aware label associator (the v4 gap-fill).
6. **Extractor 3B ELEVATION** — RL only.
7. **Extractor 3C SECTION** — slab thickness + beam depth. **Deferred for v5.3** (see `docs/PROBE_FINDINGS.md` §3C): the reference fixture's architectural sections carry no machine-readable slab/beam annotations. The extractor parses `section_id` from the filename and emits empty joints; Stage 5B falls back to `meta.yaml.slabs.default_thickness_mm` and flags every slab for the review queue. The immediate v5 priority is column W×H orientation in §3A-2.
8. **Reconciler** — cross-link OVERALL ↔ ENLARGED, strict merge, conflict policy.
9. **Type Resolver + Family Manager (5A)** — must land before any Revit emission.
10. **Geometry Emitter (5B)** — pyRevit script consuming Stage 5A plan; RVT (Revit 2023) + GLTF; strict-mode gates; fail-loud on missing inputs.

— **Milestone A reachable here** —

11. **Frontend review queue** — surface conflicts, unresolved columns, missing-coverage flags, classifier-DISCARD log, with clear UX for resolution.

No tier 2 / tier 3 work in v5.3.

---

## 15. Acceptance — Milestone A (the only milestone)

- Upload the reference sample — every PDF under `~/Documents/sample_uploaded_documents/` (81-file canonical subset; see §3.1 for the tree). Drag-drop as a flat list, or as the three-folder structure shipped — both must produce identical output.
- Pipeline runs without manual `meta.yaml.levels` editing — heights derived from elevations.
- Storey RVTs (Revit 2023) emit for every storey present in OVERALL pages (B3..UR), each with:
  - correctly-dimensioned columns at correct grid-mm positions
  - correct floor-to-floor heights from elevations
  - correct slab thickness per zone from sections
  - every placed column carrying the consultant label (`C2`, `H-C9`, …) in the Comments parameter
- `<storey>_typing.json` shows MATCHED_EXACT / MATCHED_LABEL / CREATED / REJECTED breakdown; CREATED > 0 only for shape/dim combinations not present in the starter family.
- Cross-page coordinate sanity: a column visible in `-00` and `-01` agrees within `DEDUPE_TOL_MM` after both affines are applied.
- Review queue lists every unresolvable item with raw data; zero silent coercion.

---

## 16. Risks

- **`-00` grid solve failure** — hard prerequisite. If `-00` bubbles can't be parsed, the storey fails. No fallback to enlarged-only because enlargements don't cover the whole storey footprint.
- **Cross-page coordinate agreement** — enlargements' affines must map into the same grid-mm as `-00`. Test: pick a column visible in both; positions must agree within `DEDUPE_TOL_MM`.
- **Architectural section/elevation parsing** — A-prefixed sheets may stylise differently from S-prefixed. Probe step on the four sample sections must enumerate slab/beam annotation forms.
- **Perspective false-positives** — keyword filter must catch `PERSPECTIVE` before the `ELEVATION` rule fires. Order of `classifier_rules` is load-bearing.
- **LLM classifier hallucination** — the §5.4 judge can confidently mis-bucket. Mitigations: (a) require `confidence ≥ CLASSIFIER_LLM_CONF_MIN`, (b) every LLM decision must be recorded in `_classification_report.json` with the prompt-input hash and the model's stated reason so a human can audit, (c) when the user overrides an LLM decision via the UI, save it as a filename rule so the same page never reaches the LLM tier again, (d) the four heuristic tiers always run first and short-circuit on confident hits — LLM is the catch-all, not the first responder.
- **Mixed-discipline elevations** — when both A-130 and an S-1xx elevation are uploaded, both feed the same level extractor; reconciler must agree (within `LEVEL_AGREEMENT_TOL_MM`) or flag.
- **Diameter notation variance** — Ø800, D800, 800Ø, 800 DIA, 800 dia, sometimes a bare 800 next to a circular bbox. Probe must enumerate every form before final regex.
- **L/T sections** — defer until probe confirms they appear; flag in review queue otherwise.
- **Leader-line pairing** — when two columns are close, the wrong span may pair to a column. Mitigation: prefer spans whose leader-line endpoint (if extractable) lies inside the bbox; otherwise nearest-distance.
- **Rotated text** — some labels are vertical. Verify `get_text("dict")` returns rotation; rotate bbox before regex search if so.
- **Page-overlap regions** — the four enlargements overlap slightly. Dedupe at 50 mm should handle it; test at the boundary explicitly.
- **Single-shot model** — if consultant ships a corrected sheet later, the user reruns the whole job. Acceptable for v5.3.
- **Type Resolver execution context** — Stage 5A produces JSON; Stage 5B runs in pyRevit. Decide upfront whether the user runs the pyRevit script manually or the backend launches it via headless Revit.
- **Revit 2023 family compatibility** — starter `.rfa` files must be saved as Revit 2023 format. Stage 5B fails on newer-version families.
- **Backwards compat with v4** — none. v5 is a fresh repo; old projects stay on v4.

---

## 17. Out of scope (v5.3)

- Schedules (column / beam reinforcement)
- ARCH plans (walls, doors, windows, rooms)
- ARCH elevations / sections as deliverables (elevation/section views are accepted as inputs for level RL and slab depth only)
- MEP drawings (mechanical, electrical, plumbing)
- Detail drawings (S-7xx connection details)
- Foundation drawings (S-100 series — sample doesn't contain them; bottom-up modelling stops at the lowest framing plan)
- DWG/DXF input (PDF only)
- IFC import as alternative to PDF
- OCR (vector text only; image-only fallback deferred)
- L/T column shapes (deferred; flagged in review queue if encountered)
- Page-`00` column-label re-detection (`-00` is position-only)
- Column continuity from elevations (deferred from 3B)
- Multi-job project workspace, batched / incremental uploads
- Real-time collaborative `meta.yaml` editing
- Automatic IFC export

---

## 18. Differences vs Plan v5 (the original)

|                       | v5                                                    | v5.3                                                             |
|-----------------------|-------------------------------------------------------|------------------------------------------------------------------|
| Input                 | zip, batched deliveries, persistent project workspace | loose multi-file upload, one-shot                                |
| Drawing classes kept  | many (Tier 1+2+3)                                     | four: OVERALL, ENLARGED, ELEVATION, SECTION                      |
| Tier gating           | yes                                                   | dropped — four classes, all required                             |
| Schedule extractor    | Tier 2                                                | dropped (out of scope)                                           |
| ARCH_PLAN extractor   | Tier 2                                                | dropped                                                          |
| Workspace state       | persistent across uploads                             | none — single-shot                                               |
| Perspective filtering | not addressed                                         | explicit DISCARD by keyword                                      |
| A- vs S- prefix       | classifier gated on S-                                | gated on drawing kind (section/elevation), not discipline letter |
| `-00` overall plan    | DISCARD (mistake in v5.1)                             | STRUCT_PLAN_OVERALL — grid + position truth                      |
| `PAGE_REGION_MAP`     | 01=UR, 02=UL, 03=LL, 04=LR                            | 01=UL, 02=UR, 03=LL, 04=LR                                       |
| `meta.yaml`           | extensive                                             | slimmed                                                          |

Stages 5A (type resolver, ±5 mm tolerance, auto-duplicate, audit trail, Comments parameter) and 5B (Revit 2023 emitter, hard gates) are unchanged from the original v5.
