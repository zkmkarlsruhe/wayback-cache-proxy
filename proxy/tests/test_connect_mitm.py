"""CONNECT MITM: the ClientHello sent right after the 200 must reach the TLS layer.

Real TLS handshake against a freshly minted leaf (first request to an uncached host).
Run: cd proxy && python -m pytest tests/
"""

import asyncio
import socket
import ssl

from wayback_proxy.https import CertAuthority
from wayback_proxy.server import ProxyServer

HOST = "uncached.example.org"


def _server(tmp_path):
    srv = ProxyServer.__new__(ProxyServer)  # skip redis/backends: only the CONNECT path is under test
    srv.ca = CertAuthority(str(tmp_path / "ca.crt"), str(tmp_path / "ca.key"), default_leaf_hash="sha256")

    async def process(reader, writer, method, target, headers, scheme="http", default_host=None):
        body = f"{scheme} {default_host} {method} {target}".encode()
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\nConnection: close\r\n\r\n%s" % (len(body), body))
        await writer.drain()

    async def send_error(writer, code, message):
        writer.write(f"HTTP/1.1 {code} X\r\nConnection: close\r\n\r\n".encode())
        await writer.drain()
        writer.close()

    srv._process_request = process
    srv._send_error = send_error
    return srv


def _connect(port):
    sock = socket.create_connection(("127.0.0.1", port), timeout=10)
    sock.sendall(f"CONNECT {HOST}:443 HTTP/1.1\r\nHost: {HOST}:443\r\n\r\n".encode())
    head = b""
    while b"\r\n\r\n" not in head:
        chunk = sock.recv(1)  # byte-wise: nothing past the 200 may be consumed here
        if not chunk:
            break
        head += chunk
    return sock, head


def _client(port, cafile, events):
    sock, head = _connect(port)
    assert head.startswith(b"HTTP/1.1 200"), head
    events.append("200")
    ctx = ssl.create_default_context(cafile=cafile)
    tls = ctx.wrap_socket(sock, server_hostname=HOST)  # ClientHello goes out immediately
    tls.sendall(f"GET /x HTTP/1.1\r\nHost: {HOST}\r\n\r\n".encode())
    data = b""
    while chunk := tls.recv(4096):
        data += chunk
    tls.close()
    return data


def _run(srv, fn, *args):
    async def main():
        server = await asyncio.start_server(srv._handle_client, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            return await asyncio.wait_for(asyncio.to_thread(fn, port, *args), 20)
        finally:
            server.close()

    return asyncio.run(main())


def test_clienthello_right_after_200_completes_handshake(tmp_path):
    srv = _server(tmp_path)
    assert srv.ca._ctx_cache == {}  # leaf not minted yet
    events = []
    mint = srv.ca.server_context

    def recording(host, leaf_hash=None):
        ctx = mint(host, leaf_hash)
        events.append("mint")
        return ctx

    srv.ca.server_context = recording
    data = _run(srv, _client, str(tmp_path / "ca.crt"), events)
    assert events == ["mint", "200"]  # leaf ready before the client may send its ClientHello
    assert data.startswith(b"HTTP/1.1 200 OK"), data
    assert data.endswith(f"https {HOST} GET /x".encode())


def test_leaf_failure_answers_502_not_200(tmp_path):
    srv = _server(tmp_path)

    def boom(host, leaf_hash=None):
        raise RuntimeError("mint failed")

    srv.ca.server_context = boom
    _, head = _run(srv, lambda port: _connect(port))
    assert head.startswith(b"HTTP/1.1 502"), head
