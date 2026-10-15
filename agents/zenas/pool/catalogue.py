"""Sources and the terms recorded before any text is copied.

Checked on 2026-10-13 from the publisher pages named in terms_url.
"""

from __future__ import annotations

from agents.zenas.pool.adapters.za_akn import ACTS
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
        notes=(
            "USLM bulk is titles 9, 11, 15, 17, 19, 35, 46, 49 and 50. "
            "GOVINFO_API_KEY is optional and is sent as a header when set. "
            "Without it the public house.gov download is used. The value is never put in a URL."
        ),
        adapter="govinfo-uscode",
        key_name="GOVINFO_API_KEY",
        key_required=False,
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
            "Free Law Project says its bulk opinion files are free of known copyright "
            "restrictions. The REST API requires an account token and asks callers to "
            "keep to the published rate limit. Opinion text is a public record. "
            "Criminal matters are dropped and not stored. These terms are recorded "
            "as information. Data use is the founder's decision."
        ),
        terms_url="https://www.courtlistener.com/help/api/bulk-data/",
        bulk_use="allowed",
        store_full_text=True,
        canonical_url="https://www.courtlistener.com/help/api/bulk-data/",
        notes=(
            "COURTLISTENER_API_TOKEN is read from the environment and sent as a header. "
            "A live load is skipped until it is set. The value is never written into a URL or a log. "
            "Courts: SCOTUS, the thirteen circuits, Delaware Supreme Court and Court of Chancery, "
            "the New York Court of Appeals and Appellate Division, and the Texas Supreme Court."
        ),
        adapter="courtlistener",
        key_name="COURTLISTENER_API_TOKEN",
        key_required=True,
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
        source_id="frap",
        name="Federal Rules of Appellate Procedure",
        region="us-east",
        jurisdiction="us",
        licence=_PD,
        terms=_PD_TERMS + " Text as published in the United States Code appendix.",
        terms_url=_PD_URL,
        bulk_use="allowed",
        store_full_text=True,
        canonical_url="https://www.uscourts.gov/rules-policies/current-rules-practice-procedure/federal-rules-appellate-procedure",
        notes="Full rules. The checked-in sample is Rule 1 when a snapshot is supplied.",
        adapter="frap",
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
        notes=(
            "NY_OPENLEG_API_KEY is the Open Legislation key. This loader will not "
            "place it in a URL. Until a header-based call exists, supply a snapshot. "
            "A live load is skipped until the key is set."
        ),
        adapter="ny-laws",
        key_name="NY_OPENLEG_API_KEY",
        key_required=True,
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
        notes=(
            "Terms are recorded as information. The source is enabled. "
            "No parser is wired yet, so a live load stores nothing until one is."
        ),
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
        notes="Terms are recorded as information. The source is enabled. No parser is wired yet.",
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
        notes="Terms are recorded as information. The source is enabled. No parser is wired yet.",
        adapter="refused",
    ),
    SourceSpec(
        source_id="icc-incoterms",
        name="Incoterms",
        region="us-east",
        jurisdiction="us",
        licence="Copyright International Chamber of Commerce.",
        terms="The ICC sells Incoterms rules. The publisher's conditions are at the terms URL. Recorded as information.",
        terms_url="https://iccwbo.org/business-solutions/incoterms-rules/",
        bulk_use="forbidden",
        store_full_text=False,
        canonical_url="https://iccwbo.org/business-solutions/incoterms-rules/",
        notes="Terms are recorded as information. The source is enabled. No parser is wired yet.",
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
        notes="Terms are recorded as information. The source is enabled. No parser is wired yet.",
        adapter="refused",
    ),
)


_LA_TERMS = (
    "Laws.Africa offers a content API for published consolidated legislation. "
    "Calls use an account token in the Authorization header. Their conditions "
    "are at the terms URL. These terms are recorded as information. "
    "Data use is the founder's decision."
)
_LA_URL = "https://developers.laws.africa/get-started/authentication"
_SAFLII_TERMS = (
    "SAFLII's usage policy says it gives free access so individual end-users can "
    "read, print and copy for personal use, and that it does not provide a service "
    "for other systems to collect documents for republication or for automated or "
    "AI-related use. Judgments are public records supplied by the courts and are "
    "stored as published. These terms are recorded as information. "
    "Data use is the founder's decision. Criminal matters are dropped."
)


def _za_sources() -> tuple[SourceSpec, ...]:
    rows: list[SourceSpec] = []
    for source_id, title, frbr, _number, _year, regulatory in ACTS:
        notes = (
            "Consolidated text from the Laws.Africa content API. "
            "LAWS_AFRICA_API_TOKEN is sent as Authorization: Bearer. "
            "The value is never put in a URL. A live load is skipped until it is set."
        )
        if regulatory:
            notes = "Regulatory compliance only. " + notes
        rows.append(
            SourceSpec(
                source_id=source_id,
                name=title,
                region="za",
                jurisdiction="za",
                licence="Consolidated statute text published through the Laws.Africa content API.",
                terms=_LA_TERMS,
                terms_url=_LA_URL,
                bulk_use="allowed",
                store_full_text=True,
                canonical_url=f"https://api.laws.africa/v3/{frbr}",
                notes=notes,
                adapter=source_id,
                key_name="LAWS_AFRICA_API_TOKEN",
                key_required=True,
            )
        )
    rows.append(
        SourceSpec(
            source_id="saflii",
            name="SAFLII judgments (SCA, Constitutional Court, High Court)",
            region="za",
            jurisdiction="za",
            licence="Judgments are public records, stored as published.",
            terms=_SAFLII_TERMS,
            terms_url="https://www.saflii.org/content/terms.html",
            bulk_use="unknown",
            store_full_text=True,
            canonical_url="https://www.saflii.org/za/cases/",
            notes=(
                "Page routes for the Supreme Court of Appeal, the Constitutional Court, "
                "and the High Court divisions. Commercial and civil judgments are stored. "
                "Criminal matters are dropped. No redaction step runs on a judgment."
            ),
            adapter="saflii",
        )
    )
    rows.append(
        SourceSpec(
            source_id="govza-gazette",
            name="Government Gazette notices",
            region="za",
            jurisdiction="za",
            licence="Public record. Government notice text published on gov.za.",
            terms=(
                "New Acts and notices are published in the Government Gazette and on gov.za. "
                "These terms are recorded as information. Data use is the founder's decision."
            ),
            terms_url="https://www.gov.za/documents/notices",
            bulk_use="allowed",
            store_full_text=True,
            canonical_url="https://www.gov.za/documents/notices",
            notes="HTML notices only. PDF copies are dropped.",
            adapter="govza-gazette",
        )
    )
    rows.append(
        SourceSpec(
            source_id="uniform-rules",
            name="Uniform Rules of Court",
            region="za",
            jurisdiction="za",
            licence="Public record. Rules regulating High Court proceedings.",
            terms=(
                "The Uniform Rules are published by the Rules Board and the Department of Justice. "
                "These terms are recorded as information. Data use is the founder's decision."
            ),
            terms_url="https://www.justice.gov.za/legislation/rules/rules.htm",
            bulk_use="allowed",
            store_full_text=True,
            canonical_url="https://www.justice.gov.za/legislation/rules/rules.htm",
            notes="High Court rules. The checked-in sample is Rule 6 when a snapshot is supplied.",
            adapter="uniform-rules",
        )
    )
    rows.append(
        SourceSpec(
            source_id="magistrates-rules",
            name="Magistrates' Courts Rules",
            region="za",
            jurisdiction="za",
            licence="Public record. Rules regulating Magistrates' Courts proceedings.",
            terms=(
                "The Magistrates' Courts Rules are published by the Rules Board and the Department of Justice. "
                "These terms are recorded as information. Data use is the founder's decision."
            ),
            terms_url="https://www.justice.gov.za/legislation/rules/rules.htm",
            bulk_use="allowed",
            store_full_text=True,
            canonical_url="https://www.justice.gov.za/legislation/rules/rules.htm",
            notes="The checked-in sample is Rule 1 when a snapshot is supplied.",
            adapter="magistrates-rules",
        )
    )
    return tuple(rows)


CATALOGUE = CATALOGUE + _za_sources()


def sources_for_region(region: str) -> list[SourceSpec]:
    return [source for source in CATALOGUE if source.region == region]
