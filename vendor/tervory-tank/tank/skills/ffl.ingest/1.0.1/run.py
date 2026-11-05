"""ffl.ingest — the FFL engine's intake. Maps a row from the ATF Federal Firearms Licensee list
into Base's generic record (name, address, city, state, zip, phone) and keeps the licence fields
aside for pass 6. Pure code.

ATF list columns (any case): LIC_REGN, LIC_DIST, LIC_CNTY, LIC_TYPE, LIC_XPRDTE, LIC_SEQN,
LICENSE_NAME, BUSINESS_NAME, PREMISE_STREET, PREMISE_CITY, PREMISE_STATE, PREMISE_ZIP_CODE,
MAIL_STREET, MAIL_CITY, MAIL_STATE, MAIL_ZIP_CODE, VOICE_PHONE.

A row that is not an ATF row passes through unchanged (engine == None behaviour), so a mixed
or generic batch still works.
"""

ATF_KEYS = ("lic_regn", "lic_dist", "lic_cnty", "lic_type", "lic_xprdte", "lic_seqn")


def _k(row: dict) -> dict:
    return {str(k).strip().lower(): ("" if v is None else str(v).strip()) for k, v in row.items()}


def is_atf_row(row: dict) -> bool:
    r = _k(row)
    return sum(1 for k in ATF_KEYS if k in r) >= 4


def licence_number(r: dict) -> str:
    parts = [r.get("lic_regn", ""), r.get("lic_dist", ""), r.get("lic_cnty", ""), r.get("lic_type", ""),
             r.get("lic_xprdte", ""), r.get("lic_seqn", "")]
    if not all(parts):
        return ""
    # Canonical ATF form: R-DD-CCC-TT-XX-SSSSS
    regn, dist, cnty, typ, xp, seq = parts
    return f"{regn}-{dist.zfill(2)}-{cnty.zfill(3)}-{typ.zfill(2)}-{xp.upper()}-{seq.zfill(5)}"


async def run(payload: dict, ctx) -> dict:
    if not is_atf_row(payload):
        return {"record": dict(payload), "engine": None, "mapped": False}
    r = _k(payload)
    business = r.get("business_name", "")
    licensee = r.get("license_name", "")
    name = business or licensee
    record = {
        "name": name,
        "address": r.get("premise_street", "") or r.get("mail_street", ""),
        "city": r.get("premise_city", "") or r.get("mail_city", ""),
        "state": r.get("premise_state", "") or r.get("mail_state", ""),
        "zip": r.get("premise_zip_code", "") or r.get("mail_zip_code", ""),
        "phone": r.get("voice_phone", ""),
        "website": r.get("website", "") or r.get("url", ""),
    }
    engine = {
        "licence_number": licence_number(r),
        "lic_type": r.get("lic_type", "").zfill(2) if r.get("lic_type") else "",
        "lic_xprdte": r.get("lic_xprdte", "").upper(),
        "lic_regn": r.get("lic_regn", ""),
        "lic_dist": r.get("lic_dist", ""),
        "lic_cnty": r.get("lic_cnty", ""),
        "license_name": licensee,
        "business_name": business,
        "trade_name_differs": bool(business and licensee and business.lower() != licensee.lower()),
        "mail_differs": bool(r.get("mail_city") and r.get("premise_city")
                             and r["mail_city"].lower() != r["premise_city"].lower()),
    }
    return {"record": record, "engine": engine, "mapped": True}
