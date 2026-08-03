"""
tests/test_extraction.py

Basic tests for the lga_extractor package. These focus on the parts
that don't require live network calls to OSM (cleaning, export), plus
a marked integration test that does hit OSM and can be skipped in
offline environments.
"""

import os
import shutil
import tempfile
from unittest.mock import patch

import geopandas as gpd
import pytest
from shapely.geometry import Point, LineString, box

import sys
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from lga_extractor.clean import clean_layers, utm_epsg_for_longitude, resolve_target_crs
from lga_extractor.export import export_layers
from lga_extractor.layers import extract_layers, LayerExtractionError
from lga_extractor.boundary import resolve_boundary, _validate_and_standardize, BoundaryResolutionError
from lga_extractor.visualize import build_preview_map, _strip_mapbox_token, _MAPBOX_TOKEN_PATTERN


def _dummy_raw_layers():
    """Build a small synthetic layers_dict resembling extract_layers() output."""
    points = gpd.GeoDataFrame(
        {"amenity": ["hospital", "school"]},
        geometry=[Point(5.2, 7.25), Point(5.21, 7.26)],
        crs="EPSG:4326",
    )
    lines = gpd.GeoDataFrame(
        {"highway": ["residential"]},
        geometry=[LineString([(5.2, 7.25), (5.21, 7.26)])],
        crs="EPSG:4326",
    )
    empty = gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")

    return {
        "health_facilities": points,
        "roads": lines,
        "schools": empty,
        "_warnings": ["schools returned no features (synthetic test data)"],
    }


def test_clean_layers_reprojects_and_dedupes():
    raw = _dummy_raw_layers()
    cleaned = clean_layers(raw)

    assert "_warnings" in cleaned
    assert cleaned["health_facilities"].crs.to_string() == "EPSG:32631"
    assert len(cleaned["health_facilities"]) == 2
    assert cleaned["schools"].empty


def test_clean_layers_standard_schema():
    raw = _dummy_raw_layers()
    cleaned = clean_layers(raw)

    for name, gdf in cleaned.items():
        if name == "_warnings" or gdf.empty:
            continue
        assert "geometry" in gdf.columns
        assert "osmid" in gdf.columns
        assert "name" in gdf.columns


def test_export_layers_writes_geojson_and_shapefile():
    raw = _dummy_raw_layers()
    cleaned = clean_layers(raw)
    cleaned.pop("_warnings", None)

    tmp_dir = tempfile.mkdtemp()
    try:
        exported = export_layers(cleaned, tmp_dir)

        assert "health_facilities" in exported
        assert os.path.exists(exported["health_facilities"]["geojson"])
        assert os.path.exists(exported["health_facilities"]["shapefile"])

        # empty layer should be skipped, not exported
        assert "schools" in exported["_skipped"]
    finally:
        shutil.rmtree(tmp_dir)


def test_export_layers_splits_mixed_geometry_types():
    """
    Regression test for the OSM 'highway=*' quirk: this tag matches both
    road ways (LineString) and point nodes like traffic signals (Point),
    which cannot coexist in a single Shapefile. export_layers() should
    split such a layer into per-geometry-type Shapefiles rather than
    raising a pyogrio FeatureError.
    """
    mixed_roads = gpd.GeoDataFrame(
        {"highway": ["residential", "traffic_signals"]},
        geometry=[
            LineString([(5.20, 7.25), (5.21, 7.26)]),
            Point(5.205, 7.255),
        ],
        crs="EPSG:4326",
    )
    raw = {"roads": mixed_roads, "_warnings": []}
    cleaned = clean_layers(raw)
    cleaned.pop("_warnings", None)

    tmp_dir = tempfile.mkdtemp()
    try:
        exported = export_layers(cleaned, tmp_dir)

        assert "roads" in exported
        assert os.path.exists(exported["roads"]["geojson"])

        # Mixed geometry -> shapefile value should be a dict of category -> path
        assert isinstance(exported["roads"]["shapefile"], dict)
        assert "line" in exported["roads"]["shapefile"]
        assert "point" in exported["roads"]["shapefile"]
        assert os.path.exists(exported["roads"]["shapefile"]["line"])
        assert os.path.exists(exported["roads"]["shapefile"]["point"])

        assert "roads" in exported.get("_split_layers", {})
    finally:
        shutil.rmtree(tmp_dir)


def test_utm_epsg_for_longitude_known_nigerian_locations():
    """
    Verifies the UTM zone formula against known real-world reference
    points spanning Nigeria's actual UTM zone range (31N/32N/33N), not just Ondo State, which is what the tool was originally built
    and tested against.
    """
    # Akure, Ondo State (Southwest Nigeria), the original study area,
    # zone 31N, must keep matching the original hardcoded default.
    assert utm_epsg_for_longitude(5.2, 7.25) == "EPSG:32631"

    # Abuja (Central Nigeria), zone 32N.
    assert utm_epsg_for_longitude(7.5, 9.05) == "EPSG:32632"

    # Maiduguri, Borno State (Northeast Nigeria), zone 33N.
    assert utm_epsg_for_longitude(13.15, 11.85) == "EPSG:32633"

    # Lagos (Southwest Nigeria, further west than Akure), still zone 31N.
    assert utm_epsg_for_longitude(3.4, 6.5) == "EPSG:32631"


def test_utm_epsg_for_longitude_zone_boundaries():
    """
    Checks the formula right at a UTM zone boundary (every 6 degrees of
    longitude) to confirm no off-by-one error in the zone calculation.
    Zone 31N covers 0-6 E; zone 32N covers 6-12 E.
    """
    assert utm_epsg_for_longitude(5.999) == "EPSG:32631"
    assert utm_epsg_for_longitude(6.001) == "EPSG:32632"


def test_resolve_target_crs_falls_back_with_no_boundary():
    """
    Without a boundary, resolve_target_crs() must fall back to the
    original EPSG:32631 default, this is what keeps clean_layers()
    backward compatible for existing callers/tests that don't pass a
    boundary_gdf.
    """
    assert resolve_target_crs(None) == "EPSG:32631"
    assert resolve_target_crs(gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")) == "EPSG:32631"


def test_resolve_target_crs_auto_selects_zone_from_boundary():
    """
    Given a real boundary polygon, resolve_target_crs() should pick the
    UTM zone that boundary's centroid actually falls in, not always
    EPSG:32631. This is the core behavior change this feature adds.
    """
    from shapely.geometry import box

    # A small synthetic boundary centered near Abuja (zone 32N), clearly
    # outside Ondo State's zone 31N.
    abuja_boundary = gpd.GeoDataFrame(
        geometry=[box(7.4, 9.0, 7.6, 9.1)], crs="EPSG:4326"
    )
    assert resolve_target_crs(abuja_boundary) == "EPSG:32632"

    # A synthetic boundary near Akure (zone 31N) should still resolve
    # to the original default, confirming no regression for the
    # project's actual study area.
    akure_boundary = gpd.GeoDataFrame(
        geometry=[box(5.15, 7.2, 5.25, 7.3)], crs="EPSG:4326"
    )
    assert resolve_target_crs(akure_boundary) == "EPSG:32631"


def test_clean_layers_uses_boundary_to_select_crs():
    """
    End-to-end check that clean_layers() actually reprojects into the
    boundary-derived zone, not just the hardcoded fallback, when a
    boundary is provided, this is what a real extract_lga() run for
    an LGA outside Ondo State now does correctly.
    """
    from shapely.geometry import box

    raw = _dummy_raw_layers()  # synthetic data is near Akure (lon ~5.2), but
                                 # we deliberately pass an Abuja-area boundary
                                 # to prove the CRS choice follows the
                                 # boundary, not the data's own coordinates.
    abuja_boundary = gpd.GeoDataFrame(
        geometry=[box(7.4, 9.0, 7.6, 9.1)], crs="EPSG:4326"
    )

    cleaned = clean_layers(raw, boundary_gdf=abuja_boundary)
    assert cleaned["health_facilities"].crs.to_string() == "EPSG:32632"


def _synthetic_boundary():
    return gpd.GeoDataFrame(geometry=[box(5.15, 7.2, 5.25, 7.3)], crs="EPSG:4326")


def test_extract_layers_permissive_returns_empty_on_failure():
    """
    Default (permissive) mode: a genuine query failure (simulated here
    as an exception raised by the underlying OSM call) is caught and
    recorded as a warning, with that layer returned empty, so one
    flaky layer doesn't abort extraction of the others. This is the
    original, pre-existing behavior and must not change by default.
    """
    boundary = _synthetic_boundary()

    def mock_features(polygon, tags):
        if tags == {"amenity": ["hospital", "clinic", "pharmacy"]}:
            raise RuntimeError("simulated Overpass timeout")
        return gpd.GeoDataFrame(
            {"osmid": [1]}, geometry=[Point(5.2, 7.25)], crs="EPSG:4326"
        )

    with patch("lga_extractor.layers.ox.features_from_polygon", side_effect=mock_features):
        result = extract_layers(boundary, strict=False)

    assert result["health_facilities"].empty
    assert any("health_facilities" in w and "failed to extract" in w for w in result["_warnings"])
    # Other layers should still have extracted successfully despite the failure
    assert not result["roads"].empty


def test_extract_layers_strict_raises_on_genuine_failure():
    """
    Strict mode: the same simulated failure above must now raise
    LayerExtractionError immediately, rather than being silently
    swallowed into an empty GeoDataFrame, this is the actual fix for
    the "a real failure looks identical to a genuinely empty area"
    limitation.
    """
    boundary = _synthetic_boundary()

    def mock_features(polygon, tags):
        if tags == {"amenity": ["hospital", "clinic", "pharmacy"]}:
            raise RuntimeError("simulated Overpass timeout")
        return gpd.GeoDataFrame(
            {"osmid": [1]}, geometry=[Point(5.2, 7.25)], crs="EPSG:4326"
        )

    with patch("lga_extractor.layers.ox.features_from_polygon", side_effect=mock_features):
        with pytest.raises(LayerExtractionError, match="health_facilities"):
            extract_layers(boundary, strict=True)


def test_extract_layers_strict_does_not_raise_on_genuine_empty_result():
    """
    Critical distinction this feature depends on: a layer that queries
    SUCCESSFULLY but genuinely finds zero features (e.g. an LGA with no
    OSM-tagged schools yet) must NOT raise, even in strict mode, an
    empty result is valid data, not a failure. Only an actual exception
    from the underlying query should raise in strict mode.
    """
    boundary = _synthetic_boundary()

    def mock_features(polygon, tags):
        if tags == {"amenity": "school"}:
            return gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")  # genuinely empty, not a failure
        return gpd.GeoDataFrame(
            {"osmid": [1]}, geometry=[Point(5.2, 7.25)], crs="EPSG:4326"
        )

    with patch("lga_extractor.layers.ox.features_from_polygon", side_effect=mock_features):
        result = extract_layers(boundary, strict=True)  # should NOT raise

    assert result["schools"].empty
    assert any("schools" in w and "returned no features" in w for w in result["_warnings"])


def _write_manual_boundary(geom, crs="EPSG:4326"):
    """Helper: write a synthetic boundary polygon to a temp GeoJSON file,
    so resolve_boundary()'s manual_boundary_path path can be exercised
    without needing a live OSM/Nominatim call."""
    gdf = gpd.GeoDataFrame(geometry=[geom], crs=crs)
    path = tempfile.mktemp(suffix=".geojson")
    gdf.to_file(path, driver="GeoJSON")
    return path


def test_resolve_boundary_accepts_plausible_akure_sized_boundary():
    """
    A boundary of realistic size and location for the project's actual
    study area should pass validation cleanly, with no warnings raised.
    This is the "nothing regressed for the happy path" check.
    """
    path = _write_manual_boundary(box(5.15, 7.2, 5.25, 7.3))  # ~120 km^2, near Akure
    try:
        result = resolve_boundary("Akure North", "Ondo", manual_boundary_path=path)
        assert not result.empty
        assert result["validation_warnings"].iloc[0] is None
    finally:
        os.remove(path)


def test_resolve_boundary_rejects_centroid_outside_nigeria():
    """
    A resolved boundary whose centroid falls well outside Nigeria (here,
    near Paris) must raise, this is the core case the geographic
    bounding-box check exists for: a name collision or unrelated result
    silently poisoning every downstream extraction step.
    """
    path = _write_manual_boundary(box(2.0, 48.0, 2.2, 48.2))  # near Paris, France
    try:
        with pytest.raises(BoundaryResolutionError, match="outside Nigeria"):
            resolve_boundary("Akure North", "Ondo", manual_boundary_path=path)
    finally:
        os.remove(path)


def test_resolve_boundary_rejects_implausibly_tiny_area():
    """
    A resolved boundary that's only a few metres across (e.g. a single
    point/building was resolved instead of the LGA's actual
    administrative boundary) must raise, not silently proceed with a
    boundary far too small to represent a real LGA.
    """
    path = _write_manual_boundary(Point(5.2, 7.25).buffer(0.0001))
    try:
        with pytest.raises(BoundaryResolutionError, match="implausibly small"):
            resolve_boundary("Akure North", "Ondo", manual_boundary_path=path)
    finally:
        os.remove(path)


def test_resolve_boundary_rejects_implausibly_huge_area():
    """
    A resolved boundary roughly the size of the entire country (e.g. a
    state or the whole country was resolved instead of a single LGA)
    must raise, catching the opposite failure mode from the tiny-area
    check above.
    """
    path = _write_manual_boundary(box(3.0, 5.0, 14.0, 13.0))
    try:
        with pytest.raises(BoundaryResolutionError, match="implausibly large"):
            resolve_boundary("Akure North", "Ondo", manual_boundary_path=path)
    finally:
        os.remove(path)


def test_validate_and_standardize_display_name_mismatch_warns_not_raises():
    """
    The display_name soft check must only ever produce a WARNING, never
    raise, since Nominatim's naming/abbreviation conventions vary
    enough that treating a mismatch as authoritative would cause false
    failures on genuinely correct boundaries. Tested directly against
    _validate_and_standardize() (rather than resolve_boundary(), since
    manual-file boundaries never carry a display_name column at all).
    """
    gdf = gpd.GeoDataFrame(
        {"display_name": ["Some Other Place, Some Other State, Nigeria"]},
        geometry=[box(5.15, 7.2, 5.25, 7.3)],
        crs="EPSG:4326",
    )
    result = _validate_and_standardize(gdf, source="test", lga_name="Akure North", state_name="Ondo")
    assert result["validation_warnings"].iloc[0] is not None
    assert "Akure North" in result["validation_warnings"].iloc[0]


def test_strip_mapbox_token_removes_real_token_from_export():
    """
    The installed keplergl package bundles a real Mapbox access token
    directly into its exported HTML (see visualize.py's module-level
    comment for why), this caused a real GitHub push-protection
    failure during this project's development. This test builds an
    actual preview map end-to-end and confirms the token pattern is
    genuinely absent afterward, not just theoretically stripped.

    This is the only test in this file that actually exercises
    keplergl (via build_preview_map), so it's the only one that needs
    to skip if keplergl can't be imported, every other test in this
    file is unrelated to keplergl entirely and should run regardless.
    """
    pytest.importorskip(
        "keplergl",
        reason="keplergl not importable in this environment (commonly caused by "
        "a missing 'setuptools'/'pkg_resources'), skipping this one keplergl-"
        "dependent test rather than failing collection for the whole file.",
    )
    tmp_dir = tempfile.mkdtemp()
    try:
        roads = gpd.GeoDataFrame(
            {"osmid": [1]},
            geometry=[LineString([(5.2, 7.25), (5.21, 7.26)])],
            crs="EPSG:32631",
        )
        roads.to_file(os.path.join(tmp_dir, "roads.geojson"), driver="GeoJSON")

        html_path = os.path.join(tmp_dir, "preview.html")
        build_preview_map(output_dir=tmp_dir, html_out=html_path)

        with open(html_path) as f:
            content = f.read()

        assert not _MAPBOX_TOKEN_PATTERN.search(content), (
            "A Mapbox token pattern was still found after build_preview_map(), "
            "this would fail GitHub push protection again."
        )
        assert "</html>" in content, "Stripping the token should not corrupt the HTML file."
    finally:
        shutil.rmtree(tmp_dir)


def test_strip_mapbox_token_returns_false_when_no_token_present():
    """
    _strip_mapbox_token() should report False (nothing to do) rather
    than erroring when called on a file that has no token, e.g. one
    that's already been stripped, or a future keplergl version that no
    longer bundles a default token.
    """
    path = tempfile.mktemp(suffix=".html")
    with open(path, "w") as f:
        f.write("<html><body>no token here</body></html>")
    try:
        assert _strip_mapbox_token(path) is False
    finally:
        os.remove(path)


@pytest.mark.integration
def test_extract_lga_end_to_end_live_osm():
    """
    Integration test that performs a real OSM boundary lookup and
    extraction. Requires network access. Run explicitly with:
        pytest -m integration
    """
    from lga_extractor import extract_lga

    tmp_dir = tempfile.mkdtemp()
    try:
        result = extract_lga(
            lga_name="Akure North",
            state_name="Ondo",
            output_dir=tmp_dir,
        )
        assert result["boundary_source"].startswith("osm_geocode")
        assert os.path.exists(result["run_log"])
        # Akure North is in zone 31N, confirms auto-selection gives
        # the correct real-world answer, not just a synthetic one.
        assert result["target_crs"] == "EPSG:32631"
    finally:
        shutil.rmtree(tmp_dir)
