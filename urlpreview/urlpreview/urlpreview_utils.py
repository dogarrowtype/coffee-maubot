import ipaddress
import re
import socket
import struct
import urllib.parse
from urllib.parse import urlparse

IMAGE_TYPES = [
    "image/gif", "image/jpg", "image/jpeg", "image/png", "image/webp",
    "image/svg+xml", "image/bmp", "image/tiff", "image/avif",
]
VIDEO_TYPES = [
    "video/mp4", "video/webm", "video/quicktime", "video/ogg",
    "video/x-matroska", "video/x-msvideo", "video/x-flv",
    "video/mpeg", "video/mp2t", "video/3gpp", "video/3gpp2",
    "video/x-ms-wmv", "video/x-m4v",
]
AUDIO_TYPES = [
    "audio/mpeg", "audio/ogg", "audio/opus", "audio/flac",
    "audio/wav", "audio/x-wav", "audio/aac", "audio/mp4", "audio/webm",
    "audio/x-matroska", "audio/x-ms-wma", "audio/amr", "audio/3gpp",
    "audio/aiff", "audio/x-aiff", "audio/basic", "audio/midi", "audio/x-midi",
    "audio/x-caf", "audio/x-m4a",
    "application/ogg", "application/x-flac",
]

# Extension to MIME type for fallback when content-type is ambiguous
MEDIA_EXT_MAP = {
    # Video
    ".mp4": "video/mp4",
    ".m4v": "video/x-m4v",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".mkv": "video/x-matroska",
    ".avi": "video/x-msvideo",
    ".flv": "video/x-flv",
    ".wmv": "video/x-ms-wmv",
    ".mpg": "video/mpeg",
    ".mpeg": "video/mpeg",
    ".ts": "video/mp2t",
    ".3gp": "video/3gpp",
    ".3g2": "video/3gpp2",
    ".ogv": "video/ogg",
    # Audio
    ".mp3": "audio/mpeg",
    ".ogg": "audio/ogg",
    ".oga": "audio/ogg",
    ".opus": "audio/ogg",
    ".flac": "audio/flac",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".wma": "audio/x-ms-wma",
    ".aiff": "audio/aiff",
    ".aif": "audio/aiff",
    ".mid": "audio/midi",
    ".midi": "audio/midi",
    ".amr": "audio/amr",
    ".caf": "audio/x-caf",
    ".mka": "audio/x-matroska",
    ".au": "audio/basic",
    ".weba": "audio/webm",
}

def detect_media_type_from_url(url_str):
    """Check URL path extension for known media types. Returns (mime_type, 'video'|'audio') or (None, None)."""
    path = urlparse(url_str).path.lower().split('?')[0]
    for ext, mime in MEDIA_EXT_MAP.items():
        if path.endswith(ext):
            category = "video" if mime.startswith("video/") else "audio"
            return mime, category
    return None, None

def check_all_none_except(data, keys_to_except):
    for key, value in data.items():
        if key not in keys_to_except and value is not None:
            return False
    return True

def get_image_dimensions(data: bytes):
    """Parse image width/height from raw bytes. Returns (width, height) or (None, None)."""
    if not data or len(data) < 24:
        return None, None
    try:
        # PNG: 8-byte sig + 4 length + 4 "IHDR" + 4 width + 4 height (big-endian)
        if data[:8] == b'\x89PNG\r\n\x1a\n':
            w, h = struct.unpack('>II', data[16:24])
            return w, h
        # GIF: 6-byte header + 2-byte width + 2-byte height (little-endian)
        if data[:6] in (b'GIF87a', b'GIF89a'):
            w, h = struct.unpack('<HH', data[6:10])
            return w, h
        # JPEG: scan for SOF markers (0xFF 0xCx)
        if data[:2] == b'\xff\xd8':
            i = 2
            while i + 8 < len(data):
                if data[i] != 0xFF:
                    break
                marker = data[i + 1]
                if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                              0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                    h, w = struct.unpack('>HH', data[i + 5:i + 9])
                    return w, h
                seg_len = struct.unpack('>H', data[i + 2:i + 4])[0]
                i += 2 + seg_len
        # WebP: RIFF....WEBP + subformat
        if data[:4] == b'RIFF' and data[8:12] == b'WEBP' and len(data) >= 30:
            subformat = data[12:16]
            if subformat == b'VP8 ':  # lossy
                w = struct.unpack('<H', data[26:28])[0] & 0x3FFF
                h = struct.unpack('<H', data[28:30])[0] & 0x3FFF
                return w, h
            if subformat == b'VP8L':  # lossless
                bits = struct.unpack('<I', data[21:25])[0]
                return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
            if subformat == b'VP8X':  # extended
                w = struct.unpack('<I', data[24:27] + b'\x00')[0] + 1
                h = struct.unpack('<I', data[27:30] + b'\x00')[0] + 1
                return w, h
    except (struct.error, IndexError):
        pass
    return None, None

async def check_image_content_type(self, image_url, html_custom_headers=None):
    if not image_url:
        return None
    try:
        resp = await self.http.get(str(image_url), headers=html_custom_headers)
    except Exception as err:
        self.log.exception(f"[urlpreview] [utils] check_image_content_type Error: {err} - {str(image_url)}")
        return None
    if resp.status != 200:
        return None
    if resp.content_type in IMAGE_TYPES:
        return resp.content_type
    return None

def check_line_breaks(text: str):
    if text is None:
        return None
    return text.replace('\n', '<br />')

def format_title(title, url_str: str=""):
    if not title:
        return None
    if url_str:
        return f'<h3><a href="{url_str}">{str(title)}</a></h3>'
    else:
        return f'<h3>{str(title)}</h3>'

def format_description(description, preserve_line_breaks: bool=False):
    if not description:
        return None
    if preserve_line_breaks is False:
        return f'<p>'+str(description).replace('\r', ' ').replace('\n', ' ')+'</p>'
    else:
        return f'<p>'+str(description)+'</p>'

def format_attribution(attribution):
    if not attribution:
        return None
    return f'<p><small>{str(attribution)}</small></p>'

async def process_image(self, image: str, html_custom_headers=None, content_type: str=None):
    """Returns (mxc, size, width, height) or None."""
    if not image:
        return None
    image_url = urlparse(image)
    # URL is already mxc — no size/dimension info available
    if image_url.scheme == 'mxc':
        return image, None, None, None
    # URL is not mxc
    if not content_type:
        content_type = await check_image_content_type(self, image, html_custom_headers=html_custom_headers)
    if not content_type:
        content_type = 'image/jpeg'
    result = await matrix_get_image(
        self,
        image_url=image,
        html_custom_headers=html_custom_headers,
        mime_type=content_type,
        filename=content_type.replace('/', '.').replace('jpeg', 'jpg')
    )
    return result  # (mxc, size, width, height) or None

async def matrix_get_image(self, image_url: str, html_custom_headers=None, mime_type: str="image/jpeg", filename: str="image.jpg"):
    if not image_url:
        return None
    try:
        resp = await self.http.get(image_url, headers=html_custom_headers)
    except Exception as err:
        self.log.exception(f"[urlpreview] [utils] Error matrix_get_image http.get: {str(err)}")
        return None
    if resp.status != 200:
        self.log.exception(f"[urlpreview] [utils] Error matrix_get_image resp.status: {str(resp.status)} - {str(urlparse(image_url).netloc)}")
        return None
    og_image = await resp.read()
    try:
        mxc = await self.client.upload_media(og_image, mime_type=mime_type, filename=filename)
    except Exception as err:
        self.log.exception(f"[urlpreview] [utils] Error matrix_get_image client.upload_media: {str(err)}")
        return None
    size = len(og_image)
    width, height = get_image_dimensions(og_image)
    return mxc, size, width, height

async def matrix_upload_video(self, video_url: str, html_custom_headers=None, mime_type: str="video/mp4", filename: str="video.mp4", max_video_size: int=50, media_data: bytes=None):
    if not video_url and not media_data:
        return None
    if media_data:
        video_data = media_data
    else:
        try:
            resp = await self.http.get(video_url, headers=html_custom_headers, timeout=60)
        except Exception as err:
            self.log.exception(f"[urlpreview] [utils] Error matrix_upload_video http.get: {str(err)}")
            return None
        if resp.status != 200:
            self.log.exception(f"[urlpreview] [utils] Error matrix_upload_video resp.status: {str(resp.status)} - {str(urlparse(video_url).netloc)}")
            return None
        video_data = await resp.read()
    size_bytes = len(video_data)
    if max_video_size > 0 and size_bytes > max_video_size * 1024 * 1024:
        self.log.info(f"[urlpreview] [utils] Video too large ({size_bytes} bytes, limit {max_video_size}MB): {str(urlparse(video_url).netloc)}")
        return None
    try:
        mxc = await self.client.upload_media(video_data, mime_type=mime_type, filename=filename)
    except Exception as err:
        self.log.exception(f"[urlpreview] [utils] Error matrix_upload_video client.upload_media: {str(err)}")
        return None
    return (mxc, size_bytes)

def url_check_is_in_range(ip, unsafe_url, ranges):
    for r in ranges:
        # Range item is an IP
        try:
            if ipaddress.ip_address(ip) in ipaddress.ip_network(r, strict=False):
                return True
        # Range item is a regex
        except ValueError:
            if re.search(r, unsafe_url) is not None:
                return True
    return False

def url_get_ip_from_hostname(hostname):
    # IPv4
    try:
        return socket.gethostbyname(hostname)
    except socket.gaierror:
        pass
    # IPv6
    try:
        answers = socket.getaddrinfo(hostname, None, socket.AF_INET6)
        for answer in answers:
            if answer[1] == socket.SOCK_STREAM:
                return answer[4][0]
    except (socket.gaierror, IndexError):
        pass
    return None

def url_check_blacklist(url, blacklist):
    if "://" not in url:
        url = "http://" + url
    hostname = urlparse(url).hostname
    ip = url_get_ip_from_hostname(hostname)
    if not ip:
        return False
    is_blacklisted = url_check_is_in_range(ip, url, blacklist)
    if not is_blacklisted:
        return url
    return None

def url_apply_rewrites(url, rewrite_rules):
    if not rewrite_rules:
        return url
    for pattern, replacement in rewrite_rules.items():
        if pattern and re.search(pattern, url):
            return re.sub(pattern, replacement, url)
    return url

def user_check_blacklist(user, blacklist):
    if user in blacklist:
        return True
    return False
