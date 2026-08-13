"""
pipeline.py

High-level convenience wrapper that runs the full extraction pipeline
for a single Nigerian LGA end-to-end: boundary resolution -> layer
extraction -> cleaning -> export -> run logging.
"""

import os

from .boundary import resolve_boundary
from .layers import extract_layers, DEFAULT_TAG_CONFIG, LayerExtractionError
from .clean import clean_layers, resolve_target_crs
from .export import export_layers
from .logging_utils import log_run
from .manifest import build_manifest, write_manifest
from .events import _emit


def extract_lga(
    lga_name: str,
    state_name: str = None,
    output_dir: str = None,
    tag_config: dict = None,
    manual_boundary_path: str = None,
    strict: bool = False,
    on_event=None,
) -> dict:
    """
    Run the full OSM extraction pipeline for a single Nigerian LGA.

    Parameters
    ----------
    lga_name : str
        Name of the LGA, e.g. "Akure North".
    state_name : str, optional
        Name of the state, e.g. "Ondo". Recommended for disambiguation.
    output_dir : str, optional
        Directory to write outputs into. Defaults to
        "output/{lga_name}" (spaces replaced with underscores) if not
        provided.
    tag_config : dict, optional
        Custom layer -> OSM tag filter mapping. Defaults to
        layers.DEFAULT_TAG_CONFIG.
    manual_boundary_path : str, optional
        Path to a manual boundary file to use instead of OSM geocoding.
    strict : bool, default False
        Controls how a genuine layer-extraction FAILURE is handled, see layers.extract_layers()'s `strict` parameter for the full
        explanation and the distinction between "a layer genuinely
        failed to query" versus "a layer queried successfully but found
        zero features" (the latter never raises, regardless of this
        setting). Use strict=True for automated/CI pipelines where a
        silent failure could corrupt downstream analysis unnoticed; use
        the permissive default for exploratory/demo use where a single
        flaky layer shouldn't abort the whole run.
    on_event : callable, optional
        Called with a plain event dict (see events.py's module
        docstring for the full schema) as the pipeline moves through
        its stages: boundary resolution, each layer's extraction
        (including retries), cleaning, and export, plus a final
        "pipeline_completed" event carrying this function's own return
        value. Intended for driving a progress UI (see app.py) without
        making this module depend on any particular UI framework, the
        pipeline itself stays completely UI-agnostic. Defaults to None
        (no-op) note that events for concurrent layer extraction are
        emitted from worker threads, not this function's caller thread,
        see events.py for the thread-safety implications.

    Returns
    -------
    dict
        Summary containing the resolved boundary source, the path to
        the exported boundary GeoJSON (see "boundary_path" -- this is
        what a downstream consumer should read instead of calling
        resolve_boundary() again live), exported file paths per layer,
        any warnings encountered, and the path to the written run log.
    """
    if output_dir is None:
        safe_name = lga_name.strip().replace(" ", "_").lower()
        output_dir = f"output/{safe_name}"

    if tag_config is None:
        tag_config = DEFAULT_TAG_CONFIG

    _emit(on_event, {"type": "stage_started", "stage": "boundary"})
    boundary_gdf = resolve_boundary(
        lga_name=lga_name,
        state_name=state_name,
        manual_boundary_path=manual_boundary_path,
    )
    boundary_source = boundary_gdf["boundary_source"].iloc[0]
    boundary_validation_warning = boundary_gdf["validation_warnings"].iloc[0]
    _emit(on_event, {"type": "stage_completed", "stage": "boundary",
                      "detail": f"{lga_name}, {state_name or '?'} (source: {boundary_source})"})

    raw_layers = extract_layers(boundary_gdf, tag_config=tag_config, strict=strict, on_event=on_event)
    warnings = raw_layers.get("_warnings", [])
    layer_status = raw_layers.get("_status", {})
    # Fold in the boundary's own soft validation warning (if any, see
    # boundary._validate_and_standardize()'s "SOFT check" documentation)
    # alongside per-layer extraction warnings, so both show up together
    # in one place for anyone reviewing this run.
    if boundary_validation_warning:
        warnings = [f"Boundary validation: {boundary_validation_warning}"] + list(warnings)
        _emit(on_event, {"type": "warning", "stage": "boundary", "message": boundary_validation_warning})

    _emit(on_event, {"type": "stage_started", "stage": "cleaning"})
    cleaned = clean_layers(raw_layers, boundary_gdf=boundary_gdf)
    cleaned.pop("_warnings", None)
    cleaned.pop("_status", None)
    _emit(on_event, {"type": "stage_completed", "stage": "cleaning"})

    _emit(on_event, {"type": "stage_started", "stage": "export"})
    exported = export_layers(cleaned, output_dir)

    # Write the boundary polygon itself to disk, alongside the layer
    # exports, in WGS84 (boundary_gdf is already EPSG:4326, resolve_
    # boundary()'s own standardized CRS -- see boundary.py -- so no
    # reprojection is needed here). This closes a real gap: without
    # this file, a downstream consumer that needs the boundary polygon
    # itself (not just the layer data) -- e.g. akure_access.accessibility.
    # scoring.add_access_times()'s boundary_polygon_wgs84 parameter,
    # used for consistent centroid reprojection -- had no choice but to
    # call boundary.resolve_boundary() again, live, even when every
    # other input was already being read from the extractor's cached
    # output. That made the "canonical dataset, no live OSM required"
    # story incomplete in practice: every other file could be read from
    # disk, but the boundary specifically still forced a fresh
    # Nominatim geocode call on every downstream run. Written as part
    # of the "export" stage (not its own event stage) since, from an
    # observer's perspective, it's the same conceptual step as writing
    # every other file this pipeline produces.
    boundary_path = os.path.join(output_dir, "boundary.geojson")
    boundary_gdf.to_file(boundary_path, driver="GeoJSON")
    _emit(on_event, {"type": "stage_completed", "stage": "export"})

    # Resolve the CRS the same way clean_layers() did internally, purely
    # to record it in the run log, calling this directly (rather than
    # inferring from cleaned layer output) avoids getting a wrong/None
    # answer if the first layer happens to be empty, since
    # _clean_single_layer() returns an empty GeoDataFrame early, before
    # reprojection, for empty inputs.
    resolved_crs = resolve_target_crs(boundary_gdf)

    # Build and write the formal extraction manifest: the structured
    # contract a downstream consumer (e.g. the accessibility dashboard)
    # should read for CRS and per-layer query/export outcome, instead
    # of hardcoding a CRS or inferring success/failure from file
    # presence or an ambiguous empty GeoDataFrame. See manifest.py's
    # module docstring for the full rationale. Not its own progress
    # stage (app.py's UI has no row for it) -- it's near-instant
    # bookkeeping, not a user-visible step worth its own checklist row.
    manifest = build_manifest(
        lga_name=lga_name,
        state_name=state_name,
        target_crs=resolved_crs,
        boundary_source=boundary_source,
        layer_status=layer_status,
        exported=exported,
        boundary_path=boundary_path,
    )
    manifest_path = write_manifest(manifest, output_dir)

    log_path = log_run(
        lga_name=lga_name,
        state_name=state_name,
        tag_config=tag_config,
        output_dir=output_dir,
        boundary_source=boundary_source,
        warnings=warnings,
        exported=exported,
        target_crs=resolved_crs,
        layer_status=layer_status,
    )

    result = {
        "lga_name": lga_name,
        "state_name": state_name,
        "output_dir": output_dir,
        "boundary_source": boundary_source,
        "boundary_path": boundary_path,
        "target_crs": resolved_crs,
        "exported": exported,
        "warnings": warnings,
        "layer_status": layer_status,
        "manifest": manifest,
        "manifest_path": manifest_path,
        "run_log": log_path,
    }
    _emit(on_event, {"type": "pipeline_completed", "summary": result})
    return result
