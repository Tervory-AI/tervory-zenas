"""Special Tribunal fetches: SIU's short TLS chain, and the catalogue row.

www.siu.org.za sends its certificate without the intermediate, so Python
cannot verify it although browsers can. Every network call here is mocked.
The 406 (PDF asked for as text/html) is covered in
test_zenas_pool_za_official_cases.py.
"""

import asyncio
import datetime as dt
import ssl
import sys
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.zenas.pool import http as pool_http  # noqa: E402
from agents.zenas.pool.catalogue import CATALOGUE  # noqa: E402


def test_catalogue_still_points_at_the_justice_list():
    row = next(item for item in CATALOGUE if item.source_id == "special-tribunal")
    assert "justice.gov.za/tribunal/jdgm.html" in str(row)


# --- TLS: a server that leaves out its intermediate ---------------------------


def _ca_pair(name: str, issuer=None, *, ca: bool = True):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    issuer_name, issuer_key = (subject, key) if issuer is None else issuer
    now = dt.datetime.now(dt.timezone.utc)
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer_name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
        .add_extension(
            x509.AuthorityInformationAccess(
                [
                    x509.AccessDescription(
                        x509.oid.AuthorityInformationAccessOID.CA_ISSUERS,
                        x509.UniformResourceIdentifier("http://ca.example/issuer.crt"),
                    ),
                    x509.AccessDescription(
                        x509.oid.AuthorityInformationAccessOID.CA_ISSUERS,
                        x509.UniformResourceIdentifier("ldap://ca.example/issuer"),
                    ),
                ]
            ),
            critical=False,
        )
    )
    cert = builder.sign(issuer_key, hashes.SHA256())
    return cert, (subject, key)


def test_issuer_urls_reads_http_ca_issuers_only():
    from cryptography.hazmat.primitives.serialization import Encoding

    cert, _ = _ca_pair("leaf")
    pem = cert.public_bytes(Encoding.PEM).decode("ascii")
    assert pool_http.issuer_urls(pem) == ["http://ca.example/issuer.crt"]


def test_intermediate_pem_refuses_a_root_or_a_non_ca():
    from cryptography.hazmat.primitives.serialization import Encoding

    root, root_name = _ca_pair("root")
    inter, _ = _ca_pair("inter", root_name)
    leaf, _ = _ca_pair("leaf", root_name, ca=False)
    assert "BEGIN CERTIFICATE" in pool_http.intermediate_pem(inter.public_bytes(Encoding.DER))
    assert "BEGIN CERTIFICATE" in pool_http.intermediate_pem(inter.public_bytes(Encoding.PEM))
    with pytest.raises(ValueError):
        pool_http.intermediate_pem(root.public_bytes(Encoding.DER))
    with pytest.raises(ValueError):
        pool_http.intermediate_pem(leaf.public_bytes(Encoding.DER))


def test_chain_context_keeps_partial_chains_off_and_hostname_checks_on():
    from cryptography.hazmat.primitives.serialization import Encoding

    root, root_name = _ca_pair("root")
    inter, _ = _ca_pair("inter", root_name)
    context = pool_http.chain_context([inter.public_bytes(Encoding.PEM).decode("ascii")])
    assert context.check_hostname is True
    assert context.verify_mode == ssl.CERT_REQUIRED
    partial = getattr(ssl, "VERIFY_X509_PARTIAL_CHAIN", 0)
    assert not (context.verify_flags & partial)


def _missing_issuer_error() -> urllib.error.URLError:
    reason = ssl.SSLCertVerificationError(1, "unable to get local issuer certificate")
    reason.verify_code = 20
    return urllib.error.URLError(reason)


def test_missing_issuer_only_matches_that_one_verify_error():
    assert pool_http.missing_issuer(_missing_issuer_error())
    expired = ssl.SSLCertVerificationError(1, "certificate has expired")
    expired.verify_code = 10
    assert not pool_http.missing_issuer(urllib.error.URLError(expired))
    assert not pool_http.missing_issuer(urllib.error.URLError(OSError("refused")))


def test_urllib_get_retries_once_with_the_issuer_the_host_left_out(monkeypatch: pytest.MonkeyPatch):
    import urllib.request

    contexts: list[object] = []
    sentinel = ssl.create_default_context()

    class _Response:
        headers = {"Content-Encoding": ""}

        def read(self) -> bytes:
            return b"<html>ok</html>"

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    def urlopen(request, timeout=60):
        contexts.append(None)
        raise _missing_issuer_error()

    class _Opener:
        def __init__(self, handlers):
            self.handlers = handlers

        def open(self, request, timeout=60):
            https = [h for h in self.handlers if isinstance(h, urllib.request.HTTPSHandler)]
            contexts.append(https[0]._context if https else None)
            return _Response()

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(urllib.request, "build_opener", lambda *h: _Opener(h))
    monkeypatch.setattr(pool_http, "issuer_context", lambda host, port=443: sentinel)
    monkeypatch.setattr(pool_http, "_ISSUER_CONTEXTS", {})
    body = asyncio.run(pool_http.urllib_get("https://www.siu.org.za/special-tribunal-judgements/", {}))
    assert body == b"<html>ok</html>"
    assert contexts == [None, sentinel]


def test_urllib_get_does_not_retry_other_tls_failures(monkeypatch: pytest.MonkeyPatch):
    import urllib.request

    expired = ssl.SSLCertVerificationError(1, "certificate has expired")
    expired.verify_code = 10
    asked: list[str] = []

    def urlopen(request, timeout=60):
        raise urllib.error.URLError(expired)

    def issuer_context(host, port=443):
        asked.append(host)
        return ssl.create_default_context()

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(pool_http, "issuer_context", issuer_context)
    monkeypatch.setattr(pool_http, "_ISSUER_CONTEXTS", {})
    with pytest.raises(urllib.error.URLError):
        asyncio.run(pool_http.urllib_get("https://expired.example/", {}))
    assert asked == []
