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
import threading
import time

import streamlit as st
import leafmap.foliumap as leafmap

from lga_extractor import extract_lga, BoundaryResolutionError, DEFAULT_TAG_CONFIG
from lga_extractor.events import ThreadSafeEventQueue, build_stage_order

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

st.markdown("#### About this tool and how to use it")
st.markdown(
    """
This tool turns a plain LGA name into a clean, ready-to-use OSM dataset in three steps:

1. **Type an LGA name** (and ideally its state, to disambiguate LGAs that share a name
   across different states, e.g. more than one Nigerian LGA can share a name).
2. **Click "Extract OSM Data"**. Behind the scenes, the tool resolves the LGA's official
   boundary, then queries OpenStreetMap's live Overpass API for six layers within that
   boundary: **roads**, **buildings**, **waterways**, **land use**, **health facilities**,
   and **schools**.
3. **Preview every layer on the interactive map below**, toggle individual layers on or
   off using the layer control (top-right of the map), and **download everything as a
   zip** of clean, standardized GeoJSON files and shapefiles, one per layer, ready to open in QGIS,
   load into a notebook, or feed into further analysis.

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


@st.cache_resource(show_spinner=False)
def _extraction_cache():
    """
    A plain dict, shared across reruns/sessions via st.cache_resource
    (unlike st.cache_data, this returns the SAME dict object every
    time rather than a copy, which is what we need to use it as a
    mutable cache), keyed by (lga_name, state_name) -> the extract_lga()
    result dict. Re-running the same LGA/state combination returns
    instantly from here, WITHOUT the live progress UI below, since
    there's nothing left to show progress for.
    """
    return {}


def _run_extraction_with_live_progress(lga_name: str, state_name):
    """
    Run extract_lga() in a background thread while rendering a live,
    per-stage progress interface on the main Streamlit thread, driven
    by the pipeline's on_event callback (see lga_extractor.events).

    This has to run extraction in a background thread rather than
    directly, extract_lga() is a single, several-minutes-long blocking
    call; the only way to update the UI WHILE it runs (rather than only
    before and after) is to have something else pushing events while
    it's in flight, and Streamlit's own APIs are not thread-safe to
    call directly from that background thread, hence draining a
    ThreadSafeEventQueue on this (the main/UI) thread instead of
    updating Streamlit from inside on_event itself.

    Returns the extract_lga() result dict, or raises whatever exception
    extract_lga() raised (propagated from the background thread).
    """
    stage_order = build_stage_order(DEFAULT_TAG_CONFIG)
    stage_labels = {stage: stage.split(":", 1)[-1].replace("_", " ").title() for stage in stage_order}
    stage_labels["boundary"] = "Resolving boundary"
    stage_labels["cleaning"] = "Cleaning datasets"
    stage_labels["export"] = "Exporting results"

    events = ThreadSafeEventQueue()
    outcome = {}  # populated by _worker: {"result": ...} or {"error": ...}

    def _worker():
        try:
            outcome["result"] = extract_lga(lga_name=lga_name, state_name=state_name, on_event=events)
        except Exception as exc:  # noqa: BLE001 - re-raised on the main thread below
            outcome["error"] = exc

    thread = threading.Thread(target=_worker, daemon=True)
    start_time = time.monotonic()
    thread.start()

    # "done" / "running" / "retrying" / "failed", per stage, drives which
    # symbol (✓ / ⟳ / ○ / retry note / ✗) each row below renders.
    stage_state = {stage: "pending" for stage in stage_order}
    stage_detail = {stage: "" for stage in stage_order}

    with st.status(f"Extracting OSM data for {lga_name}...", expanded=True) as status_box:
        progress_bar = st.progress(0.0)
        row_placeholders = {stage: st.empty() for stage in stage_order}

        def _render_row(stage):
            symbol = {"pending": "○", "running": "⟳", "retrying": "⟳", "done": "✓", "failed": "✗"}[stage_state[stage]]
            label = stage_labels[stage]
            detail = f" — {stage_detail[stage]}" if stage_detail[stage] else ""
            row_placeholders[stage].markdown(f"{symbol} {label}{detail}")

        for stage in stage_order:
            _render_row(stage)

        last_progress_fraction = None
        last_boundary_note_at = 0

        while thread.is_alive() or not events.empty():
            new_events = events.drain()

            for event in new_events:
                stage = event.get("stage")
                if stage not in stage_state:
                    continue  # ignore anything from a future event type this UI doesn't know about yet
                if event["type"] == "stage_started":
                    stage_state[stage] = "running"
                    stage_detail[stage] = ""
                elif event["type"] == "retry":
                    stage_state[stage] = "retrying"
                    stage_detail[stage] = f"retrying: {event['attempt']} / {event['max_attempts']}"
                elif event["type"] == "stage_completed":
                    stage_state[stage] = "done"
                    stage_detail[stage] = event.get("detail", "")
                elif event["type"] == "stage_failed":
                    stage_state[stage] = "failed"
                    stage_detail[stage] = event.get("message", "failed")
                _render_row(stage)

            # Only push a progress-bar update to the browser when the
            # fraction actually changed, not on every poll tick. A
            # multi-minute run polling every 0.2s otherwise sends
            # hundreds of no-op websocket messages, which on Streamlit
            # Community Cloud's shared resources visibly compounds into
            # the UI getting slower over the course of a single long
            # run, this is what was actually causing that, not a
            # genuinely slower extraction.
            done_count = sum(1 for s in stage_state.values() if s in ("done", "failed"))
            fraction = done_count / len(stage_order)
            if fraction != last_progress_fraction:
                progress_bar.progress(fraction)
                last_progress_fraction = fraction

            # If we're still waiting on the very first stage (boundary
            # resolution, a single call to OSM's Nominatim geocoder,
            # which enforces a strict 1 request/second policy and can
            # throttle under shared load) for a while, say so explicitly
            # instead of leaving a bare spinner that looks identical
            # whether it's slow or actually stuck.
            elapsed = time.monotonic() - start_time
            if stage_state.get("boundary") == "running" and elapsed - last_boundary_note_at >= 15:
                stage_detail["boundary"] = f"still waiting on OSM's boundary lookup ({elapsed:.0f}s so far, this can be slow under shared load, not necessarily stuck)"
                _render_row("boundary")
                last_boundary_note_at = elapsed

            # Poll less aggressively, this alone cuts total websocket
            # traffic for a 5-minute run by more than half versus 0.2s,
            # with no visible loss of responsiveness.
            time.sleep(0.5)

        thread.join()

        if "error" in outcome:
            status_box.update(label=f"Extraction failed for {lga_name}", state="error")
            raise outcome["error"]

        duration_s = time.monotonic() - start_time
        status_box.update(
            label=f"Extraction complete for {lga_name} ({duration_s:.0f}s)", state="complete"
        )

    return outcome["result"]


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
    else:
        clean_lga_name = lga_name.strip()
        clean_state_name = state_name.strip() or None
        cache = _extraction_cache()
        cache_key = (clean_lga_name.lower(), (clean_state_name or "").lower())

        try:
            if cache_key in cache:
                # Already extracted this session, nothing to show live
                # progress for, this matches the old cached-instant-return
                # behavior exactly.
                result = cache[cache_key]
            else:
                result = _run_extraction_with_live_progress(clean_lga_name, clean_state_name)
                cache[cache_key] = result
            # Persist to session_state, NOT just a local variable, this is
            # what makes the results section below survive later reruns
            # (e.g. the one Streamlit triggers when the Download button
            # is clicked), see _render_results()'s docstring for why this
            # matters, `submitted` itself is only True for the single
            # script run the form's own submit click causes.
            st.session_state["last_result"] = result
            st.session_state["last_lga_name"] = lga_name
        except BoundaryResolutionError as exc:
            st.error(f"Could not resolve LGA boundary: {exc}")
        except Exception as exc:
            st.error(f"Extraction failed: {exc}")


@st.cache_data(show_spinner=False)
def _build_preview_map_html(output_dir: str, selected_layers: tuple) -> tuple:
    """
    Build the leafmap preview map for a given output directory and
    selected-layer set, and return its HTML as a plain string (via
    m.to_html(), rendered by the caller through
    st.components.v1.html, rather than leafmap's own m.to_streamlit()
    which builds the map fresh every call), so the EXPENSIVE part,
    embedding each selected layer's GeoJSON and letting Leaflet
    construct the map, only happens once per distinct
    (output_dir, selected_layers) combination for the lifetime of this
    process, not on every single script rerun. Cached via
    st.cache_data, keyed on its own arguments, exactly the two things
    that actually determine what the map should look like.

    Combined with @st.fragment on the caller (_render_results()), this
    is what makes clicking Download NOT feel like the map is
    reloading, reluctantly or otherwise, the fragment avoids re-running
    the rest of the script, and this cache avoids rebuilding the map
    itself even on the reruns that DO still happen (e.g. changing the
    layer multiselect).

    Parameters
    ----------
    output_dir : str
        This LGA's extraction output directory.
    selected_layers : tuple of str
        Which layer names (matching keys in exported layer info) to
        render, normally ALL exported layers (the caller passes every
        layer name from result["exported"]), a tuple (not a list/set)
        specifically because st.cache_data needs a hashable argument
        to use as part of its cache key.

    Returns
    -------
    (html, skipped_layers) : (str or None, list of str)
        `html` is None only if every layer's file was missing/unreadable
        (an edge case, e.g. every layer for this LGA came back empty).
        `skipped_layers` lists any layer whose file existed but
        couldn't be read (malformed/interrupted write), so a genuinely
        broken file never crashes the whole preview or silently drops
        every layer after it.
    """
    m = leafmap.Map()

    # Add roads first with zoom_to_layer=True so the map frames the
    # whole LGA well (roads typically span the full boundary); every
    # other layer is added with zoom_to_layer=False so the view
    # doesn't keep jumping to whichever layer happens to be added last.
    layer_names_ordered = sorted(selected_layers, key=lambda name: 0 if name == "roads" else 1)

    # Track whether ANY layer has been zoomed to yet, rather than tying
    # zoom_to_layer to a fixed list index. If the first layer in sorted
    # order (roads) happens to have no file on disk (a common, valid
    # case: an empty/skipped layer for a smaller or less-mapped LGA),
    # indexing by position alone meant NO layer ever got
    # zoom_to_layer=True, since the loop moved on to the next index
    # without the map ever having zoomed to anything, this left the
    # map at Leaflet's global default view even though other layers
    # were still added successfully, just invisible at that zoom
    # level. Tracking "has anything been zoomed to yet" instead
    # guarantees whichever layer is genuinely added FIRST gets the
    # zoom, regardless of which layers earlier in the list were empty.
    zoomed_yet = False
    skipped_layers = []
    any_layer_added = False

    for layer_name in layer_names_ordered:
        geojson_path = os.path.join(output_dir, f"{layer_name}.geojson")
        if not os.path.exists(geojson_path):
            continue
        style = LAYER_STYLES.get(layer_name, {})
        try:
            m.add_geojson(
                geojson_path,
                layer_name=layer_name.replace("_", " ").title(),
                style=style,
                zoom_to_layer=(not zoomed_yet),
                info_mode="on_click",
            )
            zoomed_yet = True
            any_layer_added = True
        except (IndexError, KeyError, ValueError):
            # export_layers() already filters out genuinely empty
            # layers before writing any file (they go into
            # exported["_skipped"] instead), so this normally
            # shouldn't trigger. It's here as a backstop for a file
            # that exists on disk but is malformed or unexpectedly
            # empty, e.g. a partial/interrupted write during a
            # long-running extraction (a real risk here, since a
            # single extraction can take several minutes), rather than
            # one bad file crashing the whole preview map and silently
            # dropping every layer after it in the loop.
            skipped_layers.append(layer_name)

    if not any_layer_added:
        return None, skipped_layers

    m.add_layer_control()
    return m.to_html(), skipped_layers


@st.fragment
def _render_results(result: dict, lga_name: str):
    """
    Renders the extraction summary, preview map, and download button
    for an already-extracted result.

    Deliberately a SEPARATE, cached-map, session-state-driven,
    @st.fragment-wrapped function, rather than inline code under
    `if submitted:`, for two independent reasons:

    1. `if submitted:` is only True for the ONE script run the form's
       submit click itself causes. Streamlit reruns the ENTIRE script
       top-to-bottom on every later interaction too, including clicking
       the Download button below, on which a plain `if submitted:`
       block would evaluate False and vanish entirely, along with the
       map and download button the person was just looking at. Calling
       this function unconditionally, driven by st.session_state
       (which DOES persist across reruns) rather than by `submitted`,
       is what makes the results section survive.
    2. @st.fragment scopes reruns triggered by a widget INSIDE this
       function (the download button) to just this function, not the
       whole app script. Combined with `_build_preview_map_html()`
       below being `st.cache_data`-cached, clicking Download does NOT
       re-run boundary resolution, does NOT re-touch the extraction
       cache, and does NOT rebuild/re-embed the map's GeoJSON from
       scratch, it reuses the cached HTML, which is the fix for the
       map going slow/reluctant specifically around download clicks,
       as opposed to genuinely rebuilding it on every single one.
    """
    st.success(f"Extraction complete for {lga_name}.")

    with st.expander("Extraction summary", expanded=False):
        summary_rows = []
        for layer_name, entry in result["exported"].items():
            if layer_name.startswith("_"):
                continue
            summary_rows.append(
                {"Layer": layer_name.replace("_", " ").title(),
                 "Features": entry.get("feature_count", "?")}
            )
        if summary_rows:
            st.table(summary_rows)
        st.caption(
            f"CRS: {result['target_crs']}  •  Boundary: {result['boundary_source']}  •  "
            f"Warnings: {len(result['warnings'])}"
        )

    if result["warnings"]:
        with st.expander("Warnings"):
            for w in result["warnings"]:
                st.write(f"- {w}")

    st.subheader("Preview map")
    st.caption(
        "Every extracted layer is shown together below. Use the layer control "
        "(top-right of the map) to toggle individual layers on or off, useful "
        "since dense layers like buildings can visually overwhelm the map when "
        "everything is shown at once."
    )

    all_layer_names = tuple(sorted(
        name for name in result["exported"].keys() if not name.startswith("_")
    ))
    map_html, skipped_layers = _build_preview_map_html(result["output_dir"], all_layer_names)

    if skipped_layers:
        st.warning(
            f"Could not preview: {', '.join(skipped_layers)} (file exists but "
            f"couldn't be read, possibly an interrupted write). Other layers "
            f"and the download below are unaffected."
        )

    if map_html:
        st.components.v1.html(map_html, height=600)

    st.subheader("Download results")
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as zf:
        for root, _, files in os.walk(result["output_dir"]):
            for file in files:
                filepath = os.path.join(root, file)
                arcname = os.path.relpath(filepath, result["output_dir"])
                zf.write(filepath, arcname)
    zip_buffer.seek(0)

    st.download_button(
        label=f"Download {lga_name} OSM data (.zip)",
        data=zip_buffer,
        file_name=f"{lga_name.replace(' ', '_').lower()}_osm_data.zip",
        mime="application/zip",
        type="primary",
    )


if st.session_state.get("last_result"):
    _render_results(st.session_state["last_result"], st.session_state["last_lga_name"])

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
