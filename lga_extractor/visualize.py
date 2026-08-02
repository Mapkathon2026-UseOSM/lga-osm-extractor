"""
visualize.py

Generates a polished, standalone kepler.gl HTML preview of an
extracted LGA's OSM layers (roads, buildings, waterways, land use,
health facilities, schools). This is a visual convenience layer only, it does not perform any analysis, intended to give a quick,
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
import re
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

# The installed keplergl package bundles a real (if publicly-scoped)
# Mapbox access token directly into its exported HTML, regardless of
# which basemap style is actually configured (confirmed: it's still
# present even when kepler_config_lga_preview.json's mapStyle points at
# a free, non-Mapbox CARTO basemap instead of a Mapbox-hosted one, # the token appears to be baked into keplergl's bundled JS itself,
# likely for an unrelated internal feature such as the in-app style
# switcher, not the actual displayed basemap). This is NOT something
# this project's config controls. GitHub's push protection correctly
# flags it as a credential regardless of its public/secret scoping,
# since it still belongs to a third party.
#
# This project's kepler configs (kepler_config_lga_preview.json, and
# the companion akure-access-dashboard repo's kepler configs) use a
# free CARTO Positron basemap instead of a Mapbox-hosted style, so the
# displayed basemap itself never actually depends on this token, but
# since the token is embedded regardless of style choice, it must
# still be stripped explicitly (see _strip_mapbox_token() below) before
# any export can safely be committed to a public repository.
#
# _strip_mapbox_token() removes it after export: since this project's
# configs use a free CARTO basemap rather than a Mapbox-hosted one (see
# above), the displayed map is unaffected, stripping only removes an
# unused, embedded credential, not anything the basemap actually
# depends on to render.
_MAPBOX_TOKEN_PATTERN = re.compile(r"pk\.eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+")


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
        (self-contained: data + viewer bundled in one file). Any
        Mapbox access token bundled into the export by the keplergl
        package itself is automatically stripped before saving, see
        _strip_mapbox_token() below for why this matters.
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
        _strip_mapbox_token(html_out)

    return kepler_map


def _strip_mapbox_token(html_path: str) -> bool:
    """
    Remove any bundled Mapbox access token from an exported kepler.gl
    HTML file, in place.

    See the module-level comment above _MAPBOX_TOKEN_PATTERN for why
    this is necessary: the installed keplergl package embeds a real
    Mapbox token into every save_to_html() export regardless of
    configured basemap style. Stripping it means the exported file is
    safe to commit to a public repository (verified against GitHub's
    secret-scanning push protection) with no visual cost, since this
    project's configs use a free CARTO basemap that doesn't depend on
    this token to render.

    Parameters
    ----------
    html_path : str
        Path to an HTML file produced by KeplerGl.save_to_html().

    Returns
    -------
    bool
        True if a token was found and removed, False if none was
        present (e.g. a future keplergl version that no longer bundles
        one, or a file already stripped).
    """
    with open(html_path, "r", encoding="utf-8") as f:
        content = f.read()

    stripped, n_replaced = _MAPBOX_TOKEN_PATTERN.subn("", content)

    if n_replaced > 0:
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(stripped)

    return n_replaced > 0


def _load_config() -> dict:
    if os.path.exists(_CONFIG_PATH):
        with open(_CONFIG_PATH) as f:
            return json.load(f)
    return {}
