"""Z12: court weight, full judgments, citation treatment, and the made-up-case gate."""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agents.zenas.core.convert import COURT_UPDATE_SQL, CITE_TREATMENT_SQL, parse_worked  # noqa: E402
from agents.zenas.core.pointers import resolve_pointer  # noqa: E402
from agents.zenas.core.ladder_store import LadderMemory  # noqa: E402
from agents.zenas.core.precedent import (  # noqa: E402
    conflicting_authority,
    draft_label,
    drop_unverified_cases,
    load_rules,
    matching_rules,
    parse_forum,
    rules_are_draft,
    still_good_law,
    weight,
)
from agents.zenas.core.selftest import (  # noqa: E402
    run_made_up_test,
    run_selftest,
    zenas_skills_may_be_approved,
)
from agents.zenas.core.service import passages_for_model, rule_card_prompt  # noqa: E402
from agents.zenas.pool.adapters.za_case_common import court_identity, parse_case_blob, pdf_text  # noqa: E402
from agents.zenas.pool.chunk import chunk_document  # noqa: E402
from agents.zenas.pool.citations import cite_treatments  # noqa: E402
from agents.zenas.pool.model import SourceSpec  # noqa: E402
from agents.zenas.pool.store import MemoryStore  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "za" / "precedent"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _source() -> SourceSpec:
    return SourceSpec(
        source_id="ocj-highcourt",
        name="High Court judgments",
        region="za",
        jurisdiction="za",
        licence="Public record.",
        terms="Decided cases are public record.",
        terms_url="",
        bulk_use="allowed",
        store_full_text=True,
        canonical_url="https://www.judiciary.org.za",
        notes="",
        adapter="ocj-highcourt",
    )


def _pdf(pages: list[str]) -> bytes:
    """A text PDF with one sentence on each page. No page is omitted."""
    font = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
    contents: list[bytes] = []
    page_objs: list[bytes] = []
    for index, sentence in enumerate(pages):
        safe = sentence.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream = f"BT /F1 12 Tf 36 120 Td ({safe}) Tj ET\n".encode()
        contents.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"endstream")
        content_id = 4 + index * 2
        page_id = 3 + index * 2
        page_objs.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                f"/Contents {content_id} 0 R /Resources << /Font << /F1 {3 + len(pages) * 2} 0 R >> >> >>"
            ).encode()
        )
        del page_id
    kids = " ".join(f"{3 + index * 2} 0 R" for index in range(len(pages)))
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode(),
    ]
    for index in range(len(pages)):
        objects.append(page_objs[index])
        objects.append(contents[index])
    objects.append(font)
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets[1:]:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def test_saved_judgments_get_the_right_court_division_and_bench():
    grancy = court_identity(_read("grancy-zacc-40.txt"), listing="Constitutional Court")
    assert grancy["court"] == "Constitutional Court"
    assert grancy["division"] == ""
    assert grancy["neutral_citation"] == "[2026] ZACC 40"
    assert grancy["bench_size"] == 9
    assert "Mlambo DCJ" in grancy["judges"]
    assert "Tshiqi J" in grancy["judges"]
    assert grancy["appeal"] is True
    assert "Supreme Court of Appeal" in grancy["appeal_from"]

    sca = court_identity(_read("kuzwayo-zasca-167.txt"), listing="Supreme Court of Appeal")
    assert sca["court"] == "Supreme Court of Appeal"
    assert sca["neutral_citation"] == "[2010] ZASCA 167"
    assert sca["bench_size"] == 5
    assert "LEWIS" in sca["judges"]
    assert "K PILLAY AJA" in sca["judges"]
    assert sca["appeal"] is True
    assert "South Gauteng High Court" in sca["appeal_from"]

    full = court_identity(_read("standard-bank-lac.txt"), listing="Labour Appeal Court")
    assert full["court"] == "Labour Appeal Court"
    assert full["seat"] == "Johannesburg"
    assert full["bench_size"] == 3
    assert "Molahlehi DJP" in full["judges"]
    assert "Malindi AJJA" in full["judges"]

    two = court_identity(_read("scrap-zafshc-402.txt"), listing="Free State Division of the High Court")
    assert two["court"] == "High Court"
    assert two["division"] == "Free State Division"
    assert two["seat"] == "Bloemfontein"
    assert two["neutral_citation"] == "[2026] ZAFSHC 402"
    assert two["bench_size"] == 2
    assert "GREYLING-COETZER J" in two["judges"]
    assert "MPAMA AJ" in two["judges"]
    assert two["appeal"] is True

    joburg = court_identity(_read("lateovista.txt"), listing="Gauteng Division of the High Court")
    assert joburg["court"] == "High Court"
    assert joburg["division"] == "Gauteng Division"
    assert joburg["seat"] == "Johannesburg"
    assert joburg["bench_size"] == 1
    assert "H A VAN DER MERWE" in joburg["judges"]

    makhanda = court_identity(_read("macphail.txt"), listing="Eastern Cape Division of the High Court")
    assert makhanda["court"] == "High Court"
    assert makhanda["division"] == "Eastern Cape Division"
    assert makhanda["seat"] == "Makhanda"
    assert makhanda["bench_size"] == 1
    assert "Govindjee J" in makhanda["judges"]


def test_a_disagreement_stays_unknown():
    text = "IN THE HIGH COURT OF SOUTH AFRICA\nGAUTENG DIVISION, PRETORIA\nNeutral citation: Example v Sample [2024] ZAGPJHC 123\n"
    identity = court_identity(text, listing="Gauteng Division of the High Court")
    assert identity["court"] == "High Court"
    assert identity["division"] == "Gauteng Division"
    assert identity["seat"] == ""
    assert identity["neutral_citation"] == "[2024] ZAGPJHC 123"
    assert identity["bench_size"] is None


def test_each_seeded_rule_has_the_expected_weight():
    assert rules_are_draft()
    assert draft_label() == "precedent rules: draft"
    assert all(rule.get("status") == "draft" for rule in load_rules())

    western = {"court": "High Court", "division": "Western Cape Division", "bench_size": 1}
    gauteng = {"court": "High Court", "division": "Gauteng Division", "bench_size": 1}
    gauteng_two = {"court": "High Court", "division": "Gauteng Division", "bench_size": 2}
    gauteng_full = {"court": "High Court", "division": "Gauteng Division", "bench_size": 3}
    regional = {"court": "Regional Court", "division": "Gauteng Division", "bench_size": 1}
    magistrates = {"court": "Magistrates' Court", "division": "Gauteng Division", "bench_size": 1}

    expected = {
        "concourt-binds-every-court": (western, {"court": "Constitutional Court", "bench_size": 9}, "binding", "ConCourt binds every court."),
        "sca-binds-below": (western, {"court": "Supreme Court of Appeal", "bench_size": 5}, "binding", "SCA binds every court below it."),
        "lac-binds-labour": ({"court": "Labour Court"}, {"court": "Labour Appeal Court", "bench_size": 3}, "binding", "The Labour Appeal Court binds the Labour Court."),
        "cac-binds-tribunal": ({"court": "Competition Tribunal"}, {"court": "Competition Appeal Court", "bench_size": 3}, "binding", "The Competition Appeal Court binds the Competition Tribunal."),
        "full-bench-binds-smaller": (gauteng, gauteng_full, "binding", "A full bench binds two-judge benches and single judges in the same division."),
        "two-judge-binds-single": (gauteng, gauteng_two, "binding", "A two-judge bench binds single judges in the same division."),
        "single-judge-binds-lower": (magistrates, gauteng, "binding", "A single judge binds regional and magistrates' courts in that area."),
        "regional-not-precedent": (gauteng, regional, "not_precedent", "Regional courts create no precedent."),
        "magistrates-not-precedent": (gauteng, magistrates, "not_precedent", "Magistrates' courts create no precedent."),
        "tax-appeals": (gauteng_full, {"court": "Tax Court"}, "persuasive", "Tax Court appeals go to a full bench or the SCA."),
        "same-division-single": (gauteng, gauteng, "binding", "same division, follow unless clearly wrong"),
        "other-divisions": (western, gauteng, "persuasive", "Other divisions are persuasive."),
        "cross-division": (western, {"court": "High Court", "division": "Gauteng Division"}, "persuasive", "A cross-division decision is persuasive."),
    }
    for rule_id, (citing, cited, kind, reason) in expected.items():
        matched = [rule for rule in matching_rules(citing, cited) if rule.get("id") == rule_id]
        assert matched, rule_id
        assert matched[0]["weight"] == kind
        assert matched[0]["reason"] == reason
        if rule_id != "cross-division":
            got = weight(citing, cited)
            assert got["weight"] == kind
            assert got["reason"] == reason
            assert got["rule_id"] == rule_id
        else:
            got = weight(citing, cited)
            assert got["weight"] == kind
            assert got["reason"] == reason
            assert got["rule_id"] == "cross-division"
    refused = weight(gauteng, regional)
    assert refused["offer"] is False
    assert refused["weight"] == "not_precedent"

    forum = parse_forum("Western Cape Division, single judge")
    assert forum["court"] == "High Court"
    assert forum["division"] == "Western Cape Division"
    assert forum["bench_size"] == 1
    sca = weight("Western Cape Division, single judge", {"court": "Supreme Court of Appeal", "bench_size": 5})
    assert sca["weight"] == "binding"
    assert sca["reason"] == "SCA binds every court below it."

    clash = conflicting_authority(
        [
            {"court": "High Court", "division": "Gauteng Division", "bench_size": 1, "citation": "A v B", "outcome": "granted"},
            {"court": "High Court", "division": "Gauteng Division", "bench_size": 1, "citation": "C v D", "outcome": "refused"},
        ]
    )
    assert clash["flag"] == "conflicting authority"
    assert len(clash["groups"][0]["cases"]) == 2


def test_treatment_is_read_from_the_court_words():
    saved = cite_treatments(_read("treatment-paragraphs.txt"))
    by_cite = {row["citation"]: row for row in saved}
    declined = by_cite["[2010] ZASCA 1"]
    assert declined["treatment"] == "not followed"
    assert "We decline to follow" in declined["evidence"]
    assert declined["paragraph"] == "12"
    overruled = by_cite["[2001] ZAGPJHC 5"]
    assert overruled["treatment"] == "overruled"
    assert "was overruled in" in overruled["evidence"]
    assert overruled["paragraph"] == "18"
    assert by_cite["Fresh v Case"]["treatment"] != "overruled"
    assert by_cite["[2020] ZASCA 9"]["treatment"] != "overruled"
    distinguished = next(row for row in saved if "Computicket" in row["citation"])
    assert distinguished["treatment"] == "distinguished"
    assert "is distinguishable" in distinguished["evidence"]

    life = cite_treatments(_read("life-healthcare-zacac.txt"))
    real = next(row for row in life if "Computicket" in row["citation"])
    assert real["treatment"] == "distinguished"
    assert "is distinguishable" in real["evidence"]
    assert all(row["treatment"] != "not followed" for row in life)


def test_an_overruled_case_shows_the_warning_and_what_was_checked():
    earlier = {"court": "High Court", "division": "Gauteng Division", "bench_size": 1}
    later = [
        {
            "case": "Fresh v Case [2020] ZASCA 9",
            "court": "Supreme Court of Appeal",
            "bench_size": 5,
            "treatment": "overruled",
            "paragraph": "18",
        },
        {
            "case": "Other Division v Sample [2021] ZAWCHC 4",
            "court": "High Court",
            "division": "Western Cape Division",
            "bench_size": 1,
            "treatment": "not followed",
            "paragraph": "7",
        },
    ]
    note = still_good_law(earlier, later, as_at="2026-10-20")
    assert "Overruled by Fresh v Case [2020] ZASCA 9 para 18" in note
    assert "Not followed by Other Division v Sample [2021] ZAWCHC 4" in note
    assert "checked against 2 later judgments in the library, as at 2026-10-20" in note
    assert "complete" not in note.lower()


def test_the_nightly_made_up_gate_uses_answer_question_or_says_not_run():
    """The 40 traps and 10 real questions go through answer_question, region za.

    With no library on the nightly box the row says not run, never 50 of 50.
    """
    import asyncio

    bare = asyncio.run(run_made_up_test())
    assert bare["path"] == "not run"
    assert bare["skills_may_be_approved"] is False
    assert bare["area"] == "Made-up cases: not run."
    assert "50 of 50" not in bare["area"]
    assert zenas_skills_may_be_approved() is False
    nightly = asyncio.run(run_selftest(LadderMemory(), region="za"))
    made_up = next(run for run in nightly["runs"] if str(run["area"]).startswith("Made-up"))
    assert made_up["area"] == "Made-up cases: not run."
    assert "50 of 50" not in made_up["area"]

    quote = "Leave to appeal is refused."
    judgment = _read("grancy-zacc-40.txt")
    assert quote in judgment
    document = {
        "name": "Grancy Property Limited and Another v Gihwala and Others",
        "citation": "[2026] ZACC 40",
        "quote": quote,
        "text": judgment,
    }
    ladder = LadderMemory()

    class Model:
        def __init__(self) -> None:
            self.calls = []

        async def complete(self, messages, *, task_type: str, provider: str) -> dict:
            self.calls.append(task_type)
            if task_type != "zenas.rule_card":
                return {"content": '{"finding":"met"}', "cost_usd": 0, "model": "za-local"}
            payload = {
                "elements": [
                    {"id": "holding", "test": quote, "citation": document["citation"], "quote": quote}
                ],
                "decision_table": [{"element_id": "holding", "when": {"asked": "yes"}, "finding": "met"}],
            }
            return {"content": json.dumps(payload), "cost_usd": 0, "model": "za-local"}

    result = asyncio.run(run_made_up_test(ladder, ladder, Model(), library=[document]))
    assert result["path"] == "answer_question"
    assert result["asked"] == 50
    assert result["passed"] == 50, result["failures"]
    assert result["skills_may_be_approved"] is True
    assert "path: answer_question" in result["area"]
    assert zenas_skills_may_be_approved(result) is True
    assert len(ladder.rule_cards) == 50
    assert all(card["region"] == "za" and card["jurisdiction"] == "za" for card in ladder.rule_cards)
    real = [item for item in result["questions"] if item["kind"] == "real"]
    traps = [item for item in result["questions"] if item["kind"] == "trap"]
    assert len(real) == 10
    assert len(traps) == 40
    assert all(document["name"] in item["question"] and document["citation"] in item["question"] for item in real)


def test_one_word_in_another_sentence_does_not_taint_every_case():
    text = (
        "[12] The objection was overruled by the trial judge. "
        "In Smith v Jones [2015] ZASCA 12 and Brown v Green [2016] ZAWCHC 9 the point was merely noted."
    )
    rows = cite_treatments(text)
    by_cite = {row["citation"]: row for row in rows}
    for citation in ("Smith v Jones", "Brown v Green", "[2015] ZASCA 12", "[2016] ZAWCHC 9"):
        assert by_cite[citation]["treatment"] == "referred to", citation
        assert not by_cite[citation]["citation"].startswith("In ")
    near = (
        "[4] The court a quo was overruled. Smith v Jones [2015] ZASCA 12 stands. "
        "The appeal was dismissed."
    )
    again = {row["citation"]: row for row in cite_treatments(near)}
    assert again["Smith v Jones"]["treatment"] == "referred to"
    assert again["[2015] ZASCA 12"]["treatment"] == "referred to"


def test_a_later_decision_of_the_same_court_warns():
    assert any(
        rule.get("id") == "same-court-later-decision" and rule.get("status") == "draft"
        for rule in load_rules()
    )
    got = weight({"court": "Supreme Court of Appeal"}, {"court": "Supreme Court of Appeal"})
    assert got["rule_id"] == "same-court-later-decision"
    assert got["weight"] == "binding"
    note = still_good_law(
        {"court": "Supreme Court of Appeal", "bench_size": 5},
        [
            {
                "case": "Later v Earlier [2024] ZASCA 10",
                "court": "Supreme Court of Appeal",
                "bench_size": 5,
                "treatment": "overruled",
                "paragraph": "22",
            }
        ],
        as_at="2026-10-20",
    )
    assert "Overruled by Later v Earlier [2024] ZASCA 10 para 22" in note
    bench = still_good_law(
        {"court": "High Court", "division": "Gauteng Division", "bench_size": 3},
        [
            {
                "case": "Later Bench v Earlier Bench [2024] ZAGPJHC 10",
                "court": "High Court",
                "division": "Gauteng Division",
                "bench_size": 3,
                "treatment": "overruled",
                "paragraph": "4",
            }
        ],
        as_at="2026-10-20",
    )
    assert "Overruled by Later Bench v Earlier Bench [2024] ZAGPJHC 10 para 4" in bench


def test_a_short_name_beside_a_citation_and_the_client_matter_stay():
    library = ["Grancy Property Limited and Another v Gihwala and Others [2026] ZACC 40"]
    text = "Your client in Naidoo v Pillay should sue. Grancy v Gihwala [2026] ZACC 40 applies."
    result = drop_unverified_cases(text, [*library, "Naidoo v Pillay"])
    assert "Naidoo v Pillay" in result
    assert "Grancy v Gihwala" in result
    assert "[2026] ZACC 40" in result
    assert "could not verify" not in result
    miss = drop_unverified_cases("Imaginary Holdings v Nobody [2099] ZASCA 1 decides the point.", library)
    assert "I could not verify Imaginary Holdings v Nobody, so I have left it out" in miss


def test_a_long_judgment_is_stored_and_searched_whole():
    pages = [f"Page {index} of the commercial judgment keeps this sentence." for index in range(300)]
    pages[-1] = "The zeta marker sentence lives on the final page of this judgment."
    text = pdf_text(_pdf(pages))
    assert "Page 0 of the commercial judgment" in text
    assert "zeta marker sentence" in text
    assert text.index("zeta marker") > text.index("Page 0")
    blob = json.dumps(
        {
            "citation": "[2026] ZAGPJHC 300",
            "title": "Long Matter v Short Matter",
            "body": "Gauteng Division of the High Court",
            "url": "https://www.judiciary.org.za/long-matter",
            "text": text,
            "date": "2026-10-20",
        }
    ).encode()
    document = parse_case_blob(blob, _source())[0]
    assert document.text == text.strip() or document.text == text
    assert "zeta marker sentence" in document.text
    assert len(document.text) == len(text.strip() or text)

    async def _search():
        store = MemoryStore(home_region="za")
        await store.record_source(_source())
        chunks = chunk_document(document)
        await store.upsert_version(document, chunks, on=date(2026, 10, 20))
        return await store.search_text("zeta marker", jurisdiction="za", as_at=None)

    import asyncio

    found = asyncio.run(_search())
    assert found
    assert any("zeta marker sentence" in chunk.text for chunk in found)


def test_a_rule_card_never_cuts_a_passage():
    long_quote = "alpha " * 3000
    passages = [
        {"citation": "[2026] ZACC 40", "text": long_quote},
        {"citation": "[2010] ZASCA 167", "text": "A second whole passage about the appeal."},
    ]
    kept, left = passages_for_model(passages, budget=8000)
    assert kept == [passages[0]]
    assert left == 1
    assert long_quote in rule_card_prompt("May the appeal proceed?", passages)
    assert "1 passage was left out" in rule_card_prompt("May the appeal proceed?", passages)
    assert long_quote[:8000] + long_quote[8000:8001] in rule_card_prompt("May the appeal proceed?", passages)


def test_convert_keeps_a_likely_ratio_in_the_court_words():
    real = _read("grancy-zacc-40.txt")
    sentence = "This is an application for leave to appeal against parts of a judgment and order"
    assert sentence in real
    text = (
        f"[1] {sentence} of the Supreme Court of Appeal.\n\n"
        "[2] It is unnecessary to decide the further point, and I express no view. I dissent from the majority.\n"
    )
    words = resolve_pointer({"paragraphs": [1]}, text)
    assert sentence in words
    raw = json.dumps(
        {
            "facts_pointer": {"paragraphs": [1]},
            "question": "Should leave to appeal be granted?",
            "elements": [],
            "holding": "Leave to appeal is refused.",
            "issues": [
                {
                    "question": "Should leave to appeal be granted?",
                    "reasoning_pointer": {"paragraphs": [1]},
                    "order": "Leave to appeal is refused.",
                    "likely_ratio_pointer": {"paragraphs": [1]},
                    "obiter": True,
                    "minority": [{"judge": "Someone", "pointer": {"paragraphs": [1]}}],
                }
            ],
        }
    )
    parsed = parse_worked(raw, text)
    issue = parsed["issues"][0]
    assert issue["reasoning"] == words
    assert issue["likely_ratio"] == words
    assert issue["likely_ratio_label"] == "likely"
    assert issue["obiter"] is False
    assert issue["minority"] == []
    marked = (
        "[1] " + words + "\n\n"
        "[2] It is unnecessary to decide the further point, and I express no view on it. I dissent from the majority.\n"
    )
    obiter = parse_worked(
        json.dumps(
            {
                "facts": words,
                "question": "Should leave to appeal be granted?",
                "elements": [],
                "holding": "Leave to appeal is refused.",
                "issues": [
                    {
                        "question": "Is there a side remark?",
                        "reasoning_pointer": {"paragraphs": [1]},
                        "order": "Leave to appeal is refused.",
                        "likely_ratio_pointer": {"paragraphs": [1]},
                        "obiter_pointer": {"paragraphs": [2]},
                        "minority": [{"judge": "A judge", "pointer": {"paragraphs": [2]}}],
                    }
                ],
            }
        ),
        marked,
    )
    side = obiter["issues"][0]
    assert side["obiter"] is True
    assert "it is unnecessary to decide" in side["obiter_words"].lower()
    assert side["minority"][0]["label"] == "dissent"
    assert side["minority"][0]["words"] not in side["reasoning"]


def test_schema_updates_name_the_new_columns():
    assert "court" in COURT_UPDATE_SQL
    assert "bench_size" in COURT_UPDATE_SQL
    assert "neutral_citation" in COURT_UPDATE_SQL
    assert "treatment" in CITE_TREATMENT_SQL
    assert "evidence" in CITE_TREATMENT_SQL
    assert "paragraph" in CITE_TREATMENT_SQL


def test_nine_paragraphs_and_a_long_span_stay_whole():
    paragraphs = "\n".join(f"[{index}] One two three four five six seven eight paragraph {index}." for index in range(1, 10))
    got = resolve_pointer({"paragraphs": list(range(1, 10))}, paragraphs)
    assert "paragraph 9" in got
    assert got.count("paragraph") == 9
