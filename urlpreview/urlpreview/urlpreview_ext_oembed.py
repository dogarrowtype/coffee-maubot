import re
from html.parser import HTMLParser
from urllib.parse import quote, urlparse

from .urlpreview_utils import check_line_breaks

OEMBED_PROVIDERS_URL = "https://oembed.com/providers.json"


async def fetch_oembed(self, url_str, html_custom_headers=None, **kwargs):
    if not url_str:
        return None

    # Step 1: Try the providers registry first (no extra page fetch needed)
    oembed_url = await _resolve_from_registry(self, url_str, html_custom_headers)

    # Step 2: Fall back to HTML discovery if no registry match
    if not oembed_url:
        oembed_url = await _discover_from_html(self, url_str, html_custom_headers)

    if not oembed_url:
        self.log.debug(f"[urlpreview] [ext_oembed] No oEmbed endpoint found for {urlparse(url_str).netloc}")
        return None

    # Step 3: Fetch the oEmbed JSON endpoint
    try:
        oembed_resp = await self.http.get(oembed_url, timeout=30, headers=html_custom_headers)
    except Exception as err:
        self.log.exception(f"[urlpreview] [ext_oembed] Error fetching oEmbed endpoint: {err}")
        return None

    if oembed_resp.status != 200:
        self.log.debug(f"[urlpreview] [ext_oembed] oEmbed endpoint returned {oembed_resp.status}")
        return None

    try:
        data = await oembed_resp.json(content_type=None)
    except Exception as err:
        self.log.debug(f"[urlpreview] [ext_oembed] Failed to parse oEmbed JSON: {err}")
        return None

    self.log.debug(f"[urlpreview] [ext_oembed] oEmbed response: {str(data)[:600]}")

    # Step 4: Map oEmbed fields to the standard og dict
    oembed_type = data.get("type", "link")

    title = data.get("title")
    # Filter out known-generic oEmbed titles (e.g. fixupx returns "Embed")
    if title in {"Embed", "embed"}:
        title = None

    attribution = _build_description(data)
    image = None
    image_width = None

    if oembed_type == "photo":
        image = data.get("url")
        image_width = data.get("width")
    else:
        image = data.get("thumbnail_url")
        image_width = data.get("thumbnail_width")

    return {
        "title": title,
        "description": None,
        "oembed_attribution": check_line_breaks(attribution) if attribution else None,
        "image": image,
        "image_mxc": None,
        "content_type": None,
        "image_width": image_width,
    }


# --- Provider registry ---

async def _get_providers(self):
    """Fetch and cache the oEmbed providers list."""
    if hasattr(self, "_oembed_providers"):
        return self._oembed_providers
    try:
        resp = await self.http.get(OEMBED_PROVIDERS_URL, timeout=30)
        if resp.status == 200:
            self._oembed_providers = await resp.json(content_type=None)
            self.log.debug(f"[urlpreview] [ext_oembed] Loaded {len(self._oembed_providers)} oEmbed providers")
            return self._oembed_providers
    except Exception as err:
        self.log.exception(f"[urlpreview] [ext_oembed] Failed to fetch providers.json: {err}")
    self._oembed_providers = []
    return self._oembed_providers


def _scheme_to_regex(scheme):
    """Convert an oEmbed URL scheme pattern (with * wildcards) to a regex."""
    escaped = re.escape(scheme)
    return "^" + escaped.replace(r"\*", ".*") + "$"


def _match_provider(url_str, providers):
    """Find the oEmbed endpoint URL for a given URL from the providers list."""
    for provider in providers:
        for endpoint in provider.get("endpoints", []):
            schemes = endpoint.get("schemes", [])
            for scheme in schemes:
                if re.match(_scheme_to_regex(scheme), url_str):
                    api_url = endpoint.get("url")
                    if api_url:
                        return api_url
    return None


async def _resolve_from_registry(self, url_str, html_custom_headers):
    """Check the providers registry for a matching oEmbed endpoint."""
    providers = await _get_providers(self)
    if not providers:
        return None
    api_url = _match_provider(url_str, providers)
    if not api_url:
        return None
    # Build the oEmbed request URL
    separator = "&" if "?" in api_url else "?"
    oembed_url = f"{api_url}{separator}url={quote(url_str, safe='')}&format=json"
    self.log.debug(f"[urlpreview] [ext_oembed] Registry match: {oembed_url}")
    return oembed_url


# --- HTML discovery fallback ---

async def _discover_from_html(self, url_str, html_custom_headers):
    """Fetch a page and look for oEmbed discovery <link> tags."""
    try:
        resp = await self.http.get(url_str, timeout=30, headers=html_custom_headers)
    except Exception as err:
        self.log.exception(f"[urlpreview] [ext_oembed] Error fetching page: {err} - {urlparse(url_str).netloc}")
        return None

    if resp.status != 200:
        self.log.debug(f"[urlpreview] [ext_oembed] Non-200 status: {resp.status} - {urlparse(url_str).netloc}")
        return None

    if resp.content_type and not resp.content_type.startswith("text/html"):
        return None

    html_text = await resp.text()
    parser = OEmbedLinkDiscovery()
    parser.feed(html_text)
    return parser.oembed_url


# --- Helpers ---

def _build_description(data):
    """Build a description from oEmbed fields, preferring author attribution."""
    parts = []
    author = data.get("author_name")
    provider = data.get("provider_name")
    if author:
        parts.append(author)
    if provider:
        parts.append(provider)
    if parts:
        return " — ".join(parts)
    return None


class OEmbedLinkDiscovery(HTMLParser):
    """Parses HTML to find oEmbed discovery <link> tags."""

    def __init__(self):
        HTMLParser.__init__(self)
        self.oembed_url = None

    def handle_starttag(self, tag, attrs):
        if tag != "link" or self.oembed_url:
            return
        attrs_dict = dict(attrs)
        link_type = attrs_dict.get("type", "")
        if link_type in ("application/json+oembed", "text/json+oembed"):
            href = attrs_dict.get("href")
            if href:
                self.oembed_url = href
