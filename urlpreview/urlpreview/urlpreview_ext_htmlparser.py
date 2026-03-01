from html.parser import HTMLParser
import json
from typing import List, Type
import urllib.parse
from urllib.parse import urlparse

from .urlpreview_utils import *

async def fetch_htmlparser(self, url_str, html_custom_headers, **kwargs):
    if not url_str:
        return None

    try:
        resp = await self.http.get(url_str, timeout=30, headers=html_custom_headers) # 30s timeout matches Matrix Synapse
    except Exception as err:
        self.log.exception(f"[urlpreview] [ext_htmlparser] Error: {str(err)} - {str(urlparse(url_str).netloc)}")
        return None

    # Guard clause
    if resp.status != 200:
        self.log.exception(f"[urlpreview] [ext_htmlparser] Error: Status {str(resp.status)} - {str(urlparse(url_str).netloc)} - {str(resp)}")
        return None

    # Images
    if resp.content_type in IMAGE_TYPES:
        return {
            "title": None,
            "description": None,
            "image": url_str,
            "image_mxc": None,
            "content_type": resp.content_type,
            "image_width": None,
        }

    # Direct video links
    if resp.content_type in VIDEO_TYPES:
        media_data = await resp.read()
        return {
            "title": None,
            "description": None,
            "image": None,
            "image_mxc": None,
            "content_type": None,
            "image_width": None,
            "video": url_str,
            "video_type": resp.content_type,
            "video_width": None,
            "video_height": None,
            "media_data": media_data,
        }

    # Direct audio links
    if resp.content_type in AUDIO_TYPES:
        media_data = await resp.read()
        return {
            "title": None,
            "description": None,
            "image": None,
            "image_mxc": None,
            "content_type": None,
            "image_width": None,
            "audio": url_str,
            "audio_type": resp.content_type,
            "media_data": media_data,
        }

    # Extension-based fallback for ambiguous content types (eg. application/octet-stream)
    if resp.content_type in ["application/octet-stream", "binary/octet-stream"]:
        mime, category = detect_media_type_from_url(url_str)
        if category == "video":
            media_data = await resp.read()
            return {
                "title": None,
                "description": None,
                "image": None,
                "image_mxc": None,
                "content_type": None,
                "image_width": None,
                "video": url_str,
                "video_type": mime,
                "video_width": None,
                "video_height": None,
                "media_data": media_data,
            }
        elif category == "audio":
            media_data = await resp.read()
            return {
                "title": None,
                "description": None,
                "image": None,
                "image_mxc": None,
                "content_type": None,
                "image_width": None,
                "audio": url_str,
                "audio_type": mime,
                "media_data": media_data,
            }

    # HTML
    cont = await resp.text()
    parser = ExtractMetaTags()
    parser.feed(cont)

    # Post-processing
    if parser.og["content_type"] is None:
        content_type = await check_image_content_type(self, parser.og["image"], html_custom_headers=html_custom_headers)
        if content_type is not None:
            parser.og["content_type"] = content_type

    self.log.debug(f"[urlpreview] [ext_htmlparser] fetch_htmlparser {str(parser.og)}")
    return parser.og

def fetch_meta_content(attrs, attr_to_find):
    # <meta property="" content="" />
    for attr, value in attrs:
        if attr in ["property", "name"] and value == attr_to_find:
            for attr_2, value_2 in attrs:
                if attr_2 == "content":
                    return str(value_2)
    return None

class ExtractMetaTags(HTMLParser):
    def __init__(self):
        HTMLParser.__init__(self)
        self.og = {
            "title": None,
            "description": None,
            "image": None,
            "image_mxc": None,
            "content_type": None,
            "image_width": None,
            "image_count": 0,
            "video": None,
            "video_type": None,
            "video_width": None,
            "video_height": None,
        }

    def handle_starttag(self, tag, attrs):
        if tag == "meta":
            title = fetch_meta_content(attrs, "og:title")
            if title is None:
                title = fetch_meta_content(attrs, "og:site-title")
            if title is not None:
                self.og["title"] = title

            description = fetch_meta_content(attrs, "og:description")
            if description is None:
                description = fetch_meta_content(attrs, "description")
            if description is not None:
                self.og["description"] = check_line_breaks(description)

            image = fetch_meta_content(attrs, "og:image")
            if image is not None:
                self.og["image_count"] += 1
                if self.og["image"] is None:
                    self.og["image"] = image
            twitter_image = fetch_meta_content(attrs, "twitter:image")
            if twitter_image is not None and self.og["image"] is None:
                self.og["image"] = twitter_image

            content_type = fetch_meta_content(attrs, "og:image:type")
            if content_type is not None:
                self.og["content_type"] = content_type

            image_width = fetch_meta_content(attrs, "og:image:width")
            if image_width is not None:
                self.og["image_width"] = image_width

            video = fetch_meta_content(attrs, "og:video")
            if video is None:
                video = fetch_meta_content(attrs, "twitter:player:stream")
            if video is not None and self.og["video"] is None:
                self.og["video"] = video

            video_type = fetch_meta_content(attrs, "og:video:type")
            if video_type is None:
                video_type = fetch_meta_content(attrs, "twitter:player:stream:content_type")
            if video_type is not None:
                self.og["video_type"] = video_type

            video_width = fetch_meta_content(attrs, "og:video:width")
            if video_width is not None:
                self.og["video_width"] = video_width

            video_height = fetch_meta_content(attrs, "og:video:height")
            if video_height is not None:
                self.og["video_height"] = video_height
