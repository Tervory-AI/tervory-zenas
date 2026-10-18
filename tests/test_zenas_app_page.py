"""Zenas app page: own panel, zenas.vault only, vault starts off."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "workspace" / "app" / "zenas"


def _node_test(name: str) -> None:
    result = subprocess.run(
        [
            "node",
            "--experimental-strip-types",
            "--import",
            str(ROOT / "tests" / "market_lab_register.mjs"),
            "--test",
            str(PAGE / name),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_zenas_frame_rules() -> None:
    _node_test("frame.test.ts")


def test_moot_court_starts_empty() -> None:
    _node_test("moot.test.ts")
    view = (PAGE / "view.tsx").read_text(encoding="utf-8")
    links = view.split("const PAGE_LINKS", 1)[1].split("] as const", 1)[0]
    assert links.index("Matters") < links.index("/review")
    middle = view.split('aria-label="Zenas"', 1)[1].split("</nav>", 1)[0]
    assert middle.index("PAGE_LINKS") < middle.index("{MOOT_LABEL}") < middle.index("Plugins")
    assert "showMoot" in middle
    moot = (PAGE / "moot.ts").read_text(encoding="utf-8")
    assert "There is no training problem yet." in moot
    assert "Law students will use it later, for free." in moot
    assert "return null" in moot
    assert "criminal" not in moot.lower()
    css = (PAGE / "zenas.css").read_text(encoding="utf-8")
    panel = re.search(r"\.zn-panel\s*\{([^}]*)\}", css)
    body = re.search(r"\.zn-body\s*\{([^}]*)\}", css)
    assert panel is not None and body is not None
    assert re.search(r"position:\s*fixed", panel.group(1))
    assert re.search(r"overflow:\s*hidden", panel.group(1))
    assert "overflow-y" not in panel.group(1)
    assert re.search(r"overflow-y:\s*auto", body.group(1))
    brand = view.split('className="zn-brand"', 1)[1].split("</div>", 1)[0]
    foot = view.split('className="zn-panel-foot"', 1)[1].split("</aside>", 1)[0]
    assert "zenas-on-dark.png" in brand
    assert "tervory-logo-reversed.png" not in brand
    assert foot.index("tervory-logo-reversed.png") < foot.index("BOTTOM_LINKS")
    assert "zenas-on-dark.png" not in foot
    panel = view.split("<aside", 1)[1].split("</aside>", 1)[0]
    assert "zn-areas-row" in panel
    assert "zn-areas-menu" not in panel
    assert "Show all" not in view
    assert "DeadlinesPanel" in view
    drawn = (PAGE / "deadlines-panel.tsx").read_text(encoding="utf-8")
    assert "Upcoming" in drawn and "Alerts" in drawn
    assert "Kanos" not in drawn and "Mokoena" not in drawn


def test_page_uses_zenas_vault_and_does_not_turn_it_on() -> None:
    text = "\n".join(path.read_text(encoding="utf-8") for path in PAGE.rglob("*") if path.suffix in {".ts", ".tsx", ".css"})
    assert "This vault is off. Turn it on in Tank." in text
    assert "Search matters, drafts, documents and citations" in text
    assert "ZENAS · CIVIL · SOUTH AFRICA" in text
    assert "Sources" in text
    assert 'type="password"' in text
    assert "zenas-on-dark.png" in text
    assert "tervory-logo-reversed.png" in text
    assert "console." not in text
    assert "set_switch" not in text
    assert "tank.vault" not in text
    for name in ("vault-server.ts", "plugin-actions.ts"):
        server = (PAGE / name).read_text(encoding="utf-8")
        assert 'const VAULT = "zenas.vault"' in server
        assert 'const SQUAD = "zenas"' in server
        assert "resolveVaultSecret" not in server
    css = (PAGE / "zenas.css").read_text(encoding="utf-8")
    assert re.search(r"\.zn-panel\s*\{[^}]*position:\s*fixed", css)
    assert re.search(r"\.zn-panel\s*\{[^}]*overflow:\s*hidden", css)
    assert re.search(r"\.zn-body\s*\{[^}]*overflow-y:\s*auto", css)
    logo = ROOT / "workspace" / "public" / "brand" / "zenas-on-dark.png"
    assert logo.is_file()
    assert logo.stat().st_size > 1000
    nav = (ROOT / "workspace" / "app" / "product-nav.ts").read_text(encoding="utf-8")
    assert '"/zenas"' in nav
