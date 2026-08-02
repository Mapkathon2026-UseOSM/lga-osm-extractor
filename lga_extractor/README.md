# lga_extractor/

## Purpose

A reusable OSM data extraction tool for any Nigerian Local Government
Area (LGA), not specific to Akure. This is the "acquisition layer"
of the two-repo project: it resolves an LGA's boundary, queries OSM
for relevant features, cleans/standardizes them, and exports
GeoJSON/Shapefile outputs that downstream analysis (the sibling
`akure-access-dashboard` repo) consumes.

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

- All spatial processing is hardcoded to EPSG:32631 (UTM Zone 31N),
  which is correct for Southwest Nigeria but not universally correct
  for all Nigerian LGAs, see the root README's "Known limitations"
  section.
- `visualize.py`'s keplergl dependency is optional and imported
  lazily; the rest of the package works without it installed.

## Related

- `../../akure-access-dashboard/`, the sibling repo that consumes
  this package's output for the Akure North/South accessibility
  analysis
- `../tests/test_cross_repo_integration.py` (in the dashboard repo), verifies this package's real output schema matches what the
  dashboard's analysis functions expect
