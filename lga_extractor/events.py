"""
events.py

Defines the event schema the extraction pipeline emits as it runs, and
a couple of tiny helper utilities for consuming those events safely
from a UI thread.

Why events, not a Streamlit progress bar call scattered through the
pipeline: the pipeline (boundary.py, layers.py, pipeline.py) has no
business knowing Streamlit exists — it should be just as usable from a
plain script, a notebook, or a different UI entirely. So instead of
calling st.progress(...) directly from deep inside layers.py, the
pipeline takes an optional `on_event` callback and calls it with a
plain dict at each meaningful point (a stage starting, completing,
retrying, warning, or failing). A UI (see app.py) supplies a callback
that does something with those events; a script that doesn't care can
simply not pass one, on_event defaults to None everywhere and is a
guaranteed no-op when omitted, this is unrelated to whether Streamlit
is installed at all.

Event shape
-----------
Every event is a dict with at least a "type" and "stage" key:

    {"type": "stage_started",   "stage": "boundary"}
    {"type": "stage_completed", "stage": "boundary", "detail": "..."}
    {"type": "stage_started",   "stage": "layer:roads"}
    {"type": "retry",           "stage": "layer:roads", "attempt": 2, "message": "..."}
    {"type": "stage_completed", "stage": "layer:roads", "detail": "2,431 features", "status": "success"}
    {"type": "warning",         "stage": "layer:schools", "message": "..."}
    {"type": "stage_failed",    "stage": "layer:health_facilities", "message": "..."}
    {"type": "stage_started",   "stage": "cleaning"}
    {"type": "stage_completed", "stage": "cleaning"}
    {"type": "stage_started",   "stage": "export"}
    {"type": "stage_completed", "stage": "export"}
    {"type": "pipeline_completed", "summary": {...}}   # the same dict extract_lga() returns

Stage names for layers are "layer:{layer_name}" (e.g. "layer:roads"),
namespaced so a UI can group them separately from the top-level
"boundary" / "cleaning" / "export" stages without guessing.

Thread safety
-------------
Layer extraction runs multiple layers concurrently in a
ThreadPoolExecutor (see layers.extract_layers()), so `on_event` WILL be
called from multiple worker threads at once for "layer:*" stages, not
just the main thread. A callback that only appends to a
queue.Queue (thread-safe by design) or increments a
threading.Lock-protected counter is safe; a callback that touches
Streamlit UI elements directly is NOT safe to pass straight into
extract_lga(), Streamlit's own APIs are not thread-safe. See
`ThreadSafeEventQueue` below, and app.py's `_run_extraction_in_background()`
for the pattern of running extraction in a background thread and
draining a queue on Streamlit's main thread instead.
"""

import queue


# Canonical ordering of pipeline stages, layer stages are inserted
# between "boundary" and "cleaning" dynamically based on whatever
# tag_config the caller is actually using, since it can be extended or
# overridden by them.
STATIC_STAGES_BEFORE_LAYERS = ["boundary"]
STATIC_STAGES_AFTER_LAYERS = ["cleaning", "export"]


def build_stage_order(tag_config: dict) -> list:
    """
    Full ordered list of stage names for a given tag_config, for a UI
    to pre-render a fixed checklist of stages before extraction starts
    (see the progress-interface mockup this item is based on), rather
    than only discovering stages as events for them happen to arrive.
    """
    layer_stages = [f"layer:{name}" for name in tag_config.keys()]
    return STATIC_STAGES_BEFORE_LAYERS + layer_stages + STATIC_STAGES_AFTER_LAYERS


class ThreadSafeEventQueue:
    """
    Minimal thread-safe sink for pipeline events: a thin wrapper around
    queue.Queue exposing exactly the `on_event(event)` signature
    extract_lga()/extract_layers() expect, so a UI can do:

        events = ThreadSafeEventQueue()
        thread = threading.Thread(target=extract_lga, kwargs={..., "on_event": events})
        thread.start()
        while thread.is_alive() or not events.empty():
            for event in events.drain():
                ...update UI state from event, on the main thread...

    Safe to call `on_event()` (i.e. call the instance itself) from any
    thread; `drain()` should only be called from the thread that owns
    the UI.
    """

    def __init__(self):
        self._queue = queue.Queue()

    def __call__(self, event: dict):
        self._queue.put(event)

    def empty(self) -> bool:
        return self._queue.empty()

    def drain(self) -> list:
        """Pop and return every event currently queued, without blocking."""
        events = []
        while True:
            try:
                events.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return events


def _emit(on_event, event: dict):
    """
    Call `on_event(event)` if it's not None, swallowing any exception
    the callback itself raises. A UI-side bug in event handling must
    never be allowed to abort a live extraction, that would be a
    strictly worse regression than not having a progress UI at all.
    """
    if on_event is None:
        return
    try:
        on_event(event)
    except Exception:
        pass
