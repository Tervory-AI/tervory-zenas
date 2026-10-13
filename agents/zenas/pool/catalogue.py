"""Sources and the terms recorded before any text is copied.

Checked on 2026-10-13 from the publisher pages named in terms_url.
"""

from __future__ import annotations

from agents.zenas.pool.model import SourceSpec

_PD = "Public domain. U.S. government work, 17 U.S.C. § 105."
_PD_TERMS = (
    "GovInfo states that copyright protection is not available for a work of the "
    "United States Government. Bulk reuse of that government text is allowed. "
    "This wave does not copy embedded third-party material."
)
_PD_URL = "https://www.govinfo.gov/about/policies"
_EDICT = (
    "Public domain. Enacted statute text is an edict of government. "
    "Publisher annotations and unofficial commentary are not stored."
)

CATALOGUE: tuple[SourceSpec, ...] = (
    SourceSpec(
        source_id="govinfo-uscode",
        name="United States Code (GovInfo)",
        region="us-east",
        jurisdiction="us",
        licence=_PD,
        terms=_PD_TERMS + " This wave is 49 U.S.C. § 14706 (Carmack). 46 U.S.C. § 30701 note (COGSA) was not at the 2023 HTML path that was tried.",
        terms_url=_PD_URL,
        bulk_use="allowed",
        store_full_text=True,
        canonical_url="https://www.govinfo.gov/app/collection/uscode",
        notes="Edition snapshot, current through the date printed in the GovInfo HTML.",
        adapter="govinfo-uscode",
    ),
    SourceSpec(
        source_id="ecfr",
        name="Code of Federal Regulations (eCFR)",
        region="us-east",
        jurisdiction="us",
        licence=_PD,
        terms=_PD_TERMS + " Point-in-time renderer HTML. Wave: 19 CFR 141.1, 15 CFR 730.1 (EAR), 22 CFR 120.1 (ITAR), 31 CFR 501.101 (OFAC). Not the whole of those parts.",
        terms_url="https://www.ecfr.gov/",
        bulk_use="allowed",
        store_full_text=True,
        canonical_url="https://www.ecfr.gov/",
        notes="The as-of date is the /on/YYYY-MM-DD path in the renderer metadata.",
        adapter="ecfr",
    ),
    SourceSpec(
        source_id="federal-register",
        name="Federal Register",
        region="us-east",
        jurisdiction="us",
        licence=_PD,
        terms=_PD_TERMS + " API documents are government works. This wave stores the official abstract of 91 FR 64840, a CBP proposed rule. The full notice body is fetched only when a full_text field is present.",
        terms_url="https://www.federalregister.gov/reader-aids/developer-resources/rest-api",
        bulk_use="allowed",
        store_full_text=True,
        canonical_url="https://www.federalregister.gov/",
        notes="Proposed rules are stored as guidance. Rules are stored as regulations.",
        adapter="federal-register",
    ),
    SourceSpec(
        source_id="usitc-htsus",
        name="Harmonized Tariff Schedule of the United States",
        region="us-east",
        jurisdiction="us",
        licence=_PD + " Enacted schedule published by the U.S. International Trade Commission, 19 U.S.C. § 1202.",
        terms=(
            "The USITC-published tariff text may be stored. WCO Harmonized System "
            "explanatory notes are a separate source and are not stored. "
            "This wave is heading 8901 (vessels)."
        ),
        terms_url="https://hts.usitc.gov/",
        bulk_use="allowed",
        store_full_text=True,
        canonical_url="https://hts.usitc.gov/",
        notes="Rate lines under the heading are sections, not separate instruments.",
        adapter="usitc-htsus",
    ),
    SourceSpec(
        source_id="courtlistener",
        name="CourtListener federal appellate commercial opinions",
        region="us-east",
        jurisdiction="us",
        licence="Opinion text: public domain (edicts of government). CourtListener bulk files: free of known copyright restrictions (Free Law Project).",
        terms=(
            "Bulk opinion files may be stored. The membership API is not a bulk-copy "
            "licence for this pool. Federal appellate courts only, and only matters "
            "identified as commercial. Criminal matters are dropped and not stored. "
            "No opinion text is in the checked-in wave."
        ),
        terms_url="https://www.courtlistener.com/help/api/bulk-data/",
        bulk_use="allowed",
        store_full_text=True,
        canonical_url="https://www.courtlistener.com/help/api/bulk-data/",
        notes="COURTLISTENER_API_TOKEN is the env name for a later reviewed fetch. It is not read by the checked-in nightly path.",
        adapter="courtlistener",
    ),
    SourceSpec(
        source_id="cap",
        name="Caselaw Access Project",
        region="us-east",
        jurisdiction="us",
        licence="Unrestricted as of 2024-03-01. Harvard ended the commercial-use limits on this digitization.",
        terms=(
            "Harvard Library Innovation Lab released the corpus without access or use "
            "restrictions when the LexisNexis exclusivity ended on 1 March 2024. "
            "Criminal matters are not ingested. The bulk dump is not in this repository."
        ),
        terms_url="https://lil.law.harvard.edu/blog/2024/03/26/transitions-for-the-caselaw-access-project/",
        bulk_use="allowed",
        store_full_text=True,
        canonical_url="https://case.law/",
        notes="CourtListener now carries this collection. Document count stays zero until a snapshot is supplied.",
        adapter="cap",
    ),
    SourceSpec(
        source_id="frcp",
        name="Federal Rules of Civil Procedure",
        region="us-east",
        jurisdiction="us",
        licence=_PD,
        terms=_PD_TERMS + " Text as published in the United States Code appendix. This wave is Rule 1, including the advisory-committee notes printed by GPO.",
        terms_url=_PD_URL,
        bulk_use="allowed",
        store_full_text=True,
        canonical_url="https://www.uscourts.gov/rules-policies/current-rules-practice-procedure/federal-rules-civil-procedure",
        notes="Local rules of particular districts are not in this wave. The panel has not named the districts.",
        adapter="frcp",
    ),
    SourceSpec(
        source_id="fre",
        name="Federal Rules of Evidence",
        region="us-east",
        jurisdiction="us",
        licence=_PD,
        terms=_PD_TERMS + " Text as published in the United States Code appendix. This wave is Rule 401.",
        terms_url=_PD_URL,
        bulk_use="allowed",
        store_full_text=True,
        canonical_url="https://www.uscourts.gov/rules-policies/current-rules-practice-procedure/federal-rules-evidence",
        notes="Rule 401 is the relevance rule used in commercial cases.",
        adapter="fre",
    ),
    SourceSpec(
        source_id="delaware-code",
        name="Delaware General Corporation Law",
        region="us-east",
        jurisdiction="us",
        licence=_EDICT,
        terms=(
            "The enacted text on Delaware Code Online may be stored. Annotations are not. "
            "This wave is 8 Del. C. § 101 only."
        ),
        terms_url="https://delcode.delaware.gov/title8/c001/sc01/index.html",
        bulk_use="allowed",
        store_full_text=True,
        canonical_url="https://delcode.delaware.gov/",
        notes="The official page does not state one effective date for § 101, so effective_from is open.",
        adapter="delaware-code",
    ),
    SourceSpec(
        source_id="ny-laws",
        name="New York enacted commercial statutes",
        region="us-east",
        jurisdiction="us",
        licence=_EDICT,
        terms=(
            "Enacted New York text may be stored. McKinney's annotations and the ALI "
            "official UCC text may not. nysenate.gov returned HTTP 403, so no section was saved."
        ),
        terms_url="https://www.nysenate.gov/legislation/laws/UCC",
        bulk_use="allowed",
        store_full_text=True,
        canonical_url="https://www.nysenate.gov/legislation/laws",
        notes="Zero documents in this wave.",
        adapter="ny-laws",
    ),
    SourceSpec(
        source_id="texas-statutes",
        name="Texas enacted commercial statutes",
        region="us-east",
        jurisdiction="us",
        licence=_EDICT,
        terms=(
            "Enacted Texas text may be stored. The capitol statutes site returned a "
            "script shell with no enacted text, so no section was saved. "
            "Vernon's annotations are not stored."
        ),
        terms_url="https://statutes.capitol.texas.gov/",
        bulk_use="allowed",
        store_full_text=True,
        canonical_url="https://statutes.capitol.texas.gov/",
        notes="Zero documents in this wave.",
        adapter="texas-statutes",
    ),
    SourceSpec(
        source_id="uncitral-cisg",
        name="CISG (UNCITRAL publication)",
        region="us-east",
        jurisdiction="us",
        licence="United Nations / UNCITRAL copyright.",
        terms=(
            "UNCITRAL's copyright FAQ says its texts are copyrighted and permission is "
            "required to reproduce them. Permission is granted per edition, not as a "
            "standing bulk licence. The convention text is not stored."
        ),
        terms_url="https://uncitral.un.org/en/about/faq/copyrightpolicy",
        bulk_use="forbidden",
        store_full_text=False,
        canonical_url="https://uncitral.un.org/en/texts/salegoods/conventions/sale_of_goods/cisg",
        notes="Link only. Ask uncitrallibrary@un.org before any full-text ingest.",
        adapter="refused",
    ),
    SourceSpec(
        source_id="uncitral-model-law",
        name="UNCITRAL model laws",
        region="us-east",
        jurisdiction="us",
        licence="United Nations / UNCITRAL copyright.",
        terms=(
            "Model laws are copyrighted UNCITRAL texts and need permission to reproduce. "
            "They are model texts, not an enacted statute. Not stored."
        ),
        terms_url="https://uncitral.un.org/en/about/faq/copyrightpolicy",
        bulk_use="forbidden",
        store_full_text=False,
        canonical_url="https://uncitral.un.org/en/texts",
        notes="Link only.",
        adapter="refused",
    ),
    SourceSpec(
        source_id="ali-ucc-official",
        name="UCC official text and comments",
        region="us-east",
        jurisdiction="us",
        licence="Copyright American Law Institute and Uniform Law Commission.",
        terms=(
            "The official text and comments are not stored. Use the enacted state "
            "statutes. Those enacted texts are separate sources."
        ),
        terms_url="https://www.uniformlaws.org/acts/ucc",
        bulk_use="forbidden",
        store_full_text=False,
        canonical_url="https://www.uniformlaws.org/acts/ucc",
        notes="Link only.",
        adapter="refused",
    ),
    SourceSpec(
        source_id="icc-incoterms",
        name="Incoterms",
        region="us-east",
        jurisdiction="us",
        licence="Copyright International Chamber of Commerce.",
        terms="Incoterms rules are sold by the ICC. Bulk copy is not allowed. Link only.",
        terms_url="https://iccwbo.org/business-solutions/incoterms-rules/",
        bulk_use="forbidden",
        store_full_text=False,
        canonical_url="https://iccwbo.org/business-solutions/incoterms-rules/",
        notes="Link only.",
        adapter="refused",
    ),
    SourceSpec(
        source_id="wco-hs-notes",
        name="WCO Harmonized System explanatory notes",
        region="us-east",
        jurisdiction="us",
        licence="Copyright World Customs Organization.",
        terms=(
            "The explanatory notes are not a U.S. government work and are not stored. "
            "The enacted HTSUS is the usitc-htsus source."
        ),
        terms_url="https://www.wcoomd.org/en/topics/nomenclature/instrument-and-tools.aspx",
        bulk_use="forbidden",
        store_full_text=False,
        canonical_url="https://www.wcoomd.org/en/topics/nomenclature/instrument-and-tools.aspx",
        notes="Link only.",
        adapter="refused",
    ),
)


def sources_for_region(region: str) -> list[SourceSpec]:
    return [source for source in CATALOGUE if source.region == region]
