"""
boundary.py

Resolves a Nigerian LGA name (optionally with state) into a validated
administrative boundary polygon using OSMnx / OSM administrative
relations, with a manual-boundary fallback path.
"""

import time
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError

import geopandas as gpd
import pandas as pd
from shapely.geometry.base import BaseGeometry
import osmnx as ox

# Boundary resolution calls OSM's Nominatim geocoder (osmnx.geocode_to_gdf()).
# Two independent things can make a single attempt run far longer than
# BOUNDARY_REQUEST_TIMEOUT_SECONDS would suggest:
#
#   1. ox.settings.requests_timeout only bounds a single underlying HTTP
#      request. It does NOT bound the overall call, because...
#   2. ...osmnx's own Nominatim client (osmnx/_nominatim.py) handles HTTP
#      429 (rate-limited) and 504 (gateway timeout) responses by sleeping
#      55 seconds and then recursively re-issuing the request, with no
#      retry cap and no exception ever raised for this case. On shared
#      infrastructure (e.g. many Streamlit Cloud apps behind the same
#      outbound IP hitting Nominatim's public instance), this recursion
#      is what actually produces multi-minute "stuck" progress bars: the
#      call is technically still running, just invisibly retrying inside
#      a library we don't control, well past our own timeout setting.
#
# Because that internal retry never raises, our OWN retry loop below
# can't rely on catching an exception to know an attempt is taking too
# long -- we have to enforce a hard WALL-CLOCK timeout around the entire
# call ourselves (see _geocode_with_hard_timeout()), independent of
# whatever osmnx is doing internally.
BOUNDARY_REQUEST_TIMEOUT_SECONDS = 30
BOUNDARY_MAX_RETRIES = 3
BOUNDARY_RETRY_BACKOFF_BASE_SECONDS = 5

# Wall-clock cap (seconds) for one resolve attempt, enforced by running
# the call in a worker thread and giving up on waiting for it after this
# many seconds -- regardless of what osmnx is doing internally (see the
# 429/504 recursion note above). This is intentionally larger than
# BOUNDARY_REQUEST_TIMEOUT_SECONDS: a single legitimate slow-but-successful
# Nominatim response should still be allowed to complete, but an attempt
# stuck in osmnx's internal 55s-pause retry loop should not be allowed to
# consume many minutes before we even notice.
#
# Note: Python cannot forcibly kill a running thread. If an attempt times
# out, the worker thread is abandoned (left to finish or fail on its own)
# and we move on to the next attempt/failure rather than waiting on it --
# this bounds *our* wait, not the abandoned thread's actual lifetime.
BOUNDARY_HARD_WALL_CLOCK_TIMEOUT_SECONDS = 45

# A descriptive User-Agent (and, ideally, contact info) is expected by
# Nominatim's usage policy and can reduce how aggressively a shared/generic
# client identity gets rate-limited. osmnx passes this through via
# ox.settings.requests_kwargs on every HTTP request it makes (Nominatim and
# Overpass alike).
NOMINATIM_USER_AGENT = "lga-osm-extractor/1.0 (Map<>kathon 2026; contact via GitHub Mapkathon2026-UseOSM)"


def _geocode_with_hard_timeout(query: str, timeout_seconds: float):
    """
    Call ox.geocode_to_gdf(query) but never wait longer than
    timeout_seconds for it to return, regardless of what osmnx is doing
    internally (including its own unbounded 429/504 retry recursion, see
    the module-level note above).

    Raises
    ------
    FutureTimeoutError
        If the call has not completed within timeout_seconds. The
        underlying worker thread is NOT cancelled (Python threads can't
        be force-killed) -- it is simply abandoned and left to finish or
        error out on its own, unobserved.
    Exception
        Whatever ox.geocode_to_gdf() itself raised, if it completed
        (unsuccessfully) within the timeout.
    """
    # Deliberately NOT using ThreadPoolExecutor as a context manager here:
    # `with` calls executor.shutdown(wait=True) on exit, which would block
    # until the worker thread finishes -- exactly the multi-minute wait
    # we're trying to avoid. Instead we shut down with wait=False, which
    # lets the (possibly still-hung) worker thread be abandoned in the
    # background while this function returns/raises immediately.
    executor = ThreadPoolExecutor(max_workers=1)
    try:
        future = executor.submit(ox.geocode_to_gdf, query)
        return future.result(timeout=timeout_seconds)
    finally:
        executor.shutdown(wait=False)

# ox.settings.requests_timeout is a single shared, mutable global inside
# osmnx, not a per-call parameter (see layers.py's OVERPASS_MIRRORS /
# _OVERPASS_URL_LOCK docstring for the identical underlying issue with
# ox.settings.overpass_url). resolve_boundary() is always the FIRST
# stage of the pipeline today, and is never called concurrently with
# itself in current usage, so mutating this global without a lock is
# safe in practice right now, but only because of how the pipeline
# happens to call this function, not because this function enforces
# that safety itself. Locking the mutation removes that hidden
# assumption entirely, at effectively zero cost (this lock is only ever
# held for the duration of setting/restoring one attribute around a
# single geocode call, never contended in the common single-LGA-at-a-
# time case), so there's no reason not to hold it even though nothing
# currently exercises the concurrent path this protects against -- a
# future batch-extraction feature calling resolve_boundary() for
# several LGAs in parallel would otherwise silently race on this
# exact global, the same class of bug the Overpass mirror rotation was
# careful to avoid from the start.
_BOUNDARY_TIMEOUT_LOCK = threading.Lock()

# Nigeria's approximate bounding box (min_lon, min_lat, max_lon, max_lat),
# in WGS84 degrees, with generous margin. Used as a coarse geographic
# sanity check: a resolved boundary whose centroid falls well outside
# this box strongly suggests OSM/Nominatim resolved the wrong place
# entirely (a name collision, or an unrelated result), not just an
# LGA with unusual shape/size.
NIGERIA_BBOX = (2.5, 4.0, 15.0, 14.0)

# Plausible LGA area range, in km^2. Nigerian LGAs vary a lot in size
# (a dense urban LGA can be tens of km^2; some sparse northern LGAs
# exceed 1000 km^2), so these bounds are intentionally generous, # they're meant to catch "a single building/point was resolved" or "an
# entire state/the whole country was resolved" (both real failure
# modes for a geocoding-based lookup), not to flag genuinely unusual
# but valid LGA shapes.
MIN_PLAUSIBLE_LGA_AREA_KM2 = 2
MAX_PLAUSIBLE_LGA_AREA_KM2 = 10_000

# Plausible OSM admin_level range for a Nigerian LGA-scale administrative
# boundary. Nigeria's LGAs are conventionally tagged admin_level=6, but
# this is kept as a wide, generous band (not a strict ==6 check) since
# admin_level tagging in OSM is not perfectly consistent everywhere, and
# this check is deliberately advisory (a warning), not authoritative --
# see the "best-effort" note on this check below. A future pass that
# fetches extratags/Overpass relation metadata directly could tighten
# this to a firm admin_level == 6 hard check; this first pass does not,
# since admin_level is not reliably present in OSMnx's default geocode
# result and we don't want to force an extra network round-trip here.
PLAUSIBLE_ADMIN_LEVEL_RANGE = (3, 10)


class BoundaryResolutionError(Exception):
    """Raised when an LGA boundary cannot be confidently resolved from OSM."""
    pass


def _first_value_or_none(gdf: gpd.GeoDataFrame, column: str):
    """
    Return gdf[column].iloc[0] if the column exists and its first value
    is not missing, else None.

    Used throughout the OSM metadata checks below: a raw Nominatim
    result may either omit a column entirely, or include it with a
    NaN/None value (pandas' two different spellings of "missing"), and
    both should be treated identically as "we don't have this
    information" rather than one silently passing an `is not None`
    check that the other fails.
    """
    if column not in gdf.columns:
        return None
    value = gdf[column].iloc[0]
    return None if pd.isna(value) else value


def resolve_boundary(lga_name: str, state_name: str = None, manual_boundary_path: str = None) -> gpd.GeoDataFrame:
    """
    Resolve the administrative boundary polygon for a Nigerian LGA.

    Parameters
    ----------
    lga_name : str
        Name of the LGA, e.g. "Akure North".
    state_name : str, optional
        Name of the state, e.g. "Ondo". Recommended to disambiguate
        LGAs that share names across different states.
    manual_boundary_path : str, optional
        Path to a GeoJSON/Shapefile boundary to use instead of querying
        OSM directly. Use this when OSM boundary data for the LGA is
        missing, incomplete, or mistagged.

    Returns
    -------
    geopandas.GeoDataFrame
        A single-row GeoDataFrame containing the resolved boundary
        geometry, reprojected to EPSG:4326 (WGS84), suitable for
        passing into feature extraction functions. Carries two extra
        columns: "boundary_source" (where this boundary came from) and
        "validation_warnings" (None, or a semicolon-separated string of
        non-fatal concerns worth a manual check, see
        _validate_and_standardize()'s docstring for what these mean).

    Raises
    ------
    BoundaryResolutionError
        If no valid boundary geometry could be resolved, or if the
        resolved boundary fails a hard geographic/size sanity check
        (see _validate_and_standardize()) that strongly suggests the
        wrong place was resolved, and no manual fallback was provided.
    """
    if manual_boundary_path:
        gdf = gpd.read_file(manual_boundary_path)
        if gdf.empty or gdf.geometry.isnull().all():
            raise BoundaryResolutionError(
                f"Manual boundary file '{manual_boundary_path}' contains no valid geometry."
            )
        return _validate_and_standardize(
            gdf, source=f"manual:{manual_boundary_path}", lga_name=lga_name, state_name=state_name
        )

    query = f"{lga_name}, {state_name}, Nigeria" if state_name else f"{lga_name}, Nigeria"

    last_exc = None
    gdf = None
    # The READ of the original value must also happen under the lock,
    # not just the set-call-restore sequence -- otherwise a thread could
    # read ox.settings.requests_timeout while ANOTHER thread currently
    # holds the lock with it temporarily set to BOUNDARY_REQUEST_TIMEOUT_
    # SECONDS, capturing that temporary value as if it were the true
    # original, and later restore to the WRONG value. (This exact bug
    # was caught by test_resolve_boundary_timeout_mutation_is_lock_
    # protected_under_concurrency during review -- the first version of
    # this fix read `original_timeout` one line above this comment,
    # before the `with` statement, which is exactly this race.)
    with _BOUNDARY_TIMEOUT_LOCK:
        original_timeout = ox.settings.requests_timeout
        original_requests_kwargs = dict(ox.settings.requests_kwargs)
        try:
            ox.settings.requests_timeout = BOUNDARY_REQUEST_TIMEOUT_SECONDS
            # Merge in a descriptive User-Agent without clobbering any
            # other requests_kwargs (proxies, auth, etc.) the caller may
            # have already configured elsewhere.
            merged_headers = dict(original_requests_kwargs.get("headers", {}))
            merged_headers.setdefault("User-Agent", NOMINATIM_USER_AGENT)
            ox.settings.requests_kwargs = {
                **original_requests_kwargs,
                "headers": merged_headers,
            }
            for attempt in range(1, BOUNDARY_MAX_RETRIES + 1):
                try:
                    gdf = _geocode_with_hard_timeout(
                        query, timeout_seconds=BOUNDARY_HARD_WALL_CLOCK_TIMEOUT_SECONDS
                    )
                    break
                except FutureTimeoutError as exc:
                    last_exc = BoundaryResolutionError(
                        f"OSM boundary lookup for '{query}' did not complete within "
                        f"{BOUNDARY_HARD_WALL_CLOCK_TIMEOUT_SECONDS}s (this most likely "
                        f"means Nominatim is rate-limiting this request and osmnx is "
                        f"retrying internally; see boundary.py module docstring)."
                    )
                    if attempt < BOUNDARY_MAX_RETRIES:
                        time.sleep(BOUNDARY_RETRY_BACKOFF_BASE_SECONDS * attempt)
                        continue
                except Exception as exc:
                    last_exc = exc
                    if attempt < BOUNDARY_MAX_RETRIES:
                        time.sleep(BOUNDARY_RETRY_BACKOFF_BASE_SECONDS * attempt)
                        continue
        finally:
            ox.settings.requests_timeout = original_timeout
            ox.settings.requests_kwargs = original_requests_kwargs

    if gdf is None:
        raise BoundaryResolutionError(
            f"OSM boundary lookup failed for query '{query}' after {BOUNDARY_MAX_RETRIES} "
            f"attempt(s) (each capped at {BOUNDARY_HARD_WALL_CLOCK_TIMEOUT_SECONDS}s wall-clock). "
            f"Consider supplying manual_boundary_path. Original error: {last_exc}"
        ) from last_exc

    if gdf.empty:
        raise BoundaryResolutionError(
            f"OSM returned no boundary for query '{query}'. "
            f"This LGA may be missing or mistagged in OSM; consider a manual boundary."
        )

    return _validate_and_standardize(gdf, source=f"osm_geocode:{query}", lga_name=lga_name, state_name=state_name)


def _validate_and_standardize(
    gdf: gpd.GeoDataFrame, source: str, lga_name: str = None, state_name: str = None
) -> gpd.GeoDataFrame:
    """
    Sanity-check a resolved boundary GeoDataFrame, standardize its CRS,
    and tag it with source/validation metadata.

    Two tiers of check are applied, deliberately different in severity:

    HARD checks (raise BoundaryResolutionError, these indicate the
    resolution almost certainly picked the wrong place, not just an
    unusual-but-valid LGA):
      - Geometry is missing, invalid, or empty (pre-existing check).
      - The boundary's centroid falls outside Nigeria's approximate
        bounding box (NIGERIA_BBOX), a strong signal that a
        name-collision or unrelated place was resolved instead.
      - The boundary's area (measured in the auto-selected UTM zone, see clean.resolve_target_crs(), reused here rather than
        duplicating the UTM-zone logic) falls outside a generously wide
        plausible range for a single Nigerian LGA
        (MIN/MAX_PLAUSIBLE_LGA_AREA_KM2), catching the specific
        failure modes of "a single building/point was resolved" or "an
        entire state/the whole country was resolved."
      - If OSM/Nominatim's result carries "class"/"type" tags (it does
        for OSM-geocoded results; a manually-supplied boundary file
        won't have these columns at all, so this check is skipped
        entirely for the manual path), they must read
        class="boundary", type="administrative". This directly catches
        Nominatim resolving a same-named road, river, or place node
        instead of an actual administrative boundary relation --
        something the geographic/area checks above cannot distinguish,
        since a wrongly-resolved feature can still have a plausible
        centroid and, after being force-interpreted as a polygon, a
        plausible-looking area.

    SOFT checks (recorded in the returned GeoDataFrame's
    "validation_warnings" column, but do NOT raise, these are worth a
    human glance but are not confident enough to block extraction on
    their own, since Nominatim's display_name formatting varies and
    isn't a reliable enough signal to treat as authoritative):
      - If OSM/Nominatim returned a "display_name" field, check that it
        appears to mention the requested LGA name and (if given) state
        name. A mismatch here is often just Nominatim's naming/
        abbreviation conventions, not necessarily a wrong resolution, hence a warning, not a failure.
      - If an "osm_type" column is present, warn if it is not
        "relation". Administrative boundaries the size of an LGA are
        essentially always OSM relations (a way or node resolving here
        instead is suspicious), but this is kept as a warning rather
        than a hard check since it is a secondary signal, not as
        direct evidence of a wrong resolution as the class/type check
        above.
      - If an "admin_level" column is present (this is a best-effort
        check: OSMnx's default geocode result does not reliably
        include this field, so absence of the column produces no
        warning at all -- we don't warn about something we have no
        information on), warn if it does not parse as an integer
        within PLAUSIBLE_ADMIN_LEVEL_RANGE. This is intentionally a
        wide, advisory band, not a strict admin_level==6 check -- see
        the constant's own comment for why.

    Parameters
    ----------
    gdf : geopandas.GeoDataFrame
        Raw resolved boundary (from OSM geocoding or a manual file).
    source : str
        Human-readable description of where this boundary came from,
        recorded in the "boundary_source" output column.
    lga_name, state_name : str, optional
        The originally requested LGA/state name, used only for the
        soft display_name check above. Not required.

    Returns
    -------
    geopandas.GeoDataFrame
        Single-row, WGS84, with "boundary_source" and
        "validation_warnings" columns added, plus three explicit OSM
        metadata columns carried through from the raw geocode result
        (all None for a manually-supplied boundary, which has no such
        tags to carry through):
          - "osm_class": the raw Nominatim "class" value (expected
            "boundary" for a valid administrative resolution).
          - "osm_type_tag": the raw Nominatim "type" value (expected
            "administrative"). Named "osm_type_tag" rather than "type"
            to avoid colliding with any "type" column already present
            on the input GeoDataFrame, and to distinguish it from
            "osm_type" (element type: node/way/relation), which is
            surfaced separately below.
          - "admin_level": the raw OSM admin_level value if the
            geocode result happened to include one, else None. Kept as
            a plain string (not cast to int) since OSM tag values are
            strings by convention and a failed/unexpected value should
            be visible as-is rather than silently coerced.

    Raises
    ------
    BoundaryResolutionError
        If any hard check above fails.
    """
    geom = gdf.geometry.iloc[0]

    if geom is None or not isinstance(geom, BaseGeometry) or geom.is_empty:
        raise BoundaryResolutionError(f"Resolved boundary from '{source}' is empty or invalid.")

    if gdf.crs is None:
        gdf = gdf.set_crs("EPSG:4326")
    else:
        gdf = gdf.to_crs("EPSG:4326")

    # --- HARD check: geographic bounding box ---
    centroid = gdf.geometry.iloc[0].centroid
    min_lon, min_lat, max_lon, max_lat = NIGERIA_BBOX
    if not (min_lon <= centroid.x <= max_lon and min_lat <= centroid.y <= max_lat):
        raise BoundaryResolutionError(
            f"Resolved boundary from '{source}' has a centroid at "
            f"({centroid.x:.2f}, {centroid.y:.2f}), well outside Nigeria's "
            f"approximate bounding box. This strongly suggests the wrong place "
            f"was resolved (e.g. a name collision with a similarly-named place "
            f"elsewhere). Consider supplying manual_boundary_path instead."
        )

    # --- HARD check: plausible area ---
    # Reuses clean.resolve_target_crs() (the same UTM auto-selection
    # logic used for exported layers) rather than duplicating zone-
    # selection logic here, so area is measured in a locally-appropriate
    # metric projection rather than naively in WGS84 degrees.
    from .clean import resolve_target_crs

    target_crs = resolve_target_crs(gdf)
    area_km2 = gdf.to_crs(target_crs).geometry.iloc[0].area / 1_000_000

    if area_km2 < MIN_PLAUSIBLE_LGA_AREA_KM2:
        raise BoundaryResolutionError(
            f"Resolved boundary from '{source}' has an implausibly small area "
            f"({area_km2:.2f} km^2) for a Nigerian LGA. This suggests a specific "
            f"point or small feature was resolved rather than the LGA's full "
            f"administrative boundary. Consider supplying manual_boundary_path."
        )
    if area_km2 > MAX_PLAUSIBLE_LGA_AREA_KM2:
        raise BoundaryResolutionError(
            f"Resolved boundary from '{source}' has an implausibly large area "
            f"({area_km2:.0f} km^2) for a single Nigerian LGA. This suggests a "
            f"state or the entire country was resolved instead of one LGA. "
            f"Consider supplying manual_boundary_path."
        )

    # --- HARD check: class=boundary, type=administrative ---
    # Only evaluated if these columns are actually present -- a manually
    # supplied boundary file never carries Nominatim's class/type tags,
    # so this check is a no-op (not a failure) on the manual path. When
    # present, this is the strongest available signal that OSM actually
    # resolved an administrative boundary relation, rather than a
    # same-named road, river, or point feature that happens to pass the
    # geographic/area checks above (a wrongly-resolved feature can still
    # have a plausible centroid and, once force-interpreted as a
    # polygon, a plausible-looking area -- this check catches what those
    # two cannot).
    osm_class = _first_value_or_none(gdf, "class")
    osm_type_tag = _first_value_or_none(gdf, "type")

    if osm_class is not None and str(osm_class).lower() != "boundary":
        raise BoundaryResolutionError(
            f"Resolved boundary from '{source}' has OSM class='{osm_class}', "
            f"expected 'boundary'. This strongly suggests OSM resolved a "
            f"non-boundary feature (e.g. a road, river, or place node) rather "
            f"than an administrative boundary. Consider supplying "
            f"manual_boundary_path instead."
        )
    if osm_type_tag is not None and str(osm_type_tag).lower() != "administrative":
        raise BoundaryResolutionError(
            f"Resolved boundary from '{source}' has OSM type='{osm_type_tag}', "
            f"expected 'administrative'. This strongly suggests OSM resolved a "
            f"non-administrative boundary feature (e.g. a natural or landuse "
            f"boundary) rather than an LGA administrative boundary. Consider "
            f"supplying manual_boundary_path instead."
        )

    # --- SOFT checks (warning only) ---
    warnings = []

    if "display_name" in gdf.columns and (lga_name or state_name):
        display_name = str(gdf["display_name"].iloc[0]).lower()
        if lga_name and lga_name.split()[0].lower() not in display_name:
            warnings.append(
                f"Resolved boundary's display name does not obviously mention "
                f"the requested LGA name '{lga_name}', worth a manual check."
            )
        if state_name and state_name.lower() not in display_name:
            warnings.append(
                f"Resolved boundary's display name does not obviously mention "
                f"the requested state '{state_name}', worth a manual check."
            )

    # SOFT check: relation-level osm_type. LGA-scale administrative
    # boundaries are essentially always OSM relations (a multi-way
    # collection); a way or node resolving here instead is suspicious,
    # but kept as a warning since it's a secondary signal relative to
    # the class/type hard check above.
    osm_element_type = _first_value_or_none(gdf, "osm_type")
    if osm_element_type is not None and str(osm_element_type).lower() != "relation":
        warnings.append(
            f"Resolved boundary's OSM element type is '{osm_element_type}', "
            f"not 'relation'. LGA-scale administrative boundaries are "
            f"typically relations; worth a manual check."
        )

    # SOFT, best-effort check: admin_level plausibility. Not all OSMnx
    # geocode results include this field (it is not part of Nominatim's
    # default response), so absence of the column produces no warning
    # at all -- we only warn about a value we actually have.
    admin_level = _first_value_or_none(gdf, "admin_level")
    if admin_level is not None:
        min_level, max_level = PLAUSIBLE_ADMIN_LEVEL_RANGE
        try:
            admin_level_int = int(admin_level)
            if not (min_level <= admin_level_int <= max_level):
                warnings.append(
                    f"Resolved boundary has admin_level={admin_level}, outside "
                    f"the plausible range ({min_level}-{max_level}) for a "
                    f"Nigerian LGA-scale boundary; worth a manual check."
                )
        except (TypeError, ValueError):
            warnings.append(
                f"Resolved boundary has an unparseable admin_level value "
                f"('{admin_level}'); worth a manual check."
            )

    gdf = gdf.iloc[[0]].copy()
    gdf["boundary_source"] = source
    gdf["validation_warnings"] = "; ".join(warnings) if warnings else None
    gdf["osm_class"] = osm_class
    gdf["osm_type_tag"] = osm_type_tag
    gdf["admin_level"] = admin_level
    return gdf
