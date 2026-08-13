# lga_extractor/

## Purpose

A reusable OSM data extraction tool for any Nigerian Local Government
Area (LGA), not specific to Akure. This is the "acquisition layer"
of the two-repo project: it resolves an LGA's boundary, queries OSM
for relevant features, cleans/standardizes them while preserving
meaningful semantic detail, exports GeoJSON/Shapefile outputs, and
writes a formal manifest of exactly what was extracted, that
downstream analysis (the sibling `akure-accessibility-dashboard`
repo) reads as a contract rather than an assumption.

## Architecture

```
lga_extractor/
├── boundary.py        # resolves an LGA name to a boundary polygon (OSM geocoding or manual file),
│                       #   with hard + soft (admin_level/name) validation
├── layers.py           # queries OSM for each configured feature layer, staggered/retried,
│                        #   returns structured per-layer status, emits live progress events
├── clean.py              # standardizes CRS/geometry, deduplicates, keeps a curated semantic
│                          #   schema per layer + a full raw_tags JSON fallback
├── export.py               # writes GeoJSON (rich schema) + Shapefile (core schema only),
│                            #   handling mixed-geometry-type splitting
├── manifest.py               # reconciles query-time status + export outcome into manifest.json,
│                              #   the formal handoff contract for downstream consumers
├── events.py                   # UI-agnostic progress-event schema + ThreadSafeEventQueue,
│                                #   so the pipeline can report live progress without depending on any UI
├── logging_utils.py              # writes run_log.json (query config, warnings, package versions,
│                                  #   the same structured per-layer status as manifest.json)
├── pipeline.py                     # orchestrates all of the above into one extract_lga() call
└── visualize.py                      # optional kepler.gl HTML preview of extracted layers (visual only)
```

## Workflow

`pipeline.extract_lga(lga_name, state_name)` runs, in order, emitting
a progress event (see `events.py`) at each transition if an `on_event`
callback is supplied:

1. `boundary.resolve_boundary()`, get the LGA's geometry, hard- and
   soft-validated against plausible location/size/admin-level.
2. `layers.extract_layers()`, query OSM for each configured layer
   within that boundary, concurrently but staggered/capped, retrying
   transient failures with backoff, returning both the raw
   GeoDataFrames and a structured `_status` dict per layer
   (`"success"` / `"success_empty"` / `"failed"`, with attempt count
   and message).
3. `clean.clean_layers()`, reproject to the correct UTM zone, dedupe,
   repair invalid geometries, and standardize each layer's schema to
   its core columns plus a curated set of semantically meaningful OSM
   tags, plus a `raw_tags` JSON column preserving everything else.
4. `export.export_layers()`, write GeoJSON (full rich schema) and
   Shapefile (core schema only, to avoid DBF field-name truncation
   issues) to disk, splitting mixed-geometry layers automatically.
5. `manifest.build_manifest()` + `write_manifest()`, reconcile
   query-time status with export-time outcome (post-cleaning feature
   counts, file paths) into `manifest.json`, the file a downstream
   consumer should read instead of guessing.
6. `logging_utils.log_run()`, record what was queried, when, with what
   package versions, and the same structured per-layer status, into
   `run_log.json`.

`cli.py` and `app.py` (repo root) are two different ways to invoke this
same pipeline: a command-line interface and a Streamlit demo with a
live, per-stage progress checklist, respectively. Both, and any other
caller, can pass `extract_lga(..., on_event=...)` to receive the same
structured progress events the Streamlit demo uses, the pipeline
itself has no knowledge of Streamlit or any other UI.

## Inputs

An LGA name and state name (e.g. `"Akure North"`, `"Ondo"`). Optionally,
a manual boundary file if OSM's geocoding doesn't resolve the LGA well.

## Outputs

Per-LGA GeoJSON/Shapefile exports (roads, buildings, waterways, land
use, health facilities, schools), a `manifest.json` recording the
resolved CRS, boundary source, and per-layer query/export outcome, and
a `run_log.json` recording exactly what was extracted, when, and with
what package versions, for reproducibility and auditability.

## Notes

- `visualize.py`'s `keplergl` dependency is optional and imported
  lazily; the rest of the package works without it installed.

## Design notes & known limitations

- **Boundary validation is two-tier, deliberately.** `boundary._validate_and_standardize()`
  runs HARD checks (centroid inside Nigeria's bounding box; area
  between 2 km² and 10,000 km², catching "resolved a single
  point/building" and "resolved a whole state" respectively) that
  raise `BoundaryResolutionError`, plus SOFT checks — a `display_name`
  check (Nominatim's naming conventions vary, so this only warns) and
  an `admin_level`/`boundary=administrative` plausibility check against
  whatever metadata OSMnx's geocoding result carries, catching a
  resolved feature that's roughly the right place but the wrong *kind*
  of boundary. This is not a full verification against an independent,
  authoritative Nigerian administrative boundary dataset, that would be
  the most rigorous version, but requires a dependency this tool
  doesn't carry. It catches the failure modes most likely to actually
  occur. If a boundary passes these checks but still looks wrong,
  supply `manual_boundary_path` instead.
- **UTM zone is auto-selected per LGA, not hardcoded.** `clean.resolve_target_crs()`
  picks the correct zone (31N/32N/33N, covering all of Nigeria) from
  the resolved boundary's centroid longitude, falling back to
  `EPSG:32631` only when no boundary is available at all. The resolved
  CRS is recorded in every run's `manifest.json` and `run_log.json`
  under `target_crs`, so it's traceable, and readable directly by the
  sibling dashboard's `akure_access.data_contract` module, rather than
  assumed independently on that side. See `clean.utm_epsg_for_longitude()`
  and its tests for the exact logic and verification against known
  Nigerian reference points (Akure, Abuja, Maiduguri).
- **The exported schema is core-plus-semantic, not minimal.** Before
  cleaning, every layer only kept `osmid`/`name`/`geometry`, discarding
  every other OSM tag. `clean.SEMANTIC_COLUMNS` now preserves a curated,
  per-layer set of the tags most likely to be analytically useful (a
  road's `highway`/`surface`/`maxspeed`/etc., a health facility's
  `amenity`/`healthcare`/`beds`/etc.), and `clean.RAW_TAGS_COLUMN`
  additionally preserves every other original tag as a JSON string, so
  nothing is genuinely lost, even for tags this module didn't
  anticipate. `export.py` writes the full rich schema to GeoJSON, but
  deliberately reduces to the core schema only for Shapefile export
  (`export._shapefile_safe_columns()`), since DBF's 10-character field
  name limit would otherwise silently truncate or collide several of
  the longer semantic column names.
- **Every extraction produces a formal manifest, not just files.**
  `manifest.build_manifest()` combines `layers.py`'s query-time status
  per layer (queried successfully with N features / queried
  successfully and genuinely found zero / failed after retries, with
  the actual message) with `export.py`'s post-cleaning outcome (file
  paths, final feature counts) into one `manifest.json`. This exists
  specifically so a downstream consumer never has to infer "did this
  fail?" from an ambiguous empty GeoDataFrame or a missing file, the
  distinction is computed once, here, and carried through as
  structured, machine-readable data. See `manifest.py`'s module
  docstring for the full schema.
- **Strict vs. permissive extraction is configurable**, see
  `layers.extract_layers()`'s `strict` parameter. Permissive (default)
  catches genuine query failures, logs them as warnings, and continues
  with an empty layer, appropriate for demos/exploratory use. Strict
  raises `LayerExtractionError` immediately, appropriate for CI/
  automated pipelines where a silent failure could corrupt downstream
  analysis unnoticed. Either way, a layer that queries successfully
  but genuinely finds zero features is valid data, never a failure,
  reflected as `"success_empty"` in both the manifest and the run log.
- **Extraction progress is observable, without coupling the pipeline
  to any UI.** `events.py` defines a small, plain-dict event schema
  (`stage_started`, `retry`, `stage_completed`, `stage_failed`,
  `pipeline_completed`) that `layers.py`/`pipeline.py` emit via an
  optional `on_event` callback, and a `ThreadSafeEventQueue` helper for
  safely consuming those events from a UI thread while extraction runs
  concurrently on background threads. `app.py` is the reference
  consumer: a live, per-stage checklist with retry counters and a
  progress bar, but nothing in `lga_extractor/` itself imports
  Streamlit.
- **Mixed geometry types split automatically on Shapefile export.**
  Tag filters like `highway=*` legitimately match both line ways and
  point nodes (traffic signals, crossings) in OSM. GeoJSON handles
  this fine in one file; Shapefile cannot. `export.py` detects this and
  writes one Shapefile per geometry category (e.g. `roads_line.shp`,
  `roads_point.shp`) automatically, GeoJSON export is unaffected.
- **Boundary resolution quality depends on OSM's own tagging** for a
  given LGA; some LGAs may need a manual boundary supplied.
- **Feature completeness depends entirely on OSM contributor coverage.**
  This tool extracts what exists in OSM; it does not verify ground
  truth. (The sibling `akure-accessibility-dashboard` repo's
  completeness check and status fusion is what distinguishes "OSM
  hasn't mapped this yet" from "this area genuinely lacks the
  service.")

## Related

- [`akure-accessibility-dashboard`](https://github.com/Mapkathon2026-UseOSM/akure-accessibility-dashboard), the sibling repo that consumes
  this package's output — including `manifest.json`, via its own
  `akure_access.data_contract` module — for the Akure North/South
  accessibility analysis. Its `tests/test_cross_repo_integration.py`
  verifies this package's real output schema matches exactly what that
  repo's analysis functions expect.
