"""
cli.py

Command-line interface for the LGA OSM extractor.

Usage
-----
    python cli.py --lga "Akure North" --state "Ondo"
    python cli.py --lga "Akure South" --state "Ondo" --output-dir data/processed/akure_south
    python cli.py --lga "Some LGA" --manual-boundary path/to/boundary.geojson
    python cli.py --lga "Akure North" --state "Ondo" --strict  # abort on any genuine extraction failure
"""

import argparse
import json
import sys

from lga_extractor import extract_lga


def main():
    parser = argparse.ArgumentParser(
        description="Extract OSM data (roads, buildings, waterways, land use, "
        "health facilities, schools) for a Nigerian LGA."
    )
    parser.add_argument("--lga", required=True, help="LGA name, e.g. 'Akure North'")
    parser.add_argument("--state", default=None, help="State name, e.g. 'Ondo'")
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Output directory (default: output/<lga_name>)",
    )
    parser.add_argument(
        "--manual-boundary",
        default=None,
        help="Path to a manual boundary file (GeoJSON/Shapefile), used if OSM "
        "boundary resolution fails or is unavailable.",
    )
    parser.add_argument(
        "--preview",
        action="store_true",
        help="Also generate a standalone kepler.gl HTML preview map of the "
        "extracted layers (requires 'pip install keplergl'). Saved to "
        "visuals/<lga_name>_preview.html.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Raise immediately on a genuine layer-extraction failure "
        "(Overpass timeout, network error, bad tag config) instead of the "
        "default permissive behavior of logging a warning and continuing "
        "with an empty layer. A layer that simply finds zero features "
        "never raises, even with this flag set, see extract_layers()'s "
        "docstring for the distinction. Recommended for CI/automated use.",
    )

    args = parser.parse_args()

    try:
        result = extract_lga(
            lga_name=args.lga,
            state_name=args.state,
            output_dir=args.output_dir,
            manual_boundary_path=args.manual_boundary,
            strict=args.strict,
        )
    except Exception as exc:
        print(f"Extraction failed: {exc}", file=sys.stderr)
        sys.exit(1)

    print(json.dumps(result, indent=2))

    if args.preview:
        try:
            from lga_extractor.visualize import build_preview_map

            safe_name = args.lga.strip().replace(" ", "_").lower()
            html_path = f"visuals/{safe_name}_preview.html"
            build_preview_map(output_dir=result["output_dir"], html_out=html_path)
            print(f"Preview map saved to {html_path}")
        except ImportError:
            print(
                "Preview skipped: keplergl is not installed. "
                "Run 'pip install keplergl' to enable --preview.",
                file=sys.stderr,
            )


if __name__ == "__main__":
    main()
