"""
layers.py

Defines the default OSM tag configuration for each feature layer
(roads, buildings, waterways, land use, health facilities, schools)
and performs tag-based extraction of each layer within a resolved
LGA boundary.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed

import geopandas as gpd
import osmnx as ox

# Default tag-to-layer configuration.
# Each entry maps a layer name to the OSM tag filter used with
# osmnx.features_from_polygon(). Users can extend or override this
# dictionary to add layers (e.g. markets, places of worship) without
# touching the extraction logic itself.
DEFAULT_TAG_CONFIG = {
    "roads": {"highway": True},
    "buildings": {"building": True},
    "waterways": {"waterway": True, "natural": "water"},
    "landuse": {"landuse": True},
    "health_facilities": {"amenity": ["hospital", "clinic", "pharmacy"]},
    "schools": {"amenity": "school"},
}

# Each layer is an independent Overpass API query. The public Overpass
# endpoint is shared and queue-based, so 6 layers run one-after-another
# means paying that queue/round-trip cost 6 times over. Running them
# concurrently means total wall-clock time is roughly the SLOWEST single
# layer's query time, not the sum of all 6, this is the single biggest
# lever for reducing extraction time, since layers.py's own per-layer
# work (the Overpass call itself) dominates total runtime far more than
# anything in clean.py or export.py downstream.
#
# 6 is a safe default: it matches the number of layers exactly (no
# layer waits for a free worker), while staying well under limits
# that would trigger the public Overpass endpoint's own throttling of
# a single client sending too many simultaneous requests.
MAX_CONCURRENT_LAYER_QUERIES = 6


class LayerExtractionError(Exception):
    """
    Raised when a layer genuinely fails to extract (an Overpass error,
    timeout, network failure, or bad tag configuration) while running
    in strict mode, see extract_layers()'s `strict` parameter.

    This is distinct from a layer legitimately returning zero features
    (a successful query that simply found nothing of that type in the
    boundary), that is valid data, not a failure, and never raises
    this exception even in strict mode.
    """
    pass


def _extract_single_layer(layer_name: str, tags: dict, polygon):
    """
    Runs one layer's Overpass query. Returns (layer_name, gdf, warning_or_None,
    error_or_None). Never raises, so this is safe to call from worker threads,
    strict-mode raising is handled by the caller after all queries complete.
    """
    try:
        gdf = ox.features_from_polygon(polygon, tags)
        if gdf is None or gdf.empty:
            # A successful query that found nothing, valid data, not a
            # failure. Never raises, even in strict mode.
            warning = f"Layer '{layer_name}' returned no features within the boundary."
            return layer_name, gpd.GeoDataFrame(geometry=[], crs="EPSG:4326"), warning, None
        return layer_name, gdf, None, None
    except Exception as exc:
        message = f"Layer '{layer_name}' failed to extract: {exc}"
        return layer_name, gpd.GeoDataFrame(geometry=[], crs="EPSG:4326"), message, exc


def extract_layers(boundary_gdf: gpd.GeoDataFrame, tag_config: dict = None, strict: bool = False) -> dict:
    """
    Extract OSM feature layers within a boundary polygon.

    Layers are queried CONCURRENTLY (one Overpass request per layer,
    running in parallel via a thread pool), since each layer's query is
    independent of the others and I/O-bound (waiting on the Overpass
    API), not CPU-bound, this is a safe and significant speedup over
    querying layers one at a time.

    Parameters
    ----------
    boundary_gdf : geopandas.GeoDataFrame
        A single-row GeoDataFrame containing the LGA boundary,
        as returned by boundary.resolve_boundary().
    tag_config : dict, optional
        Mapping of layer_name -> OSM tag filter dict, in the format
        expected by osmnx.features_from_polygon(). Defaults to
        DEFAULT_TAG_CONFIG if not provided.
    strict : bool, default False
        Controls how a genuine extraction FAILURE (an Overpass error,
        timeout, network failure, or bad tag configuration, i.e. an
        exception raised by the underlying OSM query) is handled:

        - False (default, "permissive" mode): the failure is caught,
          recorded as a warning, and that layer is returned as an empty
          GeoDataFrame so the rest of the extraction can continue. This
          is convenient for demos and exploratory use, where a single
          flaky layer shouldn't abort the whole run.
        - True ("strict" mode): as soon as ALL layer queries have
          completed (queries are already in flight concurrently, so a
          strict failure can't abort other layers mid-query the way
          it could when queries ran sequentially), the first genuine
          failure encountered is raised as a LayerExtractionError. This
          is appropriate for CI/automated pipelines, where a silent
          failure masquerading as "this area has no data" could
          silently corrupt downstream analysis without anyone noticing.

        Either way, a layer that queries successfully but genuinely
        finds zero features is NOT treated as a failure, that's valid
        data (and can itself be a meaningful completeness signal), so
        it never raises, and is only ever recorded as a warning.

    Returns
    -------
    dict
        Mapping of layer_name -> geopandas.GeoDataFrame (possibly
        empty if no features of that type exist within the boundary).
        In permissive mode, layers that fail to query are also returned
        as empty GeoDataFrames rather than raising, so that one missing
        layer does not abort extraction of the others; in strict mode,
        a genuine failure raises LayerExtractionError instead. Either
        way, failures/genuine emptiness are reported via the returned
        dict's accompanying "_warnings" list.

    Raises
    ------
    LayerExtractionError
        If `strict=True` and a layer's OSM query genuinely fails (not
        simply returns zero features).
    """
    if tag_config is None:
        tag_config = DEFAULT_TAG_CONFIG

    polygon = boundary_gdf.geometry.iloc[0]
    layers = {}
    warnings = []
    first_error = None

    with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_LAYER_QUERIES) as executor:
        futures = {
            executor.submit(_extract_single_layer, layer_name, tags, polygon): layer_name
            for layer_name, tags in tag_config.items()
        }
        for future in as_completed(futures):
            layer_name, gdf, warning, error = future.result()
            layers[layer_name] = gdf
            if warning:
                warnings.append(warning)
            if error is not None and first_error is None:
                first_error = (layer_name, warning, error)

    if strict and first_error is not None:
        _, message, error = first_error
        raise LayerExtractionError(message) from error

    layers["_warnings"] = warnings
    return layers
