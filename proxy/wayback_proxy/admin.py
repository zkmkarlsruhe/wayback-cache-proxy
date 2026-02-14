"""Admin interface — serves /_admin/ HTML pages for crawl seed CRUD."""

import html
from typing import Tuple
from urllib.parse import parse_qs

from .cache import Cache


class AdminHandler:
    """Handles /_admin/* HTTP requests."""

    def __init__(self, cache: Cache):
        self.cache = cache

    async def handle(
        self, method: str, path: str, headers: dict, body: bytes,
    ) -> Tuple[int, str, bytes]:
        """Dispatch request. Returns (status_code, content_type, body)."""
        if method == "GET" and path in ("/_admin/", "/_admin"):
            return await self._dashboard()

        if method == "POST":
            form = self._parse_form(body)
            if path == "/_admin/crawl/add":
                return await self._add_seed(form)
            if path == "/_admin/crawl/remove":
                return await self._remove_seed(form)
            if path == "/_admin/crawl/start":
                return "START_CRAWL"  # signal to server
            if path == "/_admin/crawl/stop":
                return await self._stop_crawl()
            if path == "/_admin/crawl/clear-log":
                return await self._clear_log()
            if path == "/_admin/cache/clear-hot":
                return await self._clear_hot()
            if path == "/_admin/cache/delete":
                return await self._delete_url(form)
            if path == "/_admin/crawl/recrawl":
                return "RECRAWL"  # signal to server

        return (404, "text/html; charset=utf-8", b"<h1>404 Not Found</h1>")

    # ── routes ────────────────────────────────────────────────────────

    async def _dashboard(self) -> Tuple[int, str, bytes]:
        seeds = await self.cache.get_seeds()
        status = await self.cache.get_crawl_status()
        log_lines = await self.cache.get_crawl_log(100)
        stats = await self.cache.get_stats()

        state = status.get("state", "idle")
        progress = status.get("progress", {})

        # Seeds table rows
        seed_rows = ""
        for url, depth in seeds:
            esc_url = html.escape(url, quote=True)
            seed_rows += (
                f'<tr>'
                f'<td style="padding:4px 8px">{esc_url}</td>'
                f'<td style="padding:4px 8px;text-align:center">{depth}</td>'
                f'<td style="padding:4px 8px">'
                f'<form method="POST" action="/_admin/crawl/remove" style="margin:0">'
                f'<input type="hidden" name="url" value="{esc_url}">'
                f'<input type="submit" value="Remove" style="'
                f'background:#802020;color:#fff;border:1px solid #a04040;'
                f'padding:2px 8px;cursor:pointer">'
                f'</form></td>'
                f'</tr>'
            )
        if not seeds:
            seed_rows = (
                '<tr><td colspan="3" style="padding:8px;color:#888">'
                'No seeds configured.</td></tr>'
            )

        # Progress info
        progress_html = ""
        if progress:
            fetched = progress.get("fetched", 0)
            total = progress.get("total", 0)
            errs = progress.get("errors", 0)
            cur = html.escape(progress.get("current_url", ""), quote=True)
            progress_html = (
                f'<p>Fetched: {fetched} / {total} &nbsp; Errors: {errs}</p>'
            )
            if cur:
                progress_html += f'<p>Current: <code>{cur}</code></p>'

        # State badge color
        state_color = {"idle": "#888", "running": "#4a4", "stopping": "#c84"}.get(
            state, "#888"
        )

        # Log
        log_html = html.escape("\n".join(log_lines)) if log_lines else "(empty)"

        # Crawl buttons
        if state == "running":
            crawl_buttons = (
                '<form method="POST" action="/_admin/crawl/stop" style="display:inline">'
                '<input type="submit" value="Stop Crawl" style="'
                'background:#804020;color:#fff;border:1px solid #a06040;'
                'padding:4px 12px;cursor:pointer;margin-right:8px">'
                '</form>'
            )
        else:
            crawl_buttons = (
                '<form method="POST" action="/_admin/crawl/start" style="display:inline">'
                '<input type="submit" value="Start Crawl" style="'
                'background:#206040;color:#fff;border:1px solid #40a060;'
                'padding:4px 12px;cursor:pointer;margin-right:8px">'
                '</form>'
                '<form method="POST" action="/_admin/crawl/recrawl" style="display:inline">'
                '<input type="submit" value="Recrawl (force)" style="'
                'background:#604020;color:#fff;border:1px solid #906030;'
                'padding:4px 12px;cursor:pointer;margin-right:8px">'
                '</form>'
            )

        # Cache stats
        curated_n = stats.get("curated_count", 0)
        hot_n = stats.get("hot_count", 0)

        page = f"""\
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<noscript><meta http-equiv="refresh" content="5"></noscript>
<title>Wayback Proxy Admin</title>
<style>
body {{ background:#0e0e1a; color:#e0e0e0; font-family:monospace; margin:20px; }}
h1 {{ color:#c0c0ff; }}
h2 {{ color:#a0a0d0; margin-top:24px; }}
table {{ border-collapse:collapse; }}
table th, table td {{ border:1px solid #404060; }}
th {{ background:#1a1a2e; padding:4px 8px; }}
input[type=text] {{
  background:#12122a; color:#e0e0e0; border:1px solid #505070;
  padding:4px 8px; font-family:monospace; width:400px;
}}
pre {{
  background:#0a0a16; border:1px solid #303050; padding:8px;
  max-height:300px; overflow-y:auto; font-size:12px; white-space:pre-wrap;
}}
a {{ color:#8080ff; }}
</style>
</head>
<body>
<h1 style="display:inline">Wayback Proxy Admin</h1>
<button id="autoRefreshBtn" style="margin-left:16px;background:#333;color:#ccc;border:1px solid #555;\
padding:4px 12px;cursor:pointer;font-family:monospace;font-size:12px;vertical-align:middle">\
Auto-Refresh: OFF</button>

<h2>Crawl Seeds</h2>
<table>
<tr><th>URL</th><th>Depth</th><th></th></tr>
<tbody id="seedRows">{seed_rows}</tbody>
</table>

<form method="POST" action="/_admin/crawl/add" style="margin-top:8px">
<input type="text" name="url" placeholder="http://example.com  or  http://example.com|3">
<input type="submit" value="Add Seed" style="background:#203060;color:#fff;\
border:1px solid #406090;padding:4px 12px;cursor:pointer">
</form>

<h2>Crawl Status</h2>
<div id="crawlStatus">
<p>State: <strong style="color:{state_color}">{state}</strong></p>
{progress_html}
{crawl_buttons}
</div>

<h2>Crawl Log</h2>
<form method="POST" action="/_admin/crawl/clear-log" style="margin-bottom:4px">
<input type="submit" value="Clear Log" style="background:#333;color:#ccc;\
border:1px solid #555;padding:2px 8px;cursor:pointer">
</form>
<pre id="crawlLog">{log_html}</pre>

<h2>Cache</h2>
<div id="cacheStatus">
<p>Curated: <strong>{curated_n}</strong> &nbsp; Hot: <strong>{hot_n}</strong></p>
</div>
<form method="POST" action="/_admin/cache/delete" style="margin-top:8px">
<input type="text" name="url" placeholder="http://example.com/page.html">
<input type="submit" value="Delete from Cache" style="background:#802020;color:#fff;\
border:1px solid #a04040;padding:4px 12px;cursor:pointer">
</form>
<form method="POST" action="/_admin/cache/clear-hot" style="margin-top:8px;display:inline">
<input type="submit" value="Clear All Hot Cache" style="background:#802020;color:#fff;\
border:1px solid #a04040;padding:4px 12px;cursor:pointer"\
 onclick="return confirm('Clear all hot cache entries?')">
</form>

<script>
<!--
var btn=document.getElementById("autoRefreshBtn");
if(btn){{
  var ids=["seedRows","crawlStatus","crawlLog","cacheStatus"];
  var timer=null;
  var on=false;

  function wbUpdate(){{
    var xhr;
    if(window.XMLHttpRequest){{
      xhr=new XMLHttpRequest();
    }}else{{
      try{{ xhr=new ActiveXObject("Microsoft.XMLHTTP"); }}catch(e){{ return; }}
    }}
    xhr.open("GET","/_admin/",true);
    xhr.onreadystatechange=function(){{
      if(xhr.readyState!=4||xhr.status!=200) return;
      var tmp=document.createElement("div");
      tmp.innerHTML=xhr.responseText;
      for(var i=0;i<ids.length;i++){{
        var live=document.getElementById(ids[i]);
        if(!live) continue;
        var all=tmp.getElementsByTagName("*");
        for(var j=0;j<all.length;j++){{
          if(all[j].id==ids[i]){{
            live.innerHTML=all[j].innerHTML;
            break;
          }}
        }}
      }}
    }};
    xhr.send(null);
  }}

  btn.onclick=function(){{
    if(on){{
      on=false;
      btn.style.background="#333";
      btn.style.borderColor="#555";
      btn.innerHTML="Auto-Refresh: OFF";
      if(timer) clearInterval(timer);
      timer=null;
    }}else{{
      on=true;
      btn.style.background="#206040";
      btn.style.borderColor="#40a060";
      btn.innerHTML="Auto-Refresh: ON";
      wbUpdate();
      timer=setInterval(wbUpdate,5000);
    }}
  }};
}}
// -->
</script>
</body>
</html>"""

        return (200, "text/html; charset=utf-8", page.encode("utf-8"))

    async def _add_seed(self, form: dict) -> Tuple[int, str, bytes]:
        raw = form.get("url", "").strip()
        if not raw:
            return self._redirect("/_admin/")

        # Accept "url|depth" or just "url" (default depth 1)
        if "|" in raw:
            url, _, depth_str = raw.rpartition("|")
            try:
                depth = max(0, int(depth_str))
            except ValueError:
                depth = 1
        else:
            url = raw
            depth = 1

        url = url.strip()
        if url:
            await self.cache.add_seed(url, depth)
        return self._redirect("/_admin/")

    async def _remove_seed(self, form: dict) -> Tuple[int, str, bytes]:
        url = form.get("url", "").strip()
        if url:
            await self.cache.remove_seed(url)
        return self._redirect("/_admin/")

    async def _stop_crawl(self) -> Tuple[int, str, bytes]:
        status = await self.cache.get_crawl_status()
        if status.get("state") == "running":
            await self.cache.set_crawl_status(
                "stopping", status.get("progress", {}),
            )
        return self._redirect("/_admin/")

    async def _clear_log(self) -> Tuple[int, str, bytes]:
        await self.cache.clear_crawl_log()
        return self._redirect("/_admin/")

    async def _clear_hot(self) -> Tuple[int, str, bytes]:
        await self.cache.clear_hot()
        return self._redirect("/_admin/")

    async def _delete_url(self, form: dict) -> Tuple[int, str, bytes]:
        url = form.get("url", "").strip()
        if url:
            await self.cache.delete(url, tier="both")
        return self._redirect("/_admin/")

    # ── helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _parse_form(body: bytes) -> dict:
        """Parse application/x-www-form-urlencoded body."""
        if not body:
            return {}
        qs = parse_qs(body.decode("utf-8", errors="replace"), keep_blank_values=True)
        return {k: v[0] for k, v in qs.items()}

    @staticmethod
    def _redirect(location: str) -> Tuple[int, str, bytes]:
        """303 See Other redirect."""
        return (303, location, b"")
