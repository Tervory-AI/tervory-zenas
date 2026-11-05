"""Licence text checked before a source is allowed to copy rows.

Industry-specific sources keep their own wording next to the engine lists.
Anything whose terms are unknown, or which forbid a database copy, is linked.
"""

from __future__ import annotations

CHECKED = "2026-10-13"

SEC_EDGAR = (
    "SEC states that government-created content and EDGAR public filings are "
    "free to access and reuse. The JSON APIs require no authentication or key. "
    "Declare a User-Agent; maximum ten requests per second. Terms checked 2026-10-28. "
    "https://www.sec.gov/about/webmaster-frequently-asked-questions#developers "
    "https://www.sec.gov/search-filings/edgar-application-programming-interfaces"
)

CENSUS = (
    "U.S. government work, public domain (17 U.S.C. § 105). "
    "Census Bureau API terms allow a service to retrieve, display, and analyze "
    "the data. Required notice: This product uses the Census Bureau Data API "
    "but is not endorsed or certified by the Census Bureau. "
    f"Terms checked {CHECKED}. "
    "https://www.census.gov/data/developers/about/terms-of-service.html"
)

LEGISCAN = (
    "Creative Commons Attribution 4.0. LegiScan states that public API data "
    "and the public datasets are under CC BY 4.0, which allows a copy with "
    "attribution. Store bill status, title, and the LegiScan URL. Do not treat "
    "this as a licence to drop the attribution. "
    f"Terms checked {CHECKED}. https://legiscan.com/services/"
)

FEDERAL_REGISTER = (
    "U.S. government work, not subject to copyright. OFR and GPO do not "
    "restrict downstream reuse, except that a republisher may not use official "
    "logos and may not claim to be the official version. "
    f"Terms checked {CHECKED}. "
    "https://www.federalregister.gov/reader-aids/developer-resources/rest-api"
)

ECFR = (
    "U.S. government work, public domain (17 U.S.C. § 105). "
    "Electronic Code of Federal Regulations. "
    f"Terms checked {CHECKED}. "
    "https://www.ecfr.gov/developers/documentation/api/v1"
)

EIA = (
    "U.S. government work, public domain. EIA copyrights policy: publications "
    "may be used and distributed with the source and date acknowledged. "
    "API terms allow retrieve, display, and analysis. "
    f"Terms checked {CHECKED}. https://www.eia.gov/about/copyrights_reuse.cfm"
)

WORLD_BANK = (
    "Creative Commons Attribution 4.0, plus the World Bank dataset terms. "
    "Copy and storage are allowed with attribution: "
    "The World Bank: Commodity prices (pink sheet monthly). "
    f"Terms checked {CHECKED}. https://data.worldbank.org/summary-terms-of-use"
)

NOAA_NWS = (
    "U.S. government work, public domain. NWS API text: open data, free to "
    "use for any lawful purpose. Alerts on the API cover about seven days; "
    "this store keeps each alert it is given. "
    f"Terms checked {CHECKED}. "
    "https://www.weather.gov/documentation/services-web-api"
)

NOAA_NCEI = (
    "U.S. government work, public domain. NCEI open-data policy: NOAA "
    "environmental data are public domain in the United States unless a "
    "specific set says otherwise. Monthly temperature and precipitation here "
    "are agency climate series. "
    f"Terms checked {CHECKED}. https://www.ncei.noaa.gov/"
)

USFWS = (
    "U.S. government work, public domain (17 U.S.C. § 105). "
    "Paid licence-holder counts published by the U.S. Fish and Wildlife "
    "Service. "
    f"Terms checked {CHECKED}. "
    "https://www.fws.gov/program/wildlife-and-sport-fish-restoration/apportionments-and-licenses-data"
)

PORT_LA = (
    "Port of Los Angeles container statistics page: the figures are provided "
    "free of charge, and the Port asks to be credited. That is permission to "
    "store the counts with attribution. "
    f"Terms checked {CHECKED}. "
    "https://www.portoflosangeles.org/business/statistics/container-statistics"
)

BTS_FREIGHT = (
    "U.S. government work, public domain (17 U.S.C. § 105). "
    "Bureau of Transportation Statistics Freight Transportation Services Index. "
    "Taken from the agency, not from FRED. "
    f"Terms checked {CHECKED}. https://www.bts.gov/"
)

FRED_LINK = (
    "Linked only. FRED API terms prohibit storing, caching, or archiving FRED "
    "content, providing stored FRED content to a third party, or incorporating "
    "FRED content in a database (API terms item (l); site terms item (q)). "
    "Checked "
    f"{CHECKED}. Series are linked, not copied. "
    "https://fred.stlouisfed.org/legal/terms/"
)

PORT_LB_LINK = (
    "Linked only. Port of Long Beach publishes container counts, and no "
    "bulk-copy terms were found on the statistics pages. "
    f"Terms checked {CHECKED}. https://polb.com/business/port-statistics/"
)

OPEN_NY = (
    "OPEN-NY Terms of Use permit downloading and reusing published data for "
    "lawful purposes, including private-sector projects. No separate dataset "
    "restriction appears in the port dataset metadata; figures are self-reported "
    "by terminals. Terms checked 2026-10-28. "
    "https://data.ny.gov/dataset/OPEN-NY-Terms-Of-Use/77gx-ii52 "
    "SODA permits unauthenticated queries, with shared-IP throttling and HTTP 429 "
    "on excess use: https://dev.socrata.com/docs/app-tokens.html"
)

PAGE_LINK = (
    "Linked only. No bulk-copy terms were found for these pages, so the text "
    "is not stored and no diff is stored. "
    f"Terms checked {CHECKED}."
)
