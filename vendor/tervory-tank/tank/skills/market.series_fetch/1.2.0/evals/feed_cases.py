"""Offline cases for the refreshed bundle, run inside Tank's shipped files only."""
from __future__ import annotations

import json
import runpy
import tempfile
import unittest
import urllib.error
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from feeds.adapters import run_world_bank
from feeds.catalog import sources
from feeds.db import apply_schema, connect
from feeds.fetch import MemoryReader, fetch_source
from feeds.geo import area_label, mentioned_states
from feeds.live import BlockedSite, PoliteClient, census_trade_urls
from feeds.runner import run
from feeds.store import Store


class SavedPublisher:
    """Only the named external responses exist; no network fallback."""

    def __init__(self, pages: dict[str, bytes]) -> None:
        self.pages = pages
        self.calls: list[str] = []

    def get(self, url: str) -> bytes:
        self.calls.append(url)
        return self.pages[url]

    def get_text(self, url: str, headers: dict | None = None) -> str:
        return self.get(url).decode()


class BundledFeedCases(unittest.TestCase):
    def test_listing_requires_a_caller_selected_engine(self) -> None:
        series_fetch = runpy.run_path(str(Path(__file__).resolve().parents[1] / "pure.py"))["series_fetch"]
        with self.assertRaisesRegex(ValueError, "^engine is required$"):
            series_fetch({"adapter": "listing", "ref": "count:state:CA"})

    def test_unknown_adapter_is_refused(self) -> None:
        series_fetch = runpy.run_path(str(Path(__file__).resolve().parents[1] / "pure.py"))["series_fetch"]
        with self.assertRaisesRegex(ValueError, "^unknown adapter unknown$"):
            series_fetch({"adapter": "unknown"})

    def setUp(self) -> None:
        self.sources = {source.id: source for source in sources("ffl")}
        self.conn = connect(":memory:")
        self.addCleanup(self.conn.close)
        apply_schema(self.conn)
        self.store = Store(self.conn)
        for source in self.sources.values():
            self.store.upsert_source(source)
        self.conn.commit()

    def observations(self) -> list[tuple]:
        return [tuple(row) for row in self.conn.execute(
            "SELECT s.title,s.unit,p.date,p.value FROM series s "
            "JOIN points p ON p.series_id=s.id ORDER BY s.title,p.date")]

    def test_geo_names_for_both_county_title_patterns(self) -> None:
        for area, title, expected in [
            ("06037", "Population, Los Angeles County, California", "Los Angeles County, California"),
            ("06001", "Active listings in Alameda", "Alameda County, California"),
            ("48201", "Active listings in Harris", "Harris County, Texas"),
        ]:
            with self.subTest(title=title):
                self.assertEqual(area_label(area, "county", title), expected)
        self.assertEqual(mentioned_states("Listings in West Virginia and CA"), {"WV", "CA"})

    def test_source_links_are_readable_publisher_pages(self) -> None:
        copied = [source for source in self.sources.values() if source.allow_bulk]
        self.assertEqual(len(copied), 14)
        for source in copied:
            with self.subTest(source=source.id):
                url = urlsplit(source.config["presentation"]["publisher_url"])
                self.assertEqual(url.scheme, "https")
                self.assertFalse(url.hostname.startswith("api."))
                self.assertNotIn("/api/", url.path)
                self.assertNotIn("/webservices/", url.path)
                self.assertFalse(url.path.endswith(".json"))
        self.assertTrue(self.sources["eia_diesel"].url.startswith("https://api.eia.gov/"))

    def test_saved_commodity_adapter_uses_published_units(self) -> None:
        source = self.sources["world_bank_commodities"]
        reader = MemoryReader(json.dumps([{}, [
            {"indicator": {"id": "PCOPPUSDM"}, "date": "2024M01", "value": 9000},
            {"indicator": {"id": "PCOTTINDUSDM"}, "date": "2024M01", "value": 2},
        ]]))
        self.assertEqual(run_world_bank(source, reader, self.store, backfill=True), 2)
        self.assertEqual(self.observations(), [
            ("Copper", "USD/metric ton", "2024-01-01", 9000),
            ("Cotton", "USD/kg", "2024-01-01", 2),
        ])

    def test_published_commodity_download_stays_on_publisher(self) -> None:
        source = self.sources["world_bank_commodities"]
        page = source.config["download_page"]
        download = "https://www.worldbank.org/history-monthly.csv"
        publisher = SavedPublisher({
            page: b'<html><a href="/history-monthly.csv">Historical monthly prices</a>'
                  b'<a href="https://elsewhere.test/history-monthly.csv">Historical monthly</a></html>',
            download: b"Date,Copper,Cotton\n2024M01,9000,2\n2024M02,9100,2.1\n",
        })
        self.assertEqual(fetch_source(source, publisher, self.store, backfill=True), 4)
        self.assertEqual(publisher.calls, [page, download])
        self.assertEqual(self.observations(), [
            ("Copper", "USD/metric ton", "2024-01-01", 9000),
            ("Copper", "USD/metric ton", "2024-02-01", 9100),
            ("Cotton", "USD/kg", "2024-01-01", 2),
            ("Cotton", "USD/kg", "2024-02-01", 2.1),
        ])

    def test_published_freight_table_keeps_value_and_reporting_month(self) -> None:
        source = self.sources["bts_freight"]
        publisher = SavedPublisher({source.url: b"observation_date,TSIFRGHT\n2024-01-01,141.4\n"})
        self.assertEqual(fetch_source(source, publisher, self.store, backfill=True), 1)
        self.assertEqual(self.observations(), [
            ("Freight transportation services index", "index 2000=100", "2024-01-01", 141.4),
        ])

    def test_alert_pagination_refuses_a_loop(self) -> None:
        url = "https://api.weather.gov/alerts?status=actual&limit=500"
        publisher = SavedPublisher({url: json.dumps({"features": [], "pagination": {"next": url}}).encode()})
        with self.assertRaisesRegex(RuntimeError, "invalid source pagination"):
            fetch_source(self.sources["noaa_alerts"], publisher, self.store, backfill=True)
        self.assertEqual(publisher.calls, [url])

    def test_trade_parameters_distinguish_imports_and_exports(self) -> None:
        for flow, field in [("import", "I_COMMODITY"), ("export", "E_COMMODITY")]:
            with self.subTest(flow=flow):
                _, url = census_trade_urls("", ["2024-01"], ["930630"], flow)[0]
                query = parse_qs(urlsplit(url).query)
                self.assertEqual(query[field], ["930630"])
                self.assertIn(field, query["get"][0].split(","))
                self.assertNotIn("E_COMMODITY" if flow == "import" else "I_COMMODITY", query)

    def test_runner_records_a_block_and_client_never_retries_site(self) -> None:
        calls = []

        def denied(request, **kwargs):
            calls.append(request.full_url)
            raise urllib.error.HTTPError(request.full_url, 403, "blocked", {}, None)

        client = PoliteClient(opener=denied, delay=0)
        with tempfile.TemporaryDirectory() as folder:
            for _ in range(2):
                result = run(str(Path(folder) / "feeds.sqlite"), engine="ffl",
                             source="noaa_alerts", live=True, client=client)
                self.assertEqual(len(result), 1)
                self.assertEqual(result[0]["last_error"], "blocked")
                self.assertEqual(result[0]["last_rows"], 0)
        with self.assertRaises(BlockedSite):
            client.get("https://api.weather.gov/another-path")
        self.assertEqual(len(calls), 1)
