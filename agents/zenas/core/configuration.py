"""Names-only readiness for the room and its separately configured capabilities."""
import os

from agents.zenas.core.seal import key_bytes

ROOM_SETTINGS = ("DATABASE_URL", "TANK_REGION", "TANK_KEY", "ZENAS_PII_KEY")


def room_readiness():
    missing = [name for name in ROOM_SETTINGS if not os.environ.get(name, "").strip()]
    invalid = []
    region = os.environ.get("TANK_REGION", "").strip().lower()
    if region and region not in {"za", "us-east"}:
        invalid.append("TANK_REGION")
    if os.environ.get("ZENAS_PII_KEY") and key_bytes(os.environ["ZENAS_PII_KEY"]) is None:
        invalid.append("ZENAS_PII_KEY")
    if os.environ.get("CHASSIS_RLS_ADMIN_ROLE") and not os.environ.get("CHASSIS_ADMIN_DATABASE_URL"):
        missing.append("CHASSIS_ADMIN_DATABASE_URL")
    if os.environ.get("CHASSIS_ADMIN_DATABASE_URL") and not os.environ.get("CHASSIS_RLS_ADMIN_ROLE"):
        missing.append("CHASSIS_RLS_ADMIN_ROLE")
    return {"ok": not missing and not invalid, "needs_settings": missing, "invalid_settings": invalid}
