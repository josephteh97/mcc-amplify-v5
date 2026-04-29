● Plan v5: General Drawing-Set BIM Pipeline                                                                                                                                                                      
                                  
  A comprehensive plan for a fresh repo. The system ingests whatever the consultant ships, classifies it, extracts what each drawing type can offer, accumulates the data into a per-project state, resolves     
  Revit types (creating new ones as needed), and emits RVT + GLTF on demand. Target: Autodesk Revit 2023. Strict-mode and fail-loud throughout.
                                                                                                                                                                                                                 
  ---             
  1. Vision
           
  Previous versions assumed one PDF in → one BIM out. That breaks because consultants:
  - Ship deliveries in batches by drawing series (this week ARCH, next week STRUCT, never an elevation in the same zip)                                                                                          
  - Use inconsistent file naming                                                                                                                                                                                 
  - Refuse to clean up annotations or complete schedules                                                                                                                                                         
  - Cannot be asked for better data (downstream payer dynamic)                                                                                                                                                   
                                                                                                                                                                                                                 
  v5 must be tolerant of mess on input, strict on output. The pipeline accumulates what arrives, fills gaps from a manual meta.yaml, refuses to emit a half-baked RVT, and never coerces data silently.          
                                                                                                                                                                                                                 
  ---                                                                                                                                                                                                            
  2. Top-Level Architecture                                                                                                                                                                                      
                  
  ┌──────────────────────────────────────────────────────────────────────┐
  │  Project Workspace (persistent, per-project)                         │
  │  ├── inbox/         — raw uploads, unmodified                         │                                                                                                                                      
  │  ├── classified/    — same files, sorted by detected series           │                                                                                                                                      
  │  ├── extracted/     — extractor outputs, JSON-per-page                │                                                                                                                                      
  │  ├── meta.yaml      — manual overrides + extracted-value cache        │                                                                                                                                      
  │  └── catalog.json   — accumulated type catalog (columns, beams, …)    │                                                                                                                                      
  └──────────────────────────────────────────────────────────────────────┘                                                                                                                                       
         ↑                                                       ↓                                                                                                                                               
    upload zip                                              build RVT + GLTF                                                                                                                                     
         ↓                                                       ↑                                                                                                                                               
  ┌─────────────┐  ┌─────────────┐  ┌──────────────────┐  ┌──────────────────┐                                                                                                                                   
  │ 1. Ingest   │→ │ 2. Classify │→ │ 3. Extract       │→ │ 4. Reconcile     │                                                                                                                                   
  │ unzip, walk │  │ filename +  │  │ tier-gated:      │  │ merge in grid-mm,│                                                                                                                                   
  │ list PDFs   │  │ title block │  │ T1 plan/elev/sec │  │ strict conflicts │                                                                                                                                   
  └─────────────┘  └─────────────┘  └──────────────────┘  └──────────────────┘                                                                                                                                   
                                                                ↓                                                                                                                                                
                              ┌─────────────────────────────────────────┐                                                                                                                                        
                              │ 5A. Type Resolver + Revit Family Manager│                                                                                                                                        
                              │ match / duplicate types, never coerce   │                                                                                                                                        
                              └─────────────────────────────────────────┘                                                                                                                                        
                                                                ↓                                                                                                                                                
                              ┌─────────────────────────────────────────┐                                                                                                                                        
                              │ 5B. Geometry Emitter                     │                                                                                                                                       
                              │ RVT (Revit 2023) + GLTF per storey       │
                              └─────────────────────────────────────────┘                                                                                                                                        
                  
  ---                                                                                                                                                                                                            
  3. Stages       

  Stage 1 — Ingest

  - Accept .zip (primary) or loose PDFs (fallback).                                                                                                                                                              
  - Unpack to project's inbox/. Preserve original paths.
  - Walk recursively. Skip __MACOSX, .DS_Store, Thumbs.db. Recurse into nested folders.                                                                                                                          
  - For each PDF, hash-fingerprint to detect re-uploads (use latest by mtime, archive prior versions).                                                                                                           
  - Output: list of (pdf_path, page_count) ready for classification.                                                                                                                                             
                                                                                                                                                                                                                 
  Stage 2 — Classify                                                                                                                                                                                             
                                                                                                                                                                                                                 
  Each PDF page is tagged with a content type:                                                                                                                                                                   
  - STRUCT_PLAN_OVERALL (e.g. …-S-200-…-00)
  - STRUCT_PLAN_ENLARGED (e.g. …-S-200-…-01..04)                                                                                                                                                                 
  - STRUCT_ELEVATION (e.g. …-S-1xx-…)           
  - STRUCT_SECTION (e.g. …-S-4xx-…)                                                                                                                                                                              
  - STRUCT_SCHEDULE (e.g. …-S-5xx/6xx-…)                                                                                                                                                                         
  - ARCH_PLAN, ARCH_ELEVATION, ARCH_SECTION                                                                                                                                                                      
  - UNKNOWN → user-classifies via UI; rule remembered for future uploads.                                                                                                                                        
                                                                                                                                                                                                                 
  Classifier signals, in order of confidence:                                                                                                                                                                    
  1. Filename pattern — regex over the consultant's known prefix conventions (per-project config).                                                                                                               
  2. Title-block parse — pymupdf text extraction at the page's bottom-right region; sheet number + drawing title.                                                                                                
  3. Content heuristic — sample vector content (vertical level lines = elevation; horizontal floor pattern = plan).                                                                                              
                                                                                                                                                                                                                 
  Output: classified/<content_type>/<original_path> symlinks + classification.json recording the decision and confidence.                                                                                        
                                                                                                                                                                                                                 
  Stage 2.5 — Tier Gate (orchestration)                                                                                                                                                                          
                                                                                                                                                                                                                 
  The scanner runs Tier 1 extractors before Tier 2/3 so the user gets a usable RVT as fast as possible.                                                                                                          
                  
  Tier 1 — Required for first RVT (Revit 2023)                                                                                                                                                                   
                  
  ┌──────────────────────┬────────────────────────────────────────────┬────────────────────────────────┐                                                                                                         
  │     Content type     │              What we extract               │              Why               │
  ├──────────────────────┼────────────────────────────────────────────┼────────────────────────────────┤
  │ STRUCT_PLAN_ENLARGED │ columns, beams, slabs (3A in full)         │ Geometry source of truth       │
  ├──────────────────────┼────────────────────────────────────────────┼────────────────────────────────┤
  │ STRUCT_ELEVATION     │ level names + RL only (3B reduced)         │ Floor-to-floor heights         │                                                                                                         
  ├──────────────────────┼────────────────────────────────────────────┼────────────────────────────────┤                                                                                                         
  │ STRUCT_SECTION       │ slab thickness + beam depth at joints (3C) │ Vertical dimension correctness │                                                                                                         
  └──────────────────────┴────────────────────────────────────────────┴────────────────────────────────┘                                                                                                         
                  
  Tier 2 — Refinement (run if present, never block)                                                                                                                                                              
   
  - STRUCT_SCHEDULE (3D) — type catalog cross-check                                                                                                                                                              
  - ARCH_PLAN (3E) — walls, openings, rooms
                                                                                                                                                                                                                 
  Tier 3 — Deferred / stubbed
                                                                                                                                                                                                                 
  - ARCH_ELEVATION, ARCH_SECTION — stub extractors
  - Detail drawings (S-7xx)
  - MEP drawings                                                                                                                                                                                                 
   
  Orchestration logic                                                                                                                                                                                            
                  
  For each Tier-1 content type required by an output target (storey RVT):
    if present in classified/  → run extractor immediately                                                                                                                                                       
    if absent                  → check meta.yaml override
                               → if absent there too → fail Tier-1 gate, stop                                                                                                                                    
                                                                                                                                                                                                                 
  After all Tier-1 extractors complete, RVT generation can run.
  Tier-2 extractors run in background; results refine the RVT on next emission.                                                                                                                                  
                  
  The user can upload a partial set and still get an RVT, as long as STRUCT_PLAN_ENLARGED + STRUCT_ELEVATION + STRUCT_SECTION are all available (or substituted via meta.yaml). The scanner doesn't waste time   
  parsing schedules or arch plans on the critical path.
                                                                                                                                                                                                                 
  Stage 3 — Extract (per content type)                                                                                                                                                                           
   
  Each content type has its own extractor, each emitting JSON to extracted/<page_id>.json. All extractors emit data in building grid-mm coordinates so downstream stages don't care which view it came from.     
                  
  3A. STRUCT_PLAN_ENLARGED (Tier 1 — port of v4)                                                                                                                                                                 
                  
  For pages 01..04 per storey (parallelizable):                                                                                                                                                                  
                  
  - Grid extract — produces grid lines + labeled intersections.                                                                                                                                                  
  - Pixel→grid-mm transform — solve 2D affine from labeled intersections; reject pages where solution residual > 1 px.
  - YOLO detection — column + framing agents (column model imgsz=1280, framing imgsz=640).                                                                                                                       
  - Vector text extraction — page.get_text("dict") → list of {text, bbox_px, rotation}.                                                                                                                          
  - Column-label associator (shape-aware) — for each column bbox:                                                                                                                                                
    - search window = 2× bbox diagonal                                                                                                                                                                           
    - find type-code span: ^(H-)?[A-Z]{1,3}\d+$ → C2, H-C9                                                                                                                                                       
    - find dim span, in order:                                                                                                                                                                                   
  a. rectangular/square \d+x\d+                                                                                                                                                                                  
  b. round [ØøD]\s*\d+ / \d+\s*(?:DIA|dia|Ø|ø)                                                                                                                                                                   
  c. L/T 4-number form → flag + skip (deferred)                                                                                                                                                                  
    - pair type-code ↔ dim within PAIR_PROXIMITY_MM = 50                                                                                                                                                         
    - classify shape from what was matched, not from the bbox:                                                                                                                                                   
        - \d+x\d+ equal → square(s_mm)                                                                                                                                                                           
      - \d+x\d+ unequal → rectangular(dim_x, dim_y), resolve order via bbox aspect (larger dim → longer bbox axis)                                                                                               
      - diameter → round(d_mm)                                                                                                                                                                                   
    - bbox sanity check by shape:                                                                                                                                                                                
        - round: bbox aspect ∈ [0.85, 1.15]; flag otherwise                                                                                                                                                      
      - square: bbox aspect ≈ 1.0; flag otherwise                                                                                                                                                                
      - rectangular: bbox aspect vs annotation aspect within ASPECT_TOL = 0.15; flag otherwise                                                                                                                   
    - unlabeled column → emit label=None, shape="unknown", dims=None, flag for review.                                                                                                                           
  - Recipe sanitizer (ported from v4).                                                                                                                                                                           
  - Emit per page:                                                                                                                                                                                               
  {                                                                                                                                                                                                              
    type:           "column",                                                                                                                                                                                    
    label:          "C2" | "H-C9" | None,                                                                                                                                                                        
    shape:          "rectangular" | "square" | "round" | "unknown",                                                                                                                                              
    dim_along_x_mm: float | None,                                                                                                                                                                                
    dim_along_y_mm: float | None,                                                                                                                                                                                
    diameter_mm:    float | None,                                                                                                                                                                                
    grid_mm_xy:     [x, y],                                                                                                                                                                                      
    page_id:        int,                                                                                                                                                                                         
    flags:          [...]
  }                                                                                                                                                                                                              
  - Same machinery for beams with beam regex (RCB\d+, H-RCB\d+, etc.).
                                                                                                                                                                                                                 
  Project-level constants:
  PAGE_REGION_MAP   = {01: "upper-right", 02: "upper-left",                                                                                                                                                      
                       03: "lower-left",  04: "lower-right"}
  LABEL_SEARCH_PX   = 2.0 × bbox_diagonal                   
  PAIR_PROXIMITY_MM = 50                 
  DEDUPE_TOL_MM     = 50
  ASPECT_TOL        = 0.15
  ROUND_ASPECT_LO   = 0.85
  ROUND_ASPECT_HI   = 1.15

  3B. STRUCT_ELEVATION (Tier 1 — reduced scope, RL only)                                                                                                                                                         
   
  For v5-Tier-1, the elevation extractor only produces floor-to-floor heights. Column continuity is deferred.                                                                                                    
                  
  Detect horizontal level lines (long horizontal lines spanning building width)                                                                                                                                  
  For each level line:
    find the nearest text span matching:                                                                                                                                                                         
      name:   ^(B\d|L\d+|RF|UR|MEZZ)\b
      rl_mm:  signed number with optional units (+9.500, -3000, +12.500 SFL)                                                                                                                                     
    emit {name, rl_mm}
  Sort by rl_mm; floor-to-floor[i] = rl[i+1] - rl[i]                                                                                                                                                             
  Emit only: {levels: [{name, rl_mm}, ...]}                                                                                                                                                                      
   
  3C. STRUCT_SECTION (Tier 1)                                                                                                                                                                                    
                  
  Joint and depth information from section views.                                                                                                                                                                
  - Locate the section cut symbol (matches a label like "Section A-A" on the plan)
  - Detect slab/beam cross-sections at each level                                                                                                                                                                
  - Extract slab thickness and beam depth at junctions, with (grid_mm, level_name) reference
  - Emit: {section_id, joints: [{grid_xy, level, slab_thk_mm, beam_depth_mm}], ...}                                                                                                                              
                                                                                                                                                                                                                 
  3D. STRUCT_SCHEDULE (Tier 2 — best-effort)                                                                                                                                                                     
                                                                                                                                                                                                                 
  - Table detection (vector lines forming a grid)                                                                                                                                                                
  - Per-row text extraction → {label, shape, dim_x_mm, dim_y_mm/dia_mm, reinforcement}
  - Emit: {schedule_type: "column"|"beam", entries: [...]}                                                                                                                                                       
  - Tolerate missing rows; don't fail when consultant left blanks.                                                                                                                                               
                                                                                                                                                                                                                 
  3E. ARCH_PLAN (Tier 2)                                                                                                                                                                                         
                                                                                                                                                                                                                 
  - Wall detection (line pairs at consistent offset)                                                                                                                                                             
  - Door/window block detection (YOLO or symbol matching)
  - Room boundary extraction (closed polygons + room-name text)                                                                                                                                                  
  - Emit: {walls: [...], openings: [...], rooms: [...]} in grid-mm                                                                                                                                               
                                                                                                                                                                                                                 
  3F. ARCH_ELEVATION / ARCH_SECTION (Tier 3 — stubs)                                                                                                                                                             
                  
  - Emit {} and flag the page as "manual-review".                                                                                                                                                                
                  
  Stage 4 — Reconcile                                                                                                                                                                                            
                  
  Combine outputs from every extractor into a single per-storey + per-project model.                                                                                                                             
   
  Per storey:                                                                                                                                                                                                    
  - Concatenate columns/beams from all four enlarged structural pages.
  - Dedupe in grid-mm at DEDUPE_TOL_MM = 50.                                                                                                                                                                     
  - Strict conflict resolution: keep all distinct (label, shape, dims) tuples, flag conflicts.
  - Schedule cross-check (Tier 2): if schedule says C2 = 800x800 and the plan shows an unlabeled bbox ≈ 800×800, don't auto-link. Emit both records; review queue decides.                                       
                                                                                                                                                                                                                 
  Per project:                                                                                                                                                                                                   
  - Levels from elevation drawings → meta.yaml.levels[name].rl_mm = ...; source = elev:…                                                                                                                         
  - Slab thicknesses from section drawings → meta.yaml.slabs[zone].thickness_mm = ...                                                                                                                            
  - Override precedence (highest first): manual meta.yaml override > extracted from drawings > none (fail).
                                                                                                                                                                                                                 
  Stage 5A — Type Resolver + Revit Family Manager                                                                                                                                                                
                                                                                                                                                                                                                 
  Goal: for every column/beam in the storey model, decide which Revit family type to place. If no existing type matches, create one via Edit Type / Duplicate. Strict on dimensions, tolerant on label.          
                  
  Inputs:                                                                                                                                                                                                        
  - Reconciled storey columns: [{label, shape, dim_along_x_mm, dim_along_y_mm, diameter_mm, grid_mm_xy, flags}, ...]
  - Loaded Revit family inventory (scanned at session start)                                                                                                                                                     
                                                            
  Family inventory build (one-time per session):                                                                                                                                                                 
  For each loaded family in the Revit doc:                                                                                                                                                                       
    For each Type within the family:                                                                                                                                                                             
      parse type name → (shape, dims) using project rules                                                                                                                                                        
      record {family_name, type_name, shape, dims, type_id}                                                                                                                                                      
  Build index keyed by (shape, dims) and by label.                                                                                                                                                               
                                                                                                                                                                                                                 
  Matching algorithm (per column, in order; first hit wins):                                                                                                                                                     
  1. Exact dims match. Same shape + dims within ±5 mm → use that type.                                                                                                                                           
  2. Label-only match. No dim match, but a Revit type with the same label code exists AND its dims agree with the plan within ±5 mm → use it.                                                                    
  3. Auto-duplicate. No match. Create a new Revit type:                                                                                                                                                          
    - pick the family by shape (Concrete-Rectangular-Column, Concrete-Round-Column)                                                                                                                              
    - duplicate any existing type as a base                                                                                                                                                                      
    - new type name (canonical): <label>_<shape_code>_<dims> → C2_R_1150x800, C5_RD_800                                                                                                                          
    - set the family's dimension parameters (b, h for rect; d for round)                                                                                                                                         
    - register the new type in the inventory cache so subsequent columns of the same (shape, dims) reuse it                                                                                                      
  4. Reject. Shape is unknown OR dims are None OR shape is L/T (deferred). Skip placement; add to review queue.                                                                                                  
                                                                                                                                                                                                                 
  Match-tolerance rules:                                                                                                                                                                                         
  - Dimensions: ±5 mm strict. 1150x800 ≠ 800x1150 for placement (orientation already resolved in Stage 3A).                                                                                                      
  - Label: case-insensitive, whitespace-stripped. H-C9 and H-C9 match; C9 and H-C9 do not.                                                                                                                       
  - Shape: exact match only. Round never auto-substitutes for square.                     
                                                                                                                                                                                                                 
  No-fuzzy-match rule:                                                                                                                                                                                           
  - Never silently round 1150 to 1200 to fit an existing type.                                                                                                                                                   
  - Never substitute C2 800x800 with a "close enough" C3 800x800.                                                                                                                                                
  - Either match exactly, duplicate-and-create, or reject. No middle ground.                                                                                                                                     
                                                                                                                                                                                                                 
  Per-column placement payload:                                                                                                                                                                                  
  {                                                                                                                                                                                                              
    grid_mm_xy:    [x, y],                                                                                                                                                                                       
    type_id:       <Revit type id>,                                                                                                                                                                              
    type_name:     "C2_R_1150x800",
    rotation_deg:  0 | 90 | …,            # from bbox orientation
    comments:      "C2",                  # original consultant label, written to instance Comments                                                                                                              
    source_label:  "C2",                                                                                                                                                                                         
    source_dims:   { x: 1150, y: 800 } | { d: 800 },                                                                                                                                                             
    flags:         [...]                  # carried through from Stage 4                                                                                                                                         
  }                                                                                                                                                                                                              
                  
  The original consultant label always goes into the instance's Comments parameter, preserving a trace from the placed Revit element back to the drawing annotation.                                             
                                                                                                                                                                                                                 
  Audit trail: for every column, log one of:                                                                                                                                                                     
  - MATCHED_EXACT(family, type)                                                                                                                                                                                  
  - MATCHED_LABEL(family, type, dim_delta_mm)                                                                                                                                                                    
  - CREATED(family, new_type)                
  - REJECTED(reason)                                                                                                                                                                                             
                                                                                                                                                                                                                 
  Output: output/<storey>_typing.json.                                                                                                                                                                           
                                                                                                                                                                                                                 
  Stage 5B — Geometry Emitter (Revit 2023 target)
                                                                                                                                                                                                                 
  - Receives placement payloads from Stage 5A.                                                                                                                                                                   
  - Places each instance at grid_mm_xy with the resolved type_id, applying rotation_deg.
  - Writes comments to the instance.                                                                                                                                                                             
  - Vertical extents come from meta.yaml.levels for the storey (base level → top level).                                                                                                                         
  - Slab thicknesses per zone come from meta.yaml.slabs.                                                                                                                                                         
  - Same flow for beams once the beam typer is built.                                                                                                                                                            
                                                                                                                                                                                                                 
  Hard-required gates (don't emit if missing):                                                                                                                                                                   
  - structural plan for the storey                                                                                                                                                                               
  - floor-to-floor height for the storey                                                                                                                                                                         
  - starter Revit family for every shape encountered (Revit 2023-compatible)
                                                                                                                                                                                                                 
  Soft-required gates (emit, but flag in report):                                                                                                                                                                
  - completed column schedule                                                                                                                                                                                    
  - section drawings for the zone                                                                                                                                                                                
  - architectural plan                                                                                                                                                                                           
                                                                                                                                                                                                                 
  Output: output/<storey>.rvt (Revit 2023 format), output/<storey>.gltf, output/<storey>_review.json, output/<storey>_typing.json.
                                                                                                                                                                                                                 
  ---             
  4. Project State (meta.yaml)                                                                                                                                                                                   
                                                                                                                                                                                                                 
  Single source of truth for human-overridable values. Auto-populated by extractors; user edits override.
                                                                                                                                                                                                                 
  project:        
    id: TGCH                                                                                                                                                                                                     
    consultant_prefix_rules:                                                                                                                                                                                     
      - pattern: "^TD-A-110-"                                                                                                                                                                                    
        type: ARCH_PLAN                                                                                                                                                                                          
      - pattern: "^TD-S-200-.*-0[1-4]$"                                                                                                                                                                          
        type: STRUCT_PLAN_ENLARGED                                                                                                                                                                               
      - pattern: "^TD-S-200-.*-00$"                                                                                                                                                                              
        type: STRUCT_PLAN_OVERALL                                                                                                                                                                                
      # …                                                                                                                                                                                                        
                  
  target:                                                                                                                                                                                                        
    revit_version: 2023
                                                                                                                                                                                                                 
  families:
    column:                                                                                                                                                                                                      
      rectangular: "Concrete-Rectangular-Column"
      square:      "Concrete-Rectangular-Column"   # square is rect with b == h
      round:       "Concrete-Round-Column"                                                                                                                                                                       
    beam:
      rectangular: "Concrete-Rectangular-Beam"                                                                                                                                                                   
                                                                                                                                                                                                                 
  levels:
    B3: { rl_mm: -9000,  source: manual }                                                                                                                                                                        
    B2: { rl_mm: -6000,  source: manual }                                                                                                                                                                        
    B1: { rl_mm: -3000,  source: manual }
    L1: { rl_mm:  0,     source: manual }                                                                                                                                                                        
    L2: { rl_mm:  4500,  source: "elev:TD-S-110-001.pdf" }                                                                                                                                                       
    L3: { rl_mm:  8100,  source: "elev:TD-S-110-001.pdf" }                                                                                                                                                       
    # …                                                                                                                                                                                                          
                                                                                                                                                                                                                 
  slabs:                                                                                                                                                                                                         
    default_thickness_mm: 200
    zones:                                                                                                                                                                                                       
      L3_tower_A: { thickness_mm: 250, source: "section:A-A" }
                                                                                                                                                                                                                 
  review:         
    unresolved_columns: []                                                                                                                                                                                       
    conflicts: [] 
                                                                                                                                                                                                                 
  ---
  5. Strict-Mode Policy (project-wide)                                                                                                                                                                           
                                                                                                                                                                                                                 
  ┌────────────────────────────────────────────────┬────────────────────────────────────────────────────────────────────────────────────┐
  │                   Situation                    │                                       Action                                       │                                                                        
  ├────────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────┤
  │ Required input missing                         │ Fail with actionable message                                                       │
  ├────────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────┤
  │ Two extractions disagree                       │ Keep all candidates, flag conflict, emit both as distinct types                    │                                                                        
  ├────────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────┤                                                                        
  │ Annotation ambiguous (e.g. dim order)          │ Resolve via geometric ground truth (bbox aspect); flag if disagreement > tolerance │                                                                        
  ├────────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────┤                                                                        
  │ Schedule says X but plan shows Y               │ Emit both, don't reconcile silently                                                │
  ├────────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────┤                                                                        
  │ File can't be classified                       │ UI prompt; remember user's decision as a new rule                                  │
  ├────────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────┤                                                                        
  │ Elevation/section absent                       │ Use meta.yaml; if absent there too, fail                                           │
  ├────────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────┤                                                                        
  │ Plan dims don't match any loaded Revit type    │ Auto-duplicate, new type named canonically                                         │
  ├────────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────┤                                                                        
  │ Plan dims close-but-not-equal to existing type │ Auto-duplicate, do not snap to existing                                            │
  ├────────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────┤                                                                        
  │ Starter family for a shape is missing          │ Fail with message "load <family.rfa>"                                              │
  ├────────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────┤                                                                        
  │ Loaded family compiled in newer Revit version  │ Fail with version-mismatch message                                                 │
  ├────────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────┤                                                                        
  │ Column has shape unknown / L-T / no dims       │ Reject placement, add to review queue                                              │
  └────────────────────────────────────────────────┴────────────────────────────────────────────────────────────────────────────────────┘                                                                        
                  
  Never coerce. Never silently default.                                                                                                                                                                          
                  
  ---                                                                                                                                                                                                            
  6. Repo Layout  

  mcc-amplify-v5/
  ├── backend/                                                                                                                                                                                                   
  │   ├── ingest/                  # Stage 1
  │   ├── classify/                # Stage 2                                                                                                                                                                     
  │   ├── extract/
  │   │   ├── struct_plan/         # 3A — Tier 1 (port of v4)                                                                                                                                                    
  │   │   ├── struct_elev/         # 3B — Tier 1 (RL only)
  │   │   ├── struct_section/      # 3C — Tier 1                                                                                                                                                                 
  │   │   ├── struct_schedule/     # 3D — Tier 2
  │   │   └── arch_plan/           # 3E — Tier 2                                                                                                                                                                 
  │   ├── reconcile/               # Stage 4
  │   ├── resolve/                 # Stage 5A — type resolver + family manager                                                                                                                                   
  │   ├── emit/   
  │   │   ├── revit/               # Stage 5B — Revit 2023 target                                                                                                                                                
  │   │   └── gltf/                # Stage 5B
  │   └── core/                                                                                                                                                                                                  
  │       ├── workspace.py         # project state I/O
  │       ├── grid_mm.py           # canonical coordinate space                                                                                                                                                  
  │       ├── meta_yaml.py                                                                                                                                                                                       
  │       ├── tier_gate.py         # Stage 2.5 — orchestration
  │       └── orchestrator.py                                                                                                                                                                                    
  ├── ml/         
  │   └── weights/                 # YOLO models                                                                                                                                                                 
  ├── frontend/                    # workspace UI: upload, classify, review queue
  ├── tests/                                                                                                                                                                                                     
  └── docs/       
      ├── PLAN.md                  # this document                                                                                                                                                               
      └── CLASSIFIER_RULES.md                                                                                                                                                                                    
                                                                                                                                                                                                                 
  ---                                                                                                                                                                                                            
  7. Implementation Order                                                                                                                                                                                        
                         
  1. Workspace skeleton — project dir, meta.yaml, ingest, file fingerprinting.
  2. Classifier — filename + title-block parsing; manual-rule UI; per-project rule cache.                                                                                                                        
  3. Probe — script that walks every enlarged page across L1..RF, prints raw catalog {label → set of dim strings} AND enumerates every dim-annotation form (incl. all diameter notations, any 4-number L/T       
  forms). Validates regex coverage before writing the associator.                                                                                                                                                
  4. Tier 1A: STRUCT_PLAN_ENLARGED extractor — shape-aware label associator; biggest single piece; first end-to-end smoke test.                                                                                  
  5. Tier 1B: STRUCT_ELEVATION extractor (reduced) — level-line + RL text → meta.yaml.levels.                                                                                                                    
  6. Tier 1C: STRUCT_SECTION extractor — slab thickness + beam depth at joints.                                                                                                                                  
  7. Reconciler — strict merge in grid-mm, conflict policy.                                                                                                                                                      
  8. Type Resolver + Revit Family Manager (Stage 5A) — must land before any Revit emission.                                                                                                                      
  9. Geometry Emitter (Stage 5B) — RVT (Revit 2023) + GLTF; strict-mode gates; fail-loud on missing inputs.                                                                                                      
  — Milestone A reachable here —                                                                                                                                                                                 
  10. Tier 2: STRUCT_SCHEDULE extractor — best-effort table parsing.                                                                                                                                             
  11. Tier 2: ARCH_PLAN extractor — walls, openings, rooms.                                                                                                                                                      
  12. Frontend review queue — surface conflicts, unlabeled detections, missing data with clear UX for resolution.                                                                                                
                                                                                                                                                                                                                 
  ---                                                                                                                                                                                                            
  8. Acceptance Targets                                                                                                                                                                                          
                       
  Milestone A — End-to-end Tier 1 (after step 9)
                                                                                                                                                                                                                 
  - Upload zip with structural plans + elevation + section.                                                                                                                                                      
  - No manual meta.yaml.levels editing required (heights come from elevation).                                                                                                                                   
  - Slab thickness per zone comes from section drawings (not the default).                                                                                                                                       
  - Pipeline emits L3 RVT (Revit 2023) with correctly-dimensioned columns, correct floor-to-floor heights, correct slab/beam depths at zoned junctions.                                                          
  - Storey catalog shows distinct types, no coerced merges.                                                                                                                                                      
  - Every placed column's Revit type has b/h (or d) matching the consultant's annotation within ±5 mm.                                                                                                           
  - <storey>_typing.json shows every column's decision; CREATED count > 0 only for shapes/dims not present in the starter family.                                                                                
  - Original consultant label (C2, H-C9) appears in the Comments parameter of every placed instance.                                                                                                             
  - Review queue lists every unresolvable entry with raw data.                                                                                                                                                   
                                                                                                                                                                                                                 
  Milestone B — Tier 2 refinement (after step 11)                                                                                                                                                                
                                                                                                                                                                                                                 
  - Schedule cross-check on type catalog.                                                                                                                                                                        
  - Arch plan walls/openings/rooms layered into the model.
  - Conflict log < 5% of detections.                                                                                                                                                                             
                                                                                                                                                                                                                 
  ---                                                                                                                                                                                                            
  9. What's Different from v4                                                                                                                                                                                    
                  
  ┌────────────────────────┬──────────────────────┬──────────────────────────────────────────────────────────┐
  │                        │          v4          │                            v5                            │
  ├────────────────────────┼──────────────────────┼──────────────────────────────────────────────────────────┤                                                                                                   
  │ Input                  │ one zip = one job    │ persistent project workspace, accumulates over uploads   │
  ├────────────────────────┼──────────────────────┼──────────────────────────────────────────────────────────┤                                                                                                   
  │ Drawing types handled  │ structural plan only │ classifier routes to per-type extractor; tier-gated      │                                                                                                   
  ├────────────────────────┼──────────────────────┼──────────────────────────────────────────────────────────┤                                                                                                   
  │ Floor-to-floor height  │ hardcoded / unknown  │ extracted from elevation OR meta.yaml                    │                                                                                                   
  ├────────────────────────┼──────────────────────┼──────────────────────────────────────────────────────────┤                                                                                                   
  │ Slab/beam depth        │ defaults             │ extracted from section OR meta.yaml                      │
  ├────────────────────────┼──────────────────────┼──────────────────────────────────────────────────────────┤                                                                                                   
  │ Missing data           │ silent default       │ fail-loud with actionable message                        │
  ├────────────────────────┼──────────────────────┼──────────────────────────────────────────────────────────┤                                                                                                   
  │ Type matching to Revit │ unspecified          │ dedicated Type Resolver with auto-duplicate, audit trail │
  ├────────────────────────┼──────────────────────┼──────────────────────────────────────────────────────────┤                                                                                                   
  │ Revit version target   │ implicit             │ explicit (Revit 2023)                                    │
  ├────────────────────────┼──────────────────────┼──────────────────────────────────────────────────────────┤                                                                                                   
  │ Conflict handling      │ already strict       │ unchanged                                                │
  └────────────────────────┴──────────────────────┴──────────────────────────────────────────────────────────┘                                                                                                   
   
  ---                                                                                                                                                                                                            
  10. Risks / Open Questions
                                                                                                                                                                                                                 
  - Classifier accuracy on first run — title block parsing varies wildly. Mitigation: per-project rule cache; user-confirms-once for ambiguous cases.
  - Elevation extractor (even reduced) — level-line detection across drawing styles is harder than column detection. Mirror v4's probe approach.                                                                 
  - Section drawings are highly stylised — slab/beam depth notation varies. Probe before parser.                                                                                                                 
  - Diameter notation varies wildly (Ø800, D800, 800Ø, 800 DIA, 800 dia, sometimes just 800 next to a circular bbox). Probe step must enumerate every form before finalising regex.                              
  - L/T sections — defer until probe confirms whether they appear; follow-up needed if so.                                                                                                                       
  - Leader-line pairing — when two columns are close, the wrong span may pair to a column. Mitigation: prefer spans whose leader-line endpoint (if extractable) lies inside the bbox; otherwise nearest-distance.
  - Rotated text — some labels are vertical. Verify get_text("dict") returns rotation; rotate bbox before regex search if so.                                                                                    
  - Page-overlap regions — the four enlargements overlap slightly. Dedupe at 50 mm should handle it; test at boundary explicitly.                                                                                
  - Workspace persistence — pick storage upfront (filesystem? SQLite metadata? object store?). Affects multi-user / multi-machine deployment.                                                                    
  - Re-uploads of revised drawings — pipeline must support replacing a sheet without invalidating extractions for unrelated sheets.                                                                              
  - Type Resolver inside Revit context — Stage 5A needs to run with a live Revit doc available (pyRevit / Revit API). Decide upfront whether 5A executes in-Revit or pre-builds a placement plan that an in-Revit
   script consumes.                                                                                                                                                                                              
  - Revit 2023 family compatibility — starter .rfa files must be saved as Revit 2023 format. Stage 5B fails on newer-version families.                                                                           
  - Backwards compat with v4 — none. v5 is a fresh start. Old projects stay on v4.                                                                                                                               
                                                                                                                                                                                                                 
  ---                                                                                                                                                                                                            
  11. Out of Scope (for now)                                                                                                                                                                                     
                                                                                                                                                                                                                 
  - MEP drawings (mechanical, electrical, plumbing)
  - Detail drawings (S-7xx connection details)                                                                                                                                                                   
  - DWG/DXF input (PDF only; DWG would simplify but requires licensed library or converter)                                                                                                                      
  - Real-time collaborative editing of meta.yaml                                                                                                                                                                 
  - Automatic IFC import as alternative to PDF (consultant doesn't ship IFC)                                                                                                                                     
  - L/T column shapes (deferred; flagged in review queue if encountered)                                                                                                                                         
  - ARCH_ELEVATION / ARCH_SECTION (Tier 3 — stubs only)                                                                                                                                                          
  - Page 00 re-detection (rendered for diagnostic only)                                                                                                                                                          
  - OCR (vector text only; image-only fallback deferred)                                                                                                                                                         
  - Column continuity from elevations (deferred from 3B; v5-Tier-1 elevation extracts RL only)                                                                                                                   
                                                                                                                                                                                                                 
  ---                                                                                                                                                                                                            
  This is the canonical Plan v5. Save as docs/PLAN.md in the new repo. Probe-driven development — every new extractor (column-label associator, elevation, section, schedule) starts with a probe script
  enumerating what the consultant actually does, before any parser code is written. Tier 1 (structural plan + elevation + section) is the critical path; everything else refines.  
