# Nigerian LGA OSM Extractor

[![Tests](https://github.com/Mapkathon2026-UseOSM/lga-osm-extractor/actions/workflows/test.yml/badge.svg)]
(https://github.com/Mapkathon2026-UseOSM/lga-osm-extractor/actions/workflows/test.yml)

**Live demo:** https://lga-extractor.streamlit.app/ · **GitHub:** https://github.com/Mapkathon2026-UseOSM/lga-osm-extractor

Turn a plain Nigerian LGA name into a clean, ready-to-use, richly
attributed OSM dataset which includes roads, buildings, waterways, land use, health
facilities, and schools  with a verifiable boundary, a correct
projection, and a formal manifest of exactly what was extracted, with
no Overpass query syntax, GIS software, or manual data wrangling
required.

Built for **Map<>kathon 2026** (Lightweight Tool / Demo track), as the
data-extraction engine behind the sibling submission
**[akure-accessibility-dashboard](https://github.com/Mapkathon2026-UseOSM/akure-accessibility-dashboard)**,
*"Mapping the Gap: Health and Education Accessibility in Akure North
and Akure South."* The tool itself is generalized to work for **any**
Nigerian LGA, not just those two: constructing correct Overpass
queries, resolving inconsistent administrative boundaries, and
projecting into the correct UTM zone are real, recurring friction
points for Nigerian GIS students and researchers, and this tool
removes them entirely, for any of Nigeria's 774 LGAs.

> **How this serves the public good:** every Nigerian GIS student or
> researcher who has tried to pull OSM data for their own state or LGA
> knows the friction of hand-writing Overpass queries, resolving
> inconsistent administrative boundaries, and guessing which UTM zone
> applies. This tool removes that friction entirely, and does so with
> a traceable, auditable output - not just a file, but a signed record
> of how that file was produced - so more people can go from "I have
> an idea" to "I have data I can actually trust" and build something
> real with OpenStreetMap.

## Try it

The live demo requires no setup: https://lga-extractor.streamlit.app/

Or run it locally:

```bash
pip install -e ".[app]"
streamlit run app.py
```

Type in an LGA name, watch a **live, per-stage extraction progress
view** (boundary resolution, each layer's query - including retry
counts if Overpass is briefly unavailable, cleaning, export), preview
every layer on an interactive map, and download everything as a zip
alongside an extraction summary (feature counts per layer, resolved
CRS, boundary source, warning count).

The live demo is deployed on Streamlit Community Cloud with an exact
pinned dependency set (`requirements.txt`) and a pinned Python version
(`runtime.txt`, `python-3.11`), deliberately, an earlier deploy broke
when Streamlit Cloud resolved a newer, untested Python/package
combination on its own; see the sibling
`akure-accessibility-dashboard` repo's README.md ("AI Disclosure" →
"Limitations, risks, and uncertainty") for the full story of that
failure mode, since it happened there first and both repos now pin the
same way to avoid repeating it.

## What it does

Given an LGA name (e.g. `"Akure North"`, state `"Ondo"`):

1. **Resolves the boundary** from OSM, with a manual-boundary fallback
   and a two-tier sanity check - hard checks (implausible location or
   size) that reject a clearly wrong result outright, plus a soft
   `admin_level`/name plausibility check against OSM's own
   administrative-boundary metadata, so a boundary that resolves to
   roughly the right place but the wrong *kind* of feature (e.g. a
   ward instead of an LGA) is also caught, not just gross
   mis-resolutions (see `lga_extractor/README.md`).
2. **Extracts** roads, buildings, waterways, land use, health
   facilities, and schools within that boundary, from OSM's live
   Overpass API, with staggered, capped-concurrency querying and
   automatic retry/backoff on transient failures.
3. **Cleans and standardizes**: reprojects to the correct UTM zone for
   that LGA's actual location (auto-selected, not hardcoded, since
   Nigeria spans three UTM zones), repairs invalid geometries, removes
   duplicates, and standardizes the attribute schema - while
   **preserving the semantically meaningful OSM tags per layer** (a
   road's `highway`/`surface`/`maxspeed`/`lanes`/`oneway`, a health
   facility's `amenity`/`healthcare`/`beds`/`emergency`/`operator`, a
   school's `amenity`/`isced:level`/`school`, and so on), plus a full
   `raw_tags` JSON column per feature capturing every original OSM tag
   as a lossless fallback, so nothing genuinely disappears during
   cleaning, only the previous osmid/name/geometry-only schema does.
4. **Exports** each layer as GeoJSON (carrying the full rich schema)
   and Shapefile (kept to the core `osmid`/`name`/`geometry` schema
   deliberately, since Shapefile's 10-character field-name limit would
   silently truncate or collide the richer semantic columns).
5. **Writes a formal extraction manifest** (`manifest.json`) recording,
   per layer, exactly what happened - queried successfully with N
   features, queried successfully and genuinely found zero, or failed
   after retries and why - plus the resolved CRS and boundary source,
   so a downstream consumer never has to guess what an empty file
   means, and a `run_log.json` carrying the same structured status
   alongside package versions and warnings, for full auditability.

## Quickstart

```bash
git clone https://github.com/Mapkathon2026-UseOSM/lga-osm-extractor.git
cd lga-osm-extractor
pip install -e .
```

```python
from lga_extractor import extract_lga

result = extract_lga(lga_name="Akure North", state_name="Ondo")
print(result["target_crs"])       # e.g. "EPSG:32631", resolved from the boundary, not assumed
print(result["manifest_path"])    # path to this run's manifest.json
print(result["layer_status"])     # per-layer {"status", "feature_count", "attempts", "message"}
```

Or from the command line:

```bash
python cli.py --lga "Akure North" --state "Ondo"
python cli.py --lga "Akure North" --state "Ondo" --preview  # + kepler.gl HTML map
python cli.py --lga "Akure North" --state "Ondo" --strict   # raise on genuine query failure instead of continuing
```

Optional installs: `pip install -e ".[viz]"` (kepler.gl preview maps),
`".[app]"` (Streamlit demo), `".[dev]"` (tests), `".[all]"` (everything).

Verify the install: `pytest -m "not integration" -v` (fast, offline,
same command CI runs on every push).

### Live extraction progress

Both the Streamlit demo and any custom caller can subscribe to
structured, per-stage extraction events (`boundary` resolved,
`layer:{name}` started/retried/completed/failed, `cleaning`, `export`,
final `pipeline_completed`) without the extraction pipeline itself
knowing anything about Streamlit or any other UI:

```python
from lga_extractor import extract_lga
from lga_extractor.events import ThreadSafeEventQueue

events = ThreadSafeEventQueue()
# run extract_lga(..., on_event=events) in a background thread, then
# drain events.drain() on your UI thread to render a live checklist,
# see app.py for the full working pattern (a st.status() checklist
# with retry counters and a progress bar).
```

## Default OSM tags used

| Layer | OSM tag filter | Semantic columns preserved (when present) |
|---|---|---|
| Roads | `highway=*` | `highway`, `surface`, `maxspeed`, `lanes`, `oneway`, `access`, `bridge`, `tunnel`, `ref` |
| Buildings | `building=*` | `building`, `building:levels`, `building:use` |
| Waterways | `waterway=*`, `natural=water` | `waterway`, `natural`, `water`, `intermittent` |
| Land use | `landuse=*` | `landuse` |
| Health facilities | `amenity=hospital/clinic/pharmacy` | `amenity`, `healthcare`, `beds`, `emergency`, `operator`, `opening_hours` |
| Schools | `amenity=school` | `amenity`, `isced:level`, `school`, `operator` |

Every layer also gets `osmid`, `name`, `geometry` (the original
minimal schema, always present), and a `raw_tags` column (GeoJSON
only) holding the complete original OSM tag set as JSON, for anything
outside the curated semantic subset above. Configurable, see
`lga_extractor/layers.py`'s `DEFAULT_TAG_CONFIG` and `clean.py`'s
`SEMANTIC_COLUMNS`. Pass a custom `tag_config` dict into
`extract_lga()` to add or remove layers (e.g. markets, places of
worship) without touching extraction logic.

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
│   ├── boundary.py            # LGA name -> boundary polygon resolution + admin-level validation
│   ├── layers.py              # tag-to-layer config + extraction, staggered/retried, emits progress events
│   ├── clean.py                 # geometry cleaning, CRS, rich per-layer schema + raw_tags
│   ├── export.py                  # GeoJSON (rich schema) / Shapefile (core schema) export
│   ├── manifest.py                  # builds + writes manifest.json, the formal extraction contract
│   ├── events.py                      # UI-agnostic progress-event schema + thread-safe event queue
│   ├── logging_utils.py                 # run-log generation, now including structured per-layer status
│   ├── pipeline.py                        # extract_lga() end-to-end wrapper, wires all of the above together
│   └── visualize.py                         # kepler.gl preview map helper (optional)
│
├── cli.py                     # command-line entry point
├── app.py                     # Streamlit demo, with a live per-stage progress interface
├── kepler_config_lga_preview.json
│
├── examples/                  # worked tutorial notebook, see examples/README.md
├── docs/                      # standalone PDF documentation, see "Documentation" below
│   └── Nigerian_LGA_OSM_Extractor_Documentation.pdf
├── tests/                     # see tests/README.md
├── visuals/                   # kepler.gl standalone HTML exports
└── output/{lga_name}/         # extraction output: GeoJSON, Shapefile, manifest.json, run_log.json
```

**Folder guide**, for anyone jumping straight to a specific piece:

- **`lga_extractor/`**: the installable package and the only place
  extraction/cleaning/export logic actually lives. Start here for
  anything about *how* extraction works. See `lga_extractor/README.md`
  for a module-by-module breakdown.
- **`examples/`**: a worked, runnable tutorial notebook demonstrating
  the package end to end against a real LGA. See `examples/README.md`.
- **`docs/`**: the standalone PDF reference document for this
  submission (see "Documentation" below for the fuller, site-based
  reference alongside it).
- **`tests/`**: the offline unit test suite, plus the marked
  integration test that hits live OSM. See `tests/README.md`.
- **`visuals/`**: generated kepler.gl standalone HTML exports, written
  here by `cli.py --preview` and `app.py`'s preview feature.
- **`output/{lga_name}/`**: where every extraction run's actual
  deliverables land, GeoJSON, Shapefile, `manifest.json`, and
  `run_log.json`, one subfolder per LGA extracted.
- **`cli.py`** and **`app.py`**, at the repository root: the two
  user-facing entry points onto the package in `lga_extractor/`,
  covered in "What it does" and "Try it" above.

## Architecture

```
extract_lga(lga_name, state_name)
    │
    ├─► boundary.resolve_boundary()   → LGA boundary polygon, hard + soft admin-level validated
    ├─► layers.extract_layers()       → raw OSM features per configured layer, with per-layer
    │                                    status (success / success_empty / failed) and live
    │                                    progress events, not just a free-text warning
    ├─► clean.clean_layers()          → reprojected, standardized, deduplicated, with the
    │                                    curated semantic schema + raw_tags preserved per layer
    ├─► export.export_layers()        → GeoJSON (rich schema) + Shapefile (core schema) written to disk
    ├─► manifest.build_manifest()      → reconciles query-time status with export-time outcome
    │      + write_manifest()            into manifest.json, the formal handoff contract a
    │                                     downstream consumer (e.g. the dashboard) reads instead
    │                                     of hardcoding assumptions
    └─► logging_utils.log_run()       → run_log.json (query config, package versions, warnings,
                                          the same structured per-layer status, resolved CRS)
```

`cli.py` and `app.py` are two interfaces onto this same pipeline;
neither contains extraction logic itself. Both, along with any other
caller, can pass an `on_event` callback to `extract_lga()` to receive
live progress events (see `lga_extractor/events.py`) without the
pipeline itself depending on any UI framework. Full module-by-module
breakdown, design decisions, and known limitations:
**[`lga_extractor/README.md`](lga_extractor/README.md)**.

**Downstream:** this tool's output - GeoJSON/Shapefile layers plus
`manifest.json` - feeds directly into the sibling
**[akure-accessibility-dashboard](https://github.com/Mapkathon2026-UseOSM/akure-accessibility-dashboard)**
repository's analysis pipeline, which reads `manifest.json` for its
resolved CRS and per-layer status rather than re-deriving or
hardcoding either. `tests/test_cross_repo_integration.py` (in that
repo) verifies this compatibility directly on every push via a
dedicated cross-repo CI workflow, this isn't just an assumed contract
between the two repos, it's tested.

## Documentation

Two layers of documentation exist for this project, at different depths:

- **`docs/Nigerian_LGA_OSM_Extractor_Documentation.pdf`**, in this
  repository: a standalone reference document covering the tool's
  purpose, workflow, and output format, suitable for a reviewer who
  wants the full picture without cloning the repo or reading source.
- **[Documentation site](https://mapkathon2026-useosm.github.io/UseOSM_Mapkathon-Submission-documentation/lga-osm-extractor/overview/)**
  ([source repo](https://github.com/Mapkathon2026-UseOSM/UseOSM_Mapkathon-Submission-documentation)),
  a dedicated documentation site covering both submissions
  (`lga-osm-extractor` and `akure-accessibility-dashboard`) in full
  technical depth: repository architecture, design philosophy, a
  module-by-module breakdown of every significant function and class
  (what it does, why it was written that way, inputs/outputs, internal
  workflow, assumptions, and complexity where relevant), and an
  end-to-end walkthrough of what happens from process start to final
  output for each repository. This is the right place to look for a
  genuinely deep understanding of how the codebase works internally,
  beyond what this README summarizes.

## Extraction manifest (`manifest.json`)

Every run writes `manifest.json` to its output directory, alongside
the GeoJSON/Shapefile layers, this is the formal contract a downstream
consumer should read instead of inferring extraction outcome from file
presence, an empty file, or a hardcoded assumption:

```json
{
  "schema_version": 1,
  "lga_name": "Akure North",
  "state_name": "Ondo",
  "extracted_at": "2026-08-13T09:12:44+00:00",
  "target_crs": "EPSG:32631",
  "boundary_source": "osm_geocode:Akure North, Ondo State, Nigeria",
  "source": "OpenStreetMap",
  "layers": {
    "roads": {
      "query_status": "success",
      "query_attempts": 1,
      "query_message": null,
      "feature_count": 2431,
      "feature_count_raw": 2431,
      "exported": true,
      "geojson_path": "output/akure_north/roads.geojson",
      "shapefile_path": "output/akure_north/shapefiles/roads.shp"
    },
    "schools": {
      "query_status": "success_empty",
      "feature_count": 0,
      "exported": false
    }
  }
}
```

`query_status` is always one of `"success"`, `"success_empty"`, or
`"failed"` - an empty layer and a failed query never look identical,
the way a bare empty GeoDataFrame did before this existed. See
`lga_extractor/manifest.py`'s module docstring for the full schema and
rationale.

## Beyond Nigeria: a path to global generalization

This tool is generalized across all 774 Nigerian LGAs, not just Akure
North/South, today. It was not built to be Nigeria-only, and several
pieces of its architecture already generalize further still. Worth
being explicit about exactly where that seam is, what's already
portable, and what real work would remain to take this worldwide,
including concrete steps for actually doing it.

**Already global, no changes needed.** `clean.py`'s
`utm_epsg_for_longitude()` computes the correct metric coordinate
system from any latitude/longitude pair on Earth, using the standard
UTM zone formula, not a Nigeria-specific lookup table. The six
extracted layers and every semantic tag preserved for them (`highway`,
`amenity`, `healthcare`, `isced:level`, ...) are standard OpenStreetMap
tagging conventions, not regional ones.

**A moderate, mechanical tweak.** `boundary.resolve_boundary()`
currently builds its Nominatim query as
`"{lga_name}, {state_name}, Nigeria"`, hardcoding both the country and
a fixed two-level (state → LGA) administrative hierarchy. Generalizing
this means accepting a free-form place-name query or a list of
parent-area qualifiers instead, since administrative structures vary
widely (France's communes, the US's counties, Kenya's counties and
wards, Nigeria's own state/LGA system are not interchangeable
two-level hierarchies). The `lga_name` parameter name itself, echoed
across dozens of functions, tests, docstrings, and output paths
(`output/{lga_name}/`), would need a matching, purely mechanical rename
sweep (e.g. to `region_name`).

**A genuinely hard problem, not a tweak.** `boundary.py`'s hard
validation checks - `MIN_PLAUSIBLE_LGA_AREA_KM2` /
`MAX_PLAUSIBLE_LGA_AREA_KM2` and the `NIGERIA_BBOX` geographic check -
exist specifically to catch a wrongly resolved administrative
boundary, and they work precisely because Nigerian LGAs occupy a
known, narrow size band. That band isn't universal: a French commune
is often under 1 km²; a US county can exceed 10,000 km²; the right
plausible range depends on both country *and* which OSM `admin_level`
is being targeted. Generalizing this correctly needs a real country +
admin-level reference table, a genuine data task, not a wider hardcoded
constant. Doing this carelessly, e.g. simply removing the hard checks
to "support anywhere", would silently undo this project's own
boundary-correctness hardening (see "What it does" above); any future
generalization work should treat this as its own scoped sub-project,
not a side effect of a rename sweep.

**Why this wasn't done for this submission.** The size-band problem
above needs real reference data this project doesn't currently have,
and doing it carelessly would weaken validation this project just
spent real effort strengthening. This submission is scoped to
rigorously solve Akure's accessibility problem specifically; a
partially-generalized tool wouldn't make that story stronger, it would
dilute the depth of what's actually been validated for the LGAs this
tool set out to serve. The architecture already points toward global
generalization, what remains is one genuinely hard data problem and a
set of mechanical renames, not a redesign.

### How to actually adapt this for another country

Concretely, adapting this tool for a country outside Nigeria means
touching a small, specific set of places, not rewriting the pipeline.
In rough order of effort:

1. **Swap the country string and hierarchy in the Nominatim query.**
   `boundary.resolve_boundary()`'s query-building line
   (`f"{lga_name}, {state_name}, Nigeria"`) is the single hardcoded
   entry point. For a two-level hierarchy elsewhere (e.g. a Kenyan
   county and ward), the same pattern works with the country name and
   field labels swapped. For a country with a different administrative
   depth (a single-level query, like a French commune with no
   intermediate parent, or a three-level one), the function needs to
   accept a variable-length list of parent-area qualifiers and join
   them, rather than assuming exactly two.

2. **Supply a country + admin-level size-band reference table**, rather
   than the current single hardcoded `MIN_PLAUSIBLE_LGA_AREA_KM2` /
   `MAX_PLAUSIBLE_LGA_AREA_KM2` pair. This is the one piece of real,
   non-mechanical work: a small lookup (country code, target
   `admin_level`) to (min km², max km²), populated from a public source
   such as GADM or a national statistics agency, with the current
   Nigerian LGA band kept as one entry among many rather than the only
   entry. `NIGERIA_BBOX`'s geographic sanity check generalizes the same
   way, as a per-country bounding box looked up by country code instead
   of one constant.

3. **Rename `lga_name` to a neutral term** (e.g. `region_name`) across
   `boundary.py`, `pipeline.py`, `cli.py`, `app.py`, their tests, and
   the `output/{lga_name}/` path convention, a mechanical,
   find-and-replace-safe change with no logic implications, since the
   parameter is only ever used as an opaque string passed to Nominatim
   and to a filesystem path.

4. **Leave `clean.py`, `layers.py`, `export.py`, `manifest.py`,
   `events.py`, `logging_utils.py`, and `visualize.py` untouched.**
   None of these six modules contain a Nigeria-specific assumption:
   `utm_epsg_for_longitude()` is a pure function of latitude/longitude
   anywhere on Earth; the six default OSM layers and their tag filters
   (`highway=*`, `amenity=hospital/clinic/pharmacy`, etc.) are standard
   global OpenStreetMap tagging conventions, present in OSM data for
   most countries; the manifest schema, event system, retry/backoff
   logic, and Shapefile/GeoJSON export split are all geography-agnostic
   by construction. A generalized fork of this tool would still import
   these seven modules directly, unmodified.

In short: this codebase was deliberately built with its
country-specific logic concentrated in one function
(`boundary.resolve_boundary()`) rather than scattered throughout the
pipeline, specifically so that generalizing it later is a matter of
extending that one function's inputs and supplying real reference
data, not restructuring the seven other modules that already work
anywhere.

## License

Code: see `LICENSE`.
Data: all extracted content is © OpenStreetMap contributors, available
under the [Open Database License (ODbL)](https://www.openstreetmap.org/copyright).

## Part of

**Map<>kathon 2026**, in partnership with Unpatterned and the
OpenStreetMap Engineering Working Group.

- UseOSM: https://www.useosm.org/en/
- Map<>kathon 2026 event page: https://www.useosm.org/en/community-events/mapkaton-2026
- Sibling submission: [akure-accessibility-dashboard](https://github.com/Mapkathon2026-UseOSM/akure-accessibility-dashboard), https://akure-accessibility-dashboard-analysis.streamlit.app/
- Documentation site: https://mapkathon2026-useosm.github.io/UseOSM_Mapkathon-Submission-documentation/lga-osm-extractor/overview/
- Documentation site source repo: https://github.com/Mapkathon2026-UseOSM/UseOSM_Mapkathon-Submission-documentation

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

- **Pipeline structure and orchestration** - `lga_extractor/pipeline.py`'s
  `extract_lga()`, the single function that wires
  `boundary.resolve_boundary()` → `layers.extract_layers()` →
  `clean.clean_layers()` → `export.export_layers()` →
  `manifest.build_manifest()`/`write_manifest()` →
  `logging_utils.log_run()` into one call, plus the module layout
  itself (`boundary.py` / `layers.py` / `clean.py` / `export.py` /
  `manifest.py` / `events.py` / `visualize.py` / `logging_utils.py` as
  separate, single-purpose files) and the CLI/config design (`cli.py`'s
  argparse interface, `app.py`'s Streamlit form and live progress UI).

- **Boundary resolution and validation** - `boundary.py`'s
  `resolve_boundary()` and `_validate_and_standardize()`: the two-tier
  hard/soft validation scheme (hard checks that raise
  `BoundaryResolutionError` when a centroid falls outside
  `NIGERIA_BBOX` or an area falls outside `MIN_PLAUSIBLE_LGA_AREA_KM2`/
  `MAX_PLAUSIBLE_LGA_AREA_KM2`; a soft `admin_level`/`display_name`
  plausibility check that only warns), and debugging the specific edge
  cases that shaped those thresholds (a single resolved point/building
  vs. an entire state/country being returned by Nominatim, and OSM's
  inconsistent `admin_level` tagging across different LGAs).

- **Auto-CRS selection logic** - `clean.py`'s
  `utm_epsg_for_longitude()` and `resolve_target_crs()`: implementing
  and debugging the logic that auto-selects the correct UTM zone
  (31N/32N/33N, covering all of Nigeria) from a resolved boundary's
  centroid longitude, rather than a single hardcoded zone, verified
  against known reference points (Akure/Ondo, Abuja, Maiduguri) in
  `tests/test_extraction.py`, and recorded in every run's
  `manifest.json`/`run_log.json` as `target_crs` so it's traceable by
  a downstream consumer (the sibling dashboard's `data_contract.py`),
  not silently re-assumed there.

- **Richer OSM semantic schema** - `clean.py`'s `SEMANTIC_COLUMNS`
  (curated per-layer tag lists) and `RAW_TAGS_COLUMN` (a JSON-encoded
  fallback capturing every original OSM tag), replacing the previous
  osmid/name/geometry-only schema, plus `export.py`'s deliberate
  Shapefile/GeoJSON split (the rich schema exported to GeoJSON only,
  Shapefile kept to the core schema to avoid silent DBF field-name
  truncation/collisions on the longer semantic column names).

- **Formal extraction manifest** - `manifest.py`'s `build_manifest()`
  and `write_manifest()`: reconciling `layers.py`'s per-layer
  query-time status (success / success_empty / failed, with attempt
  counts and messages) with `export.py`'s post-cleaning export
  outcome into one JSON contract, designed specifically so a
  downstream consumer never has to infer "did this fail" from an
  ambiguous empty GeoDataFrame.

- **Strict vs. permissive extraction modes** - `layers.py`'s
  `extract_layers()` and `_extract_single_layer()`: designing the
  distinction between a genuine query failure (`LayerExtractionError`
  in strict mode) and a query that succeeds but legitimately finds
  zero features (never an error, either mode), plus the retry/backoff
  logic (`MAX_RETRIES`, `RETRY_BACKOFF_BASE_SECONDS`) and the
  staggered, capped-concurrency querying (`MAX_CONCURRENT_LAYER_QUERIES = 2`,
  `REQUEST_STAGGER_SECONDS = 3`) arrived at after fully unthrottled
  concurrency caused OSM's public Overpass mirror to refuse every
  connection outright.

- **Overpass mirror rotation and bounded boundary resolution** -
  `layers.py`'s `OVERPASS_MIRRORS` rotation (switch mirrors after 2
  consecutive failures against one, rather than retrying a server with
  no reason to unblock soon) and `boundary.py`'s
  `BOUNDARY_REQUEST_TIMEOUT_SECONDS` / `BOUNDARY_MAX_RETRIES` (bounding
  what was previously OSMnx's 180-second default timeout, indistinguishable
  from a frozen app in a live UI). Both mutate a shared `osmnx` global
  (`overpass_url`, `requests_timeout` respectively) rather than a
  per-request parameter, so both are lock-protected against the
  concurrent worker threads that query layers in parallel - a first
  version of the boundary-timeout lock had a real, narrow race (reading
  the "original" value to restore *outside* the lock, letting one
  thread capture another's temporary value), caught by a dedicated
  concurrency test before shipping, see `lga_extractor/README.md` for
  the full account.

- **UI-agnostic progress events** - `events.py`'s event schema
  (`stage_started` / `retry` / `stage_completed` / `stage_failed` /
  `pipeline_completed`) and `ThreadSafeEventQueue`, designed so
  `layers.py`/`pipeline.py` can emit live progress from concurrent
  worker threads without importing or depending on Streamlit at all,
  plus `app.py`'s consuming side: a background-thread extraction with
  a `st.status()`-based live checklist, retry counters, and a
  post-extraction summary table.

- **Results persistence across Streamlit reruns** - `app.py`'s
  extraction-results section (map, summary, download button) is now
  stored in `st.session_state` and rendered inside an `st.fragment`,
  fixing a real bug: clicking the Download button itself triggers a
  Streamlit script rerun (as any widget interaction does), and since
  the results section previously only rendered inside
  `if submitted:` - true only for the exact run the form's submit
  click caused - clicking Download made the whole results section,
  including the download button itself, disappear on the very next
  rerun. Requires `streamlit>=1.37` (`st.fragment`'s stable,
  non-experimental name), the dependency floor was bumped from `>=1.32`
  alongside this fix, `>=1.32` would still technically satisfy the old
  constraint on an install where `st.fragment` doesn't exist yet.

- **kepler.gl preview map helper and credential hygiene** -
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
administrative divisions for Ondo State, and against OSM's own
`admin_level`/`boundary=administrative` metadata where available,
rather than relying solely on name-string matching or AI-suggested
defaults.

### Limitations, risks, and uncertainty

- OSM feature completeness varies significantly across Nigerian LGAs;
  extraction results reflect underlying OSM data density, not
  ground-truth completeness.
- Strict mode may exclude legitimately tagged features with incomplete
  metadata; permissive mode may include lower-confidence matches.
- Boundary validation (`boundary._validate_and_standardize()`) checks
  bounding-box/area plausibility plus OSM's own `admin_level`/name
  metadata when present, it is still not a full verification against
  an independent, authoritative Nigerian administrative boundary
  dataset. It catches the failure modes most likely to occur (wrong
  place entirely, wrong scale entirely, wrong kind of boundary
  feature), not every possible misresolution.
- Auto-CRS selection relies on a longitude-based UTM zone formula and
  should be manually verified for LGAs outside the tested set (Akure
  North/South, plus the Abuja/Maiduguri reference points used in
  tests).
- The curated `SEMANTIC_COLUMNS` per layer are a deliberate subset of
  commonly useful OSM tags, not exhaustive; `raw_tags` is the fallback
  for anything not in that subset, but a consumer relying only on the
  named semantic columns will still miss less-common tags.
- Shapefile exports intentionally carry less attribute detail than
  GeoJSON (core schema only, see "Default OSM tags used" above); a
  consumer that needs the richer schema should use the GeoJSON export.
