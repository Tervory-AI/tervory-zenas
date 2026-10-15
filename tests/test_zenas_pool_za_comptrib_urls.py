from urllib.parse import unquote

from agents.zenas.pool.adapters.za_comptrib import decision_pdfs


def test_comptrib_pdf_urls_have_no_raw_spaces():
    page = (
        b'<a href="/uploads/topics/CompTrib_Case_Files/Order - Conditional Merger '
        b'Approval - LM046Jun26 - Non confidential.pdf">x</a>'
        b'<a href="/uploads/a%20b.pdf">y</a>'
    )
    urls = decision_pdfs(page)
    assert len(urls) == 2
    assert all(" " not in u for u in urls)
    assert unquote(urls[0]).endswith("LM046Jun26 - Non confidential.pdf")
    assert urls[1].endswith("/uploads/a%20b.pdf")
