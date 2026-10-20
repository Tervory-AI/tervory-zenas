"""Keyless official ZA case sources. Every HTTP call is mocked."""

import asyncio
import json
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.zenas.pool.adapters.za_cac import judgment_pdfs
from agents.zenas.pool.adapters.za_comptrib import decision_pdfs
from agents.zenas.pool.adapters.za_concourt_oai import (
    COLLECTION_UUID,
    assert_allowed,
    bitstream_content_url,
    choose_judgment_name,
    choose_text_fallback,
)
from agents.zenas.pool.adapters.za_labour import year_folders
from agents.zenas.pool.adapters.za_official import (
    MAGISTRATES_RULES_URL,
    UNIFORM_RULES_URL,
    notice_links,
    official_rule_pdfs,
    reject_saflii_rules,
)
from agents.zenas.pool.adapters.za_sars_taxcourt import case_urls, year_urls
from agents.zenas.pool.adapters.za_sca_jdownloads import category_urls, send_url, summary_sends
from agents.zenas.pool.adapters.za_special_tribunal import justice_pdfs, siu_matter_urls, siu_pdf
from agents.zenas.pool.adapters import get_adapter
from agents.zenas.pool.bulk import load_wave
from agents.zenas.pool.catalogue import CATALOGUE
from agents.zenas.pool.licence import FetchNotConfigured
from agents.zenas.pool.store import MemoryStore

ON = date(2026, 10, 15)
KEYLESS = (
    "concourt",
    "sca",
    "cac",
    "comptrib",
    "sars-taxcourt",
    "landcourt",
    "ocj-highcourt",
    "labour-court",
    "labour-appeal",
    "special-tribunal",
)


def tiny_pdf(text: str) -> bytes:
    safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 12 Tf 36 120 Td ({safe}) Tj ET\n".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"endstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets[1:]:
        out += f"{off:010d} 00000 n \n".encode()
    out += (
        f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(out)


def _source(source_id: str):
    return next(item for item in CATALOGUE if item.source_id == source_id)


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CONCOURT_MIN_INTERVAL", "0")
    monkeypatch.setenv("ZA_CASE_MIN_INTERVAL", "0")
    monkeypatch.setenv("GOVZA_MIN_INTERVAL", "0")
    monkeypatch.setenv("CONCOURT_MAX_ITEMS", "5")
    monkeypatch.setenv("ZA_CASE_MAX_DOCS", "8")


def test_new_sources_are_keyless_za_and_registered():
    for source_id in KEYLESS:
        source = _source(source_id)
        assert source.region == "za"
        assert source.jurisdiction == "za"
        assert source.key_name == ""
        assert source.key_required is False
        assert source.enabled is True
        assert source.adapter == source_id
        assert get_adapter(source).source_id == source_id
    assert COLLECTION_UUID


def test_concourt_picks_the_judgment_bitstream_and_refuses_robots_paths():
    assert (
        choose_judgment_name(
            [
                "1. Heads of argument.pdf",
                "CCT306-Media summary.pdf",
                "CCT306-Judgment.pdf",
                "[Final Judgment] CCT306.pdf",
            ]
        )
        == "[Final Judgment] CCT306.pdf"
    )
    assert choose_text_fallback(["CCT306-Judgment.pdf.txt", "Media summary.pdf.txt"], "CCT306-Judgment.pdf") == (
        "CCT306-Judgment.pdf.txt"
    )
    sample = (
        "https://collections.concourt.org.za/server/api/core/bitstreams/"
        "fde9a91d-51d9-4240-b5e1-660c8f6403c4/download"
    )
    content = bitstream_content_url(sample)
    assert content.endswith("/fde9a91d-51d9-4240-b5e1-660c8f6403c4/content")
    with pytest.raises(FetchNotConfigured):
        assert_allowed("https://collections.concourt.org.za/browse/dateissued")
    with pytest.raises(FetchNotConfigured):
        assert_allowed("https://collections.concourt.org.za/server/api/discover/search/objects")
    with pytest.raises(FetchNotConfigured):
        assert_allowed(sample)


def test_concourt_live_uses_oai_and_content_not_download():
    asyncio.run(_concourt())


async def _concourt():
    pdf = tiny_pdf("The commercial appeal is about a bill of lading.")
    oai = """<?xml version="1.0"?>
    <OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">
      <ListRecords>
        <record>
          <header><identifier>oai:collections.concourt.org.za:20.500.12144/100</identifier></header>
          <metadata>
            <title>Alpha Shipping (Pty) Ltd v Beta Carriers (Pty) Ltd</title>
            <identifier>[2024] ZACC 2</identifier>
            <identifier>http://hdl.handle.net/20.500.12144/100</identifier>
            <date>15 March 2024</date>
          </metadata>
        </record>
        <record>
          <header><identifier>oai:collections.concourt.org.za:20.500.12144/101</identifier></header>
          <metadata>
            <title>S v Dlamini</title>
            <identifier>[2024] ZACC 9</identifier>
            <identifier>http://hdl.handle.net/20.500.12144/101</identifier>
          </metadata>
        </record>
        <record>
          <header status="deleted"><identifier>oai:collections.concourt.org.za:20.500.12144/102</identifier></header>
        </record>
      </ListRecords>
    </OAI-PMH>""".encode()
    item = {
        "uuid": "07e70aa0-988e-459b-a564-21aeda631af5",
        "handle": "20.500.12144/100",
        "metadata": {
            "dc.title": [{"value": "Alpha Shipping (Pty) Ltd v Beta Carriers (Pty) Ltd"}],
            "dc.identifier.citation": [{"value": "[2024] ZACC 2"}],
            "dc.date.judgment": [{"value": "15 March 2024"}],
        },
    }
    bundles = {
        "_embedded": {
            "bundles": [
                {
                    "name": "ORIGINAL",
                    "_links": {
                        "bitstreams": {
                            "href": "https://collections.concourt.org.za/server/api/core/bundles/original/bitstreams"
                        }
                    },
                }
            ]
        }
    }
    bits = {
        "_embedded": {
            "bitstreams": [
                {
                    "name": "CCT1-Media summary.pdf",
                    "_links": {
                        "content": {
                            "href": "https://collections.concourt.org.za/server/api/core/bitstreams/media/download"
                        }
                    },
                },
                {
                    "name": "[Final Judgment] CCT1.pdf",
                    "_links": {
                        "content": {
                            "href": "https://collections.concourt.org.za/server/api/core/bitstreams/fde9a91d-51d9-4240-b5e1-660c8f6403c4/content"
                        }
                    },
                },
            ]
        }
    }
    seen: list[str] = []

    async def get(url, headers):
        seen.append(url)
        if "verb=ListRecords" in url:
            return oai
        if "/pid/find" in url:
            return json.dumps(item).encode()
        if url.endswith("/bundles?size=20"):
            return json.dumps(bundles).encode()
        if url.endswith("/bitstreams") or "bitstreams?size=100" in url:
            return json.dumps(bits).encode()
        if url.endswith("/fde9a91d-51d9-4240-b5e1-660c8f6403c4/content"):
            return pdf
        return b"{}"

    store = MemoryStore()
    report = await load_wave(store, region="za", only=[_source("concourt")], live=True, http_get=get, on=ON)
    assert not any("/download" in url or "/discover" in url or "/browse" in url or "/search" in url for url in seen)
    assert not any("20.500.12144/101" in url for url in seen)
    assert any(url.endswith("/content") for url in seen)
    row = report["sources"][0]
    assert row["documents"] == 1
    assert row["key_set"] is True
    text = await store.all_text()
    assert "Dlamini" not in text
    assert "bill of lading" in text
    found = await store.find_by_citation("[2024] ZACC 2", jurisdiction="za", as_at=None)
    assert found


def test_sca_send_links_skip_download_and_drop_criminal(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SCA_MAX_PAGES", "2")
    monkeypatch.setenv("SCA_MAX_YEARS", "1")
    assert send_url("/index.php/component/jdownloads/summary/101-judgements-2026/4744-alpha").endswith(
        "/component/jdownloads/send/101-judgements-2026/4744-alpha"
    )
    assert send_url("/download/101/4744-alpha") == ""
    asyncio.run(_sca())


async def _sca():
    sca_index = b"""
    <a href="/index.php/component/jdownloads/category/101-judgements-2026?Itemid=-1">Judgements 2026</a>
    <a href="/index.php/component/jdownloads/category/48-judgment-2023">Judgment 2023</a>
    """
    electoral = b'<a href="/index.php/component/jdownloads/category/102-judgements-2026">Electoral 2026</a>'
    page = """
    <a href="/index.php/component/jdownloads/summary/101-judgements-2026/4744-alpha">
      Alpha Shipping (Pty) Ltd v Beta Carriers (Pty) Ltd [2026] ZASCA 12 (7 October 2026)</a>
    <a href="/index.php/component/jdownloads/summary/101-judgements-2026/4737-pace">Pace v The State [2026] ZASCA 99</a>
    <a href="/index.php/component/jdownloads/category/101-judgements-2026?start=10">next</a>
    """.encode()
    electoral_page = b"""
    <a href="/index.php/component/jdownloads/summary/102-judgements-2026/10-party">
      Party v Commission [2026] ZAEC 4 (1 October 2026)</a>
    """
    pdf = tiny_pdf("This commercial appeal is about a bill of lading.")
    seen: list[str] = []

    async def get(url, headers):
        seen.append(url)
        if url.endswith("/judgements"):
            return sca_index
        if url.endswith("/electoral-court-judgments"):
            return electoral
        if "102-judgements-2026" in url and "/send/" not in url:
            return electoral_page
        if "/send/" in url:
            return pdf
        if "101-judgements-2026" in url:
            return page
        return b"<html></html>"

    store = MemoryStore()
    report = await load_wave(store, region="za", only=[_source("sca")], live=True, http_get=get, on=ON)
    assert not any("/download/" in url for url in seen)
    assert any("/component/jdownloads/send/101-judgements-2026/4744-alpha" in url for url in seen)
    assert not any("/4737-pace" in url for url in seen)
    assert any("start=10" in url for url in seen)
    assert report["sources"][0]["documents"] == 2
    text = await store.all_text()
    assert "The State" not in text
    appeal = await store.find_by_citation("[2026] ZAEC 4", jurisdiction="za", as_at=None)
    assert appeal and store.docs[appeal[0].version_id].body == "Electoral Court"
    civil = await store.find_by_citation("[2026] ZASCA 12", jurisdiction="za", as_at=None)
    assert civil and store.docs[civil[0].version_id].body == "Supreme Court of Appeal"


def test_listing_parsers_keep_official_files_and_skip_the_rest():
    assert category_urls(
        b'<a href="/index.php/component/jdownloads/category/48-judgment-2023">2023</a>'
        b'<a href="/index.php/component/jdownloads/category/101-judgements-2026">2026</a>'
    )[0].endswith("/101-judgements-2026")
    sends = summary_sends(
        b'<a href="/x/component/jdownloads/summary/101-judgements-2026/5-alpha">Alpha [2026] ZASCA 5</a>'
    )
    assert sends[0][0].endswith("/send/101-judgements-2026/5-alpha")
    cac = judgment_pdfs(
        b'<a href="https://www.cac.org.za/wp-content/uploads/2026/09/Alpha-Judgment.pdf">j</a>'
        b'<a href="https://www.cac.org.za/wp-content/uploads/2025/01/Appeal-court-rules.pdf">r</a>'
    )
    assert cac == ["https://www.cac.org.za/wp-content/uploads/2026/09/Alpha-Judgment.pdf"]
    kept = decision_pdfs(
        b'<a href="uploads/topics/CompTrib_Case_Files/Order - LM046Jun26 - Non confidential.pdf">a</a>'
        b'<a href="uploads/topics/CompTrib_Case_Files/CONFI Order - secret.pdf">b</a>'
    )
    assert len(kept) == 1 and "LM046Jun26" in kept[0]
    years = year_urls(
        b'<a href="https://www.sars.gov.za/legal-counsel/dispute-resolution-judgments/tax-court/tc-2025-2023/">y</a>'
        b'<a href="https://www.sars.gov.za/legal-counsel/dispute-resolution-judgments/tax-court/tc-2028-2026/">y</a>'
    )
    assert years[0].endswith("/tc-2028-2026/")
    cases = case_urls(
        b'<a href="https://www.sars.gov.za/legal-drj-tc-2026-15-sarstc-it-76811-it-2026-zatc-jhb-4-september-2026/">c</a>'
    )
    assert "it-76811" in cases[0]
    index = (
        b'<a href="/index.php/courts/specialised-courts/labour-and-labour-appeal-court/court-judgments/'
        b'judgements-labour-court/2025-court-judgments-lbc">2025</a>'
        b'<a href="/index.php/courts/specialised-courts/labour-and-labour-appeal-court/court-judgments/'
        b'judgements-labour-court-2/2025-judgements-labour-court-appeal">LAC</a>'
    )
    labour = year_folders(index, "/judgements-labour-court/")
    appeal = year_folders(index, "/judgements-labour-court-2/")
    assert len(labour) == 1 and "labour-court-2" not in labour[0]
    assert len(appeal) == 1 and "labour-court-2" in appeal[0]
    rules = official_rule_pdfs(
        b'<a href="Uniform-Rules-of-Court-GG999-GNR48-19650112-SAFLII.pdf">u</a>'
        b'<a href="Rules-MagistrateCourts-GG33487-GNR740-20100823.pdf">m</a>'
    )
    assert len(rules) == 1 and "SAFLII" not in rules[0] and rules[0].endswith(
        "Rules-MagistrateCourts-GG33487-GNR740-20100823.pdf"
    )
    with pytest.raises(FetchNotConfigured):
        reject_saflii_rules("https://www.justice.gov.za/legislation/rules/Uniform-Rules-SAFLII.pdf")
    justice = justice_pdfs(
        b'<a href="jdm/ST-Judgment-GP-01-2021.pdf">j</a><a href="jdm/ST-Judgment-GP-04-2020-Affidavit.pdf">a</a>'
    )
    assert justice == ["https://www.justice.gov.za/tribunal/jdm/ST-Judgment-GP-01-2021.pdf"]
    matters = siu_matter_urls(
        b'<a href="https://www.siu.org.za/judgements/judgment-in-the-matter-of-alpha/">j</a>'
        b'<a href="https://www.siu.org.za/eskom-judgments/">e</a>'
    )
    assert matters == ["https://www.siu.org.za/judgements/judgment-in-the-matter-of-alpha"]
    assert siu_pdf(
        b'<a href="https://www.siu.org.za/wp-content/uploads/2026/09/judgment-alpha.pdf">p</a>',
        "https://www.siu.org.za/judgements/judgment-in-the-matter-of-alpha/",
    ).endswith("judgment-alpha.pdf")


def test_phoca_and_sars_live_loads_are_mocked(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SARS_MAX_YEARS", "1")
    monkeypatch.setenv("LABOUR_MAX_YEARS", "1")
    monkeypatch.setenv("LAND_MAX_YEARS", "1")
    asyncio.run(_phoca_family())


async def _phoca_family():
    pdf = tiny_pdf("[2026] ZAGPJHC 10 The Johannesburg commercial claim succeeds on a bill of lading.")
    fs = tiny_pdf("[2026] ZAFSHC 3 A commercial claim in Bloemfontein.")
    ec = tiny_pdf("[2026] ZAECMKHC 4 A commercial claim in Makhanda.")
    labour = tiny_pdf("[2026] ZALCJHB 7 A commercial dismissal dispute.")
    lac = tiny_pdf("[2026] ZALAC 2 The appeal on a commercial dismissal.")
    land = tiny_pdf("[2026] ZALCC 1 A commercial land claim.")
    sars = tiny_pdf("The commercial assessment is set aside.")
    comp = tiny_pdf("The merger of two commercial carriers is approved. LM046Jun26.")
    cac = tiny_pdf("[2026] ZACAC 1 A commercial competition appeal.")
    calls: list[str] = []

    async def get(url, headers):
        calls.append(url)
        if "download=" in url or url.lower().split("?", 1)[0].endswith(".pdf"):
            if "download=2" in url:
                return fs
            if "download=3" in url:
                return ec
            if "download=4" in url:
                return labour
            if "download=5" in url:
                return lac
            if "download=6" in url:
                return land
            if "comptrib" in url:
                return comp
            if "cac.org.za" in url:
                return cac
            return pdf
        if "gauteng-division" in url:
            return b'<a href="?download=1:alpha-shipping">Alpha</a>'
        if "free-state" in url:
            return b'<a href="?download=2:beta">Beta</a>'
        if "eastern-cape" in url:
            return b'<a href="?download=3:gamma">Gamma</a>'
        if "court-judgments-lbc" in url:
            return b'<a href="?download=4:labour">L</a>'
        if "labour-court-appeal" in url:
            return b'<a href="?download=5:lac">A</a>'
        if url.rstrip("/").endswith("court-judgments") and "labour" in url:
            return (
                b'<a href="/index.php/courts/specialised-courts/labour-and-labour-appeal-court/court-judgments/'
                b'judgements-labour-court/2025-court-judgments-lbc">2025</a>'
                b'<a href="/index.php/courts/specialised-courts/labour-and-labour-appeal-court/court-judgments/'
                b'judgements-labour-court-2/2025-judgements-labour-court-appeal">LAC</a>'
            )
        if "land-court-judgements/category/" in url and "download=" not in url:
            return b'<a href="?download=6:land">Land</a>'
        if url.rstrip("/").endswith("land-court-judgements"):
            return (
                b'<a href="/landcourt/index.php/land-court-library/judgements/land-court-judgements/'
                b'category/25-judgments-2026">2026</a>'
                b'<a href="/landcourt/index.php/land-court-library/court-rolls/category/26-2026?download=9:roll">roll</a>'
            )
        if "tax-court/tc-" in url:
            return (
                b'<a href="https://www.sars.gov.za/legal-drj-tc-2026-15-sarstc-it-76811-it-2026-zatc-jhb/">c</a>'
            )
        if url.rstrip("/").endswith("tax-court"):
            return (
                b'<a href="https://www.sars.gov.za/legal-counsel/dispute-resolution-judgments/tax-court/tc-2028-2026/">y</a>'
            )
        if "decided-cases" in url:
            return (
                b'<a href="https://www.comptrib.co.za/uploads/Order-LM046Jun26-Non-confidential.pdf">a</a>'
                b'<a href="https://www.comptrib.co.za/uploads/CONFI-secret.pdf">b</a>'
            )
        if url.rstrip("/").endswith("/judgments"):
            return (
                b'<a href="https://www.cac.org.za/wp-content/uploads/2026/09/Alpha-274.CAC.Aug25-Judgment.pdf">j</a>'
                b'<a href="https://www.cac.org.za/wp-content/uploads/2025/01/Appeal-court-rules.pdf">r</a>'
            )
        if "legal-drj" in url:
            return sars
        return b"<html></html>"

    store = MemoryStore()
    for source_id, citation in (
        ("ocj-highcourt", "[2026] ZAGPJHC 10"),
        ("labour-court", "[2026] ZALCJHB 7"),
        ("labour-appeal", "[2026] ZALAC 2"),
        ("landcourt", "[2026] ZALCC 1"),
        ("sars-taxcourt", "IT 76811 [2026] ZATC"),
        ("comptrib", "CompTrib LM046JUN26"),
        ("cac", "[2026] ZACAC 1"),
    ):
        report = await load_wave(store, region="za", only=[_source(source_id)], live=True, http_get=get, on=ON)
        assert report["sources"][0]["documents"] >= 1, source_id
        found = await store.find_by_citation(citation, jurisdiction="za", as_at=None)
        assert found, source_id
    assert not any("court-rolls" in url and "download=9" in url for url in calls)
    assert not any(url.endswith("Appeal-court-rules.pdf") for url in calls)
    assert not any("CONFI-secret.pdf" in url for url in calls)
    assert not any(
        piece in url
        for url in calls
        for piece in ("limpopo", "mpumalanga", "north-west", "northern-cape")
    )


def test_special_tribunal_and_rules_and_gazette_rss():
    asyncio.run(_rest())


async def _rest():
    pdf = tiny_pdf("The commercial contract is set aside. Special Tribunal GP 1/2021.")
    rules_pdf = tiny_pdf(
        "Rule 1. Purpose. The purpose of these rules is to promote access to the courts for commercial matters today."
    )
    seen: list[str] = []

    async def get(url, headers):
        seen.append(url)
        if url.endswith("/jdgm.html"):
            return b'<a href="jdm/ST-Judgment-GP-01-2021.pdf">j</a><a href="jdm/ST-Affidavit.pdf">a</a>'
        if "special-tribunal-judgements" in url and "wp-content" not in url:
            return b'<a href="https://www.siu.org.za/judgements/judgment-in-the-matter-of-alpha/">m</a>'
        if url.rstrip("/").endswith("/judgment-in-the-matter-of-alpha"):
            return b'<a href="https://www.siu.org.za/wp-content/uploads/2026/09/judgment-alpha.pdf">p</a>'
        if url.endswith("judgment-alpha.pdf") or url.endswith("ST-Judgment-GP-01-2021.pdf"):
            return pdf
        if url == UNIFORM_RULES_URL or url == MAGISTRATES_RULES_URL:
            return rules_pdf
        if url.endswith("/documents/notices"):
            return b'<a href="/documents/notices/customs-tariff-sample">n</a>'
        if url.endswith("/rss.xml"):
            return b"<item><link>https://www.gov.za/documents/notices/from-the-rss-sample</link></item>"
        if "customs-tariff" in url or "from-the-rss" in url:
            return (
                b"<h1>Tariff</h1><p>GN R123 in GG 45678 of 1 March 2024</p>"
                b"<p>Notice of a customs tariff amendment for commercial imports.</p>"
            )
        return b""

    store = MemoryStore()
    tribunal = await load_wave(
        store, region="za", only=[_source("special-tribunal")], live=True, http_get=get, on=ON
    )
    assert tribunal["sources"][0]["documents"] >= 1
    assert not any("Affidavit" in url for url in seen)
    magistrates = await load_wave(
        store, region="za", only=[_source("magistrates-rules")], live=True, http_get=get, on=ON
    )
    assert MAGISTRATES_RULES_URL in seen
    assert not any("saflii" in url.lower() for url in seen)
    assert magistrates["sources"][0]["documents"] >= 1
    uniform = await load_wave(
        store, region="za", only=[_source("uniform-rules")], live=True, http_get=get, on=ON
    )
    assert UNIFORM_RULES_URL in seen
    assert uniform["sources"][0]["documents"] >= 1
    gazette = await load_wave(
        store, region="za", only=[_source("govza-gazette")], live=True, http_get=get, on=ON
    )
    assert any("from-the-rss-sample" in url for url in seen)
    assert gazette["sources"][0]["documents"] >= 1
    rss_only = notice_links(b"<link>https://www.gov.za/documents/notices/only-rss</link>")
    assert rss_only == ["https://www.gov.za/documents/notices/only-rss"]


def test_pdf_text_round_trip():
    from agents.zenas.pool.adapters.za_case_common import pdf_text

    text = pdf_text(tiny_pdf("[2026] ZASCA 12 Alpha Shipping commercial appeal."))
    assert "[2026] ZASCA 12" in text
    assert pdf_text(b"not a pdf") == ""
