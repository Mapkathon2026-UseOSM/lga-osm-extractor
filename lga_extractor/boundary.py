"""
boundary.py

Resolves a Nigerian LGA name (optionally with state) into a validated
administrative boundary polygon using OSMnx / OSM administrative
relations, with a manual-boundary fallback path.
"""

import geopandas as gpd
from shapely.geometry.base import BaseGeometry
import osmnx as ox

# Nigeria's approximate bounding box (min_lon, min_lat, max_lon, max_lat),
# in WGS84 degrees, with generous margin. Used as a coarse geographic
# sanity check: a resolved boundary whose centroid falls well outside
# this box strongly suggests OSM/Nominatim resolved the wrong place
# entirely (a name collision, or an unrelated result), not just an
# LGA with unusual shape/size.
NIGERIA_BBOX = (2.5, 4.0, 15.0, 14.0)

# Plausible LGA area range, in km^2. Nigerian LGAs vary a lot in size
# (a dense urban LGA can be tens of km^2; some sparse northern LGAs
# exceed 1000 km^2), so these bounds are intentionally generous --
# they're meant to catch "a single building/point was resolved" or "an
# entire state/the whole country was resolved" (both real failure
# modes for a geocoding-based lookup), not to flag genuinely unusual
# but valid LGA shapes.
MIN_PLAUSIBLE_LGA_AREA_KM2 = 2
MAX_PLAUSIBLE_LGA_AREA_KM2 = 10_000


class BoundaryResolutionError(Exception):
    """Raised when an LGA boundary cannot be confidently resolved from OSM."""
    pass


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
        non-fatal concerns worth a manual check -- see
        _validate_and_standardize()'s docstring for what these mean).

    Raises
    ------
    BoundaryResolutionError
        If no valid boundary geometry could be resolved, or if the
        resolved boundary fails a hard geographic/size sanity check
        (see _validate_and_standardize()) that strongly suggests the
        wrong place was resolved -- and no manual fallback was provided.
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

    try:
        gdf = ox.geocode_to_gdf(query)
    except Exception as exc:
        raise BoundaryResolutionError(
            f"OSM boundary lookup failed for query '{query}'. "
            f"Consider supplying manual_boundary_path. Original error: {exc}"
        ) from exc

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

    HARD checks (raise BoundaryResolutionError -- these indicate the
    resolution almost certainly picked the wrong place, not just an
    unusual-but-valid LGA):
      - Geometry is missing, invalid, or empty (pre-existing check).
      - The boundary's centroid falls outside Nigeria's approximate
        bounding box (NIGERIA_BBOX) -- a strong signal that a
        name-collision or unrelated place was resolved instead.
      - The boundary's area (measured in the auto-selected UTM zone --
        see clean.resolve_target_crs(), reused here rather than
        duplicating the UTM-zone logic) falls outside a generously wide
        plausible range for a single Nigerian LGA
        (MIN/MAX_PLAUSIBLE_LGA_AREA_KM2) -- catching the specific
        failure modes of "a single building/point was resolved" or "an
        entire state/the whole country was resolved."

    SOFT checks (recorded in the returned GeoDataFrame's
    "validation_warnings" column, but do NOT raise -- these are worth a
    human glance but are not confident enough to block extraction on
    their own, since Nominatim's display_name formatting varies and
    isn't a reliable enough signal to treat as authoritative):
      - If OSM/Nominatim returned a "display_name" field, check that it
        appears to mention the requested LGA name and (if given) state
        name. A mismatch here is often just Nominatim's naming/
        abbreviation conventions, not necessarily a wrong resolution --
        hence a warning, not a failure.

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
        "validation_warnings" columns added.

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

    # --- SOFT check: display_name sanity (warning only) ---
    warnings = []
    if "display_name" in gdf.columns and (lga_name or state_name):
        display_name = str(gdf["display_name"].iloc[0]).lower()
        if lga_name and lga_name.split()[0].lower() not in display_name:
            warnings.append(
                f"Resolved boundary's display name does not obviously mention "
                f"the requested LGA name '{lga_name}' -- worth a manual check."
            )
        if state_name and state_name.lower() not in display_name:
            warnings.append(
                f"Resolved boundary's display name does not obviously mention "
                f"the requested state '{state_name}' -- worth a manual check."
            )

    gdf = gdf.iloc[[0]].copy()
    gdf["boundary_source"] = source
    gdf["validation_warnings"] = "; ".join(warnings) if warnings else None
    return gdf
