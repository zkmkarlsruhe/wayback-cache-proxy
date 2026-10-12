"""Content transformation to clean up Wayback Machine artifacts."""

import codecs
import re
from typing import Optional

_RE_HTTP_CHARSET = re.compile(r'charset\s*=\s*["\']?([\w.:-]+)', re.IGNORECASE)
# Covers <meta charset="x">, <meta http-equiv=Content-Type content="...; charset=x">
# and CSS @charset "x"; ASCII-only, so it works on the raw bytes of any
# ASCII-compatible encoding (and on ISO-2022-JP, whose markup is plain ASCII).
_RE_DOC_CHARSET = re.compile(
    rb'(?:<meta[^>]+charset|@charset)\s*=?\s*["\']?\s*([\w.:-]+)', re.IGNORECASE
)
# Sniff order for undeclared pages. ISO-2022-JP is 7-bit and would pass as
# UTF-8, so it is only tried when its escape sequences are present. EUC-JP
# before Shift_JIS: EUC text usually also decodes as Shift_JIS (halfwidth
# katakana), Shift_JIS text almost never decodes as EUC-JP.
_SNIFF_ISO2022 = ("iso2022_jp", "iso2022_jp_ext", "iso2022_jp_2")
_SNIFF_8BIT = ("utf-8", "euc_jp", "shift_jis", "cp932")


def detect_charset(content: bytes, content_type: str = "") -> Optional[str]:
    """Return a codec that decodes *content* losslessly, or None.

    Candidates in order: HTTP Content-Type charset, <meta>/@charset in the
    document, then a sniff for ISO-2022-JP / UTF-8 / EUC-JP / Shift_JIS.
    A candidate only wins if decoding is strict AND re-encoding gives back
    the exact same bytes, so a transform round-trip never alters text it
    did not rewrite. Declared charsets are checked too, because archived
    headers and meta tags are often wrong (e.g. "utf-8" on a Shift_JIS page).
    """
    candidates = []
    if m := _RE_HTTP_CHARSET.search(content_type or ""):
        candidates.append(m.group(1))
    if m := _RE_DOC_CHARSET.search(content[:8192]):
        candidates.append(m.group(1).decode("ascii"))
    if b"\x1b$" in content or b"\x1b(" in content:
        candidates.extend(_SNIFF_ISO2022)
    candidates.extend(_SNIFF_8BIT)

    for name in candidates:
        try:
            codec = codecs.lookup(name).name
            if content.decode(codec).encode(codec) == content:
                return codec
        except (LookupError, UnicodeError):
            continue
    # ponytail: no Latin-1 catch-all, so undeclared Western pages with high
    # bytes pass through untransformed; add "cp1252"/"latin-1" to
    # _SNIFF_8BIT if that shows up in practice.
    return None


class ContentTransformer:
    """Transform content fetched from Wayback Machine."""

    def __init__(
        self,
        remove_toolbar: bool = True,
        remove_scripts: bool = True,
        fix_base_tags: bool = True,
        fix_asset_urls: bool = True,
        normalize_links: bool = True,
    ):
        self.remove_toolbar = remove_toolbar
        self.remove_scripts = remove_scripts
        self.fix_base_tags = fix_base_tags
        self.fix_asset_urls = fix_asset_urls
        self.normalize_links = normalize_links

    def transform(self, content: bytes, content_type: str) -> bytes:
        """
        Transform content based on type.

        Args:
            content: Raw content bytes
            content_type: MIME type

        Returns:
            Transformed content
        """
        if "text/html" in content_type:
            return self._transform_html(content, content_type)
        elif "text/css" in content_type:
            return self._transform_css(content, content_type)
        elif "javascript" in content_type:
            return self._transform_js(content)
        else:
            return content

    def _transform_html(self, content: bytes, content_type: str = "") -> bytes:
        """Transform HTML content in its own charset (bytes out = same encoding)."""
        charset = detect_charset(content, content_type)
        if charset is None:
            # Undecodable: never substitute U+FFFD into a period page.
            print("[TRANSFORM] unknown charset, passing through untransformed")
            return content
        html = content.decode(charset)

        if self.remove_toolbar:
            html = self._remove_wayback_toolbar(html)

        if self.remove_scripts:
            html = self._remove_wayback_scripts(html)

        if self.fix_base_tags:
            html = self._fix_base_tags(html)

        if self.fix_asset_urls:
            html = self._fix_asset_urls(html)

        if self.normalize_links:
            html = self._normalize_links(html)

        try:
            return html.encode(charset)
        except UnicodeError:
            return content

    def _transform_css(self, content: bytes, content_type: str = "") -> bytes:
        """Transform CSS content in its own charset."""
        charset = detect_charset(content, content_type)
        if charset is None:
            return content
        css = content.decode(charset)

        if self.fix_asset_urls:
            css = self._fix_css_urls(css)

        try:
            return css.encode(charset)
        except UnicodeError:
            return content

    def _transform_js(self, content: bytes) -> bytes:
        """Transform JavaScript content - minimal changes."""
        return content

    def _remove_wayback_toolbar(self, html: str) -> str:
        """Remove Wayback Machine toolbar HTML."""
        # Remove toolbar insert
        html = re.sub(
            r"<!-- BEGIN WAYBACK TOOLBAR INSERT -->.*?<!-- END WAYBACK TOOLBAR INSERT -->",
            "",
            html,
            flags=re.DOTALL | re.IGNORECASE,
        )

        # Remove footer comments
        html = re.sub(
            r"<!--\s*FILE ARCHIVED ON.*$",
            "",
            html,
            flags=re.DOTALL,
        )

        return html

    def _remove_wayback_scripts(self, html: str) -> str:
        """Remove Wayback Machine injected scripts and stylesheets."""
        # Remove the entire pre-toolbar block in one pass (from richardg867):
        # This catches the <script> includes, inline __wm config, and the
        # "End Wayback Rewrite JS Include" comment as a single block.
        html = re.sub(
            r'(?:<!-- is_embed=True -->\r?\n?)?'
            r'<script (?:type="text/javascript" )?src="[^"]*/_static/js/'
            r'.*?<!-- End Wayback Rewrite JS Include -->\r?\n?',
            "",
            html,
            count=1,
            flags=re.DOTALL,
        )

        # Remove any remaining individual script includes
        html = re.sub(
            r'<script[^>]*src="[^"]*/_static/js/[^"]*"[^>]*>.*?</script>',
            "",
            html,
            flags=re.DOTALL | re.IGNORECASE,
        )

        # Remove inline wayback scripts
        html = re.sub(
            r"<script[^>]*>.*?__wm\..*?</script>",
            "",
            html,
            flags=re.DOTALL | re.IGNORECASE,
        )

        # Remove wombat/client rewrite scripts
        html = re.sub(
            r'<script[^>]*src="[^"]*wombat\.js[^"]*"[^>]*>.*?</script>',
            "",
            html,
            flags=re.DOTALL | re.IGNORECASE,
        )

        # Remove injected web-static.archive.org stylesheets
        html = re.sub(
            r'<link[^>]*href="[^"]*web-static\.archive\.org[^"]*"[^>]*/?\s*>',
            "",
            html,
            flags=re.IGNORECASE,
        )

        # Remove any remaining "End Wayback Rewrite JS Include" comments
        html = re.sub(
            r"<!--\s*End Wayback Rewrite JS Include\s*-->\r?\n?",
            "",
            html,
            flags=re.IGNORECASE,
        )

        return html

    def _fix_base_tags(self, html: str) -> str:
        """Fix base tags pointing to web.archive.org."""
        html = re.sub(
            r'(<base\s+[^>]*href=["\']?)(?:https?:)?//web\.archive\.org/web/\d+[a-z_]*/(?:https?://)?',
            r"\1http://",
            html,
            flags=re.IGNORECASE,
        )
        return html

    def _fix_asset_urls(self, html: str) -> str:
        """Fix asset URLs with Wayback prefixes."""
        # Remove /web/TIMESTAMP/ or /web/TIMESTAMPxx_/ prefixes
        html = re.sub(
            r'(?:https?:)?//web\.archive\.org/web/\d+[a-z_]*/',
            "",
            html,
        )

        # Fix relative /web/ URLs
        html = re.sub(
            r'/web/\d+[a-z_]*/(?:https?://)?',
            "http://",
            html,
        )

        return html

    def _normalize_links(self, html: str) -> str:
        """Normalize all links to remove Wayback artifacts."""
        # Already handled by _fix_asset_urls for most cases
        # Additional cleanup for edge cases

        # Fix double protocols
        html = re.sub(r"http://https?://", "http://", html)
        html = re.sub(r"https://https?://", "https://", html)

        return html

    def inject_header_bar(self, html_content: bytes, bar_html: str) -> bytes:
        """Inject the header bar snippet into HTML content after <body>.

        Args:
            html_content: The transformed HTML bytes.
            bar_html: Rendered header bar HTML string.

        Returns:
            Modified HTML bytes with bar injected.
        """
        # Works on bytes, never decodes the page: the bar is encoded as pure
        # ASCII (non-ASCII as &#NNNN; references), which is valid in every
        # ASCII-compatible charset and in ISO-2022-JP's ASCII mode around tags.
        bar = bar_html.encode("ascii", errors="xmlcharrefreplace")

        # Insert after <body ...> tag
        body_match = re.search(rb"<body[^>]*>", html_content, re.IGNORECASE)
        if body_match:
            insert_pos = body_match.end()
            return html_content[:insert_pos] + b"\n" + bar + b"\n" + html_content[insert_pos:]
        # No body tag — prepend
        return bar + b"\n" + html_content

    def _fix_css_urls(self, css: str) -> str:
        """Fix URLs in CSS content."""
        # Fix url() references
        css = re.sub(
            r'url\(["\']?(?:https?:)?//web\.archive\.org/web/\d+[a-z_]*/([^)"\']+)["\']?\)',
            r'url("\1")',
            css,
        )
        # Fix @import statements
        css = re.sub(
            r'@import\s+(?:url\s*\()?\s*["\']?(?:https?:)?//web\.archive\.org/web/\d+[a-z_]*/([^"\')\s]+)["\']?\s*\)?',
            r'@import url("\1")',
            css,
        )
        # Fix relative /web/ URLs in CSS
        css = re.sub(
            r'url\(["\']?/web/\d+[a-z_]*/(?:https?://)?([^)"\']+)["\']?\)',
            r'url("\1")',
            css,
        )
        return css
