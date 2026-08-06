# lga_extractor/

## Purpose

A reusable OSM data extraction tool for any Nigerian Local Government
Area (LGA), not specific to Akure. This is the "acquisition layer"
of the two-repo project: it resolves an LGA's boundary, queries OSM
for relevant features, cleans/standardizes them, and exports
GeoJSON/Shapefile outputs that downstream analysis (the sibling
`akure-accessibility-dashboard` repo) consumes.

## Architecture

```
lga_extractor/
├── boundary.py        # resolves an LGA name to a boundary polygon (OSM geocoding or manual file)
├── layers.py           # queries OSM for each configured feature layer (roads, buildings, facilities, ...)
├── clean.py             # standardizes CRS/geometry/schema, deduplicates
├── export.py             # writes GeoJSON + Shapefile, handling mixed-geometry-type splitting
├── logging_utils.py       # writes a run_log.json (query config, warnings, package versions) for reproducibility
├── pipeline.py             # orchestrates the above into one extract_lga() call
└── visualize.py            # optional kepler.gl HTML preview of extracted layers (visual only, no analysis)
```

## Workflow

`pipeline.extract_lga(lga_name, state_name)` runs, in order:
1. `boundary.resolve_boundary()`, get the LGA's geometry
2. `layers.extract_layers()`, query OSM for each configured layer within that boundary
3. `clean.clean_layers()`, reproject, deduplicate, standardize schema
4. `export.export_layers()`, write GeoJSON/Shapefile to disk
5. `logging_utils.log_run()`, record what was queried, when, and with what package versions

`cli.py` and `app.py` (repo root) are two different ways to invoke this
same pipeline: a command-line interface and a Streamlit demo,
respectively.

## Inputs

An LGA name and state name (e.g. `"Akure North"`, `"Ondo"`). Optionally,
a manual boundary file if OSM's geocoding doesn't resolve the LGA well.

## Outputs

Per-LGA GeoJSON/Shapefile exports (roads, buildings, waterways, land
use, health facilities, schools) plus a `run_log.json` recording
exactly what was extracted, for reproducibility.

## Notes

- `visualize.py`'s `keplergl` dependency is optional and imported
  lazily; the rest of the package works without it installed.

## Design notes & known limitations

- **Boundary validation is two-tier, deliberately.** `boundary._validate_and_standardize()`
  runs HARD checks (centroid inside Nigeria's bounding box; area
  between 2 km² and 10,000 km², catching "resolved a single
  point/building" and "resolved a whole state" respectively) that
  raise `BoundaryResolutionError`, and one SOFT check (`display_name`
  mentions the requested LGA/state) that only warns, since Nominatim's
  naming conventions vary and aren't reliable enough to treat as
  authoritative. This is not a full `admin_level` verification against
  a reference Nigerian boundary dataset, that would be the most
  rigorous version, but requires a dependency this tool doesn't carry.
  It catches the failure modes most likely to actually occur. If a
  boundary passes these checks but still looks wrong, supply
  `manual_boundary_path` instead.
- **UTM zone is auto-selected per LGA, not hardcoded.** `clean.resolve_target_crs()`
  picks the correct zone (31N/32N/33N, covering all of Nigeria) from
  the resolved boundary's centroid longitude, falling back to
  `EPSG:32631` only when no boundary is available at all. The resolved
  CRS is recorded in every run's `run_log.json` under `target_crs`, so
  it's traceable, not assumed. See `clean.utm_epsg_for_longitude()` and
  its tests for the exact logic and verification against known
  Nigerian reference points (Akure, Abuja, Maiduguri).
- **Strict vs. permissive extraction is configurable**, see
  `layers.extract_layers()`'s `strict` parameter. Permissive (default)
  catches genuine query failures, logs them as warnings, and continues
  with an empty layer, appropriate for demos/exploratory use. Strict
  raises `LayerExtractionError` immediately, appropriate for CI/
  automated pipelines where a silent failure could corrupt downstream
  analysis unnoticed. Either way, a layer that queries successfully
  but genuinely finds zero features is valid data, never a failure.
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
  completeness check is what distinguishes "OSM hasn't mapped this
  yet" from "this area genuinely lacks the service.")

## Related

- [`akure-accessibility-dashboard`](https://github.com/Mapkathon2026-UseOSM/akure-accessibility-dashboard), the sibling repo that consumes
  this package's output for the Akure North/South accessibility
  analysis. Its `tests/test_cross_repo_integration.py` verifies this
  package's real output schema matches exactly what that repo's
  analysis functions expect.
