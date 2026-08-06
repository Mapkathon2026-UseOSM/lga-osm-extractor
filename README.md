# Nigerian LGA OSM Extractor

[![Tests](https://github.com/Mapkathon2026-UseOSM/lga-osm-extractor/actions/workflows/test.yml/badge.svg)](https://github.com/Mapkathon2026-UseOSM/lga-osm-extractor/actions/workflows/test.yml)

**Live demo:** https://lga-extractor.streamlit.app/ · **GitHub:** https://github.com/Mapkathon2026-UseOSM/lga-osm-extractor

Turn a plain Nigerian LGA name into a clean, ready-to-use OSM dataset,
roads, buildings, waterways, land use, health facilities, and schools,
with no Overpass query syntax, GIS software, or manual data wrangling
required.

Built for **Map<>kathon 2026** (Lightweight Tool / Demo track), as the
data-extraction engine behind the sibling submission
**[akure-accessibility-dashboard](https://github.com/Mapkathon2026-UseOSM/akure-accessibility-dashboard)**,
*"Mapping the Gap: Health and Education Accessibility in Akure North
and Akure South."* The tool itself is generalized to work for **any**
Nigerian LGA, not just those two: constructing correct Overpass
queries and resolving inconsistent administrative boundaries is a
real, recurring friction point for Nigerian GIS students and
researchers, and this tool removes it entirely.

> **How this serves the public good:** every Nigerian GIS student or
> researcher who has tried to pull OSM data for their own state or LGA
> knows the friction of hand-writing Overpass queries and resolving
> inconsistent administrative boundaries. This tool removes that
> friction entirely, so more people can go from "I have an idea" to
> "I have the data" and actually build something with OpenStreetMap.

## Try it

The live demo requires no setup: https://lga-extractor.streamlit.app/

Or run it locally:

```bash
pip install -e ".[app]"
streamlit run app.py
```

Type in an LGA name, watch it extract live from OSM, preview every
layer on an interactive map, and download everything as a zip.

The live demo is deployed on Streamlit Community Cloud with an exact
pinned dependency set (`requirements.txt`) and a pinned Python version
(`runtime.txt`, `python-3.11`), deliberately, an earlier deploy broke
when Streamlit Cloud resolved a newer, untested Python/package
combination on its own; see the sibling
`akure-accessibility-dashboard` repo's `AI_DISCLOSURE.md` for the full
story of that failure mode, since it happened there first and both
repos now pin the same way to avoid repeating it.

## What it does

Given an LGA name (e.g. `"Akure North"`, state `"Ondo"`):

1. **Resolves the boundary** from OSM, with a manual-boundary fallback
   and a two-tier sanity check (see `lga_extractor/README.md`) that
   catches "resolved the wrong place entirely," not just unusual-but-
   valid LGA shapes.
2. **Extracts** roads, buildings, waterways, land use, health
   facilities, and schools within that boundary, from OSM's live
   Overpass API.
3. **Cleans and standardizes**: reprojects to the correct UTM zone for
   that LGA's actual location (auto-selected, not hardcoded, since
   Nigeria spans three UTM zones), repairs invalid geometries, removes
   duplicates, standardizes the attribute schema.
4. **Exports** each layer as GeoJSON and Shapefile.
5. **Writes a run log** recording exactly what was queried, so every
   extraction is traceable and reproducible.

## Quickstart

```bash
git clone https://github.com/Mapkathon2026-UseOSM/lga-osm-extractor.git
cd lga-osm-extractor
pip install -e .
```

```python
from lga_extractor import extract_lga

result = extract_lga(lga_name="Akure North", state_name="Ondo")
print(result)
```

Or from the command line:

```bash
python cli.py --lga "Akure North" --state "Ondo"
python cli.py --lga "Akure North" --state "Ondo" --preview  # + kepler.gl HTML map
```

Optional installs: `pip install -e ".[viz]"` (kepler.gl preview maps),
`".[app]"` (Streamlit demo), `".[dev]"` (tests), `".[all]"` (everything).

Verify the install: `pytest -m "not integration" -v` (fast, offline,
same command CI runs on every push).

## Default OSM tags used

| Layer | OSM tag filter |
|---|---|
| Roads | `highway=*` |
| Buildings | `building=*` |
| Waterways | `waterway=*`, `natural=water` |
| Land use | `landuse=*` |
| Health facilities | `amenity=hospital/clinic/pharmacy` |
| Schools | `amenity=school` |

Configurable, see `lga_extractor/layers.py`, `DEFAULT_TAG_CONFIG`. Pass
a custom `tag_config` dict into `extract_lga()` to add or remove
layers (e.g. markets, places of worship) without touching extraction
logic.

## Repository structure

```
lga-osm-extractor/
├── README.md                  # this file
├── LICENSE
├── pyproject.toml             # packaging config, enables `pip install -e .`
├── requirements.txt / requirements-lock.txt / conda-environment.yml
├── runtime.txt                 # pins Python 3.11 for Streamlit Cloud (see "Try it")
├── pytest.ini
├── .github/workflows/test.yml # CI: offline tests on every push/PR
│
├── lga_extractor/             # the package, see lga_extractor/README.md
│   ├── boundary.py            # LGA name -> boundary polygon resolution
│   ├── layers.py              # tag-to-layer config + extraction
│   ├── clean.py                # geometry cleaning, CRS, schema standardization
│   ├── export.py                # GeoJSON / Shapefile export
│   ├── logging_utils.py          # run-log generation
│   ├── pipeline.py                # extract_lga() end-to-end wrapper
│   └── visualize.py               # kepler.gl preview map helper (optional)
│
├── cli.py                     # command-line entry point
├── app.py                     # Streamlit demo
├── kepler_config_lga_preview.json
│
├── examples/                  # worked tutorial notebook, see examples/README.md
├── docs/                      # how to regenerate the preview screenshot
├── tests/                     # see tests/README.md
├── visuals/                   # kepler.gl standalone HTML exports
└── output/{lga_name}/         # extraction output (GeoJSON, Shapefile, run_log.json)
```

## Architecture

```
extract_lga(lga_name, state_name)
    │
    ├─► boundary.resolve_boundary()   → LGA boundary polygon
    ├─► layers.extract_layers()       → raw OSM features per configured layer
    ├─► clean.clean_layers()          → reprojected, standardized, deduplicated
    ├─► export.export_layers()        → GeoJSON + Shapefile written to disk
    └─► logging_utils.log_run()       → run_log.json (query config, package versions, warnings)
```

`cli.py` and `app.py` are two interfaces onto this same pipeline;
neither contains extraction logic itself. Full module-by-module
breakdown, design decisions, and known limitations:
**[`lga_extractor/README.md`](lga_extractor/README.md)**.

**Downstream:** this tool's output feeds directly into the sibling
**[akure-accessibility-dashboard](https://github.com/Mapkathon2026-UseOSM/akure-accessibility-dashboard)**
repository's analysis pipeline. `tests/test_cross_repo_integration.py`
(in that repo) verifies this compatibility directly on every push via
a dedicated cross-repo CI workflow, this isn't just an assumed
contract between the two repos, it's tested.

## License

Code: see `LICENSE`.
Data: all extracted content is © OpenStreetMap contributors, available
under the [Open Database License (ODbL)](https://www.openstreetmap.org/copyright).

## Part of

**Map<>kathon 2026**, in partnership with Unpatterned and the
OpenStreetMap Engineering Working Group.

- UseOSM: https://www.useosm.org/en/
- Map<>kathon 2026 event page: https://www.useosm.org/en/community-events/mapkaton-2026
- Sibling submission: [akure-accessibility-dashboard](https://github.com/Mapkathon2026-UseOSM/akure-accessibility-dashboard) — https://akure-accessibility-dashboard-analysis.streamlit.app/

## AI Disclosure

Following Map<>kathon 2026's Responsible AI principles: state what tool
was used, explain its role, confirm outputs were reviewed, confirm no
blind write-back to OSM, confirm local knowledge/community guidelines
were respected, and acknowledge limitations/risks/uncertainty.

### Tool used

**Claude (Anthropic)** was used as a coding assistant throughout the
development of `lga-osm-extractor`, across multiple sessions.

### Role AI played

Claude assisted with the following, mapped to the actual modules in
this repository:

- **Pipeline structure and orchestration** — `lga_extractor/pipeline.py`'s
  `extract_lga()`, the single function that wires
  `boundary.resolve_boundary()` → `layers.extract_layers()` →
  `clean.clean_layers()` → `export.export_layers()` →
  `logging_utils.log_run()` into one call, plus the module layout
  itself (`boundary.py` / `layers.py` / `clean.py` / `export.py` /
  `visualize.py` / `logging_utils.py` as separate, single-purpose
  files) and the CLI/config design (`cli.py`'s argparse interface,
  `app.py`'s Streamlit form).

- **Boundary resolution and validation** — `boundary.py`'s
  `resolve_boundary()` and `_validate_and_standardize()`: the two-tier
  hard/soft validation scheme (hard checks that raise
  `BoundaryResolutionError` when a centroid falls outside
  `NIGERIA_BBOX` or an area falls outside `MIN_PLAUSIBLE_LGA_AREA_KM2`/
  `MAX_PLAUSIBLE_LGA_AREA_KM2`; a soft `display_name` check that only
  warns), and debugging the specific edge cases that shaped those
  thresholds (a single resolved point/building vs. an entire
  state/country being returned by Nominatim).

- **Auto-CRS selection logic** — `clean.py`'s
  `utm_epsg_for_longitude()` and `resolve_target_crs()`: implementing
  and debugging the logic that auto-selects the correct UTM zone
  (31N/32N/33N, covering all of Nigeria) from a resolved boundary's
  centroid longitude, rather than a single hardcoded zone, verified
  against known reference points (Akure/Ondo, Abuja, Maiduguri) in
  `tests/test_extraction.py`.

- **Strict vs. permissive extraction modes** — `layers.py`'s
  `extract_layers()` and `_extract_single_layer()`: designing the
  distinction between a genuine query failure (`LayerExtractionError`
  in strict mode) and a query that succeeds but legitimately finds
  zero features (never an error, either mode), plus the retry/backoff
  logic (`MAX_RETRIES`, `RETRY_BACKOFF_BASE_SECONDS`) and the
  staggered, capped-concurrency querying (`MAX_CONCURRENT_LAYER_QUERIES = 2`,
  `REQUEST_STAGGER_SECONDS = 3`) arrived at after fully unthrottled
  concurrency caused OSM's public Overpass mirror to refuse every
  connection outright.

- **kepler.gl preview map helper and credential hygiene** —
  `visualize.py`'s `build_preview_map()` and `_strip_mapbox_token()`:
  the lazy `keplergl` import (so the rest of the package stays
  importable even if `keplergl`'s own `pkg_resources`/`setuptools`
  dependency chain is broken in a given environment), and identifying
  that the installed `keplergl` package bundles a real Mapbox access
  token into every HTML export regardless of configured basemap style,
  then implementing the regex-based stripping step that makes exported
  HTML safe to commit to a public repository (verified against
  GitHub's secret-scanning push protection).

Claude did **not** generate, fabricate, or select any OSM feature
data. All data extracted by this tool comes directly from
OpenStreetMap via standard extraction libraries (OSMnx / Overpass),
never from AI generation.

### Local knowledge and community guidelines

Extraction logic follows standard OSM tagging conventions for the
feature categories targeted (health, education, roads, boundaries).
LGA boundary definitions were cross-checked against known
administrative divisions for Ondo State rather than relying solely on
OSM tags or AI-suggested defaults.

### Limitations, risks, and uncertainty

- OSM feature completeness varies significantly across Nigerian LGAs;
  extraction results reflect underlying OSM data density, not
  ground-truth completeness.
- Strict mode may exclude legitimately tagged features with incomplete
  metadata; permissive mode may include lower-confidence matches.
- Boundary validation (`boundary._validate_and_standardize()`) is a
  heuristic bounding-box/area check, not a full `admin_level`/relation-
  type verification against an authoritative Nigerian administrative
  boundary dataset. It catches the failure modes most likely to occur
  (wrong place entirely, wrong scale entirely), not every possible
  misresolution.
- Auto-CRS selection relies on a longitude-based UTM zone formula and
  should be manually verified for LGAs outside the tested set (Akure
  North/South, plus the Abuja/Maiduguri reference points used in
  tests).