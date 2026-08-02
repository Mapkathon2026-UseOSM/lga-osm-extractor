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
"""

import os
import geopandas as gpd


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


def export_layers(layers_dict: dict, output_dir: str) -> dict:
    """
    Export each cleaned layer to GeoJSON and Shapefile.

    Parameters
    ----------
    layers_dict : dict
        Mapping of layer_name -> cleaned GeoDataFrame, as returned by
        clean.clean_layers(). The "_warnings" key, if present, is
        skipped during export.
    output_dir : str
        Directory to write outputs into. Created if it does not exist.
        A "shapefiles" subfolder is used for .shp outputs to keep the
        directory tidy (Shapefiles produce multiple sidecar files).

    Returns
    -------
    dict
        Mapping of layer_name -> {"geojson": path, "shapefile": shapefile_value}
        for each successfully exported layer.

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
        if layer_name == "_warnings":
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
            gdf.to_file(shp_path, driver="ESRI Shapefile")
            exported[layer_name] = {"geojson": geojson_path, "shapefile": shp_path}
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
                subset.to_file(shp_path, driver="ESRI Shapefile")
                shapefile_paths[cat] = shp_path

            exported[layer_name] = {"geojson": geojson_path, "shapefile": shapefile_paths}
            split_layers[layer_name] = categories

    exported["_skipped"] = skipped
    if split_layers:
        exported["_split_layers"] = split_layers
    return exported
