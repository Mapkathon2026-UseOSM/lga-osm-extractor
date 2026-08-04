"""
layers.py

Defines the default OSM tag configuration for each feature layer
(roads, buildings, waterways, land use, health facilities, schools)
and performs tag-based extraction of each layer within a resolved
LGA boundary.
"""

import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import geopandas as gpd
import osmnx as ox
import requests

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

# Each layer is an independent Overpass API query. Running all 6 fully
# in parallel was tried first (max_workers=6, no stagger), and it made
# things WORSE, not better: the public Overpass mirror actively refused
# every single connection (Errno 111, connection refused) when 6
# requests hit it at the exact same instant, apparently treating a
# burst of simultaneous connections from one client as abusive traffic,
# rather than legitimate concurrent use. So this is deliberately more
# conservative:
#   - only 2 requests in flight at once (not 6), and
#   - each new request's start is staggered by REQUEST_STAGGER_SECONDS,
#     so the server never sees more than 2 near-simultaneous connection
#     attempts from this client, even at the very start of a run.
# This still meaningfully beats fully sequential (queries can overlap
# in twos rather than one at a time), without tripping the server's
# apparent anti-burst behavior.
MAX_CONCURRENT_LAYER_QUERIES = 2
REQUEST_STAGGER_SECONDS = 3

# Retry configuration for transient connection failures (refused
# connections, timeouts). A single refused connection on a shared
# public server is often transient, a short backoff and retry succeeds
# where an immediate second attempt would just be refused again.
MAX_RETRIES = 6
RETRY_BACKOFF_BASE_SECONDS = 5


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


def _is_transient_connection_error(exc: Exception) -> bool:
    """
    True for connection-level failures (refused connections, timeouts,
    DNS hiccups) worth retrying, as opposed to e.g. a malformed tag
    filter, which will just fail identically every time and shouldn't
    burn retry attempts.
    """
    return isinstance(exc, (requests.exceptions.ConnectionError, requests.exceptions.Timeout))


def _extract_single_layer(layer_name: str, tags: dict, polygon, start_delay: float):
    """
    Runs one layer's Overpass query, after waiting `start_delay` seconds
    (see REQUEST_STAGGER_SECONDS), retrying transient connection failures
    up to MAX_RETRIES times with exponential backoff. Returns
    (layer_name, gdf, warning_or_None, error_or_None). Never raises, so
    this is safe to call from worker threads, strict-mode raising is
    handled by the caller after all queries complete.
    """
    if start_delay > 0:
        time.sleep(start_delay)

    last_exc = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            gdf = ox.features_from_polygon(polygon, tags)
            if gdf is None or gdf.empty:
                # A successful query that found nothing, valid data, not
                # a failure. Never raises, even in strict mode.
                warning = f"Layer '{layer_name}' returned no features within the boundary."
                return layer_name, gpd.GeoDataFrame(geometry=[], crs="EPSG:4326"), warning, None
            return layer_name, gdf, None, None
        except Exception as exc:
            last_exc = exc
            if attempt < MAX_RETRIES and _is_transient_connection_error(exc):
                backoff = RETRY_BACKOFF_BASE_SECONDS * attempt
                time.sleep(backoff)
                continue
            break

    message = f"Layer '{layer_name}' failed to extract after {MAX_RETRIES} attempt(s): {last_exc}"
    return layer_name, gpd.GeoDataFrame(geometry=[], crs="EPSG:4326"), message, last_exc


def extract_layers(boundary_gdf: gpd.GeoDataFrame, tag_config: dict = None, strict: bool = False) -> dict:
    """
    Extract OSM feature layers within a boundary polygon.

    Layers are queried with LIMITED, STAGGERED concurrency (at most
    MAX_CONCURRENT_LAYER_QUERIES requests in flight at once, each new
    request's start delayed by REQUEST_STAGGER_SECONDS), and transient
    connection failures are retried with backoff. Fully unthrottled
    concurrency (all layers at once) was tried and made things worse:
    the public Overpass mirror refused every connection outright when
    hit with a burst of simultaneous requests from one client. This
    staggered, capped approach still overlaps queries (faster than
    fully sequential), without tripping that behavior.

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
        exception raised by the underlying OSM query, that also
        survives all retry attempts) is handled:

        - False (default, "permissive" mode): the failure is caught,
          recorded as a warning, and that layer is returned as an empty
          GeoDataFrame so the rest of the extraction can continue. This
          is convenient for demos and exploratory use, where a single
          flaky layer shouldn't abort the whole run.
        - True ("strict" mode): once all layer queries have completed,
          the first genuine failure encountered is raised as a
          LayerExtractionError. This is appropriate for CI/automated
          pipelines, where a silent failure masquerading as "this area
          has no data" could silently corrupt downstream analysis
          without anyone noticing.

        Either way, a layer that queries successfully but genuinely
        finds zero features is NOT treated as a failure, that's valid
        data (and can itself be a meaningful completeness signal), so
        it never raises, and is only ever recorded as a warning.

    Returns
    -------
    dict
        Mapping of layer_name -> geopandas.GeoDataFrame (possibly
        empty if no features of that type exist within the boundary).
        In permissive mode, layers that fail to query (even after
        retries) are also returned as empty GeoDataFrames rather than
        raising, so that one missing layer does not abort extraction of
        the others; in strict mode, a genuine failure raises
        LayerExtractionError instead. Either way, failures/genuine
        emptiness are reported via the returned dict's accompanying
        "_warnings" list.

    Raises
    ------
    LayerExtractionError
        If `strict=True` and a layer's OSM query genuinely fails (not
        simply returns zero features) after all retry attempts.
    """
    if tag_config is None:
        tag_config = DEFAULT_TAG_CONFIG

    polygon = boundary_gdf.geometry.iloc[0]
    layers = {}
    warnings = []
    first_error = None

    with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_LAYER_QUERIES) as executor:
        futures = {}
        for i, (layer_name, tags) in enumerate(tag_config.items()):
            # Stagger start times so no more than MAX_CONCURRENT_LAYER_QUERIES
            # requests are ever attempted at the same instant, even though
            # up to that many workers can be running concurrently overall.
            start_delay = (i // MAX_CONCURRENT_LAYER_QUERIES) * REQUEST_STAGGER_SECONDS
            future = executor.submit(_extract_single_layer, layer_name, tags, polygon, start_delay)
            futures[future] = layer_name

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
