"""Unit tests for the PDF text extractor hardening (core.extra_tools.pdf_text_extractor).

Covers: partition_pdf running off the event loop and the streamed download
being capped at MAX_DOWNLOAD_BYTES instead of blanket-buffering response.content.
"""

import asyncio
import threading

import httpx
import pytest
from testzeus_hercules.core.extra_tools import pdf_text_extractor
from testzeus_hercules.core.extra_tools.pdf_text_extractor import (
    download_pdf,
    extract_text_from_pdf,
)


class _FakeElement:
    def __init__(self, text):
        self._text = text

    def __str__(self):
        return self._text


def _install_transport(monkeypatch, handler) -> None:
    """Route pdf_text_extractor's AsyncClient through a MockTransport."""
    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient

    def client_factory(**kwargs):
        kwargs["transport"] = transport
        return real_client(**kwargs)

    monkeypatch.setattr(pdf_text_extractor.httpx, "AsyncClient", client_factory)


def test_extract_text_runs_partition_off_the_event_loop(monkeypatch, tmp_path) -> None:
    pdf_file = tmp_path / "doc.pdf"
    pdf_file.write_bytes(b"%PDF-1.4 stub")

    async def fake_download(url, path):
        return str(pdf_file)

    seen: dict = {}

    def fake_partition(path):
        seen["thread"] = threading.get_ident()
        seen["path"] = path
        return [_FakeElement("hello pdf")]

    monkeypatch.setattr(pdf_text_extractor, "download_pdf", fake_download)
    monkeypatch.setattr(pdf_text_extractor, "partition_pdf", fake_partition)
    monkeypatch.setattr(pdf_text_extractor, "PlaywrightManager", lambda: None)

    result = asyncio.run(extract_text_from_pdf("https://example.test/doc.pdf"))

    assert seen["path"] == str(pdf_file)
    assert seen["thread"] != threading.get_ident()  # parsing must not run on the event loop thread
    assert result == "Text found in the PDF:\nhello pdf"


def test_download_pdf_streams_full_file(monkeypatch, tmp_path) -> None:
    pdf_bytes = b"%PDF-1.4 stub"

    def handler(request: httpx.Request) -> httpx.Response:
        # Chunked async body: forces the streaming path through aiter_bytes().
        async def body():
            yield pdf_bytes[:4]
            yield pdf_bytes[4:]

        return httpx.Response(200, content=body())

    _install_transport(monkeypatch, handler)
    dest = tmp_path / "out.pdf"

    result = asyncio.run(download_pdf("https://example.test/doc.pdf", str(dest)))

    assert result == str(dest)
    assert dest.read_bytes() == pdf_bytes


def test_download_pdf_rejects_oversized_body(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(pdf_text_extractor, "MAX_DOWNLOAD_BYTES", 1024)
    chunk = b"x" * 2048
    reads: dict = {"chunks": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        async def body():
            while True:
                reads["chunks"] += 1
                yield chunk

        return httpx.Response(200, content=body())

    _install_transport(monkeypatch, handler)
    dest = tmp_path / "big.pdf"

    with pytest.raises(ValueError) as excinfo:
        asyncio.run(download_pdf("https://example.test/big.pdf", str(dest)))

    assert "maximum supported size" in str(excinfo.value)
    # Streaming means the oversized body was cut off, not fully consumed.
    assert reads["chunks"] < 10


def test_oversized_download_surfaces_error_message(monkeypatch, tmp_path) -> None:
    """The tool wraps the cap violation into a clean error string for the agent."""

    async def failing_download(url, path):
        raise ValueError(f"Downloaded PDF exceeds the maximum supported size of {pdf_text_extractor.MAX_DOWNLOAD_BYTES} bytes.")

    monkeypatch.setattr(pdf_text_extractor, "download_pdf", failing_download)
    monkeypatch.setattr(pdf_text_extractor, "PlaywrightManager", lambda: None)

    result = asyncio.run(extract_text_from_pdf("https://example.test/big.pdf"))

    assert "maximum supported size" in result
    assert result.startswith("An error occurred while extracting text:")
