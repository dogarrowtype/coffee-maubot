import mautrix.api
from mautrix.types import RoomID, ImageInfo, MessageType, VideoInfo, AudioInfo, MediaMessageEventContent
from mautrix.types.event.message import BaseFileInfo, Format, TextMessageEventContent
from mautrix.util.config import BaseProxyConfig, ConfigUpdateHelper
from maubot import Plugin, MessageEvent
from maubot.handlers import command

import urllib.parse
from urllib.parse import urlparse

from .urlpreview_utils import *
from .urlpreview_ext_htmlparser import *
from .urlpreview_ext_json import *
from .urlpreview_ext_oembed import *
from .urlpreview_ext_synapse import *

EXT_FALLBACK = ["synapse", "oembed", "htmlparser", "json"]
EXT_ARR = {
  "htmlparser": fetch_htmlparser,
  "json": fetch_json,
  "oembed": fetch_oembed,
  "synapse": fetch_synapse,
}

class Config(BaseProxyConfig):
    def do_update(self, helper: ConfigUpdateHelper) -> None:
        helper.copy("ext_enabled")
        helper.copy("appid")
        helper.copy("homeserver")
        helper.copy("html_custom_headers")
        helper.copy("json_max_char")
        helper.copy("max_links")
        helper.copy("min_image_width")
        helper.copy("max_image_embed")
        helper.copy("image_link")
        helper.copy("video_upload")
        helper.copy("max_video_size")
        helper.copy("no_results_react")
        helper.copy("url_blacklist")
        helper.copy("url_rewrite")
        helper.copy("user_blacklist")

class UrlPreviewBot(Plugin):
    async def start(self) -> None:
        await super().start()
        self.config.load_and_update()

    @classmethod
    def get_config_class(cls) -> Type[BaseProxyConfig]:
        return Config

    # RFC 3986 excluding: (), []
    @command.passive("(https?:\/\/[A-Za-z0-9\-._~:\/?#@!$&'*+,;=%]+)", multiple=True)
    async def handler(self, evt: MessageEvent, matches: List[str]) -> None:
        # Check USER_BLACKLIST
        USER_BLACKLIST = self.config["user_blacklist"]
        if user_check_blacklist(evt.sender, USER_BLACKLIST):
            return

        EXT_ENABLED = self.config["ext_enabled"]
        appid = self.config["appid"]
        MAX_LINKS = self.config["max_links"]
        HOMESERVER = self.config["homeserver"]
        HTML_CUSTOM_HEADERS = self.config["html_custom_headers"]
        JSON_MAX_CHAR = self.config["json_max_char"]
        MIN_IMAGE_WIDTH = self.config["min_image_width"]
        MAX_IMAGE_EMBED = self.config["max_image_embed"]
        IMAGE_LINK = self.config["image_link"]
        VIDEO_UPLOAD = self.config["video_upload"]
        MAX_VIDEO_SIZE = self.config["max_video_size"]
        NO_RESULTS_REACT = self.config["no_results_react"]
        URL_BLACKLIST = self.config["url_blacklist"]
        URL_REWRITE = self.config["url_rewrite"]
        await evt.mark_read()

        embeds = []
        media = [] # Collected video/audio og dicts for later upload
        count = 0
        max_count = 0
        for _, unsafe_url in matches:
            # Break when MAX_LINKS embeds, or processed MAX_LINKS*n links
            if count >= int(MAX_LINKS) or max_count >= int(MAX_LINKS)*3:
                self.log.debug(f"[urlpreview] Reached MAX_LINKS limit: {str(MAX_LINKS)} embeds or {str(MAX_LINKS*3)} attempts")
                break
            # URL rewriting (before blacklist check)
            unsafe_url = url_apply_rewrites(unsafe_url, URL_REWRITE)
            # Check URL_BLACKLIST
            url_str = url_check_blacklist(unsafe_url, URL_BLACKLIST)
            if url_str is None:
                self.log.debug(f"[urlpreview] {evt.sender} tried to access restricted URL: {str(unsafe_url)}")
                max_count += 1
                continue

            arg_arr = {
                "self": self,
                "url_str": url_str,
                "ext_enabled": EXT_ENABLED,
                "appid": appid,
                "homeserver": HOMESERVER,
                "html_custom_headers": HTML_CUSTOM_HEADERS,
                "json_max_char": JSON_MAX_CHAR
            }
            og = await fetch_all(**arg_arr)
            embed = await embed_url_preview(self, url_str=url_str, og=og, html_custom_headers=HTML_CUSTOM_HEADERS, max_image_embed=MAX_IMAGE_EMBED, image_link=IMAGE_LINK)
            if embed is not None:
                embeds.append(embed)
                count += 1 # Implement MAX_LINKS
            # Collect video/audio info for later upload
            if VIDEO_UPLOAD and (og.get('video') or og.get('audio')):
                media.append(og)
            max_count += 1

        if len(embeds) <= 0 and len(media) <= 0:
            if len(matches) > 0 and NO_RESULTS_REACT:
                try:
                    await evt.react(NO_RESULTS_REACT)
                except: # Silently ignore if react doesn't work
                    pass
            return
        if len(embeds) > 0:
            to_send = "".join(embeds)
            await evt.reply(to_send, allow_html=True)

        # Upload and send video/audio as separate native Matrix messages
        for og in media:
            try:
                if og.get('video'):
                    video_url = og['video']
                    mime_type = og.get('video_type', 'video/mp4')
                    ext = mime_type.split('/')[-1].split(';')[0]
                    if ext == 'quicktime':
                        ext = 'mov'
                    result = await matrix_upload_video(
                        self,
                        video_url=video_url,
                        html_custom_headers=HTML_CUSTOM_HEADERS,
                        mime_type=mime_type,
                        filename=f"video.{ext}",
                        max_video_size=int(MAX_VIDEO_SIZE),
                        media_data=og.get('media_data'),
                    )
                    if result is None:
                        continue
                    mxc, size_bytes = result
                    width = int(og['video_width']) if og.get('video_width') else None
                    height = int(og['video_height']) if og.get('video_height') else None
                    content = MediaMessageEventContent(
                        msgtype=MessageType.VIDEO,
                        body=f"video.{ext}",
                        url=mxc,
                        info=VideoInfo(
                            mimetype=mime_type,
                            size=size_bytes,
                            width=width,
                            height=height,
                        ),
                    )
                    await evt.respond(content)
                elif og.get('audio'):
                    audio_url = og['audio']
                    mime_type = og.get('audio_type', 'audio/mpeg')
                    ext = mime_type.split('/')[-1].split(';')[0]
                    if ext == 'mpeg':
                        ext = 'mp3'
                    result = await matrix_upload_video(
                        self,
                        video_url=audio_url,
                        html_custom_headers=HTML_CUSTOM_HEADERS,
                        mime_type=mime_type,
                        filename=f"audio.{ext}",
                        max_video_size=int(MAX_VIDEO_SIZE),
                        media_data=og.get('media_data'),
                    )
                    if result is None:
                        continue
                    mxc, size_bytes = result
                    content = MediaMessageEventContent(
                        msgtype=MessageType.AUDIO,
                        body=f"audio.{ext}",
                        url=mxc,
                        info=AudioInfo(
                            mimetype=mime_type,
                            size=size_bytes,
                        ),
                    )
                    await evt.respond(content)
            except Exception as err:
                self.log.exception(f"[urlpreview] Error sending media: {err}")


# Utility Commands

async def fetch_all(
        self,
        url_str,
        ext_enabled=EXT_FALLBACK,
        appid: str='BOT_ACCESS_TOKEN',
        homeserver: str='matrix-client.matrix.org',
        html_custom_headers={},
        json_max_char=2000,
        **kwargs
    ):
    final_og = {}
    for ext in ext_enabled:
        try:
            fetch_ext = EXT_ARR.get(ext, None)
            arg_arr = {
                "self": self,
                "url_str": url_str,
                "appid": appid,
                "homeserver": homeserver,
                "html_custom_headers": html_custom_headers,
                "json_max_char": json_max_char
            }
            og_resp = await fetch_ext(**arg_arr)
            if og_resp:
                final_og.update({k:v for (k,v) in og_resp.items() if v}) # Remove all 'None's
        except Exception as err:
            self.log.exception(f"[urlpreview] Error fetch_all fetch_ext: {err}")
    return final_og

async def embed_url_preview(self, url_str, og, html_custom_headers=None, max_image_embed: int=300, image_link: bool=False):
    # Check if None
    if not og:
        return None
    if all(v is None for v in og):
        return None
    # Fetch image_mxc
    image_mxc = og.get('image_mxc', None)
    if image_mxc is None:
        image_mxc = await process_image(self, image=og.get('image', None), html_custom_headers=html_custom_headers, content_type=og.get('content_type', None))
    # Check if only contains image
    image_url_str = url_str if image_link else ''
    og_meta_keys = ['image', 'image_mxc', 'content_type', 'image_width', 'image_count', 'video', 'video_type', 'video_width', 'video_height', 'audio', 'audio_type', 'media_data']
    if check_all_none_except(og, og_meta_keys):
        image_solo = format_image(image_mxc, image_url_str, og.get('content_type', None), max_image_embed=0) # Full size image
        if image_solo is not None:
            return f"<blockquote>{image_solo}</blockquote>"
        return None # Everything is empty
    # Multi-image indicator
    image_count = og.get('image_count', 0)
    image_count_note = f'<p><i>[1 of {image_count} images]</i></p>' if image_count > 1 else None
    # Default message
    title = format_title(og.get('title', None), url_str)
    description = format_description(og.get('description', None))
    image = format_image(image_mxc, image_url_str, og.get('content_type', None), format_image_width(og.get('image_width', None), max_image_embed))
    message = "".join(filter(None, [title, description, image, image_count_note]))
    if message:
        return f"<blockquote>{message}</blockquote>"
    return None
