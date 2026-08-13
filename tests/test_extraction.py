"""
tests/test_extraction.py

Basic tests for the lga_extractor package. These focus on the parts
that don't require live network calls to OSM (cleaning, export), plus
a marked integration test that does hit OSM and can be skipped in
offline environments.
"""

import json
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
from lga_extractor.layers import extract_layers, LayerExtractionError, DEFAULT_TAG_CONFIG
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


def test_clean_layers_preserves_semantic_columns_when_present():
    """
    Core fix for item #2: a road's highway/surface/maxspeed tags (and
    similar semantic attributes for other layers) must survive
    cleaning now, not just osmid/name/geometry.
    """
    from lga_extractor.clean import clean_layers

    roads = gpd.GeoDataFrame(
        {
            "highway": ["primary"],
            "surface": ["asphalt"],
            "maxspeed": ["60"],
            "lanes": ["2"],
            "some_unrelated_osm_tag": ["whatever"],  # not in SEMANTIC_COLUMNS
        },
        geometry=[LineString([(5.2, 7.25), (5.21, 7.26)])],
        crs="EPSG:4326",
    )
    raw = {"roads": roads, "_warnings": []}
    cleaned = clean_layers(raw)

    cols = cleaned["roads"].columns
    for expected in ("osmid", "name", "geometry", "highway", "surface", "maxspeed", "lanes"):
        assert expected in cols, f"expected '{expected}' to survive cleaning, got columns {list(cols)}"


def test_clean_layers_semantic_columns_are_layer_specific():
    """
    A tag that's semantically meaningful for one layer (e.g.
    'emergency' for health_facilities) should not leak into an
    unrelated layer's schema just because clean_layers() processes
    both, SEMANTIC_COLUMNS is keyed per layer_name deliberately.
    """
    from lga_extractor.clean import clean_layers

    roads = gpd.GeoDataFrame(
        {"highway": ["residential"], "emergency": ["yes"]},  # 'emergency' belongs to health_facilities, not roads
        geometry=[LineString([(5.2, 7.25), (5.21, 7.26)])],
        crs="EPSG:4326",
    )
    raw = {"roads": roads, "_warnings": []}
    cleaned = clean_layers(raw)

    assert "highway" in cleaned["roads"].columns
    assert "emergency" not in cleaned["roads"].columns


def test_clean_layers_raw_tags_preserves_everything_as_json():
    """
    raw_tags must capture the FULL original tag set (including tags
    outside the curated SEMANTIC_COLUMNS list), as parseable JSON, so
    nothing is genuinely lost even for attributes this module didn't
    anticipate.
    """
    import json as _json
    from lga_extractor.clean import clean_layers, RAW_TAGS_COLUMN

    health = gpd.GeoDataFrame(
        {
            "amenity": ["hospital"],
            "beds": ["50"],
            "some_future_tag_nobody_anticipated": ["value123"],
        },
        geometry=[Point(5.2, 7.25)],
        crs="EPSG:4326",
    )
    raw = {"health_facilities": health, "_warnings": []}
    cleaned = clean_layers(raw)

    assert RAW_TAGS_COLUMN in cleaned["health_facilities"].columns
    tags = _json.loads(cleaned["health_facilities"].iloc[0][RAW_TAGS_COLUMN])
    assert tags.get("amenity") == "hospital"
    assert tags.get("beds") == "50"
    assert tags.get("some_future_tag_nobody_anticipated") == "value123"


def test_export_layers_shapefile_stays_core_columns_only():
    """
    Item #2's cost caveat: Shapefile field-name truncation makes it
    unsafe to write the full semantic/raw_tags schema there, so
    Shapefile export must stay minimal (osmid/name/geometry) even
    though GeoJSON now carries the richer schema.
    """
    from lga_extractor.clean import clean_layers, RAW_TAGS_COLUMN

    roads = gpd.GeoDataFrame(
        {"highway": ["residential"], "surface": ["asphalt"]},
        geometry=[LineString([(5.20, 7.25), (5.21, 7.26)])],
        crs="EPSG:4326",
    )
    raw = {"roads": roads, "_warnings": []}
    cleaned = clean_layers(raw)
    cleaned.pop("_warnings", None)

    # Sanity check: GeoJSON-bound data really does have the rich schema.
    assert "highway" in cleaned["roads"].columns
    assert RAW_TAGS_COLUMN in cleaned["roads"].columns

    tmp_dir = tempfile.mkdtemp()
    try:
        exported = export_layers(cleaned, tmp_dir)
        shp_gdf = gpd.read_file(exported["roads"]["shapefile"])
        assert "highway" not in shp_gdf.columns
        assert RAW_TAGS_COLUMN not in shp_gdf.columns
        assert "osmid" in shp_gdf.columns
        assert "name" in shp_gdf.columns

        # GeoJSON should still have the full schema.
        geojson_gdf = gpd.read_file(exported["roads"]["geojson"])
        assert "highway" in geojson_gdf.columns
        assert RAW_TAGS_COLUMN in geojson_gdf.columns
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


def test_extract_layers_status_distinguishes_failed_from_empty():
    """
    The core distinction item #4 exists for: a layer that genuinely
    failed to query must be marked status="failed" in "_status", while
    a layer that queried successfully but found nothing must be marked
    "success_empty" — both look like an empty GeoDataFrame on their
    own, but a downstream consumer must be able to tell them apart
    without re-deriving it from warning text.
    """
    boundary = _synthetic_boundary()

    def mock_features(polygon, tags):
        if tags == {"amenity": ["hospital", "clinic", "pharmacy"]}:
            raise RuntimeError("simulated Overpass timeout")
        if tags == {"amenity": "school"}:
            return gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")  # genuinely empty
        return gpd.GeoDataFrame({"osmid": [1]}, geometry=[Point(5.2, 7.25)], crs="EPSG:4326")

    with patch("lga_extractor.layers.ox.features_from_polygon", side_effect=mock_features):
        result = extract_layers(boundary, strict=False)

    status = result["_status"]

    assert status["health_facilities"]["status"] == "failed"
    assert status["health_facilities"]["feature_count"] == 0
    assert status["health_facilities"]["attempts"] >= 1

    assert status["schools"]["status"] == "success_empty"
    assert status["schools"]["feature_count"] == 0

    assert status["roads"]["status"] == "success"
    assert status["roads"]["feature_count"] == 1
    assert status["roads"]["message"] is None


def test_extract_layers_emits_started_and_completed_events():
    """
    Core contract for item #9: extract_layers() must emit a
    "stage_started" and a terminal "stage_completed"/"stage_failed"
    event per layer, under stage="layer:{name}", regardless of whether
    a UI is listening. This test drives it with a plain thread-safe
    list + lock, deliberately NOT streamlit, to prove the pipeline
    itself has no UI dependency.
    """
    import threading

    boundary = _synthetic_boundary()
    events = []
    lock = threading.Lock()

    def on_event(event):
        with lock:
            events.append(event)

    def mock_features(polygon, tags):
        return gpd.GeoDataFrame({"osmid": [1]}, geometry=[Point(5.2, 7.25)], crs="EPSG:4326")

    with patch("lga_extractor.layers.ox.features_from_polygon", side_effect=mock_features):
        extract_layers(boundary, strict=False, on_event=on_event)

    stages_seen = {e["stage"] for e in events if e["stage"].startswith("layer:")}
    assert stages_seen == {f"layer:{name}" for name in DEFAULT_TAG_CONFIG}

    for layer_name in DEFAULT_TAG_CONFIG:
        stage = f"layer:{layer_name}"
        layer_events = [e for e in events if e["stage"] == stage]
        types = [e["type"] for e in layer_events]
        assert types[0] == "stage_started"
        assert types[-1] in ("stage_completed", "stage_failed")


def test_extract_layers_emits_retry_events_on_transient_failure():
    """
    A layer that fails transiently then succeeds must emit "retry"
    events in between "stage_started" and "stage_completed", so a UI
    can show "Retrying: 2 / 6" as described in the progress-interface
    mockup this item is based on.
    """
    import threading
    import requests

    boundary = _synthetic_boundary()
    events = []
    lock = threading.Lock()

    def on_event(event):
        with lock:
            events.append(event)

    call_count = {"roads": 0}

    def mock_features(polygon, tags):
        if tags == {"highway": True}:
            call_count["roads"] += 1
            if call_count["roads"] < 3:
                raise requests.exceptions.ConnectionError("simulated transient failure")
            return gpd.GeoDataFrame({"osmid": [1]}, geometry=[Point(5.2, 7.25)], crs="EPSG:4326")
        return gpd.GeoDataFrame({"osmid": [1]}, geometry=[Point(5.2, 7.25)], crs="EPSG:4326")

    with patch("lga_extractor.layers.ox.features_from_polygon", side_effect=mock_features), \
         patch("lga_extractor.layers.RETRY_BACKOFF_BASE_SECONDS", 0):
        extract_layers(boundary, strict=False, on_event=on_event)

    roads_events = [e for e in events if e["stage"] == "layer:roads"]
    retry_events = [e for e in roads_events if e["type"] == "retry"]
    assert len(retry_events) == 2  # failed on attempt 1 and 2, succeeded on 3
    assert roads_events[-1]["type"] == "stage_completed"


def test_extract_lga_emits_full_stage_sequence(monkeypatch):
    """
    End-to-end: extract_lga() must emit boundary -> layers ->
    cleaning -> export -> pipeline_completed, in that relative order
    (layers/cleaning/export are each internally concurrent/sequential,
    but boundary must complete before any layer starts, and
    pipeline_completed must be last).
    """
    from lga_extractor.pipeline import extract_lga

    events = []

    def on_event(event):
        events.append(event)

    def mock_resolve_boundary(lga_name=None, state_name=None, manual_boundary_path=None):
        return gpd.GeoDataFrame(
            {"boundary_source": ["osm_geocode:test"], "validation_warnings": [None]},
            geometry=[_synthetic_boundary().geometry.iloc[0]],
            crs="EPSG:4326",
        )

    def mock_features(polygon, tags):
        return gpd.GeoDataFrame({"osmid": [1]}, geometry=[Point(5.2, 7.25)], crs="EPSG:4326")

    tmp_dir = tempfile.mkdtemp()
    try:
        with patch("lga_extractor.pipeline.resolve_boundary", side_effect=mock_resolve_boundary), \
             patch("lga_extractor.layers.ox.features_from_polygon", side_effect=mock_features):
            extract_lga(lga_name="Test LGA", state_name="Test State", output_dir=tmp_dir, on_event=on_event)
    finally:
        shutil.rmtree(tmp_dir)

    types_in_order = [e["type"] for e in events]
    stages_in_order = [e["stage"] for e in events if "stage" in e]

    assert types_in_order[-1] == "pipeline_completed"
    boundary_idx = stages_in_order.index("boundary")
    first_layer_idx = next(i for i, s in enumerate(stages_in_order) if s.startswith("layer:"))
    assert boundary_idx < first_layer_idx

    cleaning_started_idx = next(i for i, e in enumerate(events) if e.get("stage") == "cleaning" and e["type"] == "stage_started")
    last_layer_idx = max(i for i, s in enumerate(stages_in_order) if s.startswith("layer:"))
    assert last_layer_idx < events.index(events[cleaning_started_idx])


def test_thread_safe_event_queue_drains_events_from_multiple_threads():
    from lga_extractor.events import ThreadSafeEventQueue
    import threading

    q = ThreadSafeEventQueue()
    assert q.empty()

    def producer(n):
        q({"type": "stage_started", "stage": f"layer:x{n}"})

    threads = [threading.Thread(target=producer, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    drained = q.drain()
    assert len(drained) == 10
    assert q.empty()


def test_build_stage_order_includes_layers_in_config():
    from lga_extractor.events import build_stage_order

    order = build_stage_order({"roads": {}, "schools": {}})
    assert order == ["boundary", "layer:roads", "layer:schools", "cleaning", "export"]


def test_build_manifest_reconciles_query_and_export_status():
    """
    manifest.build_manifest() must combine query-time status
    (layers.extract_layers()'s "_status") with export-time outcome
    (export.export_layers()'s return value) correctly: a layer that
    queried fine but was skipped at export (e.g. dropped entirely
    during cleaning) should show exported=False and its query status,
    not silently disappear from the manifest.
    """
    from lga_extractor.manifest import build_manifest

    layer_status = {
        "roads": {"status": "success", "feature_count": 5, "attempts": 1, "message": None},
        "schools": {"status": "success_empty", "feature_count": 0, "attempts": 1,
                    "message": "Layer 'schools' returned no features within the boundary."},
        "health_facilities": {"status": "failed", "feature_count": 0, "attempts": 6,
                               "message": "Layer 'health_facilities' failed to extract after 6 attempt(s): boom"},
    }
    exported = {
        "roads": {"geojson": "/tmp/roads.geojson", "shapefile": "/tmp/roads.shp", "feature_count": 4},
        "_skipped": ["schools", "health_facilities"],
    }

    manifest = build_manifest(
        lga_name="Akure North",
        state_name="Ondo",
        target_crs="EPSG:32631",
        boundary_source="osm_geocode",
        layer_status=layer_status,
        exported=exported,
    )

    assert manifest["target_crs"] == "EPSG:32631"
    assert manifest["source"] == "OpenStreetMap"

    roads = manifest["layers"]["roads"]
    assert roads["query_status"] == "success"
    assert roads["exported"] is True
    assert roads["feature_count"] == 4  # post-cleaning export count, not the raw query count
    assert roads["feature_count_raw"] == 5

    schools = manifest["layers"]["schools"]
    assert schools["query_status"] == "success_empty"
    assert schools["exported"] is False
    assert schools["geojson_path"] is None

    health = manifest["layers"]["health_facilities"]
    assert health["query_status"] == "failed"
    assert health["exported"] is False
    assert "boom" in health["query_message"]


def test_write_manifest_writes_valid_json(tmp_path=None):
    from lga_extractor.manifest import build_manifest, write_manifest

    manifest = build_manifest(
        lga_name="Akure North",
        state_name="Ondo",
        target_crs="EPSG:32631",
        boundary_source="osm_geocode",
        layer_status={},
        exported={},
    )

    tmp_dir = tempfile.mkdtemp()
    try:
        path = write_manifest(manifest, tmp_dir)
        assert os.path.exists(path)
        assert os.path.basename(path) == "manifest.json"
        with open(path) as f:
            reloaded = json.load(f)
        assert reloaded["lga_name"] == "Akure North"
        assert reloaded["target_crs"] == "EPSG:32631"
    finally:
        shutil.rmtree(tmp_dir)


def test_build_manifest_carries_boundary_path():
    """
    build_manifest()'s new boundary_path parameter must appear verbatim
    in the returned manifest dict as a top-level "boundary_path" field
    -- this is the field data_contract.resolve_boundary_path_from_
    manifest() reads on the dashboard side, so a consumer can load the
    boundary polygon this extraction run actually used without a live
    OSM/Nominatim call.
    """
    from lga_extractor.manifest import build_manifest

    manifest = build_manifest(
        lga_name="Akure North",
        state_name="Ondo",
        target_crs="EPSG:32631",
        boundary_source="osm_geocode",
        layer_status={},
        exported={},
        boundary_path="output/akure_north/boundary.geojson",
    )
    assert manifest["boundary_path"] == "output/akure_north/boundary.geojson"


def test_build_manifest_boundary_path_defaults_to_none():
    """
    Omitting boundary_path entirely (e.g. a caller using an older
    calling convention) must not raise, and must produce an explicit
    None rather than a missing key -- so a downstream consumer can rely
    on manifest["boundary_path"] always being present, distinguishing
    "no boundary was exported for this run" from "this manifest predates
    the field existing at all" is left to schema_version, not key
    absence.
    """
    from lga_extractor.manifest import build_manifest

    manifest = build_manifest(
        lga_name="Akure North",
        state_name="Ondo",
        target_crs="EPSG:32631",
        boundary_source="osm_geocode",
        layer_status={},
        exported={},
    )
    assert manifest["boundary_path"] is None


def test_extract_lga_writes_boundary_geojson_and_records_it_in_manifest():
    """
    End-to-end (fully offline -- both boundary AND layer queries are
    mocked, so this test never touches the network): extract_lga() must
    write '{output_dir}/boundary.geojson' as a real, loadable file, and
    the returned summary dict / manifest.json must both record its
    path -- this is the actual fix for the gap where a downstream
    consumer had no choice but to call resolve_boundary() live to
    obtain the boundary polygon, even when every other input was
    already being read from the extractor's cached output.

    Layer extraction is mocked here (not just boundary resolution)
    because manual_boundary_path only bypasses live boundary
    geocoding -- extract_lga() still calls the real extract_layers()
    against that boundary afterward, which would otherwise make live
    Overpass queries for every configured layer and make this test
    slow and network-dependent for no reason relevant to what it's
    actually testing (boundary export wiring, not layer extraction).
    """
    from lga_extractor.pipeline import extract_lga

    def mock_features(polygon, tags):
        return gpd.GeoDataFrame({"osmid": [1]}, geometry=[Point(5.2, 7.25)], crs="EPSG:4326")

    boundary_path_in = _write_manual_boundary(box(5.15, 7.2, 5.25, 7.3))
    tmp_out = tempfile.mkdtemp()
    try:
        with patch("lga_extractor.layers.ox.features_from_polygon", side_effect=mock_features):
            result = extract_lga(
                lga_name="Akure North",
                state_name="Ondo",
                output_dir=tmp_out,
                manual_boundary_path=boundary_path_in,
            )

        expected_path = os.path.join(tmp_out, "boundary.geojson")
        assert result["boundary_path"] == expected_path
        assert os.path.exists(expected_path)

        reloaded_boundary = gpd.read_file(expected_path)
        assert len(reloaded_boundary) == 1
        assert str(reloaded_boundary.crs).upper() in ("EPSG:4326", "OGC:CRS84")

        assert result["manifest"]["boundary_path"] == expected_path
        with open(result["manifest_path"]) as f:
            reloaded_manifest = json.load(f)
        assert reloaded_manifest["boundary_path"] == expected_path
    finally:
        shutil.rmtree(tmp_out, ignore_errors=True)
        os.remove(boundary_path_in)


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


def test_validate_and_standardize_rejects_non_boundary_class():
    """
    A resolved feature whose OSM class is not "boundary" (e.g. Nominatim
    matched a same-named road or river instead of an administrative
    boundary relation) must raise. This is a case the geographic/area
    checks alone cannot catch, since a wrongly-resolved feature can
    still have a plausible centroid and area once force-interpreted as
    a polygon.
    """
    gdf = gpd.GeoDataFrame(
        {"class": ["highway"], "type": ["primary"]},
        geometry=[box(5.15, 7.2, 5.25, 7.3)],
        crs="EPSG:4326",
    )
    with pytest.raises(BoundaryResolutionError, match="class='highway'"):
        _validate_and_standardize(gdf, source="test", lga_name="Akure North", state_name="Ondo")


def test_validate_and_standardize_rejects_non_administrative_type():
    """
    A resolved feature with class="boundary" but type != "administrative"
    (e.g. a natural or landuse boundary) must also raise -- both class
    AND type must match for this check to pass.
    """
    gdf = gpd.GeoDataFrame(
        {"class": ["boundary"], "type": ["national_park"]},
        geometry=[box(5.15, 7.2, 5.25, 7.3)],
        crs="EPSG:4326",
    )
    with pytest.raises(BoundaryResolutionError, match="type='national_park'"):
        _validate_and_standardize(gdf, source="test", lga_name="Akure North", state_name="Ondo")


def test_validate_and_standardize_accepts_correct_class_and_type():
    """
    The happy path for the new class/type check: class="boundary",
    type="administrative" must pass cleanly with no warnings related to
    this check, confirming the check doesn't false-positive on a
    genuinely correct administrative boundary resolution.
    """
    gdf = gpd.GeoDataFrame(
        {"class": ["boundary"], "type": ["administrative"]},
        geometry=[box(5.15, 7.2, 5.25, 7.3)],
        crs="EPSG:4326",
    )
    result = _validate_and_standardize(gdf, source="test", lga_name="Akure North", state_name="Ondo")
    assert result["validation_warnings"].iloc[0] is None
    assert result["osm_class"].iloc[0] == "boundary"
    assert result["osm_type_tag"].iloc[0] == "administrative"


def test_validate_and_standardize_missing_class_type_columns_is_a_noop():
    """
    A manually-supplied boundary (or any GeoDataFrame with no class/type
    columns at all) must NOT be rejected by the new check -- absence of
    this metadata is expected and normal for the manual-boundary path,
    and must never be conflated with "class/type present but wrong".
    """
    gdf = gpd.GeoDataFrame(geometry=[box(5.15, 7.2, 5.25, 7.3)], crs="EPSG:4326")
    result = _validate_and_standardize(gdf, source="manual:test.geojson")
    assert result["osm_class"].iloc[0] is None
    assert result["osm_type_tag"].iloc[0] is None


def test_validate_and_standardize_warns_on_non_relation_osm_type():
    """
    The osm_type (element type) soft check: a way or node resolving in
    place of an LGA-scale administrative boundary is suspicious and
    should warn, but must not raise -- this is a secondary signal, not
    as strong as the class/type hard check.
    """
    gdf = gpd.GeoDataFrame(
        {"class": ["boundary"], "type": ["administrative"], "osm_type": ["way"]},
        geometry=[box(5.15, 7.2, 5.25, 7.3)],
        crs="EPSG:4326",
    )
    result = _validate_and_standardize(gdf, source="test", lga_name="Akure North", state_name="Ondo")
    assert result["validation_warnings"].iloc[0] is not None
    assert "relation" in result["validation_warnings"].iloc[0]


def test_validate_and_standardize_accepts_relation_osm_type_silently():
    """
    The expected osm_type="relation" case must produce no warning
    related to this check.
    """
    gdf = gpd.GeoDataFrame(
        {"class": ["boundary"], "type": ["administrative"], "osm_type": ["relation"]},
        geometry=[box(5.15, 7.2, 5.25, 7.3)],
        crs="EPSG:4326",
    )
    result = _validate_and_standardize(gdf, source="test", lga_name="Akure North", state_name="Ondo")
    assert result["validation_warnings"].iloc[0] is None


def test_validate_and_standardize_warns_on_implausible_admin_level():
    """
    A present-but-implausible admin_level (e.g. 2, more like a national/
    regional level than an LGA) should warn, not raise -- this check is
    explicitly best-effort/advisory (see PLAUSIBLE_ADMIN_LEVEL_RANGE's
    own comment on why it isn't a strict admin_level==6 check).
    """
    gdf = gpd.GeoDataFrame(
        {"class": ["boundary"], "type": ["administrative"], "admin_level": ["2"]},
        geometry=[box(5.15, 7.2, 5.25, 7.3)],
        crs="EPSG:4326",
    )
    result = _validate_and_standardize(gdf, source="test", lga_name="Akure North", state_name="Ondo")
    assert result["validation_warnings"].iloc[0] is not None
    assert "admin_level" in result["validation_warnings"].iloc[0]
    assert result["admin_level"].iloc[0] == "2"


def test_validate_and_standardize_accepts_plausible_admin_level_silently():
    """
    admin_level="6" (Nigeria's conventional LGA tagging level) must
    produce no warning.
    """
    gdf = gpd.GeoDataFrame(
        {"class": ["boundary"], "type": ["administrative"], "admin_level": ["6"]},
        geometry=[box(5.15, 7.2, 5.25, 7.3)],
        crs="EPSG:4326",
    )
    result = _validate_and_standardize(gdf, source="test", lga_name="Akure North", state_name="Ondo")
    assert result["validation_warnings"].iloc[0] is None


def test_validate_and_standardize_missing_admin_level_produces_no_warning():
    """
    Absence of the admin_level column entirely (the common case, since
    OSMnx's default geocode result does not reliably include it) must
    NOT produce a warning -- we only warn about a value we actually
    have, never about missing information we were never promised.
    """
    gdf = gpd.GeoDataFrame(
        {"class": ["boundary"], "type": ["administrative"]},
        geometry=[box(5.15, 7.2, 5.25, 7.3)],
        crs="EPSG:4326",
    )
    result = _validate_and_standardize(gdf, source="test", lga_name="Akure North", state_name="Ondo")
    assert result["validation_warnings"].iloc[0] is None
    assert result["admin_level"].iloc[0] is None


def test_validate_and_standardize_treats_nan_metadata_as_absent():
    """
    A column that's present but holds NaN (pandas' other spelling of
    "missing", distinct from a column being absent entirely -- e.g. a
    raw Nominatim response with an explicit empty admin_level field)
    must be treated identically to the column not existing at all: no
    false-positive warning, no crash, "admin_level" in the output stays
    None. Guards against the is-not-None vs is-not-NaN distinction that
    is easy to get wrong when reading values out of a pandas column.
    """
    import numpy as np

    gdf = gpd.GeoDataFrame(
        {
            "class": ["boundary"],
            "type": ["administrative"],
            "admin_level": [np.nan],
            "osm_type": [np.nan],
        },
        geometry=[box(5.15, 7.2, 5.25, 7.3)],
        crs="EPSG:4326",
    )
    result = _validate_and_standardize(gdf, source="test", lga_name="Akure North", state_name="Ondo")
    assert result["validation_warnings"].iloc[0] is None
    assert result["admin_level"].iloc[0] is None


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
