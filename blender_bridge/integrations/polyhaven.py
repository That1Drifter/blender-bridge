# Blender Bridge — Poly Haven Integration
#
# CC0 assets from https://polyhaven.com — HDRIs, textures, and 3D models.
# Uses urllib.request (available in Blender Python, unlike requests).

import json
import os
import tempfile
import urllib.request

API_BASE = "https://api.polyhaven.com"
HEADERS = {"User-Agent": "blender-mcp/2.0"}


def _api_get(path, params=None):
    """Make a GET request to the Poly Haven API."""
    url = f"{API_BASE}{path}"
    if params:
        query = "&".join(f"{k}={v}" for k, v in params.items() if v is not None)
        if query:
            url += f"?{query}"

    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def search_assets(asset_type=None, categories=None, limit=20):
    """Search Poly Haven assets.

    Args:
        asset_type: 'hdris', 'textures', 'models', or None for all.
        categories: Comma-separated category filter.
        limit: Max results to return.

    Returns:
        dict with 'assets' list and 'total_count'.
    """
    params = {}
    if asset_type and asset_type != "all":
        if asset_type not in ("hdris", "textures", "models"):
            raise ValueError(
                f"Invalid asset_type '{asset_type}'. Valid: hdris, textures, models"
            )
        params["type"] = asset_type
    if categories:
        params["categories"] = categories

    data = _api_get("/assets", params)

    # Trim to limit
    assets = []
    for i, (asset_id, info) in enumerate(data.items()):
        if i >= limit:
            break
        # type field is int: 0=hdris, 1=textures, 2=models
        type_map = {0: "hdris", 1: "textures", 2: "models"}
        raw_type = info.get("type", "unknown")
        type_str = type_map.get(raw_type, str(raw_type))

        # categories can be a list or a dict
        cats = info.get("categories", [])
        if isinstance(cats, dict):
            cats = list(cats.keys())
        elif not isinstance(cats, list):
            cats = []

        assets.append({
            "id": asset_id,
            "name": info.get("name", asset_id),
            "type": type_str,
            "categories": cats,
        })

    return {
        "assets": assets,
        "total_count": len(data),
        "returned_count": len(assets),
    }


def get_asset_info(asset_id):
    """Get file info for an asset (resolutions, formats, URLs)."""
    return _api_get(f"/files/{asset_id}")


def download_file(url, suffix=".tmp"):
    """Download a file to a temporary location. Returns the temp file path."""
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=120) as resp:
        fd, tmp_path = tempfile.mkstemp(suffix=suffix)
        try:
            with os.fdopen(fd, "wb") as f:
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
        except Exception:
            os.unlink(tmp_path)
            raise
    return tmp_path


def get_hdri_url(files_data, resolution="1k", fmt="hdr"):
    """Extract HDRI download URL from files data."""
    hdri = files_data.get("hdri", {})
    res_data = hdri.get(resolution, {})
    fmt_data = res_data.get(fmt, {})
    url = fmt_data.get("url")
    if not url:
        available_res = list(hdri.keys())
        raise ValueError(
            f"HDRI not available at {resolution}/{fmt}. "
            f"Available resolutions: {available_res}"
        )
    return url


def get_texture_urls(files_data, resolution="1k", fmt="jpg"):
    """Extract texture map download URLs from files data.

    Returns dict of {map_type: url} for available maps.
    """
    skip_keys = {"blend", "gltf", "fbx", "obj"}
    urls = {}
    for map_type, res_data in files_data.items():
        if map_type in skip_keys:
            continue
        if isinstance(res_data, dict) and resolution in res_data:
            fmt_data = res_data[resolution]
            if isinstance(fmt_data, dict) and fmt in fmt_data:
                url = fmt_data[fmt].get("url")
                if url:
                    urls[map_type] = url
    return urls


def get_model_files(files_data, resolution="1k", fmt="gltf"):
    """Extract model download URLs including all dependency files.

    Returns dict: {"main_url": str, "main_filename": str, "includes": {relative_path: url}}
    """
    fmt_data = files_data.get(fmt, {})
    res_data = fmt_data.get(resolution, {})

    # Models are nested: resolution -> format -> {url, include}
    entry = None
    if isinstance(res_data, dict):
        if "url" in res_data:
            entry = res_data
        else:
            # One more nesting level: res_data[fmt] = {url, include}
            entry = res_data.get(fmt, {})
            if not isinstance(entry, dict) or "url" not in entry:
                entry = None

    if not entry or "url" not in entry:
        available_fmts = [k for k in files_data if k not in
                          ("Diffuse", "nor_dx", "nor_gl", "Metal", "Rough", "arm")]
        available_res = list(fmt_data.keys()) if fmt_data else []
        raise ValueError(
            f"Model not available at {fmt}/{resolution}. "
            f"Available formats: {available_fmts}, resolutions: {available_res}"
        )

    main_url = entry["url"]
    main_filename = main_url.rsplit("/", 1)[-1]

    # Collect include files (textures, .bin, etc.)
    includes = {}
    for rel_path, inc_info in entry.get("include", {}).items():
        if isinstance(inc_info, dict) and "url" in inc_info:
            includes[rel_path] = inc_info["url"]
        elif isinstance(inc_info, str):
            includes[rel_path] = inc_info

    return {
        "main_url": main_url,
        "main_filename": main_filename,
        "includes": includes,
    }
