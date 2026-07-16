#!/usr/bin/env python3
"""
APEX Result Parser

Parses APEX output directories and extracts structured results.

Usage:
    python parse_results.py --work-dir ./results
    python parse_results.py --work-dir ./results --property elastic --format csv
"""

import argparse
import json
import os
import sys
from pathlib import Path


def find_result_files(work_dir: Path) -> list:
    """Find all result.json and result files in APEX output."""
    results = []
    for root, dirs, files in os.walk(work_dir):
        for f in files:
            if f in ("result.json", "all_result.json", "result"):
                results.append(Path(root) / f)
    return sorted(results)


def parse_elastic_result(result_data: dict) -> dict:
    """Parse elastic property results."""
    parsed = {}
    if "elastic_tensor" in result_data:
        parsed["elastic_tensor"] = result_data["elastic_tensor"]
    
    # Extract from common APEX output format
    for key in ("BulkModulus_Voigt", "ShearModulus_Voigt",
                "YoungModulus_Voigt", "PoissonRatio_Voigt",
                "BulkModulus_Reuss", "ShearModulus_Reuss",
                "BulkModulus_VRH", "ShearModulus_VRH"):
        if key in result_data:
            parsed[key] = result_data[key]

    # Look for Cij values
    for i in range(1, 7):
        for j in range(i, 7):
            key = f"C{i}{j}"
            if key in result_data:
                parsed[key] = result_data[key]

    return parsed


def parse_eos_result(result_data: dict) -> dict:
    """Parse EOS results."""
    parsed = {}
    for key in ("equilibrium_volume", "bulk_modulus", "bulk_modulus_derivative",
                "equilibrium_energy", "volumes", "energies"):
        if key in result_data:
            parsed[key] = result_data[key]
    return parsed


def parse_surface_result(result_data: dict) -> dict:
    """Parse surface energy results."""
    parsed = {}
    if isinstance(result_data, dict):
        for key, value in result_data.items():
            if "surface_energy" in str(key).lower() or "miller" in str(key).lower():
                parsed[key] = value
    return parsed


def parse_vacancy_result(result_data: dict) -> dict:
    """Parse vacancy formation energy results."""
    parsed = {}
    if isinstance(result_data, dict):
        for key, value in result_data.items():
            if "formation" in str(key).lower() or "vacancy" in str(key).lower():
                parsed[key] = value
    return parsed


def parse_phonon_result(result_data: dict) -> dict:
    """Parse phonon results."""
    parsed = {}
    for key in ("band_structure", "dos", "force_constants_file",
                "thermal_properties"):
        if key in result_data:
            parsed[key] = result_data[key]
    return parsed


def parse_general_result(result_data: dict) -> dict:
    """Generic result parser for unrecognized property types."""
    return result_data


PROPERTY_PARSERS = {
    "elastic": parse_elastic_result,
    "eos": parse_eos_result,
    "surface": parse_surface_result,
    "vacancy": parse_vacancy_result,
    "interstitial": parse_vacancy_result,
    "phonon": parse_phonon_result,
}


def detect_property_type(path: Path) -> str:
    """Detect property type from directory path."""
    path_str = str(path).lower()
    for prop_type in PROPERTY_PARSERS:
        if prop_type in path_str:
            return prop_type
    return "unknown"


def parse_result_file(filepath: Path) -> dict:
    """Parse a single result file."""
    try:
        with open(filepath) as f:
            data = json.load(f)
        return data
    except (json.JSONDecodeError, IOError) as e:
        print(f"Warning: Could not parse {filepath}: {e}", file=sys.stderr)
        return None


def format_output(results: list, fmt: str = "json") -> str:
    """Format parsed results for output."""
    if fmt == "json":
        return json.dumps(results, indent=2, default=str)
    elif fmt == "csv":
        if not results:
            return ""
        # Simple CSV: flatten first level
        lines = []
        headers = set()
        for r in results:
            if isinstance(r.get("data"), dict):
                headers.update(r["data"].keys())
        headers = sorted(headers)
        lines.append(",".join(["source", "property"] + list(headers)))
        for r in results:
            row = [str(r.get("source", "")), str(r.get("property", ""))]
            data = r.get("data", {})
            for h in headers:
                val = data.get(h, "")
                if isinstance(val, (list, dict)):
                    val = json.dumps(val)
                row.append(str(val))
            lines.append(",".join(row))
        return "\n".join(lines)
    elif fmt == "summary":
        lines = []
        for r in results:
            lines.append(f"\n{'='*60}")
            lines.append(f"Source: {r.get('source', 'unknown')}")
            lines.append(f"Property: {r.get('property', 'unknown')}")
            lines.append(f"{'='*60}")
            data = r.get("data", {})
            for k, v in data.items():
                if isinstance(v, float):
                    lines.append(f"  {k}: {v:.6f}")
                elif isinstance(v, list) and len(v) <= 10:
                    lines.append(f"  {k}: {v}")
                elif isinstance(v, list):
                    lines.append(f"  {k}: [{v[0]:.4f}, ..., {v[-1]:.4f}] ({len(v)} points)")
                else:
                    lines.append(f"  {k}: {v}")
        return "\n".join(lines)
    else:
        return json.dumps(results, indent=2, default=str)


def main():
    parser = argparse.ArgumentParser(
        description="Parse APEX calculation results"
    )
    parser.add_argument("--work-dir", "-w", required=True,
                        help="APEX work/output directory")
    parser.add_argument("--property", "-p",
                        help="Filter by property type")
    parser.add_argument("--format", "-f", default="summary",
                        choices=["json", "csv", "summary"],
                        help="Output format (default: summary)")
    parser.add_argument("--output", "-o",
                        help="Output file (default: stdout)")

    args = parser.parse_args()
    work_dir = Path(args.work_dir)

    if not work_dir.exists():
        print(f"ERROR: Directory not found: {work_dir}", file=sys.stderr)
        sys.exit(1)

    # Find result files
    result_files = find_result_files(work_dir)
    if not result_files:
        print(f"No result files found in {work_dir}", file=sys.stderr)
        # Try looking for specific output patterns
        for pattern in ("*.csv", "*.json"):
            found = list(work_dir.rglob(pattern))
            if found:
                print(f"Found {len(found)} {pattern} files", file=sys.stderr)
        sys.exit(1)

    # Parse all results
    all_results = []
    for rf in result_files:
        prop_type = detect_property_type(rf)
        if args.property and prop_type != args.property:
            continue

        data = parse_result_file(rf)
        if data is None:
            continue

        parser_func = PROPERTY_PARSERS.get(prop_type, parse_general_result)
        parsed = parser_func(data)

        all_results.append({
            "source": str(rf.relative_to(work_dir)),
            "property": prop_type,
            "data": parsed,
        })

    # Format and output
    output = format_output(all_results, args.format)

    if args.output:
        with open(args.output, "w") as f:
            f.write(output)
        print(f"Results written to {args.output}")
    else:
        print(output)


if __name__ == "__main__":
    main()
