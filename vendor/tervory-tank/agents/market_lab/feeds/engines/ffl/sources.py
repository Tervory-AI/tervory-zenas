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
            id="comtrade_preview", name="UN Comtrade", kind="series",
            url="https://comtradeapi.un.org/public/v1/preview/C/A/HS",
            licence="UN Comtrade public preview; up to 500 records per request; attribution to UN Statistics Division.",
            terms_checked_at="2026-10-28", cadence="monthly", adapter="comtrade_preview",
            config={"files": ["engines/ffl/comtrade_preview.json"], "reporter": "842", "codes": lists.HS_CODES},
        ),
        _copy(
            id="searxng", name="SearXNG", kind="events", url="https://docs.searxng.org/",
            licence="Public search result titles, links and supplied snippets only; source publishers retain their rights.",
            terms_checked_at="2026-10-28", cadence="daily", adapter="searxng",
            config={"files": ["searxng.json"], "queries": ["firearms ammunition manufacturing trade"]},
        ),
        SourceSpec(
            id="uspto", name="USPTO", kind="events", url="https://data.uspto.gov/",
            licence="USPTO Open Data Portal; API access requires registration and a key.",
            terms_checked_at="2026-10-28", cadence="weekly", enabled=False, allow_bulk=False,
            adapter="link", key_name="USPTO_API_KEY", key_required=True,
        ),
        _copy(
            id="sec_edgar",
            name="Recent SEC company filings",
            kind="events",
            url="https://data.sec.gov/submissions/",
            licence=licences.SEC_EDGAR,
            terms_checked_at="2026-10-28",
            cadence="daily",
            adapter="sec_submissions",
            config={"ciks": lists.SEC_CIKS, "files": ["sec_edgar/ruger.json", "sec_edgar/swbi.json"]},
        ),
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
            name="Federal notices about firearms and hazardous-materials shipping",
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
            name="Federal rules for shipping hazardous materials",
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
            name="Census trade: imports and exports by product, country and port",
            kind="series",
            url="https://api.census.gov/data/timeseries/intltrade/imports/hs",
            licence=licences.CENSUS,
            terms_checked_at=licences.CHECKED,
            cadence="monthly",
            adapter="census_trade",
            config={
                "codes": lists.HS_CODES,
                "start_month": "2013-01",
                "files": [
                    {"path": "census_trade/imports-2024-01.json", "flow": "import"},
                    {"path": "census_trade/exports-2024-01.json", "flow": "export"},
                ],
            },
        ),
        _copy(
            id="eia_diesel",
            name="U.S. diesel prices",
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
                "download_page": "https://www.worldbank.org/en/research/commodity-markets",
                "columns": {
                    "copper": {"ref": "PCOPPUSDM", "title": "Copper", "unit": "USD/metric ton"},
                    "zinc": {"ref": "PZINCUSDM", "title": "Zinc", "unit": "USD/metric ton"},
                    "lead": {"ref": "PLEADUSDM", "title": "Lead", "unit": "USD/metric ton"},
                    "cotton": {"ref": "PCOTTINDUSDM", "title": "Cotton", "unit": "USD/kg"},
                    "cottonaindex": {"ref": "PCOTTINDUSDM", "title": "Cotton", "unit": "USD/kg"},
                },
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
                "unit": "TEU", "geo_level": "port",
                "headings": {"date": ["date", "month"], "month": ["month"], "year": ["year"], "value": ["total_teu", "total", "total TEUs"]},
            },
        ),
        _copy(
            id="bts_freight",
            name="Freight volumes compared with 2000",
            kind="series",
            url="https://www.bts.gov/freight-transportation-services-index-tsi",
            licence=licences.BTS_FREIGHT,
            terms_checked_at=licences.CHECKED,
            cadence="monthly",
            adapter="month_index",
            config={
                "files": ["bts/freight.csv"],
                "ref": "tsifrght",
                "title": "Freight transportation services index",
                "unit": "index 2000=100",
                "headings": {"date": ["date", "month", "observation_date"], "month": ["month"], "year": ["year"], "value": ["index", "Freight TSI", "Freight Transportation Services Index", "TSIFRGHT"]},
            },
        ),
        _copy(
            id="licence_holders",
            name="Paid hunting licence holders by state",
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
                "frequency": "annual", "geo_level": "state", "download_terms": ["hunting"],
                "headings": {"year": ["year", "certification year"], "geo": ["state", "state name"], "value": ["holders", "paid hunting license holders", "paid license holders"]},
            },
        ),
        _link(
            "port_long_beach",
            "Port of Long Beach container statistics",
            "https://polb.com/business/port-statistics/",
            licences.PORT_LB_LINK,
            "monthly",
        ),
        _copy(
            id="port_nynj",
            name="Port of New York and New Jersey loaded containers",
            kind="series",
            url="https://data.ny.gov/Transportation/Loaded-Containers-Monthly-Imports-and-Exports-Thro/629s-5a55",
            licence=licences.OPEN_NY,
            terms_checked_at="2026-10-28",
            cadence="annual",
            adapter="socrata_monthly",
            config={
                "files": ["ports/nynj.json"],
                "api_url": "https://data.ny.gov/resource/629s-5a55.json",
                "year_column": "year", "month_column": "month",
                "type_column": "type", "value_column": "volume",
                "geo": "New York and New Jersey", "geo_level": "port", "unit": "TEU",
                "titles": {"Imports": "Loaded container imports, Port of New York and New Jersey",
                           "Exports": "Loaded container exports, Port of New York and New Jersey"},
            },
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
    # Industry meanings and publisher attribution belong to this engine.
    presentation = {
        "comtrade_preview": {"publisher": "UN Comtrade"},
        "searxng": {"publisher": "SearXNG"},
        "sec_edgar": {"publisher": "U.S. Securities and Exchange Commission", "answer_topics": ["SEC", "EDGAR", "filing", "filings"]},
        "port_nynj": {"publisher": "Port Authority of New York and New Jersey",
                      "geo_states": {"New York and New Jersey": ["NY", "NJ"]}},
        "listing_counts": {"publisher": "Bureau of Alcohol, Tobacco, Firearms and Explosives", "listing_types": {
            "01": "Firearms dealers", "02": "Firearms pawnbrokers", "03": "Collectors of historic firearms",
            "06": "Ammunition manufacturers", "07": "Firearms manufacturers", "08": "Firearms importers",
            "09": "Dealers in destructive devices", "10": "Manufacturers of destructive devices",
            "11": "Importers of destructive devices"},
            "explanation": "Counts of entries in the licence register, grouped by place or licence type. Added and removed entries do not necessarily mean businesses opened or closed."},
        "check_counts": {"publisher": "Federal Bureau of Investigation", "check_types": {"Permit": "Checks for permits", "Permit recheck": "Repeat checks for permits"}, "explanation": "These are checks for firearm purchases or permits. Repeat permit checks concern existing permits. Checks are not a count of sales; one check may cover more than one firearm."},
        "census_population": {"publisher": "U.S. Census Bureau", "notice": "This product uses the Census Bureau Data API but is not endorsed or certified by the Census Bureau."},
        "census_trade": {"publisher": "U.S. Census Bureau", "answer_topics": ["trade", "import", "imports", "export", "exports"], "notice": "This product uses the Census Bureau Data API but is not endorsed or certified by the Census Bureau.", "trade_products": {
            "930630": "Cartridges and parts (excluding shotgun cartridges)", "930621": "Shotgun cartridges",
            "360100": "Propellant powders", "360300": "Igniters, detonators and percussion caps",
            "930599": "Other firearm parts and accessories"}},
        "legiscan": {"publisher": "LegiScan", "answer_topics": ["bill", "bills", "legislation"], "notice": "Bill information from LegiScan, shared under the Creative Commons Attribution 4.0 licence."},
        "federal_register": {"publisher": "Federal Register", "notice": "This saved copy is not the official version. Read the original notice for the full legal text."},
        "ecfr": {"publisher": "Electronic Code of Federal Regulations", "answer_topics": ["regulation", "regulations"], "event_titles": {"49 CFR 171": "General rules for shipping hazardous materials", "49 CFR 172": "Identifying hazardous materials and labelling shipments", "49 CFR 173": "Packing requirements for hazardous materials", "49 CFR 177": "Transporting hazardous materials by road"}, "explanation": "Rules for identifying, packing, labelling and transporting hazardous materials. Read the original regulation before making a shipping decision."},
        "eia_diesel": {"publisher": "U.S. Energy Information Administration"},
        "world_bank_commodities": {"publisher": "World Bank", "notice": "The World Bank: Commodity prices (pink sheet monthly). Shared under the Creative Commons Attribution 4.0 licence."},
        "noaa_alerts": {"publisher": "National Weather Service"},
        "noaa_climate": {"publisher": "National Oceanic and Atmospheric Administration"},
        "port_los_angeles": {"publisher": "Port of Los Angeles", "explanation": "Volume is measured in 20-foot container units. A 40-foot container counts as two units."},
        "bts_freight": {"publisher": "Bureau of Transportation Statistics", "explanation": "The average freight volume in 2000 is set to 100. A reading of 110 means 10% more freight than that baseline; it is not a price."},
        "licence_holders": {"publisher": "U.S. Fish and Wildlife Service"},
    }
    # Human-readable dataset pages, separate from the transport/API URLs above.
    publisher_pages = {
        "comtrade_preview": "https://comtradeplus.un.org/",
        "searxng": "https://docs.searxng.org/",
        "sec_edgar": "https://www.sec.gov/search-filings",
        "port_nynj": "https://data.ny.gov/Transportation/Loaded-Containers-Monthly-Imports-and-Exports-Thro/629s-5a55",
        "listing_counts": lists.LISTING_URL,
        "check_counts": "https://www.fbi.gov/how-we-can-help-you/more-fbi-services-and-information/nics",
        "census_population": "https://www.census.gov/programs-surveys/acs/data.html",
        "legiscan": "https://legiscan.com/",
        "federal_register": "https://www.federalregister.gov/",
        "ecfr": "https://www.ecfr.gov/current/title-49",
        "census_trade": "https://www.census.gov/foreign-trade/data/index.html",
        "eia_diesel": "https://www.eia.gov/petroleum/gasdiesel/",
        "world_bank_commodities": "https://www.worldbank.org/en/research/commodity-markets",
        "noaa_alerts": "https://www.weather.gov/alerts",
        "noaa_climate": "https://www.ncei.noaa.gov/access/monitoring/climate-at-a-glance/statewide/time-series",
        "port_los_angeles": "https://www.portoflosangeles.org/business/statistics/container-statistics",
        "bts_freight": "https://www.bts.gov/freight-transportation-services-index-tsi",
        "licence_holders": "https://www.fws.gov/program/wildlife-and-sport-fish-restoration/apportionments-and-licenses-data",
    }
    for source_id, info in presentation.items():
        info["publisher_url"] = publisher_pages[source_id]
    return [replace(row, config={**row.config, "presentation": presentation.get(row.id, {})}) for row in rows]
