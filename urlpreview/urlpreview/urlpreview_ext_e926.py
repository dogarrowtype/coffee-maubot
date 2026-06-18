import asyncio
import html
import re
import time
from urllib.parse import urlparse

# e926's API allows max ~1 request/sec (over 2 RPS is hard-blocked), so space
# requests out a little past 1s. State lives on the plugin instance.
E926_MIN_INTERVAL = 1.1

# Map e926 file extensions to image MIME types so process_image can skip
# the extra content-type probe request. Unknown extensions fall back to None.
E926_IMAGE_MIME = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
}

# Map video file extensions to MIME types for the video upload pipeline.
E926_VIDEO_MIME = {
    "mp4": "video/mp4",
    "webm": "video/webm",
    "mov": "video/quicktime",
    "mkv": "video/x-matroska",
}


def _video_mime(url):
    """Guess a video MIME type from a URL's file extension (defaults to mp4)."""
    ext = url.rsplit(".", 1)[-1].split("?")[0].lower() if url and "." in url else ""
    return E926_VIDEO_MIME.get(ext, "video/mp4")


def e926_match_host(url_str, hosts):
    """Return True if url_str's hostname is one of the configured e926 hosts."""
    if not url_str or not hosts:
        return False
    host = (urlparse(url_str).hostname or "").lower()
    return host in [str(h).lower() for h in hosts]


def e926_to_api_url(url_str, api_v2=False):
    """Rewrite a /posts/<id> URL to its .json API equivalent.

    v1 (default): insert .json before the path, preserving any query string.
      https://e926.net/posts/6480895?q=white_fur -> https://e926.net/posts/6480895.json?q=white_fur
    v2: same .json path, but replace the query with v2=true (the original query
        is not needed and is dropped).
      https://e926.net/posts/6480895?q=white_fur -> https://e926.net/posts/6480895.json?v2=true

    Returns None if the URL is not a post URL or is already a .json request.
    """
    parsed = urlparse(url_str)
    match = re.match(r"^(/posts/\d+)/?$", parsed.path)
    if not match:
        return None
    new = parsed._replace(path=match.group(1) + ".json")
    if api_v2:
        new = new._replace(query="v2=true")
    return new.geturl()


def _join_body(parts):
    """Join non-empty inline body fragments with line breaks, or None if empty."""
    parts = [p for p in parts if p]
    return "<br />".join(parts) if parts else None


def _tags_details(tags):
    """Render a flat tag list as a collapsible <details> dropdown (Matrix HTML)."""
    if not tags:
        return None
    items = ", ".join(html.escape(str(t)) for t in tags)
    return f"<details><summary>Tags ({len(tags)})</summary><p>{items}</p></details>"


def _extract_v1(post):
    """Parse a v1 API post object into an og dict (artist + description body)."""
    if not post:
        return None

    sample = post.get("sample") or {}
    if sample.get("has"):
        image = sample.get("alt")  # the webp sample (sample.url is the jpg)
        content_type = "image/webp"
    else:
        file_obj = post.get("file") or {}
        image = file_obj.get("url")
        content_type = E926_IMAGE_MIME.get(str(file_obj.get("ext", "")).lower())
    if not image:
        return None

    artists = (post.get("tags") or {}).get("artist") or []
    description = post.get("description") or ""
    body = _join_body([
        f"<b>Artist:</b> {', '.join(html.escape(str(a)) for a in artists)}" if artists else None,
        html.escape(str(description)) if description else None,
    ])

    return {
        "title": None,
        "description": body,
        "extra_html": None,
        "image": image,
        "image_mxc": None,
        "content_type": content_type,
        "image_width": None,
    }


def _extract_v2(post):
    """Parse a v2 API post object into an og dict.

    v2 tags are a flat, uncategorized list, so the artist can't be isolated;
    the full tag list is dumped into a collapsible <details> dropdown instead.
    """
    if not post:
        return None

    files = post.get("files") or {}
    description = post.get("description") or ""
    tags = post.get("tags") or []
    og = {
        "title": None,
        "description": html.escape(str(description)) if description else None,
        "extra_html": _tags_details(tags),
        "image": None,
        "image_mxc": None,
        "content_type": None,
        "image_width": None,
    }

    # Video posts: prefer the 720p sample, then 480p, then the original file.
    video = files.get("video") or {}
    if video.get("has"):
        samples = video.get("samples") or {}
        chosen = samples.get("720p") or samples.get("480p") or video.get("original") or {}
        url = chosen.get("url")
        if not url:
            return None
        og["video"] = url
        og["video_type"] = _video_mime(url)
        og["video_width"] = chosen.get("width")
        og["video_height"] = chosen.get("height")
        return og

    # Image posts: webp sample if present, otherwise the full-size file.
    meta = files.get("meta") or {}
    has = post.get("has") or {}
    has_sample = meta.get("has_sample") or has.get("sample")
    if has_sample:
        og["image"] = (files.get("sample") or {}).get("webp")
        og["content_type"] = "image/webp"
    else:
        og["image"] = (files.get("original") or {}).get("url")
        og["content_type"] = E926_IMAGE_MIME.get(str(meta.get("ext", "")).lower())
    if not og["image"]:
        return None
    return og


def _extract_post(data):
    """Detect the API response shape (v1 wrapped vs v2 flat) and parse it."""
    if not isinstance(data, dict):
        return None
    if "post" in data:  # v1: {"post": {...}}
        return _extract_v1(data.get("post"))
    if data.get("id") and "files" in data:  # v2: flat post object
        return _extract_v2(data)
    return None


async def _e926_throttle(self):
    """Serialize e926 requests and keep them ~1 RPS to avoid being blocked.

    Holds a per-instance lock while spacing successive requests at least
    E926_MIN_INTERVAL seconds apart. Lazily created — no setup in start().
    """
    lock = getattr(self, "_e926_lock", None)
    if lock is None:
        lock = self._e926_lock = asyncio.Lock()
    async with lock:
        wait = E926_MIN_INTERVAL - (time.monotonic() - getattr(self, "_e926_last_request", 0.0))
        if wait > 0:
            await asyncio.sleep(wait)
        self._e926_last_request = time.monotonic()


async def fetch_e926(self, url_str, config, html_custom_headers=None):
    """Custom handler for e926-style post URLs.

    Rewrites the human-facing post URL to the .json API, fetches it, and returns
    an og dict with the sample (webp) image — or the full-size file when no
    sample exists — plus the message body (artist + description on v1, or
    description + a tags dropdown on v2).

    Returns None when the URL is not a handled e926 post URL (so the caller can
    fall through to the generic parsers).
    """
    if not config or not config.get("enabled"):
        return None
    if not e926_match_host(url_str, config.get("hosts") or []):
        return None

    api_url = e926_to_api_url(url_str, api_v2=bool(config.get("api_v2")))
    if not api_url:
        return None

    json_headers = {**(html_custom_headers or {}), "Accept": "application/json"}
    await _e926_throttle(self)
    try:
        resp = await self.http.get(api_url, headers=json_headers, timeout=30)
    except Exception as err:
        self.log.exception(f"[urlpreview] [ext_e926] Error: {str(err)} - {str(urlparse(api_url).netloc)}")
        return None

    if resp.status != 200:
        self.log.debug(f"[urlpreview] [ext_e926] Non-200 status {resp.status} - {urlparse(api_url).netloc}")
        return None

    try:
        data = await resp.json(content_type=None)
    except Exception as err:
        self.log.debug(f"[urlpreview] [ext_e926] Response is not json: {str(err)}")
        return None

    og = _extract_post(data)
    if not og:
        self.log.debug(f"[urlpreview] [ext_e926] No usable post/image in response - {urlparse(api_url).netloc}")
        return None
    return og
