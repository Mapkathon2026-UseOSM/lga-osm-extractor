# examples/

## Purpose

A single worked-example notebook, `extract_akure_lgas.ipynb`, that
exercises the full `lga_extractor` pipeline end to end against two
real LGAs (Akure North and Akure South, Ondo State), the same two
areas the sibling `akure-accessibility-dashboard` repo analyzes. It
doubles as both a tutorial (read it to learn the package) and a real
provenance record (its outputs are the actual data the accessibility
project was built from).

## What it walks through

Eight numbered sections, in order:

1. **Introduction** — what this notebook is and what it produces.
2. **Imports** — the one import that matters: `extract_lga`.
3. **Configuration** — Colab-vs-local environment detection, then both
   study areas' names/states collected in one place.
4. **Data loading** — runs `extract_lga()` for Akure North, then Akure
   South, one call each.
5. **Processing** — validates both extractions (feature counts per
   layer, CRS), then a side-by-side comparison table between the two
   LGAs, a quick way to catch an unexpectedly empty layer.
6. **Visualization** — a fast matplotlib sanity-check plot, then a
   polished kepler.gl preview map via `build_preview_map()`.
7. **Export** — inspects each run's `run_log.json` to show exactly
   what was queried and when, for reproducibility.
8. **Summary** — recap of what was extracted and where to find it.

## Running it

```bash
cd examples
jupyter notebook extract_akure_lgas.ipynb
```

Or open it directly in Google Colab, the first code cell detects the
environment automatically and adjusts paths/installs accordingly, no
manual editing needed either way.

Takes a few minutes to run end to end (two full LGA extractions
against OSM's live Overpass API, see the root README's "why extraction
can take a few minutes" note, same underlying constraint here).

## Outputs

Running this notebook produces:

- `../output/akure_north/`, `../output/akure_south/` — GeoJSON +
  Shapefile exports per layer, plus a `run_log.json` each.
- `../visuals/akure_north_preview.html`, `../visuals/akure_south_preview.html`
  — standalone kepler.gl preview maps (already committed in this repo
  from a prior run, so you can view them without re-running anything).

## Related

- `../lga_extractor/README.md` — the package this notebook exercises,
  module by module.
- [`akure-accessibility-dashboard`](https://github.com/Mapkathon2026-UseOSM/akure-accessibility-dashboard) — the repo that
  consumes exactly this notebook's output for the real accessibility analysis.
