#!/usr/bin/env python3
"""Rise SCORM -> one self-contained HTML content inventory.

Copyright (c) 2026. Released under the MIT licence.
This is an independent, offline, read-only extractor, not affiliated with Articulate.
No third-party Python modules are required.
"""
from __future__ import annotations

import argparse
import base64
import binascii
from collections import Counter
import gzip
import html
from html.parser import HTMLParser
import json
import mimetypes
from pathlib import Path, PurePosixPath
import re
import sys
import urllib.parse
import zlib
from zipfile import ZipFile, BadZipFile

MAX_CANDIDATE_SIZE = 100 * 1024 * 1024
MAX_IMAGE_SIZE = 16 * 1024 * 1024
MAX_TOTAL_EMBED = 200 * 1024 * 1024
MAX_VIDEO_SIZE = 32 * 1024 * 1024
SAFE_TAGS = {"p", "br", "b", "strong", "i", "em", "u", "s", "h1", "h2", "h3", "h4", "h5", "h6",
             "ul", "ol", "li", "a", "blockquote", "pre", "code", "small", "mark", "sub", "sup", "hr",
             "table", "thead", "tbody", "tr", "td", "th", "div", "span"}
VOID_TAGS = {"br", "hr"}
MEDIA_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".avif", ".mp4", ".webm", ".mov", ".m4v",
             ".mp3", ".m4a", ".ogg", ".wav", ".vtt", ".srt", ".pdf", ".docx", ".pptx"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif"}
VIDEO_EXT = {".mp4", ".webm", ".mov", ".m4v"}
AUDIO_EXT = {".mp3", ".m4a", ".ogg", ".wav"}
TEXT_KEYS = {"title", "heading", "subheading", "subtitle", "paragraph", "body", "content", "text", "description",
             "statement", "quote", "attribution", "caption", "label", "prompt", "question", "answer", "feedback",
             "hint", "explanation", "placeholder", "intro", "instruction", "instructions", "name", "transcript",
             "summary", "message", "alt", "alttext", "correctfeedback", "incorrectfeedback", "successfeedback",
             "failurefeedback", "front", "back", "term", "definition"}
SKIP_KEYS = {"id", "uuid", "guid", "blockid", "lessonid", "itemid", "objectid", "assetid", "componentid", "createdat",
             "updatedat", "createdby", "lastmodified", "lastmodifiedat", "styles", "style", "settings", "layoutsettings",
             "theme", "properties", "metadata", "tracking", "lms", "css", "classes", "class", "classname", "color",
             "backgroundcolor", "padding", "alignment", "align", "variant", "family", "type", "authoring", "version",
             "width", "height", "duration", "loop", "autoplay", "controls", "muted", "hidden", "visible", "size",
             "orientation", "aspectratio", "position", "x", "y", "z", "language", "lang", "font", "fontsize", "weight",
             "slug", "key", "hash", "order", "sort", "index", "columnswidth", "responsive", "default", "sourceid"}
URL_KEYS = {"url", "src", "href", "source", "path", "file", "filename", "asset", "poster", "thumbnail", "imageurl", "videourl", "audiourl", "embedurl", "link", "image", "video", "audio"}
GROUP_KEYS = {"items", "blocks", "panels", "tabs", "cards", "steps", "markers", "questions", "options", "answers",
              "choices", "responses", "feedback", "images", "slides", "scenes", "rows", "columns", "pairs", "labels",
              "categories", "groups", "terms", "elements", "contents", "bullets", "statements", "sections", "points"}


def esc(s):
    return html.escape(str(s), quote=True)


def safe_url(value):
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or any(ord(c) < 32 for c in value):
        return None
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme.lower() in {"https", "http", "mailto"}:
        return value
    return None


class CleanHTML(HTMLParser):
    """Allow only presentational markup, with no scripts, embedded frames or event handlers."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0
        self.stack = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in {"script", "style", "iframe", "object", "embed", "svg", "template"}:
            self.hidden += 1
            return
        if self.hidden:
            return
        if tag not in SAFE_TAGS:
            return
        props = ""
        if tag == "a":
            link = next((val for key, val in attrs if key.lower() == "href"), "")
            u = safe_url(link)
            if u:
                props = ' href="%s" rel="noopener noreferrer" target="_blank"' % esc(u)
        self.parts.append("<%s%s>" % (tag, props))
        if tag not in VOID_TAGS:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in {"script", "style", "iframe", "object", "embed", "svg", "template"}:
            self.hidden = max(0, self.hidden - 1)
            return
        if self.hidden or tag not in SAFE_TAGS or tag in VOID_TAGS:
            return
        if tag in self.stack:
            # Close any badly nested tags to avoid breaking report layout.
            while self.stack:
                t = self.stack.pop()
                self.parts.append("</%s>" % t)
                if t == tag:
                    break

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(esc(data))

    def handle_entityref(self, name):
        self.handle_data(html.unescape("&%s;" % name))

    def handle_charref(self, name):
        self.handle_data(html.unescape("&#%s;" % name))

    def finish(self):
        while self.stack:
            self.parts.append("</%s>" % self.stack.pop())
        return "".join(self.parts)


def rich_text(value):
    s = str(value or "").strip()
    if not s:
        return ""
    if "<" in s and ">" in s:
        parser = CleanHTML()
        try:
            parser.feed(s)
            return parser.finish()
        except (ValueError, AssertionError):
            pass
    return esc(s).replace("\n", "<br>")


def course_like(doc):
    if not isinstance(doc, dict):
        return False
    if isinstance(doc.get("course"), dict):
        c = doc["course"]
        return any(k in c for k in ("lessons", "items", "sections", "title"))
    return "lessons" in doc and isinstance(doc["lessons"], (list, dict))


def parse_json_payload(raw):
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    choices = [raw]
    try:
        choices.extend([zlib.decompress(raw), gzip.decompress(raw)])
    except (zlib.error, OSError):
        pass
    for candidate in choices:
        try:
            obj = json.loads(candidate.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if course_like(obj):
            return obj
        if isinstance(obj, dict):
            for value in obj.values():
                if course_like(value):
                    return value
    return None


def decode_base64(value):
    value = re.sub(r"\s", "", value)
    if len(value) < 24:
        return None
    try:
        raw = base64.b64decode(value.replace("-", "+").replace("_", "/") + "=" * (-len(value) % 4), validate=True)
    except (binascii.Error, ValueError):
        return None
    return parse_json_payload(raw)


def extract_document(text):
    # Some exports contain the JSON object directly.
    doc = parse_json_payload(text.encode("utf-8"))
    if doc:
        return doc
    expressions = [
        r'(?:__jsonp|__resolveJsonp)\s*\(\s*[\'\"][^\'\"]+[\'\"]\s*,\s*[\'\"]([A-Za-z0-9+/_=\-]{24,})[\'\"]',
        r'window\.courseData\s*=\s*(?:deserialize\s*\(\s*)?[\'\"]([A-Za-z0-9+/_=\-]{24,})[\'\"]',
        r'deserialize\s*\(\s*[\'\"]([A-Za-z0-9+/_=\-]{24,})[\'\"]\s*\)',
    ]
    for rx in expressions:
        for match in re.finditer(rx, text):
            result = decode_base64(match.group(1))
            if result:
                return result
    # Last resort: locate quoted base64 literals and verify that they decode into a Rise AST.
    for match in re.finditer(r'[\'\"]([A-Za-z0-9+/_=\-]{150,})[\'\"]', text):
        result = decode_base64(match.group(1))
        if result:
            return result
    return None


def read_course(zf):
    infos = [x for x in zf.infolist() if not x.is_dir()]
    candidates = [x for x in infos if Path(x.filename).name.lower() in
                  {"runtime-data.js", "und.js", "index.html", "course-data.js", "course.json", "data.json"}]
    def priority(info):
        name = Path(info.filename).name.lower()
        return ({"runtime-data.js": 0, "und.js": 1, "course-data.js": 2, "course.json": 3,
                 "index.html": 4, "data.json": 5}.get(name, 9), 0 if "scormcontent/" in info.filename.lower() else 1)
    for info in sorted(candidates, key=priority):
        if info.file_size > MAX_CANDIDATE_SIZE:
            continue
        try:
            content = zf.read(info).decode("utf-8-sig", errors="replace")
            document = extract_document(content)
            if document:
                return document, info.filename
        except (OSError, BadZipFile):
            continue
    raise ValueError("Could not locate a supported Rise course payload. This export may use a newer or compressed format, "
                     "or may not be from Rise. Check for scormcontent/runtime-data.js or scormcontent/index.html.")


def normalize_key(key):
    return re.sub("[^a-z]", "", str(key).lower())


def display_key(key):
    s = re.sub(r"([a-z])([A-Z])", r"\1 \2", str(key)).replace("_", " ").replace("-", " ")
    return s[:1].upper() + s[1:]


def meaningful(value):
    return value is not None and value != "" and value != [] and value != {}


class ReportBuilder:
    def __init__(self, zf, *, embed_video=False, show_raw=True):
        self.zf = zf
        self.names = {i.filename.replace("\\", "/").lower(): i for i in zf.infolist() if not i.is_dir()}
        self.basename_index = {}
        for path, info in self.names.items():
            self.basename_index.setdefault(PurePosixPath(path).name, []).append(info)
        self.asset_cache = {}
        self.total_embedded = 0
        self.embed_video = embed_video
        self.show_raw = show_raw
        self.notices = []
        self.broken_assets = set()

    def resolve(self, src):
        if not isinstance(src, str):
            return None
        s = urllib.parse.unquote(urllib.parse.urlsplit(src.strip()).path).replace("\\", "/")
        s = s.split("#", 1)[0].lstrip("./")
        if not s or ":" in s or ".." in PurePosixPath(s).parts:
            return None
        candidates = [s.lower()]
        if s.startswith("/"):
            candidates.append(s.lstrip("/").lower())
        if not s.lower().startswith("scormcontent/"):
            candidates += ["scormcontent/" + s.lower(), "scormcontent/assets/" + s.lower()]
        for path in candidates:
            if path in self.names:
                return self.names[path]
        # Only use basename fallback when there is exactly one possible file.
        matches = self.basename_index.get(PurePosixPath(s.lower()).name, [])
        return matches[0] if len(matches) == 1 else None

    def embed(self, info, *, video=False):
        ext = Path(info.filename).suffix.lower()
        if ext == ".svg":
            return None  # Avoid SVG active content and external refs.
        cap = MAX_VIDEO_SIZE if video else MAX_IMAGE_SIZE
        if info.file_size > cap or self.total_embedded + info.file_size > MAX_TOTAL_EMBED:
            self.notices.append("Media not embedded due to size limit: " + info.filename)
            return None
        if info.filename in self.asset_cache:
            return self.asset_cache[info.filename]
        mime = mimetypes.guess_type(info.filename)[0] or "application/octet-stream"
        if not (mime.startswith("image/") or (video and mime.startswith(("video/", "audio/")))):
            return None
        data = self.zf.read(info)
        uri = "data:%s;base64,%s" % (mime, base64.b64encode(data).decode("ascii"))
        self.asset_cache[info.filename] = uri
        self.total_embedded += len(data)
        return uri

    def media(self, src, hint="", alt=""):
        if not isinstance(src, str) or not src.strip():
            return ""
        src = src.strip()
        parsed = urllib.parse.urlsplit(src)
        ext = Path(parsed.path).suffix.lower()
        hint = normalize_key(hint)
        is_image = ext in IMAGE_EXT or hint in {"image", "imageurl", "poster", "thumbnail", "photo"}
        is_video = ext in VIDEO_EXT or hint in {"video", "videourl"}
        is_audio = ext in AUDIO_EXT or hint in {"audio", "audiourl"}
        url = safe_url(src)
        if url:
            if is_image:
                return '<figure><img loading="lazy" src="%s" alt="%s"><figcaption>Image URL: <a href="%s" target="_blank" rel="noopener noreferrer">%s</a></figcaption></figure>' % (esc(url), esc(alt), esc(url), esc(url))
            return '<p class="resource"><strong>%s:</strong> <a href="%s" target="_blank" rel="noopener noreferrer">%s</a></p>' % ("Video URL" if is_video else "Audio URL" if is_audio else "External URL", esc(url), esc(url))
        info = self.resolve(src)
        if info:
            if is_image:
                encoded = self.embed(info)
                if encoded:
                    return '<figure><img loading="lazy" src="%s" alt="%s"><figcaption>%s</figcaption></figure>' % (esc(encoded), esc(alt), esc(info.filename))
            if (is_video or is_audio) and self.embed_video:
                encoded = self.embed(info, video=True)
                if encoded:
                    tag = "video" if is_video else "audio"
                    return '<div class="media"><%s controls preload="none" src="%s"></%s><p class="muted">%s</p></div>' % (tag, esc(encoded), tag, esc(info.filename))
            return '<p class="resource"><strong>Packaged %s:</strong> <code>%s</code> <span class="muted">(not embedded)</span></p>' % ("video" if is_video else "audio" if is_audio else "file", esc(info.filename))
        if ext in MEDIA_EXT or hint in URL_KEYS or ("/" in src and " " not in src):
            self.broken_assets.add(src)
            return '<p class="resource"><strong>Media reference:</strong> <code>%s</code> <span class="muted">(not found in ZIP)</span></p>' % esc(src)
        return ""

    def render_value(self, value, key="", depth=0):
        if depth > 20:
            return '<p class="muted">Nested content exceeds display depth; see original data below.</p>'
        norm = normalize_key(key)
        if not meaningful(value):
            return ""
        if isinstance(value, dict):
            # Full media objects typically have src/url/asset plus alt/title/caption.
            media_key = next((k for k in ("src", "url", "imageUrl", "videoUrl", "audioUrl", "href", "path", "filename", "file")
                              if isinstance(value.get(k), str) and value.get(k)), None)
            parts = []
            if media_key:
                hint = key or value.get("type") or media_key
                media_html = self.media(value[media_key], hint, value.get("alt") or value.get("altText") or "")
                if media_html:
                    parts.append(media_html)
            for k, v in value.items():
                nk = normalize_key(k)
                if not meaningful(v) or nk in SKIP_KEYS or k == media_key:
                    continue
                if nk in {"alt", "alttext"} and media_key:
                    # Alternative text is recorded as a label as well as on the image.
                    parts.append('<p><strong>Alternative text:</strong> %s</p>' % rich_text(v))
                    continue
                rendered = self.render_value(v, k, depth + 1)
                if not rendered:
                    continue
                # The heading inside item objects should appear without a redundant 'Heading:' label.
                if nk in {"heading", "title", "question", "prompt"}:
                    parts.append('<div class="item-heading">%s</div>' % rendered)
                elif nk in {"paragraph", "body", "content", "text", "description", "caption", "feedback", "answer"}:
                    parts.append(rendered)
                else:
                    parts.append('<div class="field"><span class="field-name">%s</span>%s</div>' % (esc(display_key(k)), rendered))
            return "".join(parts)
        if isinstance(value, list):
            if not value:
                return ""
            chunks = []
            for idx, item in enumerate(value, 1):
                if not meaningful(item):
                    continue
                content = self.render_value(item, "", depth + 1)
                if content:
                    chunks.append('<li><span class="item-number">%d</span><div>%s</div></li>' % (idx, content))
            return '<ol class="nested">%s</ol>' % "".join(chunks) if chunks else ""
        if isinstance(value, bool):
            if norm in {"correct", "iscorrect", "right", "isright", "valid", "isanswer", "correctanswer"}:
                return '<span class="%s">%s</span>' % ("correct" if value else "muted", "Correct answer" if value else "Not correct")
            return "Yes" if value else "No"
        if isinstance(value, (float, int)):
            return esc(value)
        if isinstance(value, str):
            val = value.strip()
            if not val:
                return ""
            if norm in URL_KEYS or re.match(r"^https?://", val, re.I) or (Path(val.split("?", 1)[0]).suffix.lower() in MEDIA_EXT):
                media_html = self.media(val, norm)
                if media_html:
                    return media_html
            return rich_text(val)
        return esc(value)

    def render_block(self, block, fallback_type="Unknown block"):
        if not isinstance(block, dict):
            return "Unknown", '<p>%s</p>' % rich_text(block), ""
        family = str(block.get("family") or "").strip()
        kind = str(block.get("type") or "").strip()
        variant = str(block.get("variant") or "").strip()
        block_type = " / ".join(x for x in (family, kind) if x) or fallback_type
        if variant and variant.lower() not in {family.lower(), kind.lower()}:
            block_type += " · " + variant
        # Preserve top-level item content in authored order.
        content_keys = [k for k, v in block.items() if normalize_key(k) not in SKIP_KEYS and meaningful(v)]
        pieces = []
        for key in content_keys:
            value = block[key]
            rendered = self.render_value(value, key)
            if not rendered:
                continue
            norm = normalize_key(key)
            if norm in {"items", "blocks", "questions", "answers", "options", "panels", "tabs", "cards", "steps", "markers"}:
                pieces.append(rendered)
            elif norm in {"title", "heading", "paragraph", "content", "description", "text", "body", "statement", "question"}:
                pieces.append(rendered)
            else:
                pieces.append('<section class="field"><span class="field-name">%s</span>%s</section>' % (esc(display_key(key)), rendered))
        if not pieces:
            pieces.append('<span class="muted">No readable content extracted from this block.</span>')
        raw = ''
        if self.show_raw:
            raw = '<details class="raw"><summary>View original block data</summary><pre>%s</pre></details>' % esc(json.dumps(block, indent=2, ensure_ascii=False))
        return block_type, "".join(pieces), raw


def doc_to_html(document, zf, title_hint, *, embed_video=False, show_raw=True):
    course = document.get("course", document)
    builder = ReportBuilder(zf, embed_video=embed_video, show_raw=show_raw)
    title = str(course.get("title") or course.get("name") or title_hint)
    description = course.get("description") or ""
    lessons = course.get("lessons") or course.get("sections") or []
    if isinstance(lessons, dict):
        lessons = list(lessons.values())
    if not isinstance(lessons, list):
        raise ValueError("The course payload did not contain a recognisable lesson list.")
    toc = []
    sections = []
    counter = Counter()
    nblocks = 0
    nlessons = 0
    for i, lesson in enumerate(lessons, 1):
        if isinstance(lesson, str):
            continue
        if not isinstance(lesson, dict):
            continue
        lesson_name = str(lesson.get("title") or lesson.get("name") or "Lesson %d" % i)
        typ = str(lesson.get("type") or lesson.get("family") or "").lower()
        blocks = lesson.get("items") or lesson.get("blocks") or lesson.get("content") or []
        if isinstance(blocks, dict):
            blocks = blocks.get("items") or blocks.get("blocks") or [blocks]
        if not isinstance(blocks, list):
            blocks = []
        quiz_fallback = "Quiz question" if typ == "quiz" else "Unknown block"
        if not blocks:
            for qkey in ("questions", "quiz", "assessment"):
                if qkey in lesson:
                    qdata = lesson[qkey]
                    blocks = qdata if isinstance(qdata, list) else [qdata]
                    quiz_fallback = "Quiz question" if qkey == "questions" else "Quiz / assessment"
                    break
        nlessons += 1
        anchor = "lesson-%d" % i
        toc.append('<li><a href="#%s">%s</a> <span class="muted">(%d blocks)</span></li>' % (anchor, esc(lesson_name), len(blocks)))
        lesson_html = ['<section class="lesson" id="%s"><h2>%d. %s</h2>' % (anchor, nlessons, esc(lesson_name))]
        if typ:
            lesson_html.append('<p class="lesson-kind">Lesson type: %s</p>' % esc(typ))
        lesson_html.append('<table><thead><tr><th>Original Rise block type</th><th>Extracted content</th></tr></thead><tbody>')
        if not blocks:
            lesson_html.append('<tr><td>Section / empty lesson</td><td><span class="muted">No content blocks in the exported lesson data.</span></td></tr>')
        for j, block in enumerate(blocks, 1):
            block_type, content, raw = builder.render_block(block, fallback_type=quiz_fallback)
            counter[block_type] += 1
            nblocks += 1
            lesson_html.append('<tr><td class="typecell"><span class="block-no">%d.%d</span><strong>%s</strong></td><td class="contentcell">%s%s</td></tr>'
                               % (nlessons, j, esc(block_type), content, raw))
        lesson_html.append('</tbody></table></section>')
        sections.append("".join(lesson_html))
    css = """
    :root{font-family:system-ui,-apple-system,'Segoe UI',Arial,sans-serif;color:#202c3c;background:#f6f8fa}
    *{box-sizing:border-box}body{margin:0}main{max-width:1200px;margin:0 auto;padding:30px 24px 80px}
    header{background:#132a45;color:white;padding:26px 30px;border-radius:12px}h1{font-size:clamp(1.5rem,3vw,2.25rem);margin:0 0 10px}header p{margin:0;opacity:.85}
    nav{background:white;border:1px solid #dee4ea;border-radius:10px;padding:18px 24px;margin:22px 0}nav h2{margin-top:0;font-size:1.1rem}nav ol{columns:2;line-height:1.8;padding-left:22px}
    a{color:#145b92;overflow-wrap:anywhere}a:hover{text-decoration-thickness:2px}.stats{display:flex;gap:14px;flex-wrap:wrap;margin:18px 0}
    .stat{background:white;border:1px solid #e0e5eb;border-radius:9px;padding:10px 16px;font-size:.94rem}.stat b{font-size:1.2rem}
    .lesson{margin-top:34px;break-before:page}.lesson h2{font-size:1.48rem;line-height:1.3}.lesson-kind{margin-top:-8px;color:#66778a}
    table{width:100%;border-collapse:collapse;background:white;border:1px solid #dce3ec;table-layout:fixed}
    th{background:#edf2f7;color:#314760;text-align:left;font-size:.88rem;padding:13px;border-bottom:2px solid #cdd7e1}
    th:first-child,td:first-child{width:24%}td{vertical-align:top;padding:16px 15px;border-bottom:1px solid #dae3eb;overflow-wrap:anywhere}
    tbody tr:nth-child(even){background:#fbfcfe}.typecell{font-size:.91rem;color:#365069}.typecell strong{display:block;line-height:1.5}
    .block-no{display:inline-block;padding:3px 7px;margin-bottom:8px;background:#e5edf5;border-radius:4px;font-size:.75rem;font-weight:700}
    .contentcell{width:76%;font-size:.97rem;line-height:1.55}.contentcell p{margin:.3em 0 .8em}.contentcell img{max-width:100%;height:auto;max-height:400px;object-fit:contain;border:1px solid #e1e7ee;border-radius:5px}
    .contentcell video{max-width:100%;max-height:400px}.contentcell audio{max-width:100%}.contentcell figure{margin:10px 0}
    figcaption{font-size:.8rem;color:#667788;margin-top:3px;word-break:break-all}
    .nested{margin:8px 0 8px 4px;padding:0;list-style:none;counter-reset:items}.nested>li{display:flex;gap:10px;padding:9px 0;border-top:1px solid #eef1f5}
    .item-number{background:#e7edf3;color:#445a72;border-radius:20px;min-width:26px;height:26px;text-align:center;line-height:26px;font-size:.79rem;flex-shrink:0}
    .nested>li>div{min-width:0;flex:1}.nested .nested{margin:7px 0 0 0}
    .field{margin:8px 0}.field-name{display:block;font-size:.77rem;letter-spacing:.025em;color:#58708b;font-weight:700;margin-bottom:3px}
    .item-heading{font-weight:650;margin:5px 0}.muted{color:#718093;font-size:.87em}.correct{color:#136c41;font-weight:700}
    .resource{background:#f0f5fa;padding:8px 10px;border-radius:5px}code{font-size:.86em;white-space:break-spaces}
    details.raw{margin-top:17px;border-top:1px dashed #ccd7e3;padding-top:8px}details.raw>summary{color:#536c87;cursor:pointer;font-size:.77rem}
    details.raw pre{max-height:420px;overflow:auto;background:#eff3f8;color:#263645;font-size:.73rem;padding:12px;border-radius:6px;white-space:pre-wrap;word-break:break-word}
    .note{background:#fffdf5;border-left:3px solid #b9883b;padding:11px 15px;margin:18px 0;font-size:.88rem}
    .footer{font-size:.79rem;color:#687b8c;margin-top:28px}.inventory{margin:12px 0 20px;color:#506172}.inventory summary{cursor:pointer}
    .inventory li{margin:4px 0}table table{width:auto;max-width:100%;font-size:.9rem}table table td,table table th{width:auto!important;padding:6px 8px}
    @media(max-width:640px){main{padding:13px}nav ol{columns:1}td{padding:10px}th:first-child,td:first-child{width:29%}}
    @media print{body{background:white}main{max-width:none;padding:0}header{color:#132a45;background:none;border-bottom:2px solid #132a45}nav{display:none}
    .lesson{break-before:page}tr{break-inside:avoid}.contentcell img{max-height:280px}details.raw{display:none}}
    """
    stats = '<div class="stats"><div class="stat"><b>%d</b> lessons/sections</div><div class="stat"><b>%d</b> blocks</div><div class="stat"><b>%d</b> distinct block labels</div></div>' % (nlessons, nblocks, len(counter))
    inventory = '<details class="inventory"><summary>Block-type inventory (%d distinct labels)</summary><ul>%s</ul></details>' % (
        len(counter), "".join('<li>%s: %d</li>' % (esc(k), count) for k, count in counter.most_common()))
    notes = ['This is a static content inventory, not an interactive SCORM player. Quizzes and knowledge checks are expanded for review rather than scored.',
             'Images included in the ZIP are embedded where size permits. Videos/audio are recorded as URLs or packaged file references unless optional media embedding is enabled.',
             'Always check interactive elements, embedded Storyline, custom/code blocks, externally hosted content and older export formats against the original course.']
    if builder.notices:
        notes.extend(sorted(set(builder.notices))[:50])
    if builder.broken_assets:
        notes.append('%d media references were not found inside the ZIP.' % len(builder.broken_assets))
    html_doc = '<!doctype html><html lang="en-AU"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">' \
               '<title>%s — Rise block inventory</title><style>%s</style></head><body><main>' % (esc(title), css)
    html_doc += '<header><h1>%s</h1><p>Articulate Rise SCORM content inventory · generated locally</p></header>' % esc(title)
    if description:
        html_doc += '<div class="note"><strong>Course description</strong><div>%s</div></div>' % rich_text(description)
    html_doc += stats + inventory + '<nav><h2>Contents</h2><ol>%s</ol></nav>' % "".join(toc)
    html_doc += "".join(sections) + '<div class="note"><strong>Extraction notes</strong><ul>%s</ul></div>' % "".join('<li>%s</li>' % esc(n) for n in notes)
    html_doc += '<p class="footer">Produced by Rise SCORM to HTML (standalone local extractor). Original SCORM ZIP is not modified.</p></main></body></html>'
    return html_doc, {"title": title, "lessons": nlessons, "blocks": nblocks, "block_types": dict(counter),
                      "assets_not_found": len(builder.broken_assets), "media_embedded_bytes": builder.total_embedded}


def convert(source, target=None, *, embed_video=False, show_raw=True):
    source = Path(source)
    if not source.is_file():
        raise FileNotFoundError(str(source))
    if target is None:
        target = source.with_name(source.stem + '_content_inventory.html')
    target = Path(target)
    if source.resolve() == target.resolve():
        raise ValueError("The output file must be different from the input ZIP.")
    with ZipFile(source, "r") as zf:
        document, payload = read_course(zf)
        output, stats = doc_to_html(document, zf, source.stem, embed_video=embed_video, show_raw=show_raw)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(output, encoding="utf-8")
    return target, payload, stats


def gui():
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox
        root = tk.Tk()
        root.withdraw()
        source = filedialog.askopenfilename(title="Select Articulate Rise SCORM ZIP", filetypes=[("SCORM ZIP files", "*.zip"), ("All files", "*.*")])
        if not source:
            return
        suggested = Path(source).stem + "_content_inventory.html"
        output = filedialog.asksaveasfilename(title="Save single HTML content inventory", defaultextension=".html",
                                               initialfile=suggested, filetypes=[("HTML document", "*.html")])
        if not output:
            return
        target, payload, stats = convert(source, output)
        messagebox.showinfo("Conversion completed", "Saved: %s\n\nLessons: %d\nBlocks: %d\nSource: %s" % (
            target, stats["lessons"], stats["blocks"], payload))
        print("Saved:", target)
    except Exception as exc:
        try:
            from tkinter import messagebox
            messagebox.showerror("Conversion failed", str(exc))
        except Exception:
            pass
        print("Error:", exc, file=sys.stderr)
        sys.exit(1)


def main():
    ap = argparse.ArgumentParser(description="Convert an Articulate Rise SCORM ZIP to one locally viewable HTML block inventory.")
    ap.add_argument("zip", nargs="?", help="Path to exported Rise SCORM .zip. Omit to choose using a file picker.")
    ap.add_argument("-o", "--output", help="Destination .html; defaults to <zipname>_content_inventory.html")
    ap.add_argument("--embed-video", action="store_true", help="Embed eligible packaged videos/audio as base64 (can create enormous HTML files).")
    ap.add_argument("--no-raw", action="store_true", help="Omit collapsible original block JSON for a smaller HTML file.")
    args = ap.parse_args()
    if not args.zip:
        return gui()
    try:
        target, payload, stats = convert(args.zip, args.output, embed_video=args.embed_video, show_raw=not args.no_raw)
    except (OSError, BadZipFile, ValueError) as e:
        ap.exit(1, "Conversion failed: %s\n" % e)
    print("Saved HTML: %s\nPayload found: %s\nLessons/sections: %d\nBlocks: %d\nEmbedded media: %.1f MB\nUnresolved media references: %d" % (
        target, payload, stats["lessons"], stats["blocks"], stats["media_embedded_bytes"]/1024**2, stats["assets_not_found"]))


if __name__ == "__main__":
    main()
