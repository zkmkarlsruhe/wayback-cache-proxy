"""Charset round-trip of the transformer and follow_redirects config wiring.

Run: cd proxy && python -m pytest tests/
"""

from wayback_proxy.config import Config
from wayback_proxy.wayback import ContentTransformer, WaybackClient, build_backend
from wayback_proxy.wayback.transformer import detect_charset

WB = b"http://web.archive.org/web/20010101000000/"
JA = "まるびうみ大学 情報デザイン学科"  # Japanese body text


def page(charset_decl: str, enc: str) -> bytes:
    """A period page with a Wayback-prefixed link and Japanese text."""
    return (
        f"<html><head>{charset_decl}<title>{JA}</title></head>\n"
        f'<body><a href="{WB.decode()}http://www.idd.tamabi.ac.jp/a.html">{JA}</a>\n'
        f'<img src="/web/20010101000000im_/http://www.idd.tamabi.ac.jp/b.gif">'
        f"<p>{JA}</p></body></html>"
    ).encode(enc)


def expected(raw: bytes) -> bytes:
    """Same bytes, only the Wayback prefixes rewritten."""
    return raw.replace(WB, b"").replace(b"/web/20010101000000im_/http://", b"http://")


def test_shift_jis_meta_round_trip():
    raw = page('<meta http-equiv="Content-Type" content="text/html; charset=Shift_JIS">', "shift_jis")
    out = ContentTransformer().transform(raw, "text/html")
    assert out == expected(raw)
    assert out.decode("shift_jis").count(JA) == 3


def test_shift_jis_wrong_http_charset_falls_through():
    # Archived header claims utf-8, body is Shift_JIS (the idd.tamabi.ac.jp case)
    raw = page("", "shift_jis")
    out = ContentTransformer().transform(raw, "text/html; charset=utf-8")
    assert out == expected(raw)
    assert "�" not in out.decode("shift_jis")


def test_euc_jp_sniffed_round_trip():
    raw = page("", "euc_jp")
    assert detect_charset(raw, "text/html") == "euc_jp"
    out = ContentTransformer().transform(raw, "text/html")
    assert out == expected(raw)


def test_iso2022jp_sniffed_round_trip():
    raw = page("", "iso2022_jp")
    assert detect_charset(raw, "text/html") == "iso2022_jp"
    assert ContentTransformer().transform(raw, "text/html") == expected(raw)


def test_undecodable_passes_through_untouched():
    raw = b'<html><body>\x81 <a href="' + WB + b'http://x.jp/">x</a></body></html>'
    assert detect_charset(raw, "text/html") is None
    assert ContentTransformer().transform(raw, "text/html") == raw


def test_header_bar_is_ascii_and_page_bytes_untouched():
    raw = page("", "shift_jis")
    out = ContentTransformer().inject_header_bar(raw, '<div id="bar">über 日</div>')
    bar = b'\n<div id="bar">&#252;ber &#26085;</div>\n'
    i = raw.index(b"<body>") + len(b"<body>")
    assert out == raw[:i] + bar + raw[i:]


def test_follow_redirects_default_off():
    assert Config().transform.follow_redirects is False


def test_follow_redirects_env(monkeypatch):
    for value, want in (("1", True), ("true", True), ("On", True), ("0", False), ("no", False)):
        monkeypatch.setenv("FOLLOW_REDIRECTS", value)
        assert Config.from_env().transform.follow_redirects is want
    monkeypatch.delenv("FOLLOW_REDIRECTS")
    assert Config.from_env().transform.follow_redirects is False


def test_follow_redirects_yaml_reaches_every_wayback_client(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        "transform:\n  follow_redirects: true\n"
        "backends:\n  chain:\n    - type: cache\n    - type: wayback\n"
    )
    config = Config.from_yaml(str(cfg_file))
    assert config.transform.follow_redirects is True

    chains = [build_backend(config, cache=None)]
    config.backends.chain = []  # default chain
    chains.append(build_backend(config, cache=None))
    config.backends.chain = [{"type": "bogus"}]  # empty-after-config fallback chain
    chains.append(build_backend(config, cache=None))

    for chain in chains:
        clients = [b for b in chain._backends if isinstance(b, WaybackClient)]
        assert len(clients) == 1 and clients[0].follow_redirects is True
