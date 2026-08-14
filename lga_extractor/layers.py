"""
layers.py

Defines the default OSM tag configuration for each feature layer
(roads, buildings, waterways, land use, health facilities, schools)
and performs tag-based extraction of each layer within a resolved
LGA boundary.
"""

import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import geopandas as gpd
import osmnx as ox
import requests

from .events import _emit

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

# Public Overpass instances, each run by a different, independent
# operator. When overpass-api.de (the default) starts actively
# refusing connections mid-run — this server appears to rate-limit
# based on a client's CUMULATIVE request count over a short rolling
# window, not just simultaneous bursts, so even a staggered,
# capped-concurrency run (see MAX_CONCURRENT_LAYER_QUERIES below) can
# trip it partway through, after earlier layers already succeeded —
# retrying against that SAME server cannot succeed until its block
# window passes, which can outlast this module's entire retry budget.
# Rotating to a different operator's mirror after a couple of failed
# attempts sidesteps that: a fresh server has no reason to have
# rate-limited a client it's never talked to.
OVERPASS_MIRRORS = [
    "https://overpass-api.de/api",
    "https://overpass.kumi.systems/api",
    "https://overpass.private.coffee/api",
]

# osmnx exposes the Overpass endpoint as a single shared, mutable
# global (ox.settings.overpass_url), not a per-call parameter. Layers
# query concurrently (see MAX_CONCURRENT_LAYER_QUERIES), so switching
# mirrors is only safe if every thread that MUTATES this global does
# so under a lock: set it, issue the one request that needs it, then
# restore it, all while holding the lock. The common case (every
# attempt on the default, first mirror) never touches this lock at
# all, since it never mutates the setting, so this adds no contention
# to the normal, unblocked path.
_OVERPASS_URL_LOCK = threading.Lock()

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
# Kept deliberately low (4, not a larger number): retries beyond the
# 2nd failure on a given mirror are rarely useful on their own, since
# by then FAILURES_BEFORE_MIRROR_ROTATION has already kicked in and
# switched to a different Overpass operator, see OVERPASS_MIRRORS.
# Burning many more attempts against a server that's actively
# rate-limiting this client just adds wait time without adding a
# real chance of success. 4 gives the fallback mirror 2 attempts of
# its own (rather than just 1), a little more room in case that
# mirror also needs a moment to recover from a blip.
MAX_RETRIES = 4
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


def _query_overpass(polygon, tags: dict, mirror_url: str):
    """
    Run one osmnx.features_from_polygon() call against a specific
    Overpass mirror, rather than whatever ox.settings.overpass_url
    currently happens to be.

    If `mirror_url` is the FIRST entry in OVERPASS_MIRRORS (the
    default), this makes the call directly with no locking or global
    mutation at all, that's the common, unblocked path, and it stays
    exactly as cheap/concurrent as before this function existed.

    For any OTHER mirror, this briefly takes _OVERPASS_URL_LOCK,
    swaps ox.settings.overpass_url for the duration of the single
    request, and restores it afterward, since that setting is a
    shared global osmnx reads from any thread, see OVERPASS_MIRRORS'
    module-level docstring for why this must be lock-protected rather
    than just set-and-forget from a worker thread.
    """
    if mirror_url == OVERPASS_MIRRORS[0]:
        return ox.features_from_polygon(polygon, tags)

    with _OVERPASS_URL_LOCK:
        original = ox.settings.overpass_url
        ox.settings.overpass_url = mirror_url
        try:
            return ox.features_from_polygon(polygon, tags)
        finally:
            ox.settings.overpass_url = original


def _extract_single_layer(layer_name: str, tags: dict, polygon, start_delay: float, on_event=None):
    """
    Runs one layer's Overpass query, after waiting `start_delay` seconds
    (see REQUEST_STAGGER_SECONDS), retrying transient connection failures
    up to MAX_RETRIES times with exponential backoff, AND rotating to a
    different Overpass mirror (see OVERPASS_MIRRORS) after 2 consecutive
    transient failures on the current one.

    That rotation exists specifically because a "connection refused"
    from a public Overpass instance frequently means that instance has
    temporarily rate-limited this client based on cumulative request
    volume, not a one-off network blip, retrying the SAME server
    repeatedly cannot succeed until its block window passes, which can
    outlast this function's entire retry budget. A different operator's
    mirror has no reason to have rate-limited a client it's never seen.

    Emits "stage_started" (once, after start_delay elapses and the
    query actually begins), zero or more "retry" events (one per retry
    attempt), and exactly one terminal event, "stage_completed" on
    success (including success_empty) or "stage_failed" on failure, all
    under stage="layer:{layer_name}". Runs inside a worker thread (see
    extract_layers()), so `on_event` will be called from that thread,
    not the caller's thread, see events.py's module docstring for what
    that means for thread safety.

    Returns (layer_name, gdf, status, exc), where `status` is a dict:
        {
            "status": "success" | "success_empty" | "failed",
            "feature_count": int,
            "attempts": int,
            "message": str or None,
        }
    and `exc` is the underlying exception object on failure (None
    otherwise) — kept separate from `status` (rather than folded into
    it) purely so extract_layers() can `raise ... from exc` with a
    proper traceback in strict mode; `status` itself is plain,
    JSON-serializable data intended to be written straight into the
    run log / extraction manifest.

    This status dict is the thing that later gets promoted, unchanged,
    into the run log / extraction manifest (see logging_utils.log_run()
    and pipeline.extract_lga()) — the goal is that a caller consuming
    the log never has to re-derive "did this actually fail, or did it
    succeed and just find nothing" from an empty GeoDataFrame plus a
    free-text warning string; that distinction is computed exactly
    once, right here, and carried through as structured data.

    Never raises, so this is safe to call from worker threads,
    strict-mode raising is handled by the caller after all queries
    complete.
    """
    if start_delay > 0:
        time.sleep(start_delay)

    stage = f"layer:{layer_name}"
    _emit(on_event, {"type": "stage_started", "stage": stage})

    # How many consecutive failures to tolerate on ONE mirror before
    # assuming it's actively rate-limiting this client (rather than a
    # one-off blip) and rotating to the next. 2 gives a genuinely
    # transient blip a real second chance without burning most of the
    # retry budget on a server that's simply not going to un-block
    # itself in time.
    FAILURES_BEFORE_MIRROR_ROTATION = 2

    mirror_index = 0
    consecutive_failures_on_mirror = 0
    last_exc = None
    for attempt in range(1, MAX_RETRIES + 1):
        mirror_url = OVERPASS_MIRRORS[mirror_index]
        try:
            gdf = _query_overpass(polygon, tags, mirror_url)
            if gdf is None or gdf.empty:
                # A successful query that found nothing, valid data, not
                # a failure. Never raises, even in strict mode.
                message = f"Layer '{layer_name}' returned no features within the boundary."
                status = {
                    "status": "success_empty",
                    "feature_count": 0,
                    "attempts": attempt,
                    "message": message,
                }
                _emit(on_event, {"type": "stage_completed", "stage": stage, "status": "success_empty",
                                  "detail": "0 features"})
                return layer_name, gpd.GeoDataFrame(geometry=[], crs="EPSG:4326"), status, None
            status = {
                "status": "success",
                "feature_count": len(gdf),
                "attempts": attempt,
                "message": None,
            }
            _emit(on_event, {"type": "stage_completed", "stage": stage, "status": "success",
                              "detail": f"{len(gdf):,} features"})
            return layer_name, gdf, status, None
        except Exception as exc:
            last_exc = exc
            if attempt < MAX_RETRIES and _is_transient_connection_error(exc):
                consecutive_failures_on_mirror += 1
                retry_message = str(exc)
                if consecutive_failures_on_mirror >= FAILURES_BEFORE_MIRROR_ROTATION and len(OVERPASS_MIRRORS) > 1:
                    mirror_index = (mirror_index + 1) % len(OVERPASS_MIRRORS)
                    consecutive_failures_on_mirror = 0
                    next_mirror = OVERPASS_MIRRORS[mirror_index]
                    retry_message = f"{retry_message} — switching to mirror {next_mirror}"
                backoff = RETRY_BACKOFF_BASE_SECONDS * attempt
                _emit(on_event, {"type": "retry", "stage": stage, "attempt": attempt + 1,
                                  "max_attempts": MAX_RETRIES, "message": retry_message})
                time.sleep(backoff)
                continue
            break

    message = f"Layer '{layer_name}' failed to extract after {MAX_RETRIES} attempt(s): {last_exc}"
    status = {
        "status": "failed",
        "feature_count": 0,
        "attempts": attempt,
        "message": message,
    }
    _emit(on_event, {"type": "stage_failed", "stage": stage, "message": message})
    return layer_name, gpd.GeoDataFrame(geometry=[], crs="EPSG:4326"), status, last_exc


def extract_layers(
    boundary_gdf: gpd.GeoDataFrame, tag_config: dict = None, strict: bool = False, on_event=None
) -> dict:
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
    on_event : callable, optional
        Called with a plain event dict at each per-layer stage
        transition (started / retry / completed / failed), see
        events.py's module docstring for the full event schema and,
        importantly, the thread-safety note: this callback WILL be
        invoked concurrently from multiple worker threads, since
        layers are queried concurrently. Defaults to None (no-op).

    Returns
    -------
    dict
        Mapping of layer_name -> geopandas.GeoDataFrame (possibly
        empty if no features of that type exist within the boundary).
        In permissive mode, layers that fail to query (even after
        retries) are also returned as empty GeoDataFrames rather than
        raising, so that one missing layer does not abort extraction of
        the others; in strict mode, a genuine failure raises
        LayerExtractionError instead.

        Two accompanying keys carry the same information in different
        shapes:
        - "_warnings": a flat list of human-readable strings, kept for
          backward compatibility with existing callers/logs.
        - "_status": {layer_name: {"status", "feature_count",
          "attempts", "message"}}, the same per-layer outcome as
          STRUCTURED, machine-readable data. "status" is one of
          "success", "success_empty", "failed" — this is the field a
          downstream consumer should actually branch on; never infer
          "failed" from an empty GeoDataFrame, an empty GeoDataFrame is
          also what a genuinely empty area (status="success_empty")
          looks like, and those two situations must not be conflated.

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
    layer_status = {}
    first_error = None

    with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_LAYER_QUERIES) as executor:
        futures = {}
        for i, (layer_name, tags) in enumerate(tag_config.items()):
            # Stagger start times so no more than MAX_CONCURRENT_LAYER_QUERIES
            # requests are ever attempted at the same instant, even though
            # up to that many workers can be running concurrently overall.
            start_delay = (i // MAX_CONCURRENT_LAYER_QUERIES) * REQUEST_STAGGER_SECONDS
            future = executor.submit(_extract_single_layer, layer_name, tags, polygon, start_delay, on_event)
            futures[future] = layer_name

        for future in as_completed(futures):
            layer_name, gdf, status, exc = future.result()
            layers[layer_name] = gdf
            layer_status[layer_name] = status
            if status["message"]:
                warnings.append(status["message"])
            if status["status"] == "failed" and first_error is None:
                first_error = (layer_name, status["message"], exc)

    if strict and first_error is not None:
        _, message, exc = first_error
        raise LayerExtractionError(message) from exc

    layers["_warnings"] = warnings
    layers["_status"] = layer_status
    return layers
