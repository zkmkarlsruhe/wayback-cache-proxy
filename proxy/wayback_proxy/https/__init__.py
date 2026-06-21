"""HTTPS MITM support — a trusted-CA man-in-the-middle for the proxy.

The conservation use-case has full control of the guest VM, so instead of
tunnelling CONNECT opaquely (which would bypass the whole archive backend chain)
we terminate TLS *at the proxy* with on-the-fly leaf certificates signed by a
per-stack root CA the guest is made to trust. The decrypted request then re-enters
the normal proxy pipeline, so HTTPS gets the exact same archive resolution, cache,
throttle and transform as HTTP.

This module owns:
  * loading/generating the persisted root CA(s) (``ca.crt`` / ``ca.key``),
  * minting + caching short per-hostname leaf certs signed by that CA,
  * building (and caching) an ``ssl.SSLContext`` per hostname to wrap the
    accepted client socket as the TLS *server*.

The CA cert (``ca.crt``) is the file that gets installed into the guest's trust
stores. Old guests (e.g. Windows XP / Firefox 52) speak TLS 1.0–1.2 with RSA
ciphers, so the leaf certs are RSA-2048 and the server context is left at
permissive defaults (no TLS 1.3-only restriction).

Per-guest-OS cert profile (research/30)
---------------------------------------
**Windows 2000 and XP pre-SP3 have no SHA-2 in CryptoAPI** — they cannot compute
the hash to verify a SHA-256 signature, so a SHA-256-signed chain fails at the
hash layer regardless of trust. Those guests need a **SHA-1-signed CA + SHA-1
leaves** (RSA PKCS#1 v1.5; SHA-1 is present in CryptoAPI back to Win2000). SHA-1
still validates on modern Windows for a *manually-installed* root (the 2017 SHA-1
distrust hits only Microsoft-trusted-program roots — Advisory 4010323), so SHA-1
is the safe universal fallback.

So the proxy maintains **two stack root CAs** — ``ca-sha1.{crt,key}`` and
``ca-sha256.{crt,key}`` — and mints each leaf with the hash matching the active
profile, signed by the CA whose hash that guest can validate. The controller
selects the profile per project (SNI carries no guest OS) and installs the
matching CA into the guest. ``ca_cert_path`` stays the SHA-256 CA for back-compat;
``ca-sha1.crt`` sits beside it.
"""

from __future__ import annotations

import datetime
import ipaddress
import ssl
import threading
from pathlib import Path
from typing import Optional

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


_CA_ORG = "ZKM Conservation"
# Distinct CN per hash so the two roots are unambiguous in a guest's trust list.
_CA_CN = {
    "sha1": "Wayback Cache Proxy MITM Root CA (SHA-1)",
    "sha256": "Wayback Cache Proxy MITM Root CA",
}

# Supported signature hashes -> cryptography hash instance factory.
_HASHES = {"sha1": hashes.SHA1, "sha256": hashes.SHA256}

# Cipher floor per profile. `@SECLEVEL=0` re-permits what stock OpenSSL compiled
# in (SHA-1/RSA-CBC); an explicit list makes sure 3DES/AES-CBC is actually offered
# rather than relying on a build-dependent DEFAULT. `legacy` adds DES-CBC3-SHA
# (the only cipher Win2000 / XP-RTM share with stock OpenSSL); `modern` keeps the
# stock default. See research/30 §4.
_CIPHER_PROFILES = {
    "legacy": "ALL:@SECLEVEL=0",
    "modern": "DEFAULT",
}


def _norm_hash(h: str | None) -> str:
    h = (h or "").strip().lower()
    return h if h in _HASHES else "sha256"


def _hash_alg(h: str):
    return _HASHES[_norm_hash(h)]()


_OPENSSL_HAS_DATES: bool | None = None


def _openssl_supports_dates() -> bool:
    """Whether the in-box ``openssl`` accepts ``-not_before``/``-not_after`` (3.2+).
    Older 3.0/3.1 lack them; we then fall back to ``-days`` (no backdating)."""
    global _OPENSSL_HAS_DATES
    if _OPENSSL_HAS_DATES is None:
        import subprocess
        try:
            h = subprocess.run(["openssl", "x509", "-help"],
                               capture_output=True, text=True)
            _OPENSSL_HAS_DATES = "-not_before" in (h.stdout + h.stderr)
        except Exception:
            _OPENSSL_HAS_DATES = False
    return _OPENSSL_HAS_DATES


def _sign_cert(builder: x509.CertificateBuilder, issuer_key, hash_alg: str,
               *, subject_key=None, issuer_cert: x509.Certificate | None = None
               ) -> x509.Certificate:
    """Sign a ``CertificateBuilder`` with RSA PKCS#1 v1.5 + the given hash.

    ``issuer_key`` signs; for a self-signed CA ``subject_key`` is the same key, for
    a leaf it is the leaf's own key (needed to build the CSR in the CLI fallback).

    SHA-256 takes the fast native ``cryptography`` path. **SHA-1 is rejected by the
    high-level x509 ``.sign()`` on modern OpenSSL/cryptography builds** (the very
    "modern OpenSSL drops legacy" problem research/30 §B flags) — so SHA-1 falls
    back to the in-box ``openssl`` CLI, which still issues SHA-1 certs. We always
    *try* native first so a permissive runtime stays fast and CLI-free.
    """
    hash_alg = _norm_hash(hash_alg)
    try:
        return builder.sign(issuer_key, _hash_alg(hash_alg))
    except Exception:
        if hash_alg != "sha1":
            raise
        return _sign_cert_openssl(
            builder, issuer_key, subject_key=subject_key or issuer_key,
            issuer_cert=issuer_cert)


def _sign_cert_openssl(builder: x509.CertificateBuilder, issuer_key, *,
                       subject_key, issuer_cert: x509.Certificate | None
                       ) -> x509.Certificate:
    """Issue a SHA-1-signed cert via the openssl CLI when native signing refuses.

    We first build the cert with SHA-256 (allowed) purely to read back its fields
    uniformly, then re-issue them with ``-sha1`` via ``openssl req -x509`` (CA) or
    ``openssl x509 -req`` (leaf). PKCS#1 v1.5 is openssl's RSA default — never PSS.
    """
    import subprocess
    import tempfile
    # A native SHA-256 build to read fields back (subject, SAN, validity, CA flag).
    native = builder.sign(issuer_key, hashes.SHA256())
    is_ca = False
    try:
        bc = native.extensions.get_extension_for_class(x509.BasicConstraints).value
        is_ca = bool(bc.ca)
    except x509.ExtensionNotFound:
        pass

    def _key_pem(k):
        return k.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )

    from cryptography.x509.oid import NameOID as _N
    parts = []
    for attr in native.subject:
        short = {_N.COMMON_NAME: "CN", _N.ORGANIZATION_NAME: "O"}.get(attr.oid)
        if short:
            parts.append(f"/{short}={attr.value}")
    subj_arg = "".join(parts) or "/CN=unknown"

    with tempfile.TemporaryDirectory() as d:
        D = Path(d)
        out = D / "cert.pem"
        # ``*_utc`` props are newer (cryptography 42+); fall back to the naive ones.
        nva = getattr(native, "not_valid_after_utc", None) or native.not_valid_after
        nvb = getattr(native, "not_valid_before_utc", None) or native.not_valid_before
        # CRITICAL: preserve the ~1-day BACKDATING. openssl's -days alone starts the
        # window at "now"; on a guest with even slight clock skew that makes the leaf
        # NOT_TIME_VALID, which CryptoAPI surfaces as a PARTIAL_CHAIN (root dropped).
        # openssl 3.2+ accepts -not_before/-not_after; older 3.0/3.1 fall back to
        # -days with a generous extra day of backdating baked into nvb via -days.
        days = max(1, (nva - nvb).days)
        if _openssl_supports_dates():
            nb_args = ["-not_before", nvb.strftime("%Y%m%d%H%M%SZ"),
                       "-not_after", nva.strftime("%Y%m%d%H%M%SZ")]
        else:
            # No explicit dates: -days from "now". Acceptable for the long-lived CA;
            # for leaves we lose the 1-day backdate, so a guest clock AHEAD of the
            # host could briefly see NOT_TIME_VALID. The runtime image (openssl 3.5)
            # has the date flags, so this branch is the legacy-host safety net only.
            nb_args = ["-days", str(days)]
        if is_ca:
            (D / "ca.key").write_bytes(_key_pem(issuer_key))
            (D / "ca.ext").write_text(
                "[v3]\n"
                "basicConstraints=critical,CA:TRUE,pathlen:0\n"
                "keyUsage=critical,digitalSignature,keyCertSign,cRLSign\n"
                "subjectKeyIdentifier=hash\n"
            )
            subprocess.run(
                ["openssl", "req", "-new", "-x509", "-sha1", "-key", str(D / "ca.key"),
                 "-out", str(out), "-subj", subj_arg, *nb_args,
                 "-extensions", "v3", "-config", str(D / "ca.ext")],
                check=True, capture_output=True,
            )
        else:
            assert issuer_cert is not None, "leaf signing needs an issuer cert"
            (D / "leaf.key").write_bytes(_key_pem(subject_key))
            (D / "ca.key").write_bytes(_key_pem(issuer_key))
            (D / "ca.pem").write_bytes(
                issuer_cert.public_bytes(serialization.Encoding.PEM))
            san_line = ""
            try:
                san = native.extensions.get_extension_for_class(
                    x509.SubjectAlternativeName).value
                names = []
                for gn in san:
                    if isinstance(gn, x509.DNSName):
                        names.append(f"DNS:{gn.value}")
                    elif isinstance(gn, x509.IPAddress):
                        names.append(f"IP:{gn.value}")
                if names:
                    san_line = "subjectAltName=" + ",".join(names) + "\n"
            except x509.ExtensionNotFound:
                pass
            (D / "leaf.ext").write_text(
                "[v3]\n"
                "basicConstraints=critical,CA:FALSE\n"
                "extendedKeyUsage=serverAuth\n" + san_line
            )
            subprocess.run(
                ["openssl", "req", "-new", "-sha1", "-key", str(D / "leaf.key"),
                 "-out", str(D / "leaf.csr"), "-subj", subj_arg],
                check=True, capture_output=True,
            )
            subprocess.run(
                ["openssl", "x509", "-req", "-sha1", "-in", str(D / "leaf.csr"),
                 "-CA", str(D / "ca.pem"), "-CAkey", str(D / "ca.key"),
                 "-CAcreateserial", "-out", str(out), *nb_args,
                 "-extfile", str(D / "leaf.ext"), "-extensions", "v3"],
                check=True, capture_output=True,
            )
        return x509.load_pem_x509_certificate(out.read_bytes())


class CertAuthority:
    """Persisted root CA(s) + on-the-fly leaf-cert minting for MITM.

    Maintains a SHA-1 and a SHA-256 root CA (created on demand) so legacy guests
    with no SHA-2 in CryptoAPI can still validate the chain. ``leaf_hash`` /
    ``ca_hash`` and the cipher/TLS floor are profile-driven: the controller picks
    a profile per project and the proxy mints leaves to match.

    Thread/async-safe enough for the proxy's single event loop plus the rare
    concurrent first-hit on a hostname (guarded by a lock). Leaf certs and their
    SSLContexts are cached in-process keyed by ``(hostname, leaf_hash)``.

    ``ca_cert_path``/``ca_key_path`` name the **SHA-256** CA (back-compat default);
    the SHA-1 CA is derived as ``ca-sha1.crt``/``ca-sha1.key`` in the same dir.
    """

    def __init__(
        self,
        ca_cert_path: str,
        ca_key_path: str,
        *,
        default_leaf_hash: str = "sha256",
        cipher_profile: str = "legacy",
        tls_min: str = "TLSv1",
    ) -> None:
        self.ca_cert_path = Path(ca_cert_path)
        self.ca_key_path = Path(ca_key_path)
        self.default_leaf_hash = _norm_hash(default_leaf_hash)
        self.cipher_profile = cipher_profile if cipher_profile in _CIPHER_PROFILES else "legacy"
        self.tls_min = tls_min or "TLSv1"
        self._lock = threading.Lock()
        # ctx cache keyed by (host, leaf_hash) — a host may be served to guests on
        # different profiles, each needing a differently-signed leaf.
        self._ctx_cache: dict[tuple[str, str], ssl.SSLContext] = {}
        # per-hash (cert, key) pair, loaded/created lazily.
        self._cas: dict[str, tuple[x509.Certificate, object]] = {}
        # back-compat attributes (the SHA-256 CA, the historical "the CA").
        self.ca_cert: Optional[x509.Certificate] = None
        self.ca_key = None
        # Always provision both roots so the controller can install whichever a
        # guest needs (installing both is harmless — research/30 §6).
        for h in ("sha256", "sha1"):
            self._load_or_create_ca(h)
        self.ca_cert, self.ca_key = self._cas["sha256"]

    # --- root CA paths ---------------------------------------------------
    def _ca_paths(self, hash_alg: str) -> tuple[Path, Path]:
        """(.crt, .key) paths for a given CA hash. SHA-256 keeps the legacy
        ``ca.crt``/``ca.key`` names; SHA-1 gets ``ca-sha1.{crt,key}`` alongside."""
        if _norm_hash(hash_alg) == "sha256":
            return self.ca_cert_path, self.ca_key_path
        crt = self.ca_cert_path.with_name("ca-sha1.crt")
        key = self.ca_key_path.with_name("ca-sha1.key")
        return crt, key

    def ca_cert_path_for(self, hash_alg: str) -> Path:
        return self._ca_paths(hash_alg)[0]

    # --- root CA ---------------------------------------------------------
    def _load_or_create_ca(self, hash_alg: str) -> None:
        hash_alg = _norm_hash(hash_alg)
        crt_path, key_path = self._ca_paths(hash_alg)
        if crt_path.exists() and key_path.exists():
            cert = x509.load_pem_x509_certificate(crt_path.read_bytes())
            key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
            self._cas[hash_alg] = (cert, key)
            print(f"[HTTPS] Loaded MITM root CA ({hash_alg}) from {crt_path}")
            return
        print(f"[HTTPS] Generating MITM root CA ({hash_alg}) (first run)")
        self._generate_ca(hash_alg)

    def _generate_ca(self, hash_alg: str) -> None:
        hash_alg = _norm_hash(hash_alg)
        crt_path, key_path = self._ca_paths(hash_alg)
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, _CA_CN[hash_alg]),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, _CA_ORG),
        ])
        now = datetime.datetime.now(datetime.timezone.utc)
        cert = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            # long-lived: the guest installs it once and keeps it for the artwork's life
            .not_valid_after(now + datetime.timedelta(days=3650))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True, key_cert_sign=True, crl_sign=True,
                    content_commitment=False, key_encipherment=False,
                    data_encipherment=False, key_agreement=False,
                    encipher_only=False, decipher_only=False,
                ),
                critical=True,
            )
            .add_extension(
                x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
                critical=False,
            )
        )
        # PKCS#1 v1.5 (RSA default) — never PSS, which XP/2003 CryptoAPI can't
        # verify (research/30 §6). SHA-1 routes through the openssl CLI fallback.
        cert = _sign_cert(cert, key, hash_alg, subject_key=key)
        self._cas[hash_alg] = (cert, key)
        crt_path.parent.mkdir(parents=True, exist_ok=True)
        key_path.parent.mkdir(parents=True, exist_ok=True)
        crt_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        key_path.write_bytes(
            key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )
        try:
            key_path.chmod(0o600)
        except OSError:
            pass
        print(f"[HTTPS] Wrote MITM root CA ({hash_alg}) -> {crt_path}")

    @property
    def ca_cert_pem(self) -> bytes:
        """The SHA-256 root CA (PEM) — back-compat default."""
        return self.ca_cert.public_bytes(serialization.Encoding.PEM)

    def ca_cert_pem_for(self, hash_alg: str) -> bytes:
        """The root CA (PEM) for a given signature hash (``sha1``|``sha256``)."""
        cert, _ = self._cas[_norm_hash(hash_alg)]
        return cert.public_bytes(serialization.Encoding.PEM)

    # --- leaf certs ------------------------------------------------------
    def _mint_leaf(self, hostname: str, leaf_hash: str) -> tuple[bytes, bytes]:
        """Return (cert_pem, key_pem) for ``hostname``, signed by the CA whose
        hash matches ``leaf_hash`` so the guest can validate the whole chain."""
        leaf_hash = _norm_hash(leaf_hash)
        ca_cert, ca_key = self._cas[leaf_hash]
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        now = datetime.datetime.now(datetime.timezone.utc)

        try:
            ip = ipaddress.ip_address(hostname)
            san = x509.SubjectAlternativeName([x509.IPAddress(ip)])
        except ValueError:
            san = x509.SubjectAlternativeName([x509.DNSName(hostname)])

        cert = (
            x509.CertificateBuilder()
            # CN kept populated for old CN-only clients (Win2000/XP); SAN for Win10+.
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname[:64])]))
            .issuer_name(ca_cert.subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=825))
            .add_extension(san, critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(
                x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False
            )
        )
        # PKCS#1 v1.5 (RSA default) — never PSS for legacy CryptoAPI. SHA-1 routes
        # through the openssl CLI fallback (modern cryptography blocks SHA-1 .sign).
        cert = _sign_cert(cert, ca_key, leaf_hash, subject_key=key, issuer_cert=ca_cert)
        cert_pem = cert.public_bytes(serialization.Encoding.PEM)
        key_pem = key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
        return cert_pem, key_pem

    def server_context(self, hostname: str, leaf_hash: str | None = None) -> ssl.SSLContext:
        """An ``SSLContext`` (server side) presenting a leaf cert for ``hostname``,
        signed with ``leaf_hash`` (defaults to the CA's ``default_leaf_hash``).

        Cached per ``(hostname, leaf_hash)``. The TLS floor + cipher list come from
        the CA's profile so very old guest TLS stacks (XP / FF52, TLS 1.0, 3DES /
        AES-CBC) still negotiate.
        """
        leaf_hash = _norm_hash(leaf_hash or self.default_leaf_hash)
        host = (hostname or "").strip().lower() or "unknown"
        cache_key = (host, leaf_hash)
        ctx = self._ctx_cache.get(cache_key)
        if ctx is not None:
            return ctx
        with self._lock:
            ctx = self._ctx_cache.get(cache_key)
            if ctx is not None:
                return ctx
            cert_pem, key_pem = self._mint_leaf(host, leaf_hash)
            # Write to temp files: load_cert_chain needs paths on this Python.
            import tempfile, os
            cf = tempfile.NamedTemporaryFile(delete=False, suffix=".pem")
            kf = tempfile.NamedTemporaryFile(delete=False, suffix=".pem")
            try:
                cf.write(cert_pem); cf.close()
                kf.write(key_pem); kf.close()
                ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                # Allow legacy guests: assert the profile's minimum TLS version
                # (default TLSv1) so a strict host OpenSSL can't push it to 1.2.
                try:
                    ctx.minimum_version = getattr(
                        ssl.TLSVersion, self.tls_min, ssl.TLSVersion.TLSv1)
                except (ValueError, AttributeError):
                    pass
                # Explicit cipher list + SECLEVEL so 3DES/AES-CBC is actually offered
                # rather than relying on a build-dependent DEFAULT.
                ciphers = _CIPHER_PROFILES.get(self.cipher_profile, "ALL:@SECLEVEL=0")
                try:
                    ctx.set_ciphers(ciphers)
                except ssl.SSLError:
                    try:
                        ctx.set_ciphers("DEFAULT:@SECLEVEL=0")
                    except ssl.SSLError:
                        pass
                ctx.load_cert_chain(certfile=cf.name, keyfile=kf.name)
            finally:
                for p in (cf.name, kf.name):
                    try:
                        os.unlink(p)
                    except OSError:
                        pass
            self._ctx_cache[cache_key] = ctx
            return ctx
