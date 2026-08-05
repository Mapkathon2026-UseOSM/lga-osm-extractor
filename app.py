"""
app.py

Streamlit demo interface for the Nigerian LGA OSM extractor. Lets a
user type an LGA (and optional state), run the extraction pipeline,
preview every extracted layer on an interactive, toggleable map, and
download the results as a zip.

Run with:
    streamlit run app.py
"""

import os
import zipfile
import io

import streamlit as st
import geopandas as gpd
import leafmap.foliumap as leafmap

from lga_extractor import extract_lga, BoundaryResolutionError

st.set_page_config(page_title="Nigerian LGA OSM Extractor", page_icon="\U0001F5FA", layout="wide")

st.markdown(
    """
    <style>
    /* Global font-size bump (~2pt) so widget labels, expander body text,
       captions, and every other default Streamlit text element grows
       consistently, without needing a separate CSS rule per element. */
    html {
        font-size: 112%;
    }
    .hero-title {
        font-size: 40px;
        font-weight: 700;
        margin-bottom: 0.3rem;
    }
    .hero-sub {
        font-size: 21px;
        color: #9a9a9a;
        max-width: 60rem;
        line-height: 1.5;
        margin-bottom: 1rem;
    }
    .callout {
        background: rgba(76, 154, 140, 0.08);
        border-left: 3px solid #4C9A8C;
        border-radius: 6px;
        padding: 0.9rem 1.1rem;
        font-size: 1.05rem;
        color: #c9c9c9;
        line-height: 1.55;
        margin: 0.75rem 0 1.25rem 0;
    }
    div[data-testid="stFormSubmitButton"] button,
    div[data-testid="stDownloadButton"] button {
        border: 2px solid #C4622D;
        box-shadow: 0 0 0 1px rgba(196, 98, 45, 0.25);
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown('<div class="hero-title">Nigerian LGA OSM Extractor</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="hero-sub">Pull roads, buildings, waterways, land use, health facilities, '
    "and schools for any Nigerian Local Government Area directly from OpenStreetMap, no "
    "Overpass query syntax, GIS software, or manual data wrangling required.</div>",
    unsafe_allow_html=True,
)

with st.expander("About this tool and how to use it", expanded=False):
    st.markdown(
        """
This tool turns a plain LGA name into a clean, ready-to-use OSM dataset in three steps:

1. **Type an LGA name** (and ideally its state, to disambiguate LGAs that share a name
   across different states, e.g. more than one Nigerian LGA can share a name).
2. **Click "Extract OSM Data"**. Behind the scenes, the tool resolves the LGA's official
   boundary, then queries OpenStreetMap's live Overpass API for six layers within that
   boundary: **roads**, **buildings**, **waterways**, **land use**, **health facilities**,
   and **schools**.
3. **Preview every layer on the interactive map below**, click any feature to see its OSM
   attributes, toggle individual layers on or off using the layer control (top-right of
   the map), and **download everything as a zip** of clean, standardized GeoJSON files,
   one per layer, ready to open in QGIS, load into a notebook, or feed into further
   analysis.

**Why extraction can take a few minutes.** Every run queries OpenStreetMap's live,
shared Overpass API server in real time, this tool doesn't use a pre-downloaded or
cached copy of OSM data, so results are always current. Larger, denser urban LGAs
(especially the **buildings** layer, which can include tens of thousands of individual
building footprints) take longer to fetch and process than smaller or more rural ones.
A wait of one to five minutes for a full extraction is normal and expected, not a sign
anything is stuck; re-running the exact same LGA and state again in this session will
be instant, since results are cached.

**What each layer contains:**
- **Roads**: the road/path network (`highway=*` tags), as lines.
- **Buildings**: building footprints (`building=*` tags), as polygons.
- **Waterways**: rivers, streams, and water bodies (`waterway=*` and `natural=water`).
- **Land use**: zoned/designated land areas (`landuse=*` tags), as polygons.
- **Health facilities**: hospitals, clinics, and pharmacies (`amenity=hospital/clinic/pharmacy`).
- **Schools**: `amenity=school` features.

Any layer that genuinely has zero matching features in OpenStreetMap for a given LGA
(common for smaller or less-mapped areas) is simply empty, not a failure, this reflects
real current OSM coverage for that area, which is itself useful information.
        """
    )

with st.form("extract_form"):
    col1, col2 = st.columns(2)
    with col1:
        lga_name = st.text_input("LGA name", placeholder="e.g. Akure North")
    with col2:
        state_name = st.text_input("State name (optional, recommended)", placeholder="e.g. Ondo")
    submitted = st.form_submit_button("Extract OSM Data", type="primary")


@st.cache_data(show_spinner=False)
def _cached_extract(lga_name: str, state_name):
    """
    Thin cache wrapper around extract_lga(). Extraction hits OpenStreetMap's
    live Overpass API for six separate layers and is genuinely slow
    (see the "why extraction can take a few minutes" note above), caching
    means re-running the SAME lga_name/state_name combination again in
    this session (or by another user, since Streamlit's cache is shared
    across sessions by default) returns instantly instead of re-querying
    Overpass from scratch. extract_lga()'s own output_dir defaults to a
    path derived deterministically from lga_name, so the files already
    written to disk on the first run remain valid for a cached return.
    """
    return extract_lga(lga_name=lga_name, state_name=state_name)


# Distinct color per layer type, applied to both the map styling below
# and kept visually consistent with the companion dashboard's own
# palette conventions, so a line/fill color means roughly the same
# thing to anyone who has also seen the Akure Access Dashboard.
LAYER_STYLES = {
    "roads": {"color": "#4A4A4A", "weight": 2, "opacity": 0.9},
    "buildings": {"color": "#C4622D", "weight": 1, "fillColor": "#C4622D", "fillOpacity": 0.35},
    "waterways": {"color": "#1F6FB2", "weight": 2, "fillColor": "#1F6FB2", "fillOpacity": 0.4},
    "landuse": {"color": "#4C9A8C", "weight": 1, "fillColor": "#4C9A8C", "fillOpacity": 0.25},
    "health_facilities": {"color": "#C0392B", "weight": 1, "fillColor": "#C0392B", "fillOpacity": 0.9},
    "schools": {"color": "#8E44AD", "weight": 1, "fillColor": "#8E44AD", "fillOpacity": 0.9},
}

if submitted:
    if not lga_name.strip():
        st.error("Please enter an LGA name.")
        st.session_state.pop("extraction_result", None)
    else:
        with st.spinner(
            f"Resolving boundary and extracting OSM layers for {lga_name} "
            f"(roads, buildings, waterways, land use, health facilities, schools). "
            f"This can take a few minutes for larger or denser LGAs, this is normal, "
            f"see \"About this tool\" above for why."
        ):
            try:
                result = _cached_extract(lga_name.strip(), state_name.strip() or None)
            except BoundaryResolutionError as exc:
                st.error(f"Could not resolve LGA boundary: {exc}")
                result = None
            except Exception as exc:
                st.error(f"Extraction failed: {exc}")
                result = None

        # Store the result (plus which LGA it belongs to) in session_state
        # instead of only holding it in a local variable. Streamlit reruns
        # the ENTIRE script top-to-bottom on any widget interaction,
        # including clicking st.download_button below, that's simply how
        # Streamlit implements downloads. Without session_state, this whole
        # block (map included) was gated behind `if submitted:`, which is
        # only True on the exact run where the form was just submitted, so
        # the very next rerun (triggered by clicking download) had
        # submitted=False again and the entire preview/download section,
        # map included, disappeared. Storing the result here means it
        # survives that rerun; it's only ever replaced when a NEW
        # extraction actually succeeds (or cleared above if the LGA name
        # was blank), not simply whenever the script happens to rerun.
        if result:
            st.session_state["extraction_result"] = result
            st.session_state["extraction_lga_name"] = lga_name

if "extraction_result" in st.session_state:
    result = st.session_state["extraction_result"]
    lga_name = st.session_state["extraction_lga_name"]

    st.success(f"Extraction complete for {lga_name}.")

    if result["warnings"]:
        with st.expander("Warnings"):
            for w in result["warnings"]:
                st.write(f"- {w}")

    st.subheader("Preview map")
    st.caption(
        "Every extracted layer is shown together below. Click any feature to see "
        "its OSM attributes, and use the layer control (top-right of the map) to "
        "toggle individual layers on or off, useful since dense layers like "
        "buildings can visually overwhelm the map when everything is shown at once."
    )
    m = leafmap.Map()
    output_dir = result["output_dir"]

    layer_items = [
        (name, paths) for name, paths in result["exported"].items()
        if not name.startswith("_")
    ]
    layer_items.sort(key=lambda item: 0 if item[0] == "roads" else 1)

    # Collect bounds from every non-empty layer so the map frames ALL
    # extracted data, not just whichever layer happens to be added
    # first. Relying on add_geojson's own zoom_to_layer for only one
    # layer is fragile: if that particular layer is empty, small, or
    # has odd geometry, every other (non-empty) layer ends up added
    # to the map but outside the visible viewport, which looks
    # exactly like "the map loaded but nothing is on it".
    combined_bounds = None
    added_any_layer = False

    for layer_name, paths in layer_items:
        geojson_path = paths.get("geojson")
        if not geojson_path or not os.path.exists(geojson_path):
            continue

        # Guard against genuinely empty layers (0 features is valid
        # and expected per the "About this tool" note above, e.g. no
        # mapped waterways in a given LGA). Without this check,
        # leafmap's add_geojson() tries to read
        # data["features"][0]["properties"] to build tooltip/popup
        # fields and raises IndexError on an empty FeatureCollection,
        # which breaks the rest of this loop.
        gdf = gpd.read_file(geojson_path)
        if gdf.empty:
            continue

        style = LAYER_STYLES.get(layer_name, {})
        m.add_geojson(
            geojson_path,
            layer_name=layer_name.replace("_", " ").title(),
            style=style,
            info_mode="on_click",
            zoom_to_layer=False,  # we zoom once, explicitly, below instead
        )
        added_any_layer = True

        bounds = gdf.total_bounds  # [minx, miny, maxx, maxy]
        if combined_bounds is None:
            combined_bounds = list(bounds)
        else:
            combined_bounds[0] = min(combined_bounds[0], bounds[0])
            combined_bounds[1] = min(combined_bounds[1], bounds[1])
            combined_bounds[2] = max(combined_bounds[2], bounds[2])
            combined_bounds[3] = max(combined_bounds[3], bounds[3])

    if added_any_layer and combined_bounds is not None:
        m.zoom_to_bounds(combined_bounds)
    else:
        st.info(
            "No features were found in any layer for this LGA, there is "
            "nothing to preview on the map for this extraction."
        )

    m.add_layer_control()
    m.to_streamlit(height=600)

    st.subheader("Download results")
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as zf:
        for root, _, files in os.walk(output_dir):
            for file in files:
                filepath = os.path.join(root, file)
                arcname = os.path.relpath(filepath, output_dir)
                zf.write(filepath, arcname)
    zip_buffer.seek(0)

    st.download_button(
        label=f"Download {lga_name} OSM data (.zip)",
        data=zip_buffer,
        file_name=f"{lga_name.replace(' ', '_').lower()}_osm_data.zip",
        mime="application/zip",
        type="primary",
    )

st.markdown(
    """
    <div style="display: flex; align-items: center; gap: 0.5rem; margin-top: 2rem;">
        <a href="https://github.com/Mapkathon2026-UseOSM/lga-osm-extractor"
           target="_blank" rel="noopener noreferrer"
           style="display: flex; align-items: center; gap: 0.5rem; color: #9a9a9a; text-decoration: none;">
            <svg height="20" width="20" viewBox="0 0 16 16" fill="#9a9a9a" aria-hidden="true">
                <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38
                0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13
                -.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66
                .07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15
                -.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0
                1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82
                1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01
                1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8z"/>
            </svg>
            <span>View the full source code and project history on GitHub</span>
        </a>
    </div>
    """,
    unsafe_allow_html=True,
)
