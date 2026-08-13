"""
export.py

Exports cleaned layer GeoDataFrames to GeoJSON and Shapefile formats,
organized under a per-LGA output directory.

Note on mixed geometry types
-----------------------------
Some OSM tag filters legitimately return more than one geometry type in a
single query. The clearest example is `highway=*` (used for the "roads"
layer): this matches not only road ways (LineStrings) but also point nodes
such as traffic signals and crossings (`highway=traffic_signals`,
`highway=crossing`, etc.), producing a mix of Point and LineString
geometries. Similarly, `waterway=*` + `natural=water` can mix LineStrings
(rivers/streams) with Polygons (lakes/reservoirs).

GeoJSON handles mixed geometry types within one file without any issue.
Shapefile does not: every feature in a single .shp file must share the
same geometry type, and GDAL infers that type from the first feature
written, raising a FeatureError as soon as it encounters a different type.

To keep GeoJSON export simple (one file per layer) while still producing
valid Shapefiles, this module splits a layer's Shapefile export by
geometry-type category (point / line / polygon) whenever a layer contains
more than one category, writing e.g. "roads_point.shp" and
"roads_line.shp" instead of a single "roads.shp". Layers with a single
geometry category (the common case) still export as one plain
"{layer_name}.shp", unchanged from before.

Note on which format carries the rich attribute schema
--------------------------------------------------------
clean.clean_layers() preserves richer per-layer OSM attributes
(clean.SEMANTIC_COLUMNS) plus a full raw_tags JSON column on top of the
core osmid/name/geometry schema. GeoJSON exports that full schema
as-is. Shapefile exports deliberately do NOT: DBF field names are
truncated to 10 characters, which would silently collide or mangle
several of these columns, and a JSON blob has no sensible home in a
fixed-width DBF field. Shapefile output therefore stays at the original
minimal core schema only, see _shapefile_safe_columns() below.
"""

import os
import geopandas as gpd

from .clean import CORE_COLUMNS, RAW_TAGS_COLUMN


_GEOM_CATEGORY = {
    "Point": "point",
    "MultiPoint": "point",
    "LineString": "line",
    "MultiLineString": "line",
    "Polygon": "polygon",
    "MultiPolygon": "polygon",
}


def _geom_categories_present(gdf: gpd.GeoDataFrame) -> list:
    cats = gdf.geometry.geom_type.map(lambda t: _GEOM_CATEGORY.get(t, "other")).unique().tolist()
    return sorted(cats)


def _shapefile_safe_columns(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """
    Reduce a cleaned layer to CORE_COLUMNS only, for Shapefile export.

    Since clean.clean_layers() started preserving richer per-layer
    semantic OSM attributes plus a full RAW_TAGS_COLUMN JSON blob (see
    clean.py's SEMANTIC_COLUMNS/RAW_TAGS_COLUMN), writing that full
    schema to Shapefile is actively harmful, not just imprecise:
    Shapefile field names are truncated to 10 characters (silently
    colliding e.g. "building:levels" and "building:use" both to
    "building:l"/similar, and RAW_TAGS_COLUMN's JSON blob has no
    business being crammed into a 10-char-named DBF field at all).
    GeoJSON has no such limit and is where the full schema belongs;
    Shapefile stays deliberately minimal (osmid/name/geometry) and
    fully backward compatible with every pre-existing Shapefile
    consumer of this extractor's output.
    """
    keep = [c for c in CORE_COLUMNS if c in gdf.columns]
    return gdf[keep]


def export_layers(layers_dict: dict, output_dir: str) -> dict:
    """
    Export each cleaned layer to GeoJSON and Shapefile.

    Parameters
    ----------
    layers_dict : dict
        Mapping of layer_name -> cleaned GeoDataFrame, as returned by
        clean.clean_layers(). The "_warnings" and "_status" keys, if
        present, are skipped during export.
    output_dir : str
        Directory to write outputs into. Created if it does not exist.
        A "shapefiles" subfolder is used for .shp outputs to keep the
        directory tidy (Shapefiles produce multiple sidecar files).

    Returns
    -------
    dict
        Mapping of layer_name -> {"geojson": path, "shapefile": shapefile_value,
        "feature_count": int} for each successfully exported layer. The
        "feature_count" is the number of features actually written
        (i.e. post-cleaning), distinct from the pre-cleaning
        "feature_count" recorded in extract_layers()'s "_status" —
        pipeline.extract_lga() reconciles both into a single manifest.

        `shapefile_value` is:
          - a single path string, when the layer contains only one
            geometry-type category (the common case, e.g. buildings,
            health_facilities), unchanged from previous behavior.
          - a dict of {category: path}, when the layer contains more
            than one geometry-type category (e.g. roads mixing point
            nodes and line ways), one Shapefile per category, since a
            single Shapefile cannot mix geometry types.

        Empty layers are skipped and noted under the "_skipped" key.
        Layers that were split are noted under the "_split_layers" key
        for visibility/logging.
    """
    os.makedirs(output_dir, exist_ok=True)
    shp_dir = os.path.join(output_dir, "shapefiles")
    os.makedirs(shp_dir, exist_ok=True)

    exported = {}
    skipped = []
    split_layers = {}

    for layer_name, gdf in layers_dict.items():
        if layer_name in ("_warnings", "_status"):
            continue

        if gdf is None or gdf.empty:
            skipped.append(layer_name)
            continue

        geojson_path = os.path.join(output_dir, f"{layer_name}.geojson")
        gdf.to_file(geojson_path, driver="GeoJSON")

        categories = _geom_categories_present(gdf)

        if len(categories) <= 1:
            # Single geometry type (or empty), export as one Shapefile,
            # same as before.
            shp_path = os.path.join(shp_dir, f"{layer_name}.shp")
            _shapefile_safe_columns(gdf).to_file(shp_path, driver="ESRI Shapefile")
            exported[layer_name] = {
                "geojson": geojson_path,
                "shapefile": shp_path,
                "feature_count": len(gdf),
            }
        else:
            # Mixed geometry types, split into one Shapefile per category,
            # since Shapefile cannot store mixed types in a single file.
            shapefile_paths = {}
            for cat in categories:
                geom_type_names = [k for k, v in _GEOM_CATEGORY.items() if v == cat]
                subset = gdf[gdf.geometry.geom_type.isin(geom_type_names)]
                if subset.empty:
                    continue
                shp_path = os.path.join(shp_dir, f"{layer_name}_{cat}.shp")
                _shapefile_safe_columns(subset).to_file(shp_path, driver="ESRI Shapefile")
                shapefile_paths[cat] = shp_path

            exported[layer_name] = {
                "geojson": geojson_path,
                "shapefile": shapefile_paths,
                "feature_count": len(gdf),
            }
            split_layers[layer_name] = categories

    exported["_skipped"] = skipped
    if split_layers:
        exported["_split_layers"] = split_layers
    return exported
