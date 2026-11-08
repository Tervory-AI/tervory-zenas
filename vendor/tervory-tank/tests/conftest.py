"""Test harness: Tank's package directory is not installed, so put it on sys.path.

pytest loads this file before any test module. That lets test_smoke import
tank.main (which imports skala) and lets the offline skill tests import skala.
"""
import sys
import importlib.util
import os
import ipaddress
import shutil
import socket
from pathlib import Path

import pytest
import pytest_asyncio

_CHASSIS = Path(__file__).resolve().parents[1] / "agents" / "_chassis"
if str(_CHASSIS) not in sys.path:
    sys.path.insert(0, str(_CHASSIS))

_TANK = Path(__file__).resolve().parents[1] / "tank"
if str(_TANK) not in sys.path:
    sys.path.insert(0, str(_TANK))

# paid_http.Client inherits httpx.AsyncClient at import time. Establish its real
# base before offline fixtures temporarily replace the shared httpx constructor;
# otherwise an earlier room fixture permanently changes later Tank lifecycles.
# Minimal compose-only lanes deliberately do not install the Tank HTTP stack.
if all(importlib.util.find_spec(name) is not None for name in ("httpx", "fastapi")):
    __import__("paid_http")


@pytest.fixture(autouse=True)
def _no_skill_temp_sweep(monkeypatch):
    """run_skill sweeps old trigger-skill-* folders (tank/skala.py); never the box's shared /tmp."""
    monkeypatch.setenv("TANK_SKILL_TEMP_SWEEP", "off")


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config: pytest.Config) -> None:
    # pytest-asyncio warns, and will later error, when this is unset. There is no
    # project config file in this lane, so set the ini cache before the plugin reads it.
    if not config.getini("asyncio_default_fixture_loop_scope"):
        config._inicache["asyncio_default_fixture_loop_scope"] = "function"
    _worker_databases()
    _block_dns(config)


def _block_dns(config: pytest.Config) -> None:
    """pytest-socket blocks connect() but not name lookups, so the offline suite
    still sent real DNS queries (Autumbra Watch's lifespan loops alone resolve
    tervory.com, api.github.com, tank, ...). Those lookups run in worker threads
    that a cancelled task cannot stop, and on the CI runner a slow resolver held
    TestClient teardown until the 300 s timeout killed the xdist worker. Under
    --disable-socket, refuse every lookup that is not a loopback name or a literal
    address, the same way a missing host fails."""
    if not config.getoption("--disable-socket", default=False):
        return
    allowed = {"localhost", "testserver"}
    for hosts in config.getoption("--allow-hosts", default=None) or []:
        allowed.update(h.strip().lower() for h in str(hosts).split(",") if h.strip())
    real = socket.getaddrinfo

    def offline_getaddrinfo(host, *args, **kwargs):
        name = host.decode() if isinstance(host, bytes) else host
        if name is None or str(name).lower().rstrip(".") in allowed:
            return real(host, *args, **kwargs)
        try:
            ipaddress.ip_address(str(name).split("%", 1)[0])
        except ValueError:
            raise socket.gaierror(socket.EAI_NONAME, f"offline suite: no DNS for {name}") from None
        return real(host, *args, **kwargs)

    socket.getaddrinfo = offline_getaddrinfo


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    """Expose failed test identities when archived Actions logs are unavailable."""
    if not report.failed or os.environ.get("GITHUB_ACTIONS") != "true":
        return
    path, line, name = report.location
    # Parameter values and exception contents can contain private input. Only
    # publish the source location and unparameterized test name.
    def escape(value: str) -> str:
        return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A").replace(",", "%2C")
    # A crashed xdist worker reports its test with no line number.
    line = (line or 0) + 1
    print(f"\n::error file={escape(path)},line={line},title=Pytest failure::{escape(name.split('[', 1)[0])} ({report.when})", flush=True)


@pytest_asyncio.fixture
async def real_chromium():
    """Required browser tests fail, rather than skip, if CI setup is missing."""
    from playwright.async_api import async_playwright
    executable = os.environ.get('READ1_CHROMIUM_EXECUTABLE') or next(
        (path for name in ('chromium', 'chromium-browser', 'google-chrome', 'google-chrome-stable')
         if (path := shutil.which(name))), None)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(executable_path=executable, args=['--no-sandbox'])
        try:
            yield browser
        finally:
            await browser.close()


# --- Parallel runs (pytest-xdist): one database per worker -------------------
#
# CI gives the suite one Postgres database (POOL_SCHEMA_DATABASE_URL). Tests
# create and drop schemas in it, so two workers sharing it would race. Each
# worker gets its own copy of the database name (pooltest_gw0, ...), and every
# *DATABASE_URL variable that pointed at the shared database is pointed at the
# worker's own one. Serial runs are unchanged.

def _worker_databases() -> None:
    worker = os.environ.get("PYTEST_XDIST_WORKER")
    if not worker:
        return
    import asyncio
    from urllib.parse import urlsplit, urlunsplit

    import asyncpg

    def own_copy(shared: str, name: str) -> str:
        async def create() -> None:
            conn = await asyncpg.connect(shared)
            try:
                if not await conn.fetchval("SELECT 1 FROM pg_database WHERE datname=$1", name):
                    await conn.execute(f'CREATE DATABASE "{name}"')
            finally:
                await conn.close()

        asyncio.run(create())
        return urlunsplit(urlsplit(shared)._replace(path="/" + name))

    shared = os.environ.get("POOL_SCHEMA_DATABASE_URL")
    if shared:
        parts = urlsplit(shared)
        own = own_copy(shared, f"{parts.path.lstrip('/') or 'postgres'}_{worker}")
        for key, value in list(os.environ.items()):
            if key.endswith("DATABASE_URL") and value == shared:
                os.environ[key] = own
    # DATABASE_URL: the Market Lab and other real-Postgres proofs insist on a loopback
    # database whose name ends in _test. Each worker gets market_gw0_test, ... so the
    # proofs' committed rows (queued jobs, seeded tenants) never meet another worker's.
    market = os.environ.get("DATABASE_URL", "")
    parts = urlsplit(market)
    stem = parts.path.lstrip("/")
    if parts.hostname in {"127.0.0.1", "localhost"} and stem.endswith("_test") and market != shared:
        os.environ["DATABASE_URL"] = own_copy(market, f"{stem[:-len('_test')]}_{worker}_test")

    # Radix fixtures truncate their dedicated database between tests. They must
    # not share that database across workers, even though its URL differs from
    # POOL_SCHEMA_DATABASE_URL. Keep both Radix fixture families on the same
    # worker-local copy when they were configured with the same disposable URL.
    copies = {}
    for key in ('RADIX_PASSIVE_TEST_DATABASE_URL', 'RADIX_I8_TEST_DATABASE_URL'):
        url = os.environ.get(key, '')
        parts = urlsplit(url)
        if parts.hostname == '127.0.0.1' and parts.path == '/radix_passive_test' and not parts.password:
            if url not in copies:
                copies[url] = own_copy(url, f'radix_passive_test_{worker}')
            os.environ[key] = copies[url]


# --- Quarantine ---------------------------------------------------------------
#
# tests/quarantine.txt lists tests that already fail on main, each with its
# reason. They still run and are reported (xfail, not hidden). xfail is strict:
# a quarantined test that starts passing (XPASS) fails the run until its entry
# is removed, so no fixed test stays ungated. There is no skip mode: every
# quarantined test runs. Each entry is one exact pytest node id (parameters
# included); a file, class or unparameterised name never covers other tests.
# tests/test_quarantine_rules.py fails if an entry is not on its reviewed
# allow-list, so a new entry needs an explicit edit there too.

_QUARANTINE_FILE = Path(__file__).with_name("quarantine.txt")


def _quarantine() -> list[tuple[str, str, str]]:
    entries = []
    if not _QUARANTINE_FILE.exists():
        return entries
    seen = set()
    for line in _QUARANTINE_FILE.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = [part.strip() for part in line.split("|", 2)]
        if len(parts) != 3:
            raise pytest.UsageError(f"tests/quarantine.txt: bad line: {line!r}")
        node, mode, reason = parts
        if mode != "xfail":
            raise pytest.UsageError(f"tests/quarantine.txt: only strict xfail is allowed, not {mode!r}: {line!r}")
        if not reason or "::" not in node or not node.startswith("tests/") or node in seen:
            raise pytest.UsageError(f"tests/quarantine.txt: bad line: {line!r}")
        seen.add(node)
        entries.append((node, mode, reason))
    return entries


_QUARANTINED = _quarantine()
_QUARANTINE_BY_ID = {entry[0]: entry for entry in _QUARANTINED}


def _quarantine_entry(nodeid: str) -> tuple[str, str, str] | None:
    # Exact node ids only: no prefix, file, class or parameter-family matches.
    return _QUARANTINE_BY_ID.get(nodeid)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    for item in items:
        entry = _quarantine_entry(item.nodeid)
        if entry is not None:
            item.add_marker(pytest.mark.xfail(reason=f"quarantined: {entry[2]}", strict=True))


def pytest_terminal_summary(terminalreporter, exitstatus, config) -> None:  # noqa: ARG001
    if not _QUARANTINED:
        return
    outcomes: dict[str, set[str]] = {}
    for key in ("xfailed", "xpassed", "skipped", "failed", "passed", "error"):
        for report in terminalreporter.stats.get(key, []):
            entry = _quarantine_entry(getattr(report, "nodeid", ""))
            if entry:
                outcomes.setdefault(entry[0], set()).add(key)
    terminalreporter.section(f"quarantine ({len(_QUARANTINED)} entries, tests/quarantine.txt)")
    for node, mode, reason in _QUARANTINED:
        seen = ",".join(sorted(outcomes.get(node, set()))) or "not collected"
        terminalreporter.write_line(f"{seen:>16}  {node}  [{mode}] {reason}")


# --- Tests that never ran -----------------------------------------------------
#
# The offline suite runs with --max-worker-restart 0, so a crashed xdist worker
# ends the run. -q hides xdist's own note about it, and the summary line counts
# only tests that reported. The controller remembers what was collected and
# names the gap. Skipped, xfailed and crashed tests count as ran.

_COLLECTED_IDS: set[str] = set()


@pytest.hookimpl(optionalhook=True)
def pytest_xdist_node_collection_finished(node, ids) -> None:
    if hasattr(node.config, "workerinput"):
        return
    _COLLECTED_IDS.update(ids)  # --dist loadfile: every worker collects the same ids


# pytest reads only pytest_* names; specname keeps this apart from the quarantine summary above.
@pytest.hookimpl(specname="pytest_terminal_summary", trylast=True)
def pytest_terminal_summary_never_ran(terminalreporter, config) -> None:
    if hasattr(config, "workerinput") or not _COLLECTED_IDS:
        return
    ran = {report.nodeid for key in ("passed", "failed", "skipped", "xfailed", "xpassed", "error")
           for report in terminalreporter.stats.get(key, []) if getattr(report, "nodeid", None) in _COLLECTED_IDS}
    never = len(_COLLECTED_IDS) - len(ran)
    if never > 0:
        terminalreporter.write_line(f"::error::{never} of {len(_COLLECTED_IDS)} collected tests never ran")
