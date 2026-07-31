"""
clean.py

Cleans and standardizes raw OSM-extracted GeoDataFrames:
reprojection, geometry repair, deduplication, and a consistent
minimal attribute schema across layers and across LGA runs.
"""

import geopandas as gpd

# Fallback target CRS, used only if no boundary geometry is available
# to auto-select an appropriate UTM zone from (see
# utm_epsg_for_longitude() below). EPSG:32631 (UTM Zone 31N) is correct
# for Southwest Nigeria (including Ondo State) specifically -- it is
# NOT universally correct for all Nigerian LGAs, since Nigeria spans
# multiple UTM zones (31N, 32N, 33N) depending on longitude.
FALLBACK_CRS = "EPSG:32631"

# Minimal, consistent attribute schema retained per layer.
# OSM columns vary a lot between queries; this keeps exports
# predictable and comparable across different LGA extractions.
KEEP_COLUMNS = ["osmid", "name", "geometry"]


def utm_epsg_for_longitude(longitude: float, latitude: float = 0.0) -> str:
    """
    Determine the correct UTM zone EPSG code for a given longitude
    (and, for hemisphere, latitude).

    UTM divides the world into 60 zones, each 6 degrees of longitude
    wide. This is the standard formula for finding which zone a given
    longitude falls in: zone = floor((longitude + 180) / 6) + 1. The
    EPSG code then follows the pattern 326XX for the northern
    hemisphere or 327XX for the southern hemisphere, where XX is the
    two-digit zone number.

    Nigeria spans UTM zones 31N (west, e.g. Lagos/Ondo/Oyo), 32N
    (central, e.g. Abuja/Kaduna), and 33N (east, e.g. Borno/Adamawa) --
    using a single hardcoded zone for the whole country would distort
    distance and area calculations for LGAs outside that zone's true
    coverage. This function is what makes extraction correct for any
    Nigerian LGA, not just ones in the zone the tool was originally
    built and tested against (Zone 31N, for the Akure/Ondo study area).

    Parameters
    ----------
    longitude : float
        Longitude in decimal degrees (WGS84), typically the centroid
        of a resolved LGA boundary.
    latitude : float, default 0.0
        Latitude in decimal degrees, used only to pick the northern vs.
        southern hemisphere EPSG code. Defaults to 0.0 (northern
        hemisphere code) since Nigeria lies entirely in the northern
        hemisphere -- this parameter exists mainly so this function
        isn't silently wrong if ever reused for a non-Nigerian LGA.

    Returns
    -------
    str
        An EPSG code string, e.g. "EPSG:32631".

    Examples
    --------
    >>> utm_epsg_for_longitude(5.2)   # Akure, Ondo State
    'EPSG:32631'
    >>> utm_epsg_for_longitude(7.5)   # Abuja
    'EPSG:32632'
    >>> utm_epsg_for_longitude(13.2)  # Maiduguri, Borno State
    'EPSG:32633'
    """
    zone = int((longitude + 180) / 6) + 1
    zone = max(1, min(60, zone))  # clamp to the valid 1-60 range
    hemisphere_prefix = 326 if latitude >= 0 else 327
    return f"EPSG:{hemisphere_prefix}{zone:02d}"


def resolve_target_crs(boundary_gdf: gpd.GeoDataFrame = None) -> str:
    """
    Determine the correct projected CRS to clean/export layers in,
    based on an LGA boundary's location.

    If a boundary is provided, this reprojects a safe copy of it to
    WGS84 (if not already), takes its centroid, and selects the UTM
    zone that centroid actually falls in via utm_epsg_for_longitude().
    If no boundary is provided (or its geometry is empty/invalid), this
    falls back to FALLBACK_CRS with a printed warning, rather than
    failing outright -- callers that don't have a boundary handy (e.g.
    cleaning already-extracted data with no boundary reference) still
    get a usable, if less precise, result.

    Parameters
    ----------
    boundary_gdf : geopandas.GeoDataFrame, optional
        The resolved LGA boundary, as returned by
        boundary.resolve_boundary(). Any CRS is accepted; it will be
        reprojected to WGS84 internally if needed to compute a
        longitude/latitude centroid.

    Returns
    -------
    str
        An EPSG code string appropriate for this boundary's location.
    """
    if boundary_gdf is None or boundary_gdf.empty:
        print(
            f"Warning: no boundary provided for CRS auto-selection; "
            f"falling back to {FALLBACK_CRS} (correct for Southwest "
            f"Nigeria only)."
        )
        return FALLBACK_CRS

    try:
        wgs84_boundary = boundary_gdf.to_crs("EPSG:4326") if boundary_gdf.crs else boundary_gdf.set_crs("EPSG:4326")
        centroid = wgs84_boundary.union_all().centroid
        return utm_epsg_for_longitude(centroid.x, centroid.y)
    except Exception as e:
        print(
            f"Warning: could not auto-select UTM zone from boundary "
            f"({e}); falling back to {FALLBACK_CRS} (correct for "
            f"Southwest Nigeria only)."
        )
        return FALLBACK_CRS


def clean_layers(layers_dict: dict, boundary_gdf: gpd.GeoDataFrame = None) -> dict:
    """
    Clean and standardize all layers produced by extract_layers().

    Parameters
    ----------
    layers_dict : dict
        Mapping of layer_name -> raw GeoDataFrame, as returned by
        layers.extract_layers(). May include a "_warnings" key,
        which is passed through unchanged.
    boundary_gdf : geopandas.GeoDataFrame, optional
        The resolved LGA boundary (see boundary.resolve_boundary()),
        used to auto-select the correct UTM zone for this LGA's actual
        location (see resolve_target_crs()). If not provided, falls
        back to FALLBACK_CRS (EPSG:32631, correct for Southwest Nigeria
        only) -- this keeps clean_layers() usable standalone (e.g. in
        existing tests or scripts) without requiring every caller to
        be updated, while giving pipeline.extract_lga() (which always
        has the boundary on hand) the more accurate, location-aware
        behavior by default.

    Returns
    -------
    dict
        Mapping of layer_name -> cleaned GeoDataFrame, reprojected to
        the resolved target CRS, with invalid/empty/duplicate
        geometries removed and a standardized minimal attribute schema.
    """
    target_crs = resolve_target_crs(boundary_gdf)

    cleaned = {}

    for layer_name, gdf in layers_dict.items():
        if layer_name == "_warnings":
            cleaned[layer_name] = gdf
            continue
        cleaned[layer_name] = _clean_single_layer(gdf, target_crs)

    return cleaned


def _clean_single_layer(gdf: gpd.GeoDataFrame, target_crs: str = FALLBACK_CRS) -> gpd.GeoDataFrame:
    if gdf.empty:
        return gdf

    gdf = gdf.copy()

    # Drop null / empty geometries
    gdf = gdf[~gdf.geometry.isnull()]
    gdf = gdf[~gdf.geometry.is_empty]

    if gdf.empty:
        return gdf

    # Repair invalid geometries
    gdf["geometry"] = gdf["geometry"].apply(
        lambda geom: geom if geom.is_valid else geom.buffer(0)
    )

    # Reset index (OSMnx often returns a MultiIndex of element_type/osmid)
    if isinstance(gdf.index, gpd.pd.MultiIndex):
        gdf = gdf.reset_index()

    # Standardize an 'osmid' column if not already present
    if "osmid" not in gdf.columns:
        id_col = next((c for c in gdf.columns if "id" in c.lower()), None)
        gdf["osmid"] = gdf[id_col] if id_col else range(len(gdf))

    # Standardize a 'name' column if not already present
    if "name" not in gdf.columns:
        gdf["name"] = None

    # Drop duplicate geometries
    gdf = gdf.drop_duplicates(subset="geometry")

    # Reproject to the resolved target CRS (auto-selected UTM zone, or
    # the fallback -- see resolve_target_crs())
    if gdf.crs is None:
        gdf = gdf.set_crs("EPSG:4326")
    gdf = gdf.to_crs(target_crs)

    # Keep only the minimal standardized schema (retain geometry + id + name)
    keep = [c for c in KEEP_COLUMNS if c in gdf.columns]
    gdf = gdf[keep]

    return gdf.reset_index(drop=True)
