import asyncio

import httpx
import pytest

import app.services.ingest_service as ingest_module
from app.core.config import Settings
from app.core.url_safety import UnsafeUrlError, ensure_public_url
from app.services.ingest_service import IngestService, _PageTooLargeError


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8000/admin",
        "http://169.254.170.2/v2/credentials",  # ECS task credentials
        "http://169.254.169.254/latest/meta-data/",  # EC2 metadata
        "http://10.0.1.25/",
        "http://[::1]/",
        "http://8.8.8.8:6333/collections",  # non-web port
        "file:///etc/passwd",
        "gopher://8.8.8.8/",
    ],
)
def test_internal_and_non_web_urls_are_rejected(url: str) -> None:
    with pytest.raises(UnsafeUrlError):
        asyncio.run(ensure_public_url(url))


def test_public_url_is_allowed() -> None:
    asyncio.run(ensure_public_url("https://8.8.8.8/page"))


def test_private_hosts_can_be_allowed_explicitly() -> None:
    asyncio.run(ensure_public_url("http://10.0.1.25/intranet", allow_private_hosts=True))


def _service_with_transport(monkeypatch, handler) -> IngestService:
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        ingest_module.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    service = object.__new__(IngestService)
    service._settings = Settings()
    return service


def test_redirect_to_internal_address_is_blocked(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://169.254.170.2/v2/credentials/x"})

    service = _service_with_transport(monkeypatch, handler)

    with pytest.raises(UnsafeUrlError):
        asyncio.run(service._fetch_page("https://8.8.8.8/start"))


def test_oversized_page_is_rejected(monkeypatch) -> None:
    monkeypatch.setattr(ingest_module, "WEBSITE_MAX_BYTES", 1000)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 5000, headers={"content-type": "text/html"})

    service = _service_with_transport(monkeypatch, handler)

    with pytest.raises(_PageTooLargeError):
        asyncio.run(service._fetch_page("https://8.8.8.8/big"))


def test_public_redirect_is_followed(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/old":
            return httpx.Response(301, headers={"location": "/new"})
        return httpx.Response(200, content=b"<html>ok</html>", headers={"content-type": "text/html"})

    service = _service_with_transport(monkeypatch, handler)

    content, content_type = asyncio.run(service._fetch_page("https://8.8.8.8/old"))
    assert content == b"<html>ok</html>"
    assert content_type == "text/html"


def test_zip_bomb_office_file_is_rejected(monkeypatch) -> None:
    import io
    import zipfile

    import app.parsers.base as parser_base
    from app.parsers.base import ensure_safe_archive

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("xl/worksheets/sheet1.xml", b"0" * 5_000_000)
    content = buffer.getvalue()
    assert len(content) < 100_000  # tiny upload

    monkeypatch.setattr(parser_base, "MAX_UNCOMPRESSED_ARCHIVE_BYTES", 1_000_000)
    with pytest.raises(ValueError, match="expands to more than"):
        ensure_safe_archive(content, "xlsx")


def test_streaming_xlsx_parser_reads_rows() -> None:
    import io

    from openpyxl import Workbook

    from app.parsers.xlsx_parser import XlsxParser

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Programmes"
    sheet.append(["Name", "Duration"])
    sheet.append(["AI Fundamentals", "3 days"])
    buffer = io.BytesIO()
    workbook.save(buffer)

    parsed = asyncio.run(XlsxParser().parse("courses.xlsx", buffer.getvalue(), None))

    assert len(parsed.documents) == 1
    assert "Name: AI Fundamentals" in parsed.documents[0].content
    assert "Sheet: Programmes" in parsed.documents[0].content


def test_xlsx_with_wrong_stored_dimension_keeps_all_rows() -> None:
    import io
    import re
    import zipfile

    from openpyxl import Workbook

    from app.parsers.xlsx_parser import XlsxParser

    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Name", "Duration", "Level"])
    sheet.append(["AI 101", "3 days", "Beginner"])
    sheet.append(["ML 201", "5 days", "Advanced"])
    saved = io.BytesIO()
    workbook.save(saved)

    # Some exporters write a wrong <dimension>; openpyxl read-only mode trusts it.
    source = zipfile.ZipFile(io.BytesIO(saved.getvalue()))
    rewritten = io.BytesIO()
    with zipfile.ZipFile(rewritten, "w") as target:
        for item in source.infolist():
            data = source.read(item.filename)
            if item.filename == "xl/worksheets/sheet1.xml":
                data = re.sub(rb'<dimension ref="[^"]+"/>', b'<dimension ref="A1"/>', data)
            target.writestr(item, data)

    parsed = asyncio.run(XlsxParser().parse("courses.xlsx", rewritten.getvalue(), None))

    assert len(parsed.documents) == 2
    assert "Level: Advanced" in parsed.documents[1].content


def test_intranet_ports_are_allowed_when_private_hosts_are_enabled() -> None:
    asyncio.run(ensure_public_url("http://10.0.1.25:8080/wiki", allow_private_hosts=True))
