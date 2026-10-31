"""PDF text is parsed in a worker thread, so a case load never stalls the room's event loop."""
import asyncio
import threading

from agents.zenas.pool.adapters import za_case_common


def test_pdfs_to_cases_parses_off_the_event_loop(monkeypatch):
    seen = []

    def fake_pdf_text(blob, *, max_pages=None):
        seen.append(threading.current_thread() is threading.main_thread())
        return ""

    async def http_get(url, headers):
        return b"%PDF-1.4 fixture"

    async def no_pause(_env):
        return None

    monkeypatch.setattr(za_case_common, "pdf_text", fake_pdf_text)
    monkeypatch.setattr(za_case_common, "case_pause", no_pause)
    out = asyncio.run(za_case_common.pdfs_to_cases(
        http_get, ["https://example.org/a.pdf"], body="fixture", interval_env="X", citation_for=lambda u, t: None))
    assert out == [] and seen == [False]
