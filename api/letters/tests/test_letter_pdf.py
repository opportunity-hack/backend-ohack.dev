"""The template (api/letters/letter_pdf.py): locked wording, sentence-drop for
every optional fact, rendering (accents, CJK, single page, byte-stable)."""
import itertools
import os
import re
from datetime import date

os.environ.setdefault("ENVIRONMENT", "test")

import pytest

from api.letters import letter_pdf as lp

FULL = {
    "template_version": 1, "event_id": "fall26",
    "event_title": "Opportunity Hack Fall 2026", "season_label": "FALL 2026",
    "city_state": "Tempe, Arizona", "venue": "ASU Tempe Campus", "attendance": "in_person",
    "served_dates": ["2026-11-15"], "served_date": "November 15, 2026",
    "name": "Ada Lovelace", "title_line": "Engineer, Analytical Co",
    "n_teams": 6, "nonprofits": ["Zuri's Circle", "NMTSA"], "mentor_teams": ["Byte Bandits"],
    "team_name": "Byte Bandits", "project_title": "Volunteer Scheduler",
    "nonprofit_name": "Zuri's Circle", "awards": ["1st Place"],
}
OPTIONAL = ["title_line", "event_title", "season_label", "city_state", "venue", "attendance",
            "served_date", "n_teams", "nonprofits", "mentor_teams", "team_name",
            "project_title", "nonprofit_name", "awards"]
ROLES = ("judge", "mentor", "volunteer", "hacker")
BANNED = ("{", "}", "None", "undefined", "  ", " ,", " .", ",,", "..", "[]")
# No pronouns, praise adjectives, immigration language or hours.
FORBIDDEN_WORDS = re.compile(
    r"\b(he|she|his|her|him|they|them|their|outstanding|exceptional|excellent|"
    r"invaluable|remarkable|dedicated|tireless|visa|immigration|OPT|CPT|employ\w*|hours?)\b")


def facts(role, **overrides):
    return dict(FULL, role=role, **overrides)


def test_ein_constant():
    assert lp.ORG_EIN == "84-5113049"
    assert "EIN 84-5113049" in lp.FOOTER_LEFT
    assert "501(c)(3) NONPROFIT" in lp.FOOTER_LEFT
    assert "PHOENIX, ARIZONA 85016" in lp.FOOTER_LEFT


def test_judge_wording():
    text = lp.letter_text(facts("judge"))
    assert ("On behalf of the Opportunity Hack team, I am writing to confirm that you served "
            "as a judge at Opportunity Hack Fall 2026, held in person at ASU Tempe Campus in "
            "Tempe, Arizona on November 15, 2026.") in text
    assert ("You applied to judge, were approved by the Opportunity Hack team, and evaluated "
            "6 team projects built for nonprofit organizations, including Zuri's Circle and "
            "NMTSA, against the published Opportunity Hack judging criteria (Scope, "
            "Documentation, Polish and Security).") in text
    assert ("This letter is provided at your request. Thank you for your service and your "
            "support of our nonprofit partners.") in text


def test_hacker_wording():
    text = lp.letter_text(facts("hacker"))
    assert ("You were a member of team Byte Bandits, which built Volunteer Scheduler for "
            "Zuri's Circle. The team received 1st Place.") in text
    assert "you participated in Opportunity Hack Fall 2026" in text


def test_mentor_and_volunteer_wording():
    mentor = lp.letter_text(facts("mentor"))
    assert ("You applied to mentor, were approved by the Opportunity Hack team, and supported "
            "participating teams during the event. Event records show you worked with "
            "Byte Bandits.") in mentor
    assert "served as an event volunteer at" in lp.letter_text(facts("volunteer"))


def test_virtual_and_unknown_attendance():
    assert ", held virtually on November 15, 2026." in lp.letter_text(
        facts("mentor", attendance="virtual"))
    text = lp.letter_text(facts("mentor", attendance=None))
    assert "at Opportunity Hack Fall 2026 on November 15, 2026." in text
    assert "held" not in text


@pytest.mark.parametrize("role", ROLES)
def test_dropping_any_optional_fact_leaves_clean_text(role):
    """Every optional fact, alone and in pairs, set to None / empty."""
    for n in (1, 2):
        for keys in itertools.combinations(OPTIONAL, n):
            f = facts(role, **{k: ([] if isinstance(FULL[k], list) else None) for k in keys})
            text = lp.letter_text(f)
            for bad in BANNED:
                assert bad not in text, (role, keys, bad, text)
            assert not FORBIDDEN_WORDS.search(text), (role, keys, text)


@pytest.mark.parametrize("role", ROLES)
def test_everything_optional_missing(role):
    f = facts(role, **{k: ([] if isinstance(FULL[k], list) else None) for k in OPTIONAL})
    text = lp.letter_text(f)
    for bad in BANNED:
        assert bad not in text, (bad, text)
    assert text.startswith("Ada Lovelace\n\nDear Ada Lovelace,")


def test_singular_team_count_and_long_lists():
    assert "evaluated one team project built for a nonprofit organization," in lp.letter_text(
        facts("judge", n_teams=1, nonprofits=[]))
    many = [f"Org {i}" for i in range(9)]
    assert "Org 4, and others" in lp.letter_text(facts("judge", nonprofits=many))


def test_hacker_partial_team_facts():
    assert "member of team Byte Bandits, which built Volunteer Scheduler." in lp.letter_text(
        facts("hacker", nonprofit_name=None, awards=[]))
    assert "which built a project for Zuri's Circle." in lp.letter_text(
        facts("hacker", project_title=None))
    assert "The team received 1st Place and Best Design." in lp.letter_text(
        facts("hacker", awards=["1st Place", "Best Design"]))


@pytest.mark.parametrize("name", ["Zoë Ñúñez-Østergård", "Nguyễn Thị Minh Khai",
                                  "Ἀλέξανδρος Σεργέεевич", "王小明", "山田 はなこ", "김민준",
                                  "Kenji Watanabe 渡辺健二"])
def test_renders_accents_and_cjk_on_one_page(name):
    assert lp.unsupported_characters(name) == []
    for preview in (False, True):
        pdf = lp.render_pdf(facts("judge", name=name), letter_id="ABCDEFGHJKMN",
                            issued_on=date(2026, 11, 27), preview=preview)
        assert pdf.startswith(b"%PDF")
        assert lp.page_count(pdf) == 1


@pytest.mark.parametrize("role", ROLES)
def test_worst_case_fits_one_page(role):
    long_name = "Maximiliana " * 9
    f = facts(role, name=long_name.strip(), title_line="Senior Principal Staff Engineer, "
              "A Very Long Company Name International Holdings",
              nonprofits=[f"Long Nonprofit Organization Name {i}" for i in range(5)],
              mentor_teams=[f"Team With A Long Name {i}" for i in range(5)],
              project_title="An Extremely Long Project Title For A Volunteer Management "
                            "Platform", awards=["1st Place", "Best Design", "Hackers' Choice"])
    pdf = lp.render_pdf(f, letter_id="ABCDEFGHJKMN", issued_on=date(2026, 11, 27))
    assert lp.page_count(pdf) == 1


def test_unsupported_characters_are_reported():
    assert lp.unsupported_characters("Ada 🙂") == ["🙂"]
    assert lp.unsupported_characters("राम") != []


def test_render_is_byte_stable_and_final_needs_an_id():
    a = lp.render_pdf(facts("mentor"), letter_id="ABCDEFGHJKMN", issued_on=date(2026, 11, 27))
    b = lp.render_pdf(facts("mentor"), letter_id="ABCDEFGHJKMN", issued_on=date(2026, 11, 27))
    assert a == b
    assert a != lp.render_pdf(facts("mentor"), issued_on=date(2026, 11, 27), preview=True)
    with pytest.raises(ValueError):
        lp.render_pdf(facts("mentor"))


def test_signature_image_replaces_font(tmp_path):
    from PIL import Image
    path = tmp_path / "sig.png"
    Image.new("RGBA", (400, 120), (0, 0, 0, 0)).save(path)
    signer = lp.SignerConfig(signature_image=str(path))
    pdf = lp.render_pdf(facts("mentor"), letter_id="ABCDEFGHJKMN",
                        issued_on=date(2026, 11, 27), signer=signer)
    assert lp.page_count(pdf) == 1


def test_markup_escapes_and_routes_cjk():
    assert lp._markup("A&B <x>") == "A&amp;B &lt;x&gt;"
    assert 'name="STSong-Light"' in lp._markup("王小明")
    assert 'name="HeiseiMin-W3"' in lp._markup("山田 はなこ")
    assert 'name="HYSMyeongJo-Medium"' in lp._markup("김민준")
