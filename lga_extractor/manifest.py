"""
manifest.py

Builds the formal extraction manifest: the machine-readable contract
a downstream consumer (chiefly akure_access_dashboard, but any future
consumer too) should read instead of inferring extraction outcome from
file presence/absence or an empty GeoDataFrame.

This exists because "empty GeoDataFrame" is ambiguous on its own — it's
what BOTH a genuinely empty area (query succeeded, found nothing) and a
failed query (Overpass down, timed out, bad tags) look like once
they've been through the extraction pipeline. layers.extract_layers()
already computes the distinction internally, per layer, as it happens
(see layers._extract_single_layer()'s returned status dict); this
module's only job is to combine that already-computed per-layer status
with the export outcome (paths, post-cleaning feature counts) and the
resolved CRS into one flat, JSON-serializable structure, and write it
to disk as its own top-level file (manifest.json) rather than leaving
it buried inside run_log.json, so a consumer can depend on
manifest.json's shape as a stable contract without needing to also
understand the run log's broader, more free-form structure.
"""

import json
import os
from datetime import datetime, timezone

MANIFEST_SCHEMA_VERSION = 1


def build_manifest(
    lga_name: str,
    state_name: str,
    target_crs: str,
    boundary_source: str,
    layer_status: dict,
    exported: dict,
    boundary_path: str = None,
) -> dict:
    """
    Combine per-layer query status (from layers.extract_layers()'s
    "_status") with per-layer export outcome (from
    export.export_layers()) into one manifest dict.

    Parameters
    ----------
    lga_name, state_name : str
    target_crs : str
        The CRS actually used to clean/export this run's layers, e.g.
        "EPSG:32631" (see clean.resolve_target_crs()).
    boundary_source : str
        As returned by boundary.resolve_boundary().
    layer_status : dict
        The "_status" dict from layers.extract_layers()'s return
        value: {layer_name: {"status", "feature_count", "attempts",
        "message"}}. This is the query-time outcome, before cleaning.
    exported : dict
        The dict returned by export.export_layers(): {layer_name:
        {"geojson", "shapefile", "feature_count"}, "_skipped": [...],
        "_split_layers": {...}}. "feature_count" here is the
        post-cleaning count actually written to disk, which is what a
        downstream consumer cares about (it's the count in the file
        they'll load), and can legitimately differ from the query-time
        feature_count in `layer_status` (cleaning drops invalid/empty/
        duplicate geometries).
    boundary_path : str, optional
        Path to the exported boundary GeoJSON file (see
        pipeline.extract_lga(), which writes this alongside the layer
        exports), so a downstream consumer can read the exact
        boundary polygon this extraction run actually used, instead of
        re-resolving it live via a fresh OSM/Nominatim geocode call.
        This is the fix for the gap where, even with per-layer data
        already being read from disk, a consumer still needed a live
        network call just to obtain the boundary polygon itself (e.g.
        to reproject grid-cell centroids consistently, see
        akure_access.accessibility.scoring.add_access_times()). None
        if the boundary wasn't exported for this run (e.g. an older
        pipeline version, or the export step was skipped).

    Returns
    -------
    dict
        {
          "schema_version": 1,
          "lga_name": ...,
          "state_name": ...,
          "extracted_at": "<UTC ISO8601>",
          "target_crs": "EPSG:32631",
          "boundary_source": ...,
          "boundary_path": "output/akure_north/boundary.geojson" or None,
          "source": "OpenStreetMap",
          "layers": {
            "<layer_name>": {
              "query_status": "success" | "success_empty" | "failed",
              "query_attempts": int,
              "query_message": str or None,
              "feature_count": int,          # post-cleaning, as exported
              "feature_count_raw": int,      # pre-cleaning, at query time
              "exported": bool,              # False if skipped (empty after cleaning)
              "geojson_path": str or None,
              "shapefile_path": str or dict or None,
            },
            ...
          }
        }
    """
    skipped = set((exported or {}).get("_skipped", []))
    layers_out = {}

    all_layer_names = set(layer_status.keys()) | (
        set((exported or {}).keys()) - {"_skipped", "_split_layers"}
    )

    for layer_name in sorted(all_layer_names):
        query = layer_status.get(layer_name, {})
        export_info = (exported or {}).get(layer_name)

        layers_out[layer_name] = {
            "query_status": query.get("status", "unknown"),
            "query_attempts": query.get("attempts"),
            "query_message": query.get("message"),
            "feature_count": export_info["feature_count"] if export_info else query.get("feature_count", 0),
            "feature_count_raw": query.get("feature_count", 0),
            "exported": export_info is not None,
            "geojson_path": export_info["geojson"] if export_info else None,
            "shapefile_path": export_info["shapefile"] if export_info else None,
        }
        if layer_name in skipped and export_info is None:
            layers_out[layer_name]["query_status"] = layers_out[layer_name]["query_status"] or "success_empty"

    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "lga_name": lga_name,
        "state_name": state_name,
        "extracted_at": datetime.now(timezone.utc).isoformat(),
        "target_crs": target_crs,
        "boundary_source": boundary_source,
        "boundary_path": boundary_path,
        "source": "OpenStreetMap",
        "layers": layers_out,
    }


def write_manifest(manifest: dict, output_dir: str) -> str:
    """
    Write the manifest to '{output_dir}/manifest.json'.

    This is the file a downstream consumer (e.g. the accessibility
    dashboard's CRS-handling code) should read, rather than
    hardcoding assumptions like a fixed UTM zone, or trying to infer
    a target_crs from the run log's less-stable structure.
    """
    os.makedirs(output_dir, exist_ok=True)
    manifest_path = os.path.join(output_dir, "manifest.json")
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)
    return manifest_path
