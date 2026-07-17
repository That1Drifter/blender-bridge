# Blender Bridge — Poly Haven Integration
#
# CC0 assets from https://polyhaven.com — HDRIs, textures, and 3D models.
# Uses urllib.request (available in Blender Python, unlike requests).

import json
import os
import tempfile
import threading
import urllib.request
import urllib.parse

from .. import constants

API_BASE = "https://api.polyhaven.com"
HEADERS = {"User-Agent": "blender-mcp/2.0"}

_CACHE_LOCK = threading.RLock()
_CACHE_FILE_LOCKS = {}


class DownloadCancelled(Exception):
    """Raised when a cooperative Poly Haven transfer is cancelled."""


def get_cache_dir():
    """Return the configured Poly Haven cache directory without creating it."""
    return (
        os.environ.get("BLENDER_BRIDGE_PH_CACHE")
        or constants.POLYHAVEN_CACHE_DIR
        or os.path.join(tempfile.gettempdir(), "blender_bridge_polyhaven_cache")
    )


def _safe_cache_parts(value, label):
    """Encode a cache-key component while preserving harmless path nesting."""
    if not isinstance(value, str) or not value:
        raise ValueError(f"Poly Haven cache {label} must be a non-empty string")
    normalized = value.replace("\\", "/")
    if normalized.startswith("/") or any(part in ("", ".", "..") for part in normalized.split("/")):
        raise ValueError(f"Unsafe Poly Haven cache {label}: {value!r}")
    return [urllib.parse.quote(part, safe="-_.") for part in normalized.split("/")]


def cache_file_path(asset_id, resolution, filename, cache_dir=None):
    """Return the completed-file path for an asset/resolution/filename key."""
    root = cache_dir or get_cache_dir()
    return os.path.join(
        root,
        *_safe_cache_parts(asset_id, "asset id"),
        *_safe_cache_parts(resolution, "resolution"),
        *_safe_cache_parts(filename, "filename"),
    )


def cache_manifest_path(asset_id, resolution, asset_type, fmt, cache_dir=None):
    """Return the plan manifest path for one asset type/format cache variant."""
    root = cache_dir or get_cache_dir()
    asset_parts = _safe_cache_parts(asset_id, "asset id")
    resolution_parts = _safe_cache_parts(resolution, "resolution")
    type_part = _safe_cache_parts(asset_type, "asset type")
    format_part = _safe_cache_parts(fmt, "format")
    return os.path.join(
        root, *asset_parts, *resolution_parts,
        ".manifest_" + "_".join(type_part + format_part) + ".json",
    )


def load_cached_manifest(asset_id, resolution, asset_type, fmt):
    """Load a valid cached transfer plan, or return ``None`` if absent/corrupt."""
    try:
        with open(cache_manifest_path(asset_id, resolution, asset_type, fmt), "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def save_cached_manifest(asset_id, resolution, asset_type, fmt, plan):
    """Atomically save a resolved network plan so full cache hits avoid metadata I/O."""
    destination = cache_manifest_path(asset_id, resolution, asset_type, fmt)
    part_path = destination + ".part"
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    try:
        with open(part_path, "w", encoding="utf-8") as f:
            json.dump(plan, f, sort_keys=True)
        os.replace(part_path, destination)
    except Exception:
        try:
            os.unlink(part_path)
        except OSError:
            pass
        raise


def filename_from_url(url):
    """Extract a stable cache filename from a download URL."""
    filename = os.path.basename(urllib.parse.unquote(urllib.parse.urlparse(url).path))
    if not filename:
        raise ValueError(f"Poly Haven download URL has no filename: {url!r}")
    return filename


def _file_lock(path):
    with _CACHE_LOCK:
        return _CACHE_FILE_LOCKS.setdefault(path, threading.Lock())


def download_to_cache(asset_id, resolution, filename, url, *, cancel_requested=None,
                      progress_callback=None, timeout=120, chunk_size=65536,
                      cache_dir=None):
    """Download one file into the atomic Poly Haven cache.

    A completed cache file is returned immediately without opening the URL. New
    files are written as ``<path>.part`` and atomically promoted with
    :func:`os.replace`; interrupted and failed writes remove that partial file.
    ``progress_callback`` receives ``(bytes_written, content_length_or_none)``.
    """
    destination = cache_file_path(asset_id, resolution, filename, cache_dir=cache_dir)
    part_path = destination + ".part"

    with _file_lock(destination):
        if os.path.isfile(destination):
            return destination

        os.makedirs(os.path.dirname(destination), exist_ok=True)
        try:
            if cancel_requested and cancel_requested():
                raise DownloadCancelled("Poly Haven download cancelled")

            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw_length = resp.headers.get("Content-Length")
                try:
                    content_length = int(raw_length) if raw_length else None
                except ValueError:
                    content_length = None
                written = 0
                # Opening in wb intentionally overwrites stale/corrupt .part files.
                with open(part_path, "wb") as f:
                    while True:
                        if cancel_requested and cancel_requested():
                            raise DownloadCancelled("Poly Haven download cancelled")
                        chunk = resp.read(chunk_size)
                        if not chunk:
                            break
                        f.write(chunk)
                        written += len(chunk)
                        if progress_callback:
                            progress_callback(written, content_length)
                        if cancel_requested and cancel_requested():
                            raise DownloadCancelled("Poly Haven download cancelled")
            os.replace(part_path, destination)
            return destination
        except Exception:
            try:
                os.unlink(part_path)
            except OSError:
                pass
            raise


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


def download_file(url, suffix=".tmp", *, asset_id=None, resolution=None,
                  filename=None, cancel_requested=None, progress_callback=None):
    """Download a file, using the cache when its Poly Haven identity is known.

    The temporary-file fallback remains for compatibility with callers that do
    not have an asset/resolution cache key yet.
    """
    if asset_id is not None or resolution is not None or filename is not None:
        if asset_id is None or resolution is None:
            raise ValueError("asset_id and resolution are required for cached downloads")
        return download_to_cache(
            asset_id,
            resolution,
            filename or filename_from_url(url),
            url,
            cancel_requested=cancel_requested,
            progress_callback=progress_callback,
        )

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
