# Nigerian LGA OSM Extractor

[![Tests](https://github.com/Mapkathon2026-UseOSM/lga-osm-extractor/actions/workflows/test.yml/badge.svg)](https://github.com/Mapkathon2026-UseOSM/lga-osm-extractor/actions/workflows/test.yml)

A reusable Python tool for extracting OpenStreetMap (OSM) data, roads,
buildings, waterways, land use, health facilities, and schools, for any
Nigerian Local Government Area (LGA), and exporting clean GeoJSON and
Shapefile outputs.

Built for **Map<>kathon 2026** (OSM Dashboard or Analysis track), as the
data-extraction engine behind *"Mapping the Gap: Health and Education
Accessibility in Akure North and Akure South."* The tool itself is
generalized to work for **any** Nigerian LGA, solving a recurring pain
point: constructing correct Overpass queries and administrative boundary
lookups for Nigerian LGAs is non-trivial for many students and researchers.

> **How this serves the public good:** every Nigerian GIS student or
> researcher who has tried to pull OSM data for their own state or LGA
> knows the friction of hand-writing Overpass queries and resolving
> inconsistent administrative boundaries. This tool removes that
> friction entirely, turning a task that used to require OSM expertise
> into a single function call, so more people can go from "I have an
> idea" to "I have the data" and actually build something useful with
> OpenStreetMap.

<!--
Preview screenshot / GIF placeholder.
Generate this after running the tool once, e.g.:
    python cli.py --lga "Akure North" --state "Ondo" --preview
This creates visuals/akure_north_preview.html, open it in a browser,
take a screenshot (or screen-record a quick pan/zoom for a GIF), save it
as docs/preview.png (or .gif) in this repo, then uncomment the line below.
-->
<!-- ![Preview of extracted Akure North layers in kepler.gl](docs/preview.png) -->

*(Preview screenshot to be added here once generated, see comment in
source for the one-line command that produces it.)*

## What it does

Given an LGA name (e.g. `"Akure North"`, state `"Ondo"`), the tool:

1. Resolves the LGA's administrative boundary from OSM (with a manual
   boundary fallback if OSM's boundary data is missing or mistagged),
   and validates it against a geographic bounding box and plausible
   area range before proceeding, see "Known limitations" below for
   what this does and doesn't catch.
2. Extracts roads, buildings, waterways, land use, health facilities, and
   schools within that boundary.
3. Cleans and standardizes the data (reprojects to the correct UTM zone
   for that LGA's location, auto-selected from the boundary's
   centroid, e.g. `EPSG:32631` for Southwest Nigeria, repairs
   invalid geometries, removes duplicates, standardizes the attribute
   schema).
4. Exports each layer as GeoJSON and Shapefile.
5. Writes a run log recording exactly what was queried, so extractions
   are traceable and reproducible.

## Installation

```bash
git clone <repo-url>
cd lga-osm-extractor
pip install -e .
```

This installs the `lga_extractor` package in editable mode, so `import
lga_extractor` works from anywhere on your system, not just from inside
this directory, and any edits to the source take effect immediately
without reinstalling.

Optional extras:

```bash
pip install -e ".[viz]"   # adds keplergl (for the preview map / --preview)
pip install -e ".[app]"   # adds streamlit + leafmap (for the demo form)
pip install -e ".[dev]"   # adds pytest (for running the test suite)
pip install -e ".[all]"   # everything above
```

Or with conda:

```bash
conda env create -f conda-environment.yml
conda activate lga_extractor_env
pip install -e .
```

> Note: this file is deliberately named `conda-environment.yml`, not the
> conventional `environment.yml`. Streamlit Community Cloud auto-detects
> a root-level `environment.yml` and uses conda to build the deployed
> app's environment instead of pip + `requirements.txt` -- conda's
> dependency solver can take a very long time (or effectively hang) on
> a graph this size with open-ended version constraints, which is what
> caused this app's deployment to spin indefinitely at "Solving
> environment" before this rename. Renaming it keeps conda available
> for local development while ensuring Streamlit Cloud uses the
> faster, already-verified pip path.

Or, without installing at all, just add the repo to your path:

```bash
pip install -r requirements.txt
# then in Python: sys.path.append("path/to/lga-osm-extractor")
```

## Verifying the install

```bash
pytest -m "not integration" -v
```

This runs the offline unit test suite (cleaning, export, and the
mixed-geometry Shapefile-splitting logic) without requiring network
access. The same command runs automatically on every push via GitHub
Actions, see the badge at the top of this README.

## Usage

### As a Python module

```python
from lga_extractor import extract_lga

result = extract_lga(lga_name="Akure North", state_name="Ondo")
print(result)
```

### From the command line

```bash
python cli.py --lga "Akure North" --state "Ondo"
python cli.py --lga "Akure South" --state "Ondo" --output-dir data/processed/akure_south
```

If OSM boundary resolution fails for a given LGA:

```bash
python cli.py --lga "Some LGA" --manual-boundary path/to/boundary.geojson
```

Add `--preview` to also generate a standalone kepler.gl HTML preview map:

```bash
python cli.py --lga "Akure North" --state "Ondo" --preview
```

### Polished preview map (kepler.gl)

For a quick, all-layers-at-once visual of an extraction, useful for
sanity-checking results or sharing a nice-looking preview without GIS
software, use the `visualize` helper directly:

```python
from lga_extractor import build_preview_map

build_preview_map(
    output_dir="output/akure_north",
    html_out="visuals/akure_north_preview.html",
)
```

This is purely a visual convenience layer (not an analysis tool): it
loads whichever layers exist for the LGA, applies a default style
(`kepler_config_lga_preview.json`), and saves a standalone HTML file
(data + viewer bundled) that opens in any browser. Requires
`pip install keplergl`, kept optional since the core extractor works
without it.

See `docs/README.md` for how to turn a generated preview into the
screenshot shown at the top of this README.

### As a Streamlit demo

```bash
streamlit run app.py
```

Type in an LGA name, preview the extracted layers on a map, and download
the results as a zip file.

## Default OSM tags used

| Layer | OSM tag filter |
|---|---|
| Roads | `highway=*` |
| Buildings | `building=*` |
| Waterways | `waterway=*`, `natural=water` |
| Land use | `landuse=*` |
| Health facilities | `amenity=hospital/clinic/pharmacy` |
| Schools | `amenity=school` |

This mapping is configurable, see `lga_extractor/layers.py`,
`DEFAULT_TAG_CONFIG`. Pass a custom `tag_config` dict into `extract_lga()`
to add or remove layers (e.g. markets, places of worship) without
touching the extraction logic.

## Repository structure

```
lga-osm-extractor/
├── README.md
├── LICENSE
├── pyproject.toml             # packaging config, enables `pip install -e .`
├── requirements.txt
├── requirements-lock.txt      # exact pip-resolved versions, see "Reproducible installs" below
├── conda-environment.yml
├── pytest.ini
│
├── .github/
│   └── workflows/
│       └── test.yml           # CI: runs offline tests on every push/PR
│
├── lga_extractor/
│   ├── __init__.py
│   ├── boundary.py            # LGA name -> boundary polygon resolution
│   ├── layers.py              # tag-to-layer config and extraction functions
│   ├── clean.py               # geometry cleaning and schema standardization
│   ├── export.py              # GeoJSON / Shapefile export
│   ├── logging_utils.py       # run-log generation
│   ├── pipeline.py            # extract_lga() end-to-end wrapper
│   └── visualize.py           # kepler.gl preview map helper (optional)
│
├── kepler_config_lga_preview.json   # default styling for preview maps
│
├── cli.py                     # command-line entry point (supports --preview)
├── app.py                     # Streamlit demo form
│
├── examples/
│   └── extract_akure_lgas.ipynb
│
├── visuals/
│   └── {lga_name}_preview.html   # kepler.gl standalone exports
│
├── docs/
│   ├── README.md                 # how to generate the preview screenshot/GIF
│   └── preview.png                (add after generating; referenced by main README)
│
├── output/
│   └── {lga_name}/
│       ├── roads.geojson
│       ├── buildings.geojson
│       ├── waterways.geojson
│       ├── landuse.geojson
│       ├── health_facilities.geojson
│       ├── schools.geojson
│       ├── shapefiles/
│       └── run_log.json
│
└── tests/
    └── test_extraction.py
```

## Architecture overview

**Pipeline, in order:**

```
extract_lga(lga_name, state_name)
    │
    ├─► boundary.resolve_boundary()   → LGA boundary polygon
    ├─► layers.extract_layers()       → raw OSM features per configured layer
    ├─► clean.clean_layers()          → reprojected, standardized, deduplicated
    ├─► export.export_layers()        → GeoJSON + Shapefile written to disk
    └─► logging_utils.log_run()       → run_log.json (query config, package versions, warnings)
```

`cli.py` and `app.py` are two interfaces onto this same pipeline (a
command-line tool and a Streamlit demo, respectively); neither
contains extraction logic itself. See `lga_extractor/README.md` for
the package's internal module-by-module breakdown.

**Downstream:** this tool's output feeds directly into the sibling
`akure-accessibility-dashboard` repository's analysis pipeline, see that
repo's `tests/test_cross_repo_integration.py` for a test that verifies
this compatibility directly, and `akure_access/README.md` there for
how the two repos' data flow connects end to end.

## Known limitations

- Boundary resolution quality depends on how well an LGA is tagged in
  OSM; some Nigerian LGAs may need a manual boundary supplied.
- Every resolved boundary is validated before extraction proceeds, via
  `boundary._validate_and_standardize()`, in two tiers:
  - **Hard checks (raise `BoundaryResolutionError`, aborting
    extraction):** the boundary's centroid must fall within Nigeria's
    approximate bounding box, and its area (measured in the
    auto-selected UTM zone, see below) must fall within a generously
    wide plausible range for a single LGA (2 km² to 10,000 km²). These
    catch the specific failure modes of "a name collision resolved to
    an unrelated place," "a single point/building was resolved instead
    of the LGA boundary," and "a whole state or the entire country was
    resolved instead of one LGA."
  - **Soft check (recorded as a warning, does not raise):** if OSM
    provided a `display_name` field, it's checked for an obvious
    mention of the requested LGA/state name. A mismatch here is often
    just Nominatim's naming conventions, not necessarily a wrong
    result, so it's surfaced as a warning (in
    `result["warnings"]` and the run log), not treated as a failure.

  This is not a full `admin_level`/relation-type verification against
  an authoritative Nigerian administrative boundary dataset, that
  would be the most rigorous version of this check, but requires a
  reference dataset this tool doesn't currently depend on. What's
  implemented catches the failure modes most likely to actually occur
  (wrong place entirely, wrong scale entirely) without adding that
  dependency. If a resolved boundary passes these checks but still
  looks wrong on inspection, supply a manual boundary instead.
- The correct UTM zone is auto-selected per LGA.
  `clean.resolve_target_crs()` takes the resolved boundary's centroid
  longitude and picks the correct UTM zone from it (31N/32N/33N,
  covering all of Nigeria), falling back to EPSG:32631 only if no
  boundary is available at all. The resolved CRS for any given run is
  recorded in that run's `run_log.json` under `target_crs`, so it's
  traceable rather than assumed. See `clean.utm_epsg_for_longitude()`
  and its tests (`test_utm_epsg_for_longitude_known_nigerian_locations`,
  `test_resolve_target_crs_auto_selects_zone_from_boundary`) for the
  exact logic and verification against known Nigerian reference points.
- Feature completeness depends entirely on existing OSM contributor
  coverage, the tool extracts what exists in OSM, it does not verify
  ground truth.
- Strict vs. permissive extraction mode is configurable.
  `extract_lga(..., strict=True)` raises a `LayerExtractionError`
  immediately if a layer's OSM query genuinely fails (an Overpass
  timeout, network error, or bad tag configuration), while a layer
  that queries successfully but simply finds zero features (a valid
  result, not a failure) still never raises, even in strict mode. The
  default is permissive (`strict=False`: failures are caught, logged as
  warnings, and that layer is returned empty so the rest of the
  extraction can continue), which stays appropriate for demos and
  exploratory use. Use `strict=True` for automated/CI pipelines, where
  a silent failure could otherwise corrupt downstream analysis without
  anyone noticing. See `layers.extract_layers()`'s docstring and
  `test_extract_layers_strict_raises_on_genuine_failure` /
  `test_extract_layers_strict_does_not_raise_on_genuine_empty_result`
  for the exact behavior and its test coverage.
- Default tags cover the most common cases; extend `DEFAULT_TAG_CONFIG`
  for additional feature types.
- Some tag filters (notably `highway=*` for roads) match both line
  geometries and point nodes in OSM (e.g. traffic signals, crossings).
  Since Shapefile requires a single geometry type per file, such layers
  are automatically split into per-type Shapefiles on export (e.g.
  `roads_line.shp` and `roads_point.shp`), GeoJSON export is unaffected
  and always contains the full mixed-type layer in one file. See
  `exported[layer]["_split_layers"]` in the run log for which layers
  were split on a given run.

## Documentation overhaul (Phase 1)

As part of a repository-wide readability/maintainability pass:
- New folder-level README: `lga_extractor/README.md` (package
  architecture, module-by-module breakdown)
- The "Architecture overview" section above, showing the extraction
  pipeline end to end and how it connects to the sibling dashboard repo
- Existing module/function docstrings were audited; all package files
  already carry complete module-level docstrings (added/refined across
  earlier sessions covering the run-log environment capture, mixed-
  geometry Shapefile export handling, and dependency lockfile work)

A full tutorial-style rebuild of `examples/extract_akure_lgas.ipynb`
is a larger undertaking, tracked separately given proximity to the
Aug 7 submission deadline.

## License

Code: see `LICENSE`.
Data: all extracted content is © OpenStreetMap contributors, available
under the [Open Database License (ODbL)](https://www.openstreetmap.org/copyright).

## Part of

**Map<>kathon 2026**, in partnership with Unpatterned and the
OpenStreetMap Engineering Working Group. https://www.useosm.org/
