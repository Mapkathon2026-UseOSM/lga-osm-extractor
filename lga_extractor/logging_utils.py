"""
logging_utils.py

Writes a run log recording exactly what was queried, when, and with
what tag configuration, so any extraction can be traced and
reproduced later.
"""

import json
import os
import platform
import sys
from datetime import datetime, timezone
from importlib.metadata import version, PackageNotFoundError

# Packages whose versions matter for reproducing an extraction. Captured
# per-run so that a discrepancy between two runs (e.g. different OSMnx
# versions returning different Overpass query results) is visible and
# traceable, rather than a silent, unexplained difference in output.
_TRACKED_PACKAGES = ["osmnx", "geopandas", "shapely", "fiona", "pandas"]


def _capture_environment() -> dict:
    """Capture package versions and basic runtime info for the run log."""
    package_versions = {}
    for pkg in _TRACKED_PACKAGES:
        try:
            package_versions[pkg] = version(pkg)
        except PackageNotFoundError:
            package_versions[pkg] = "not installed"

    return {
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
        "package_versions": package_versions,
    }


def log_run(
    lga_name: str,
    state_name: str,
    tag_config: dict,
    output_dir: str,
    boundary_source: str,
    warnings: list = None,
    exported: dict = None,
    target_crs: str = None,
) -> str:
    """
    Write a JSON run log for a single LGA extraction.

    Parameters
    ----------
    lga_name : str
    state_name : str
    tag_config : dict
        The tag configuration actually used for this run.
    output_dir : str
        Directory the outputs were written to; the log is saved here
        as 'run_log.json'.
    boundary_source : str
        Description of how the boundary was resolved (OSM query string
        or manual file path), as set by boundary.resolve_boundary().
    warnings : list, optional
        Any warnings raised during extraction (e.g. empty layers).
    exported : dict, optional
        The dict returned by export.export_layers(), recording which
        files were written and which layers were skipped. If any layer
        contained mixed geometry types (e.g. roads mixing point nodes
        and line ways) and was split into multiple category-specific
        Shapefiles, that is recorded under "split_layers" in the log.
    target_crs : str, optional
        The projected CRS actually used to clean/export this LGA's
        layers, e.g. "EPSG:32631", see clean.resolve_target_crs(),
        which auto-selects the correct UTM zone based on the boundary's
        location rather than assuming a single fixed zone for all of
        Nigeria. Recorded here so the exact projection used for any
        given run is traceable later, not just assumed.

    Returns
    -------
    str
        Path to the written run_log.json file.
    """
    log = {
        "lga_name": lga_name,
        "state_name": state_name,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "environment": _capture_environment(),
        "boundary_source": boundary_source,
        "target_crs": target_crs,
        "tag_config": tag_config,
        "warnings": warnings or [],
        "exported_layers": {
            k: v for k, v in (exported or {}).items() if not k.startswith("_")
        },
        "skipped_layers": (exported or {}).get("_skipped", []),
        "split_layers": (exported or {}).get("_split_layers", {}),
    }

    os.makedirs(output_dir, exist_ok=True)
    log_path = os.path.join(output_dir, "run_log.json")

    with open(log_path, "w") as f:
        json.dump(log, f, indent=2)

    return log_path
