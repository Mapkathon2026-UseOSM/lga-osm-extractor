"""
pipeline.py

High-level convenience wrapper that runs the full extraction pipeline
for a single Nigerian LGA end-to-end: boundary resolution -> layer
extraction -> cleaning -> export -> run logging.
"""

from .boundary import resolve_boundary
from .layers import extract_layers, DEFAULT_TAG_CONFIG, LayerExtractionError
from .clean import clean_layers, resolve_target_crs
from .export import export_layers
from .logging_utils import log_run


def extract_lga(
    lga_name: str,
    state_name: str = None,
    output_dir: str = None,
    tag_config: dict = None,
    manual_boundary_path: str = None,
    strict: bool = False,
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
        Controls how a genuine layer-extraction FAILURE is handled --
        see layers.extract_layers()'s `strict` parameter for the full
        explanation and the distinction between "a layer genuinely
        failed to query" versus "a layer queried successfully but found
        zero features" (the latter never raises, regardless of this
        setting). Use strict=True for automated/CI pipelines where a
        silent failure could corrupt downstream analysis unnoticed; use
        the permissive default for exploratory/demo use where a single
        flaky layer shouldn't abort the whole run.

    Returns
    -------
    dict
        Summary containing the resolved boundary source, exported file
        paths per layer, any warnings encountered, and the path to the
        written run log.
    """
    if output_dir is None:
        safe_name = lga_name.strip().replace(" ", "_").lower()
        output_dir = f"output/{safe_name}"

    if tag_config is None:
        tag_config = DEFAULT_TAG_CONFIG

    boundary_gdf = resolve_boundary(
        lga_name=lga_name,
        state_name=state_name,
        manual_boundary_path=manual_boundary_path,
    )
    boundary_source = boundary_gdf["boundary_source"].iloc[0]
    boundary_validation_warning = boundary_gdf["validation_warnings"].iloc[0]

    raw_layers = extract_layers(boundary_gdf, tag_config=tag_config, strict=strict)
    warnings = raw_layers.get("_warnings", [])
    # Fold in the boundary's own soft validation warning (if any -- see
    # boundary._validate_and_standardize()'s "SOFT check" documentation)
    # alongside per-layer extraction warnings, so both show up together
    # in one place for anyone reviewing this run.
    if boundary_validation_warning:
        warnings = [f"Boundary validation: {boundary_validation_warning}"] + list(warnings)

    cleaned = clean_layers(raw_layers, boundary_gdf=boundary_gdf)
    cleaned.pop("_warnings", None)

    exported = export_layers(cleaned, output_dir)

    # Resolve the CRS the same way clean_layers() did internally, purely
    # to record it in the run log -- calling this directly (rather than
    # inferring from cleaned layer output) avoids getting a wrong/None
    # answer if the first layer happens to be empty, since
    # _clean_single_layer() returns an empty GeoDataFrame early, before
    # reprojection, for empty inputs.
    resolved_crs = resolve_target_crs(boundary_gdf)

    log_path = log_run(
        lga_name=lga_name,
        state_name=state_name,
        tag_config=tag_config,
        output_dir=output_dir,
        boundary_source=boundary_source,
        warnings=warnings,
        exported=exported,
        target_crs=resolved_crs,
    )

    return {
        "lga_name": lga_name,
        "state_name": state_name,
        "output_dir": output_dir,
        "boundary_source": boundary_source,
        "target_crs": resolved_crs,
        "exported": exported,
        "warnings": warnings,
        "run_log": log_path,
    }
