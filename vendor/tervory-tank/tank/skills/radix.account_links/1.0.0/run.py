"""account_links 1.0.0: code only, supplied tenant evidence only."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys

_NAME = "radix_business_engine_v1"
if _NAME not in sys.modules:
    _spec = spec_from_file_location(_NAME, Path(__file__).resolve().parents[2] / "radix.shared" / "engine_v1.py")
    _module = module_from_spec(_spec)
    sys.modules[_NAME] = _module
    _spec.loader.exec_module(_module)
_ENGINE = sys.modules[_NAME]


async def run(payload: dict, ctx) -> dict:
    return await _ENGINE.run("account_links", payload, ctx)
