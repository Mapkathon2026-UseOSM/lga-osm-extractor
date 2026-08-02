"""
visualize.py

Generates a polished, standalone kepler.gl HTML preview of an
extracted LGA's OSM layers (roads, buildings, waterways, land use,
health facilities, schools). This is a visual convenience layer only
-- it does not perform any analysis -- intended to give a quick,
shareable look at what was extracted for a given LGA without needing
GIS software installed.

Usage
-----
    from lga_extractor.visualize import build_preview_map

    build_preview_map(
        output_dir="output/akure_north",
        html_out="visuals/akure_north_preview.html",
    )

Requires: pip install keplergl
"""

import os
import json

import geopandas as gpd

try:
    from keplergl import KeplerGl
except ImportError as exc:
    raise ImportError(
        "keplergl is not installed. Run 'pip install keplergl' to use "
        "the visualization helper."
    ) from exc

_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "kepler_config_lga_preview.json")

# Layers to attempt to load, in draw order (roads/waterways under
# buildings/facilities so points aren't hidden beneath fills).
_LAYER_FILES = [
    "landuse",
    "waterways",
    "roads",
    "buildings",
    "health_facilities",
    "schools",
]


def build_preview_map(output_dir: str, html_out: str = None, height: int = 600) -> "KeplerGl":
    """
    Build a kepler.gl preview map from a single LGA's extracted output.

    Parameters
    ----------
    output_dir : str
        Directory containing the extracted GeoJSON layers for one LGA
        (as produced by lga_extractor.extract_lga()), e.g.
        "output/akure_north".
    html_out : str, optional
        If provided, saves a standalone HTML file to this path
        (self-contained: data + viewer bundled in one file).
    height : int
        Map height in pixels when rendered in a notebook.

    Returns
    -------
    keplergl.KeplerGl
        The map object. In a Jupyter notebook, display it by returning
        it as the last expression in a cell.
    """
    data = {}
    for layer_name in _LAYER_FILES:
        path = os.path.join(output_dir, f"{layer_name}.geojson")
        if not os.path.exists(path):
            continue
        gdf = gpd.read_file(path)
        if gdf.empty:
            continue
        data[layer_name] = gdf.to_crs("EPSG:4326")

    if not data:
        raise ValueError(
            f"No non-empty layers found in '{output_dir}'. "
            "Run extraction for this LGA first."
        )

    config = _load_config()
    kepler_map = KeplerGl(height=height, data=data, config=config)

    if html_out:
        os.makedirs(os.path.dirname(html_out) or ".", exist_ok=True)
        kepler_map.save_to_html(file_name=html_out)

    return kepler_map


def _load_config() -> dict:
    if os.path.exists(_CONFIG_PATH):
        with open(_CONFIG_PATH) as f:
            return json.load(f)
    return {}
