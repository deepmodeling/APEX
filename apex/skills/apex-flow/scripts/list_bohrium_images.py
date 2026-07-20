#!/usr/bin/env python3
"""
List the user's private Bohrium Docker images (keyword-filtered).

Mirrors MatMaster Bohrium tool action ``list_images``:
  GET /openapi/v2/image/private  (header: accessKey)

Usage:
    python list_bohrium_images.py --keyword vasp
    python list_bohrium_images.py --keyword vasp --max-results 20 --json
    python list_bohrium_images.py --keyword vasp --require   # exit 1 if none

Environment:
    BOHRIUM_ACCESS_KEY   required (or pass --access-key)
    BOHRIUM_BASE_URL     optional, default https://openapi.dp.tech
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

DEFAULT_BASE_URL = "https://openapi.dp.tech"
PRIVATE_IMAGE_PATH = "/openapi/v2/image/private"


def _get_json(
    base_url: str,
    path: str,
    access_key: str,
    params: dict[str, Any] | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    query = f"?{urlencode(params)}" if params else ""
    url = f"{base_url.rstrip('/')}{path}{query}"
    req = Request(url, method="GET")
    req.add_header("accessKey", access_key)
    req.add_header("Accept", "application/json")
    try:
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except HTTPError as exc:
        body = exc.read().decode(errors="replace") if exc.fp else ""
        raise RuntimeError(
            f"Bohrium API HTTP {exc.code} for {path}: {body[:300]}"
        ) from exc
    except URLError as exc:
        raise RuntimeError(f"Bohrium API request failed for {path}: {exc}") from exc


def fetch_private_images(
    access_key: str,
    *,
    base_url: str = DEFAULT_BASE_URL,
    page_size: int = 200,
    max_pages: int = 50,
) -> list[dict[str, Any]]:
    """Paginate ``/openapi/v2/image/private`` and return raw records."""
    private_images: list[dict[str, Any]] = []
    page = 1
    while page <= max_pages:
        payload = _get_json(
            base_url,
            PRIVATE_IMAGE_PATH,
            access_key,
            params={
                "page": page,
                "pageSize": page_size,
                "device": "container",
                "type": "image",
            },
        )
        data_block = payload.get("data") or {}
        batch = data_block.get("items") or []
        if not batch:
            break
        private_images.extend(batch)
        total = data_block.get("total")
        if isinstance(total, int) and len(private_images) >= total:
            break
        if len(batch) < page_size:
            break
        page += 1
    return private_images


def filter_images(
    records: list[dict[str, Any]],
    keyword: str,
    *,
    max_results: int = 20,
) -> dict[str, Any]:
    """Filter private image records by keyword; shape like MatMaster list_images."""
    lowered = (keyword or "").strip().lower()
    if lowered:
        filtered = [
            record
            for record in records
            if lowered
            in str(record.get("name") or record.get("imageName") or "").lower()
            or lowered in str(record.get("description") or "").lower()
            or lowered in str(record.get("url") or "").lower()
        ]
    else:
        filtered = list(records)

    results: list[dict[str, Any]] = []
    for record in filtered[:max_results]:
        img_id = record.get("id") or record.get("imageId")
        if img_id is None:
            continue
        name = record.get("name") or record.get("imageName") or ""
        description = record.get("description") or ""
        direct_url = record.get("url") or ""
        entry: dict[str, Any] = {}
        if direct_url:
            entry["url"] = direct_url
        ver_str = name.split(":")[-1] if ":" in name else ""
        if ver_str:
            entry["version"] = ver_str
        size = record.get("size") or ""
        if size:
            entry["size"] = size
        item: dict[str, Any] = {
            "id": img_id,
            "name": name,
            "versions": [entry] if entry else [],
            "private": True,
        }
        if description:
            item["description"] = description
        results.append(item)

    return {
        "success": True,
        "keyword": lowered,
        "total_found": len(filtered),
        "returned": len(results),
        "images": results,
        "source": "openapi_v2_image_private",
    }


def image_urls(payload: dict[str, Any]) -> list[str]:
    """Extract ready-to-use image URLs / names from a list_images payload."""
    urls: list[str] = []
    for image in payload.get("images") or []:
        versions = image.get("versions") or []
        found_url = False
        for ver in versions:
            url = (ver.get("url") or "").strip()
            if url:
                urls.append(url)
                found_url = True
        if not found_url:
            # Fallback when API returns registry path only in name.
            name = (image.get("name") or "").strip()
            if name:
                urls.append(name)
    # Deduplicate preserving order
    seen = set()
    unique: list[str] = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            unique.append(u)
    return unique


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "List the user's own private Bohrium Docker images "
            "(filtered by keyword). Same API as MatMaster "
            'Bohrium(action="list_images").'
        )
    )
    parser.add_argument(
        "--keyword", "-k", default="vasp",
        help="Filter keyword (default: vasp)",
    )
    parser.add_argument(
        "--max-results", type=int, default=20,
        help="Maximum images to return (default: 20)",
    )
    parser.add_argument(
        "--access-key",
        help="Bohrium access key (or set BOHRIUM_ACCESS_KEY)",
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("BOHRIUM_BASE_URL", DEFAULT_BASE_URL),
        help="Bohrium OpenAPI base URL",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Print full JSON payload (default)",
    )
    parser.add_argument(
        "--urls-only", action="store_true",
        help="Print one image URL/name per line",
    )
    parser.add_argument(
        "--require", action="store_true",
        help="Exit 1 when no images match (for VASP gate)",
    )
    args = parser.parse_args(argv)

    access_key = args.access_key or os.environ.get("BOHRIUM_ACCESS_KEY")
    if not access_key:
        print(
            "ERROR: BOHRIUM_ACCESS_KEY not set and --access-key not provided",
            file=sys.stderr,
        )
        return 2

    try:
        records = fetch_private_images(access_key, base_url=args.base_url)
        payload = filter_images(
            records, args.keyword, max_results=args.max_results
        )
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    urls = image_urls(payload)
    if args.require and not urls and not payload.get("images"):
        print(
            f"ERROR: no private Bohrium images matched keyword "
            f"{args.keyword!r}. User must provide an authorized VASP "
            "image address, or the VASP workflow must stop.",
            file=sys.stderr,
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 1

    if args.urls_only:
        for url in urls:
            print(url)
        if not urls:
            for image in payload.get("images") or []:
                name = (image.get("name") or "").strip()
                if name:
                    print(name)
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))

    return 0


if __name__ == "__main__":
    sys.exit(main())
