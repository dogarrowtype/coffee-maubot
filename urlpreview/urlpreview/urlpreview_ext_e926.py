import html
import re
from urllib.parse import urlparse

# Map e926 file extensions to image MIME types so process_image can skip
# the extra content-type probe request. Unknown extensions fall back to None.
E926_IMAGE_MIME = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
}


def e926_match_host(url_str, hosts):
    """Return True if url_str's hostname is one of the configured e926 hosts."""
    if not url_str or not hosts:
        return False
    host = (urlparse(url_str).hostname or "").lower()
    return host in [str(h).lower() for h in hosts]


def e926_to_api_url(url_str):
    """Rewrite a /posts/<id> URL to its .json API equivalent, preserving the query.

    https://e926.net/posts/6480895?q=white_fur -> https://e926.net/posts/6480895.json?q=white_fur
    Returns None if the URL is not a post URL or is already a .json request.
    """
    parsed = urlparse(url_str)
    match = re.match(r"^(/posts/\d+)/?$", parsed.path)
    if not match:
        return None
    return parsed._replace(path=match.group(1) + ".json").geturl()


def _build_description(post):
    """Build a basic message body from the artist tag(s) and description fields.

    Returns inline HTML (the caller wraps it in a <p>), or None when neither
    field is present.
    """
    artists = (post.get("tags") or {}).get("artist") or []
    description = post.get("description") or ""

    parts = []
    if artists:
        names = ", ".join(html.escape(str(a)) for a in artists)
        parts.append(f"<b>Artist:</b> {names}")
    if description:
        parts.append(html.escape(str(description)))
    if not parts:
        return None
    return "<br />".join(parts)


async def fetch_e926(self, url_str, config, html_custom_headers=None):
    """Custom handler for e926-style post URLs.

    Rewrites the human-facing post URL to the .json API, fetches it, and returns
    an og dict with the sample (webp) image — or the full-size file when no
    sample exists — plus the artist(s) and description as the message body.

    Returns None when the URL is not a handled e926 post URL (so the caller can
    fall through to the generic parsers).
    """
    if not config or not config.get("enabled"):
        return None
    if not e926_match_host(url_str, config.get("hosts") or []):
        return None

    api_url = e926_to_api_url(url_str)
    if not api_url:
        return None

    json_headers = {**(html_custom_headers or {}), "Accept": "application/json"}
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

    post = (data or {}).get("post")
    if not post:
        self.log.debug(f"[urlpreview] [ext_e926] No 'post' in response - {urlparse(api_url).netloc}")
        return None

    # Pick the image: webp sample if present, otherwise the full-size file.
    sample = post.get("sample") or {}
    if sample.get("has"):
        image = sample.get("alt")  # the webp sample (sample.url is the jpg)
        content_type = "image/webp"
    else:
        file_obj = post.get("file") or {}
        image = file_obj.get("url")
        content_type = E926_IMAGE_MIME.get(str(file_obj.get("ext", "")).lower())

    if not image:
        self.log.debug(f"[urlpreview] [ext_e926] No image url found - {urlparse(api_url).netloc}")
        return None

    return {
        "title": None,
        "description": _build_description(post),
        "image": image,
        "image_mxc": None,
        "content_type": content_type,
        "image_width": None,
    }
