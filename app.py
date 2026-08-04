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
