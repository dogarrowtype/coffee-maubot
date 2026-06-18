# urlpreview

> **Fork notice:** This is a fork of the `urlpreview` plugin from [coffeebank/coffee-maubot](https://github.com/coffeebank/coffee-maubot), with added support for oEmbed, URL rewrites, native image/video uploads, twitter video handling, and other changes. Plugin id is `dog.maubot.urlpreview` to avoid collision with upstream.

A bot that responds to links with a link preview embed, using Matrix API to fetch meta tags

![preview.jpg](https://coffeebank.github.io/coffee-maubot/assets/urlpreview-preview.jpg)

<div className="hidden">

## [Download >](https://coffeebank.github.io/coffee-maubot/urlpreview)

- [Join our Matrix room >](https://coffeebank.github.io/matrix)

</div>

<br />


## Usage

Sending any link in chat will have the bot reply to your message with the link's embed details.

The bot will first mark the chat as read, to indicate that it has initiated properly.

If there are multiple links in the message, the bot will fetch up to `max_links` (3) links. If it fails, it will skip embedding that link.

If the link returns a 404, the bot will return an emoji `no_results_react` (💨) on your message, to show that no results were returned. Blacklisted URLs will not trigger this reaction.

To suppress embedding for a specific link, prefix it with `<`: `<https://example.com`. The bot will ignore any URLs preceded by `<`.

Images from embeds are sent as native Matrix image attachments (rather than inline HTML), so users can click, download, and interact with them normally in their clients. If a link has both an image and a video, only the video is sent.

`url_blacklist` and `user_blacklist` can allow you to control how urlpreview is used.

<br />

## Config

- `ext_enabled` - Change which data sources to use for meta tags (last in array takes priority)
- `html_custom_headers` - Set custom headers (ie. User-Agent, Accept-Encoding, etc.) for data fetching
- `max_links` - Change how many links you'd like to process per message. 1-3 is recommended.
- `video_upload` - Set to `true` to download and re-upload videos and audio from pages (eg. fixupx) or direct media links as native Matrix messages. Default `true`.
- `max_video_size` - Maximum video/audio file size in MB before skipping the upload. Default `50`. Set to `0` for no limit.
- `no_results_react` - Adds a reaction emoji to the message to show that no results were returned. Put `''` to disable.
- `e926` - Custom handler for [e926](https://e926.net) post links (see [e926 Custom Handler](#e926-custom-handler) below)
- `url_blacklist` - Disable urlpreview for an IP range or a Regex entry
- `url_rewrite` - Rewrite URLs before fetching (see [URL Rewriting](#url-rewriting) below)
- `user_blacklist` - Disable urlpreview for a user

<details open>
{( <summary><b>htmlparser</b></summary> )}

N/A

</details>

<details open>
{( <summary><b>json</b></summary> )}

- `json_max_char` - Set a maximum character limit for outputted JSON, to prevent long files from blocking chat. Default 2000.

</details>

<details open>
{( <summary><b>synapse</b></summary> )}

- `appid` - Your bot's access token. This is needed to make the request to the Matrix Synapse URL Preview API.
- `homeserver` - Your homeserver (matrix-client.matrix.org by default, don't add https in front

</details>

<details open>
{( <summary><b>oembed</b></summary> )}

N/A — The oEmbed parser has no additional config options. It automatically discovers oEmbed endpoints via the [oEmbed providers registry](https://oembed.com/providers.json) and HTML `<link>` tag discovery.

</details>

<br />

## URL Rewriting

`url_rewrite` lets you automatically rewrite URLs before they are fetched. This is useful for replacing sites like Twitter/X with embed-friendly alternatives like FixupX.

The config is a dictionary where each key is a regex pattern and each value is the replacement string (using Python `re.sub` syntax):

```yaml
url_rewrite:
  'https?://(?:www\.)?(?:x|twitter)\.com/': 'https://fixupx.com/'
  'https?://(?:www\.)?instagram\.com/': 'https://ddinstagram.com/'
```

The rewritten URL replaces the original everywhere — both for fetching preview data and in the displayed embed. Only the first matching rule is applied per URL.

<br />

## e926 Custom Handler

The `e926` config enables a dedicated handler for [e926](https://e926.net) post links. When a matching post URL is seen, this handler takes priority over the generic parsers.

It works by:

1. Rewriting the human-facing post URL to its machine-readable `.json` API equivalent, inserting `.json` before any query string:
   - `https://e926.net/posts/6480895?q=white_fur` → `https://e926.net/posts/6480895.json?q=white_fur`
   - `https://e926.net/posts/6480895` → `https://e926.net/posts/6480895.json`
2. Fetching that API response and selecting the media to upload:
   - **Video posts** (when the post has a video): the **720p sample** is preferred, then the **480p sample**, then the **original** video file.
   - **Image posts:** the **webp sample** is used if the post has one, otherwise the full-size **file**.
3. Building the message body from the post's **description** plus, depending on the API version, the **artist** tag(s) (see [API v2](#api-v2) below).

The media is uploaded as a native Matrix `m.image` or `m.video` attachment, with the text sent as the accompanying message. (Video selection requires the v2 API and the global `video_upload` option, which is enabled by default; oversized videos are skipped per `max_video_size`.)

```yaml
e926:
  enabled: true
  api_v2: true
  hosts:
    - e926.net
```

- `enabled` - Set to `false` to disable the e926 handler and fall back to the generic parsers. Default `true`.
- `api_v2` - Use the v2 API response format (see [API v2](#api-v2) below). Set to `false` to use the older v1 format. Default `true`.
- `hosts` - List of hostnames to treat as e926-compatible. Only `/posts/<id>` URLs on these hosts are handled.

### API v2

e926 is rolling out a new v2 API response format. While it's being phased in, it is requested by appending `?v2=true` to the API URL. This is enabled by default (`api_v2: true`); set it to `false` to fall back to the v1 format.

When enabled, the handler requests `…/posts/<id>.json?v2=true` (any original query string on the link is dropped) and parses the v2 response shape.

The main behavioral difference is the message body:

- **v1 (`api_v2: false`):** tags are categorized, so the body shows the **artist** tag(s) followed by the **description**.
- **v2 (`api_v2: true`):** tags are returned as a single flat, uncategorized list, so the artist can't be isolated. The body shows the **description**, followed by the full tag list in a collapsible `<details>` dropdown.

The image selection (webp sample, else full-size file) is the same in both versions.

### Rate limiting

e926's API allows roughly 1 request per second (sustained bursts over 2 RPS are hard-blocked). The handler serializes its own requests and spaces them ~1.1s apart, so a message containing several e926 links is fetched sequentially rather than all at once. This only throttles e926 requests; other links are unaffected.

<br />

## Notes

- This bot comes with four parsers: `htmlparser`, `json`, `oembed`, and `synapse`. By default, all are enabled.
- You can control which ones to enable/disable or prioritize using `ext_enabled` (last in array takes priority).
- Due to the length of some embeds, line-breaks are stripped from any `og:description` tags.
- Images from embeds are uploaded to the Matrix homeserver and sent as native `m.image` attachments, allowing users to interact with them normally in their clients.
- When `video_upload` is enabled, the bot detects `og:video` / `twitter:player:stream` meta tags (eg. from fixupx video tweets), downloads the video, uploads it to the Matrix homeserver, and sends it as a native `m.video` message alongside the text embed.
- When `video_upload` is enabled, direct links to media files are detected by content-type or URL extension, downloaded, and re-uploaded as native `m.video` or `m.audio` Matrix messages. This also works when the server returns `application/octet-stream` as the content-type. Supported formats include:
  - **Video:** `.mp4`, `.webm`, `.mov`, `.mkv`, `.avi`, `.flv`, `.wmv`, `.mpg`/`.mpeg`, `.ts`, `.3gp`, `.3g2`, `.ogv`, `.m4v`
  - **Audio:** `.mp3`, `.ogg`, `.oga`, `.opus`, `.flac`, `.wav`, `.m4a`, `.aac`, `.wma`, `.aiff`/`.aif`, `.mid`/`.midi`, `.amr`, `.caf`, `.mka`, `.au`, `.weba`
- When multiple `og:image` tags are found on a page (eg. fixupx album tweets), the embed shows a `[1 of N images]` indicator.

<details>
{( <summary><b>htmlparser</b></summary> )}

- `htmlparser` works out-of-the-box by directly fetching the HTML page and parsing using `htmlparser` (built-in).
- `htmlparser` may leak your server's IP, and is recommended for bots hosted in a VPS/server environment.
- Some sites protected by Cloudflare/similar services may not return results.

</details>

<details>
{( <summary><b>json</b></summary> )}

- `json` works out-of-the-box by directly fetching pages with `application/json` mime_type and parsing using `json` (built-in).
- `json` may leak your server's IP, and is recommended for bots hosted in a VPS/server environment.
- By default, JSON results are truncated to `json_max_char` (2000) characters in chat.

</details>

<details>
{( <summary><b>oembed</b></summary> )}

- `oembed` supports the [oEmbed protocol](https://oembed.com/) for rich embeds from 500+ providers (YouTube, Flickr, Spotify, etc.).
- On first use, it fetches and caches the [oEmbed providers registry](https://oembed.com/providers.json) to match URLs to known endpoints directly — no extra page fetch needed.
- For URLs not in the registry, it falls back to HTML discovery by looking for `<link rel="alternate" type="application/json+oembed">` tags.
- `oembed` may leak your server's IP when fetching oEmbed endpoints or doing HTML discovery.

</details>

<details>
{( <summary><b>synapse</b></summary> )}

- `synapse` depends on the [Matrix Synapse URL Previews API](https://matrix-org.github.io/synapse/latest/setup/installation.html?highlight=url%20previews#url-previews).
- `synapse` requires you to specify an `appid` and `homeserver` that runs Synapse and supports URL Previews.
- Synapse URL Previews works best with the default [matrix.org homeserver](https://matrix.org/legal/terms-and-conditions/).
  - Some homeservers return 404s at an increased rate. You can check your homeserver's acceptance [on Hoppscotch *(update URL with your homeserver, and BOT_ACCESS_TOKEN in Headers)*](https://hopp.sh/r/wpEdCHsQ8YHM)
- `min_image_width` - Change the minimum image width before the bot sends an image. 475 is recommended to avoid favicons.  - Not implemented yet, to be restored soon

</details>

<br />

### Upgrade Guide

If you're updating from older urlpreview versions, delete the whole `ext_enabled: [...]` line and click "Save" to activate new parsers.

To get new Config entries, in your Maubot Manager's Instances, please click "Save" (even with no changes) to force-update the default Config values. This will restore missing Config values and defaults. You can also delete some or all of your Config entries and click "Save" to restore defaults.

### Known Bugs

- As of v0.3, image previews will expire after a few days. If you would like to preserve any images, please manually copy-paste reupload the images into chat as an uploaded image.
- YouTube doesn't put line breaks in their `og:description`, which may lead to improperly parsed links in your Matrix client.
