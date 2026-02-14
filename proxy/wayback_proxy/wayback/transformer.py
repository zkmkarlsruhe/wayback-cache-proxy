"""Content transformation to clean up Wayback Machine artifacts."""

import re
from typing import Optional


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
            return self._transform_html(content)
        elif "text/css" in content_type:
            return self._transform_css(content)
        elif "javascript" in content_type:
            return self._transform_js(content)
        else:
            return content

    def _transform_html(self, content: bytes) -> bytes:
        """Transform HTML content."""
        try:
            html = content.decode("utf-8", errors="replace")
        except Exception:
            return content

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

        return html.encode("utf-8")

    def _transform_css(self, content: bytes) -> bytes:
        """Transform CSS content."""
        try:
            css = content.decode("utf-8", errors="replace")
        except Exception:
            return content

        if self.fix_asset_urls:
            css = self._fix_css_urls(css)

        return css.encode("utf-8")

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
        try:
            html = html_content.decode("utf-8", errors="replace")
        except Exception:
            return html_content

        # Insert after <body ...> tag
        body_match = re.search(r"<body[^>]*>", html, re.IGNORECASE)
        if body_match:
            insert_pos = body_match.end()
            html = html[:insert_pos] + "\n" + bar_html + "\n" + html[insert_pos:]
        else:
            # No body tag — prepend
            html = bar_html + "\n" + html

        return html.encode("utf-8")

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
