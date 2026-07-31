"""
app.py

Lightweight Streamlit demo interface for the LGA OSM extractor.
Lets a user type an LGA (and optional state), run the extraction
pipeline, preview the layers on a map, and download results.

Run with:
    streamlit run app.py
"""

import os
import zipfile
import io

import streamlit as st
import leafmap.foliumap as leafmap

from lga_extractor import extract_lga, BoundaryResolutionError

st.set_page_config(page_title="Nigerian LGA OSM Extractor", layout="wide")

st.title("Nigerian LGA OSM Extractor")
st.write(
    "Pick a Nigerian LGA to pull roads, buildings, waterways, land use, "
    "health facilities, and schools directly from OpenStreetMap — no "
    "Overpass query syntax required."
)

with st.form("extract_form"):
    col1, col2 = st.columns(2)
    with col1:
        lga_name = st.text_input("LGA name", placeholder="e.g. Akure North")
    with col2:
        state_name = st.text_input("State name (optional, recommended)", placeholder="e.g. Ondo")
    submitted = st.form_submit_button("Extract OSM Data")

if submitted:
    if not lga_name.strip():
        st.error("Please enter an LGA name.")
    else:
        with st.spinner(f"Resolving boundary and extracting OSM layers for {lga_name}..."):
            try:
                result = extract_lga(lga_name=lga_name, state_name=state_name or None)
            except BoundaryResolutionError as exc:
                st.error(f"Could not resolve LGA boundary: {exc}")
                result = None
            except Exception as exc:
                st.error(f"Extraction failed: {exc}")
                result = None

        if result:
            st.success(f"Extraction complete for {lga_name}.")

            if result["warnings"]:
                with st.expander("Warnings"):
                    for w in result["warnings"]:
                        st.write(f"- {w}")

            st.subheader("Preview map")
            m = leafmap.Map()
            output_dir = result["output_dir"]
            for layer_name, paths in result["exported"].items():
                if layer_name.startswith("_"):  # skip metadata keys like _skipped, _split_layers
                    continue
                geojson_path = paths.get("geojson")
                if geojson_path and os.path.exists(geojson_path):
                    m.add_geojson(geojson_path, layer_name=layer_name)
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
            )
