"""Saved public product page used by offline checks and local Workspace proof."""
from test_tiger import ROOT, _load

URL = "https://www.adafruit.com/product/4296"
LABEL = "Raspberry Pi 4 Model B - 4 GB RAM"
PAGE = ROOT / "tests/tiger_alert_page/fixtures/live/www-adafruit-com--product-4296.html"
READER = _load("tiger_works_reader", ROOT / "tank/skills/tiger.price_extract/1.1.0/run.py")


def saved_page(price: str = "120", stock: str = "InStock") -> str:
    """The original snapshot is $120/in stock; other values are test mutations."""
    html = PAGE.read_text()
    return html.replace('"price":120,', f'"price":{price},').replace("120.00", f"{price}.00").replace("InStock", stock)


async def extract(html: str, url: str) -> dict:
    return READER.extract(html, url)


async def no_paid_call(*args, **kwargs):
    raise AssertionError("The fixture must be read deterministically without a paid call")


async def robots_fixture(url: str) -> str:
    # Offline policy fixture. No request to the saved page's source is made.
    return "User-agent: *\nAllow: /\n"
