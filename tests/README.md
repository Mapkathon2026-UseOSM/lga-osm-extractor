# tests/

## Purpose

Automated tests for the `lga_extractor` package, run against small
synthetic geometries rather than live OSM data, fast, deterministic,
and safe to run in CI without network access.

## Contents

| File | Covers |
|---|---|
| `test_extraction.py` | See breakdown below, one file, 19 tests, covering every module in `lga_extractor/` |

`test_extraction.py`'s tests, grouped by what they actually verify:

- **Layer cleaning & export** — `test_clean_layers_reprojects_and_dedupes()`,
  `test_clean_layers_standard_schema()`, `test_export_layers_writes_geojson_and_shapefile()`,
  `test_export_layers_splits_mixed_geometry_types()` (roads containing
  both line ways and point nodes, e.g. `highway=*` matching both roads
  and traffic-signal nodes, must split into separate Shapefiles since
  Shapefile requires one geometry type per file, GeoJSON does not).
- **Auto-CRS selection** (`clean.py`) —
  `test_utm_epsg_for_longitude_known_nigerian_locations()` (verified
  against Akure/Ondo, Abuja, Maiduguri), `test_utm_epsg_for_longitude_zone_boundaries()`,
  `test_resolve_target_crs_falls_back_with_no_boundary()`,
  `test_resolve_target_crs_auto_selects_zone_from_boundary()`,
  `test_clean_layers_uses_boundary_to_select_crs()` (end-to-end: the
  right UTM zone actually gets used, not just computed).
- **Strict vs. permissive extraction** (`layers.py`) —
  `test_extract_layers_permissive_returns_empty_on_failure()`,
  `test_extract_layers_strict_raises_on_genuine_failure()`,
  `test_extract_layers_strict_does_not_raise_on_genuine_empty_result()`
  (a real zero-feature result is never treated as a failure, in either
  mode, only a genuine query error is).
- **Boundary resolution & validation** (`boundary.py`) —
  `test_resolve_boundary_accepts_plausible_akure_sized_boundary()`,
  `test_resolve_boundary_rejects_centroid_outside_nigeria()`,
  `test_resolve_boundary_rejects_implausibly_tiny_area()`,
  `test_resolve_boundary_rejects_implausibly_huge_area()` (the four
  hard-check failure modes), and
  `test_validate_and_standardize_display_name_mismatch_warns_not_raises()`
  (the one soft check, confirming it warns rather than blocking a
  legitimate boundary over a Nominatim naming quirk).
- **Mapbox token hygiene** (`visualize.py`) —
  `test_strip_mapbox_token_removes_real_token_from_export()`,
  `test_strip_mapbox_token_returns_false_when_no_token_present()`,
  guarding against `keplergl`'s bundled Mapbox token ending up in a
  committed HTML export.
- **End-to-end, live OSM** — `test_extract_lga_end_to_end_live_osm()`,
  the one test marked `@pytest.mark.integration`, runs the full
  `extract_lga()` pipeline against real Overpass data. Excluded from
  the default test run (see `pytest.ini`), since it needs network
  access and live OSM/Overpass availability.

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
  logic in `export.py`, extend `test_extraction.py` accordingly, particularly the mixed-geometry-type test, since that's the one
  real correctness trap in the export path (Shapefile requires a
  single geometry type per file; GeoJSON does not).
