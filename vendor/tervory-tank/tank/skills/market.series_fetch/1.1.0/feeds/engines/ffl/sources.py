"""FFL engine sources. Core feeds call this; they do not import these lists."""

from __future__ import annotations

from dataclasses import replace

from feeds import licences
from feeds.engines.ffl import lists
from feeds.keys import FRED_KEY
from feeds.models import SourceSpec


def _copy(**kwargs: object) -> SourceSpec:
    return SourceSpec(allow_bulk=True, enabled=True, **kwargs)  # type: ignore[arg-type]


def _link(
    source_id: str,
    name: str,
    url: str,
    licence: str,
    cadence: str,
    *,
    watch: bool = False,
) -> SourceSpec:
    config = {"watch": True} if watch else {}
    return SourceSpec(
        id=source_id,
        name=name,
        kind="link",
        url=url,
        licence=licence,
        terms_checked_at=licences.CHECKED,
        cadence=cadence,
        enabled=True,
        allow_bulk=False,
        adapter="link",
        config=config,
    )


def sources() -> list[SourceSpec]:
    rows: list[SourceSpec] = [
        _copy(
            id="listing_counts",
            name="Monthly licence listings, counts by place and type",
            kind="series",
            url=lists.LISTING_URL,
            licence=lists.LISTING_LICENCE,
            terms_checked_at=licences.CHECKED,
            cadence="monthly",
            adapter="listing",
            config={
                "files": lists.LISTING_FILES,
                "columns": lists.LISTING_COLUMNS,
                "crosswalk": "geo/zip_county.csv",
                "month_page": lists.LISTING_MONTH_PAGE,
                "start_year": lists.LISTING_START_YEAR,
            },
        ),
        _copy(
            id="check_counts",
            name="Monthly background-check counts by state and type",
            kind="series",
            url=lists.CHECK_URL,
            licence=lists.CHECK_LICENCE,
            terms_checked_at=licences.CHECKED,
            cadence="monthly",
            adapter="typed_counts",
            config={
                "files": lists.CHECK_FILES,
                "date_column": "month",
                "geo_column": "state",
                "type_column": "type",
                "value_column": "count",
                "geo_level": "state",
                "frequency": "monthly",
                "unit": "checks",
                "title": "{type} checks in {geo}",
                "pdf_type": "total",
                "pdf_title": "Monthly checks in {geo}",
            },
        ),
        _copy(
            id="census_population",
            name="Census population by state and county",
            kind="series",
            url="https://api.census.gov/data/2023/acs/acs5",
            licence=licences.CENSUS,
            terms_checked_at=licences.CHECKED,
            cadence="annual",
            adapter="census_population",
            config={
                "year": "2023",
                "files": [
                    "census_population/acs-v1.json",
                    "census_population/acs-v2.json",
                    "census_population/county-v1.json",
                ],
            },
        ),
        _copy(
            id="legiscan",
            name="State bill status changes",
            kind="events",
            url="https://legiscan.com/legiscan",
            licence=licences.LEGISCAN,
            terms_checked_at=licences.CHECKED,
            cadence="weekly",
            adapter="legiscan",
            config={"files": lists.LEGISCAN_FILES, "topics": lists.LEGISCAN_TOPICS},
        ),
        _copy(
            id="federal_register",
            name="Federal Register notices for the engine's agencies",
            kind="events",
            url="https://www.federalregister.gov/api/v1/documents.json",
            licence=licences.FEDERAL_REGISTER,
            terms_checked_at=licences.CHECKED,
            cadence="weekly",
            adapter="federal_register",
            config={"files": ["federal_register/page.json"], "agencies": lists.FR_AGENCIES},
        ),
        _copy(
            id="ecfr",
            name="49 CFR parts on the engine's list",
            kind="events",
            url="https://www.ecfr.gov/current/title-49",
            licence=licences.ECFR,
            terms_checked_at=licences.CHECKED,
            cadence="monthly",
            adapter="ecfr",
            config={
                "files": ["ecfr/title-49.json"],
                "title_number": lists.ECFR_TITLE,
                "parts": lists.ECFR_PARTS,
            },
        ),
        _copy(
            id="census_trade",
            name="Census trade by code, partner, and port",
            kind="series",
            url="https://api.census.gov/data/timeseries/intltrade/imports/hs",
            licence=licences.CENSUS,
            terms_checked_at=licences.CHECKED,
            cadence="monthly",
            adapter="census_trade",
            config={
                "codes": lists.HS_CODES,
                "files": [
                    {"path": "census_trade/imports-2024-01.json", "flow": "import"},
                    {"path": "census_trade/exports-2024-01.json", "flow": "export"},
                ],
            },
        ),
        _copy(
            id="eia_diesel",
            name="EIA on-highway diesel",
            kind="series",
            url="https://api.eia.gov/v2/petroleum/pri/gnd/data/",
            licence=licences.EIA,
            terms_checked_at=licences.CHECKED,
            cadence="weekly",
            adapter="eia",
            config={"files": ["eia/diesel.json"], "series": lists.EIA_SERIES},
        ),
        _copy(
            id="world_bank_commodities",
            name="World Bank monthly commodity prices",
            kind="series",
            url="https://api.worldbank.org/v2/country/WLD/indicator/PCOPPUSDM?format=json",
            licence=licences.WORLD_BANK,
            terms_checked_at=licences.CHECKED,
            cadence="monthly",
            adapter="world_bank",
            config={
                "files": ["world_bank/commodities.json"],
                "indicators": ["PCOPPUSDM", "PZINCUSDM", "PLEADUSDM", "PCOTTINDUSDM"],
            },
        ),
        _copy(
            id="noaa_alerts",
            name="Severe-weather alerts",
            kind="events",
            url="https://api.weather.gov/alerts/active",
            licence=licences.NOAA_NWS,
            terms_checked_at=licences.CHECKED,
            cadence="daily",
            adapter="noaa_alerts",
            config={"files": ["noaa/alerts.json"]},
        ),
        _copy(
            id="noaa_climate",
            name="Monthly temperature and precipitation by state",
            kind="series",
            url="https://www.ncei.noaa.gov/cdo-web/webservices/v2",
            licence=licences.NOAA_NCEI,
            terms_checked_at=licences.CHECKED,
            cadence="monthly",
            adapter="noaa_climate",
            config={"files": ["noaa/climate.json"], "datatypes": ["TAVG", "PRCP"]},
        ),
        _copy(
            id="port_los_angeles",
            name="Port of Los Angeles container volume",
            kind="series",
            url="https://www.portoflosangeles.org/business/statistics/container-statistics",
            licence=licences.PORT_LA,
            terms_checked_at=licences.CHECKED,
            cadence="monthly",
            adapter="port_csv",
            config={
                "files": ["ports/los_angeles.csv"],
                "month_column": "month",
                "value_column": "total_teu",
                "geo": "Los Angeles",
                "title": "Container volume, Port of Los Angeles",
            },
        ),
        _copy(
            id="bts_freight",
            name="BTS freight transportation services index",
            kind="series",
            url="https://www.bts.gov/",
            licence=licences.BTS_FREIGHT,
            terms_checked_at=licences.CHECKED,
            cadence="monthly",
            adapter="month_index",
            config={
                "files": ["bts/freight.csv"],
                "ref": "tsifrght",
                "title": "Freight transportation services index",
                "unit": "index 2000=100",
            },
        ),
        _copy(
            id="licence_holders",
            name="Paid licence holders by state",
            kind="series",
            url="https://www.fws.gov/program/wildlife-and-sport-fish-restoration/apportionments-and-licenses-data",
            licence=licences.USFWS,
            terms_checked_at=licences.CHECKED,
            cadence="annual",
            adapter="annual_csv",
            config={
                "files": ["holders/annual.csv"],
                "year_column": "year",
                "geo_column": "state",
                "value_column": "holders",
                "unit": "holders",
                "title": "Paid licence holders in {geo}, {year}",
            },
        ),
        _link(
            "port_long_beach",
            "Port of Long Beach container statistics",
            "https://polb.com/business/port-statistics/",
            licences.PORT_LB_LINK,
            "monthly",
        ),
        _link(
            "port_nynj",
            "Port of New York and New Jersey loaded containers",
            "https://data.ny.gov/Transportation/Loaded-Containers-Monthly-Imports-and-Exports-Thro/629s-5a55",
            licences.PORT_NYNJ_LINK,
            "monthly",
        ),
    ]
    for source_id, name, url in lists.FRED_SERIES:
        rows.append(
            replace(
                _link(source_id, name, url, licences.FRED_LINK, "monthly"),
                key_name=FRED_KEY,
                key_required=True,
            )
        )
    for source_id, name, url in lists.CARRIER_PAGES:
        rows.append(_link(source_id, name, url, licences.PAGE_LINK, "weekly", watch=True))
    for source_id, name, url in lists.STATE_PAGES:
        rows.append(_link(source_id, name, url, licences.PAGE_LINK, "weekly", watch=True))
    for source_id, name, url in lists.SEASON_PAGES:
        rows.append(_link(source_id, name, url, licences.PAGE_LINK, "weekly", watch=True))
    return rows
