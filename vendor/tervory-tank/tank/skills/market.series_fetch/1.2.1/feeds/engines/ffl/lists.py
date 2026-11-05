"""Choices for the firearms and ammunition case study.

Core adapters read these values. They do not name the industry themselves.
"""

from __future__ import annotations

CHECKED = "2026-10-13"

LISTING_URL = "https://www.atf.gov/firearms/listing-federal-firearms-licensees"
LISTING_MONTH_PAGE = (
    "https://www.atf.gov/firearms/listing-federal-firearms-licensees/complete"
    "?field_ffl_date_value[value][year]={year}&ffl_date_month[value][month]={month}"
)
LISTING_START_YEAR = 2021
CHECK_URL = "https://www.fbi.gov/file-repository/cjis/nics_firearm_checks_-_month_year_by_state.pdf"

LISTING_LICENCE = (
    "U.S. government work, public domain (17 U.S.C. § 105). "
    "ATF monthly federal firearms licensee listings. data.gov marks the "
    "dataset public. No separate no-bulk clause was found on the listing "
    "page. This store keeps counts only, not names or street addresses. "
    f"Terms checked {CHECKED}. "
    "https://www.atf.gov/firearms/listing-federal-firearms-licensees"
)

CHECK_LICENCE = (
    "U.S. government work, public domain (17 U.S.C. § 105). "
    "FBI NICS monthly firearm background-check counts by state and type. "
    "The FBI says these are checks, not sales. "
    f"Terms checked {CHECKED}. "
    "https://www.fbi.gov/file-repository/cjis/nics_firearm_checks_-_month_year_by_state.pdf"
)

# Census HS codes (no dots). Comments say what the heading covers.
HS_CODES = [
    "930630",  # 9306.30 other cartridges and parts, including ammunition
    "930621",  # 9306.21 shotgun cartridges
    "360100",  # 3601.00 propellant powders
    "360300",  # 3603 percussion caps, detonators, igniters (primers)
    "930599",  # 9305.99 parts and accessories of 9301-9304, other
]

LEGISCAN_TOPICS = [
    "firearms",
    "firearm",
    "ammunition",
    "background check",
    "magazine",
    "suppressor",
    "pistol",
    "rifle",
    "shotgun",
    "propellant",
    "primer",
]

# Federal Register agency slugs.
FR_AGENCIES = [
    "alcohol-tobacco-firearms-and-explosives",
    "pipeline-and-hazardous-materials-safety-administration",
]

# 49 CFR hazmat parts the delivery rules rest on.
ECFR_TITLE = "49"
ECFR_PARTS = ["171", "172", "173", "177"]

EIA_SERIES = {
    "id": "EMD_EPD2D_PTE_NUS_DPG",
    "product": "EPD2D",
    "duoarea": "NUS",
    "title": "U.S. on-highway diesel",
    "unit": "dollars per gallon",
}

# Linked, not copied. FRED terms forbid a database copy.
FRED_SERIES = [
    ("fred_tsifrght", "Freight Transportation Services Index", "https://fred.stlouisfed.org/series/TSIFRGHT"),
    ("fred_tsifrghtc", "Freight Transportation Services Index, monthly change", "https://fred.stlouisfed.org/series/TSIFRGHTC"),
    ("fred_pcoppusdm", "Copper price on FRED", "https://fred.stlouisfed.org/series/PCOPPUSDM"),
    ("fred_pzincusdm", "Zinc price on FRED", "https://fred.stlouisfed.org/series/PZINCUSDM"),
    ("fred_pleadusdm", "Lead price on FRED", "https://fred.stlouisfed.org/series/PLEADUSDM"),
    ("fred_pcottindusdm", "Cotton price on FRED", "https://fred.stlouisfed.org/series/PCOTTINDUSDM"),
    ("fred_gasdesw", "On-highway diesel on FRED", "https://fred.stlouisfed.org/series/GASDESW"),
]

CARRIER_PAGES = [
    ("ups_hazmat", "UPS hazardous materials", "https://www.ups.com/us/en/support/shipping-support/shipping-special-care-regulated-items/hazardous-materials.page"),
    ("fedex_hazmat", "FedEx hazardous materials", "https://www.fedex.com/en-us/shipping/hazardous-materials.html"),
    ("usps_pub52", "USPS Publication 52", "https://pe.usps.com/text/pub52/welcome.htm"),
    ("ups_firearms", "UPS firearms shipping rules", "https://www.ups.com/us/en/support/shipping-support/shipping-special-care-regulated-items/prohibited-items/firearms.page"),
]

STATE_PAGES = [
    ("ca_oag_firearms", "California Attorney General, firearms", "https://oag.ca.gov/firearms"),
    ("atf_state_ordinances", "ATF state laws and published ordinances", "https://www.atf.gov/firearms/state-laws-and-published-ordinances"),
]

SEASON_PAGES = [
    ("season_ca", "California hunting seasons", "https://wildlife.ca.gov/Hunting"),
    ("season_tx", "Texas hunting seasons", "https://tpwd.texas.gov/huntwild/hunt/"),
    ("season_fl", "Florida hunting seasons", "https://myfwc.com/hunting/"),
    ("season_pa", "Pennsylvania hunting seasons", "https://www.pgc.pa.gov/"),
    ("season_ny", "New York hunting seasons", "https://www.dec.ny.gov/outdoor/hunting.html"),
]

LISTING_COLUMNS = {
    "id_fields": ["LIC_REGN", "LIC_DIST", "LIC_CNTY", "LIC_TYPE", "LIC_SEQN"],
    "state": "PREMISE_STATE",
    "zip": "PREMISE_ZIP_CODE",
    "type": "LIC_TYPE",
}

LISTING_FILES = ["listing/2024-01.tsv", "listing/2024-02.tsv"]
CHECK_FILES = ["checks/counts.csv"]
LEGISCAN_FILES = ["legiscan/search.json"]
