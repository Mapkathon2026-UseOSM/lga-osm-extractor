"""
lga_extractor

A reusable Python tool for extracting OpenStreetMap data
(roads, buildings, waterways, land use, health facilities, schools)
for any Nigerian Local Government Area (LGA).

Quick start
-----------
    from lga_extractor import extract_lga

    result = extract_lga(lga_name="Akure North", state_name="Ondo")
"""

from .pipeline import extract_lga
from .boundary import resolve_boundary, BoundaryResolutionError
from .layers import extract_layers, DEFAULT_TAG_CONFIG, LayerExtractionError
from .clean import clean_layers
from .export import export_layers
from .logging_utils import log_run
from .manifest import build_manifest, write_manifest, MANIFEST_SCHEMA_VERSION
from .events import ThreadSafeEventQueue, build_stage_order

__all__ = [
    "extract_lga",
    "resolve_boundary",
    "BoundaryResolutionError",
    "extract_layers",
    "DEFAULT_TAG_CONFIG",
    "LayerExtractionError",
    "clean_layers",
    "export_layers",
    "log_run",
    "build_manifest",
    "write_manifest",
    "MANIFEST_SCHEMA_VERSION",
    "ThreadSafeEventQueue",
    "build_stage_order",
]

# visualize.py depends on the optional 'keplergl' package. Import it
# lazily so the rest of the package works fine without keplergl installed.
try:
    from .visualize import build_preview_map  # noqa: F401
    __all__.append("build_preview_map")
except ImportError:
    pass

__version__ = "0.1.0"
