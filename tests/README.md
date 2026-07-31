# tests/

## Purpose

Automated tests for the `lga_extractor` package, run against small
synthetic geometries rather than live OSM data -- fast, deterministic,
and safe to run in CI without network access.

## Contents

| File | Covers |
|---|---|
| `test_extraction.py` | Layer cleaning (reprojection, deduplication, schema standardization), GeoJSON/Shapefile export including the mixed-geometry-type split (e.g. roads containing both line ways and point nodes) |

One test in this file is marked `@pytest.mark.integration` and makes a
real call to OSM/Overpass to build a live road network graph -- this
is excluded from the default test run (see `pytest.ini`).

## Running

```bash
# Fast, offline tests only (default; what CI runs on every push)
pytest -m "not integration"

# Everything, including the live-OSM test (slow, needs network)
pytest -m integration
```

## Notes

- These tests never make real Overpass API calls except the one
  explicitly marked `integration`. This keeps the default test suite
  fast and independent of OSM server availability/rate limits.
- If you change the tag configuration in `layers.py` or the export
  logic in `export.py`, extend `test_extraction.py` accordingly --
  particularly the mixed-geometry-type test, since that's the one
  real correctness trap in the export path (Shapefile requires a
  single geometry type per file; GeoJSON does not).
