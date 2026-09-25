"""Tests for search-term normalization and symbolic-title expansion (task 112).

A Spotify title is not always a searchable string: Coldplay really does have
tracks titled ``❤️`` and ``♾️``, and titles carry decoration emoji, full-width
text and unusual separators. These tests pin the expansion layer's contract:

- the first form is the title as the query builder already normalized it, so
  ordinary titles produce exactly the queries they produced before,
- symbol-bearing titles gain the symbol-preserving form and named forms,
- non-Latin text is kept as written,
- duplicates and meaningless expansions are dropped.
"""

from __future__ import annotations

from spotm3u.models import Track
from spotm3u.online import build_search_queries, search_title_forms
from spotm3u.online.search import MAX_EXPANDED_TITLES


def _query(track: Track) -> tuple[str, ...]:
    """Build queries with a single artist, the common case in these tests."""
    return build_search_queries(track)


# ---------------------------------------------------------------------------
# Title forms
# ---------------------------------------------------------------------------


def test_emoji_only_title_expands_into_named_forms() -> None:
    """The Coldplay ``❤️`` single: the symbol is the whole title."""
    assert search_title_forms("❤️") == ("❤", "heart", "red heart")


def test_infinity_symbol_title_expands_to_infinity() -> None:
    """The Coldplay ``♾️`` single."""
    assert search_title_forms("♾️") == ("♾", "infinity")


def test_symbolic_titles_expand_common_music_symbols() -> None:
    assert search_title_forms("★") == ("★", "star")
    assert search_title_forms("∞") == ("∞", "infinity")
    assert search_title_forms("🔥") == ("🔥", "fire")
    assert search_title_forms("№ 1 ♡") == ("no 1", "no 1 ♡", "no 1 heart", "no 1 red heart")


def test_emoji_beside_normal_text_keeps_the_text_form_first() -> None:
    assert search_title_forms("My Universe ❤️") == (
        "my universe",
        "my universe ❤",
        "my universe heart",
        "my universe red heart",
    )


def test_emoji_named_from_unicode_when_not_curated() -> None:
    """An emoji with no curated term still expands, via its Unicode name."""
    assert search_title_forms("Song 🐈") == ("song", "song 🐈", "song cat")


def test_symbols_without_a_useful_term_are_only_preserved() -> None:
    """Note glyphs and typographic signs never become queries like ``musical note``."""
    assert search_title_forms("Song 🎵🎶") == ("song", "song 🎵🎶")
    assert search_title_forms("Song ©") == ("song", "song ©")


def test_repeated_symbols_do_not_duplicate_the_named_form() -> None:
    assert search_title_forms("❤️❤️") == ("❤❤", "heart", "red heart")


def test_punctuation_and_unusual_separators_collapse() -> None:
    assert search_title_forms("Song — Title") == ("song title",)
    assert search_title_forms("Song｜Title") == ("song title",)
    assert search_title_forms("Song •• Title") == ("song title",)
    assert search_title_forms("Song\u200bName") == ("song name",)
    assert search_title_forms("  Song   Name  ") == ("song name",)


def test_unicode_compatibility_variants_fold_to_their_plain_form() -> None:
    """Full-width and compatibility characters search as their plain form."""
    assert search_title_forms("Ｓｏｎｇ Ｎａｍｅ") == ("song name",)
    assert search_title_forms("𝐒𝐨𝐧𝐠") == ("song",)


def test_non_latin_titles_are_preserved_without_transliteration() -> None:
    assert search_title_forms("演员") == ("演员",)
    assert search_title_forms("薛之谦 演员 ❤️") == (
        "薛之谦 演员",
        "薛之谦 演员 ❤",
        "薛之谦 演员 heart",
        "薛之谦 演员 red heart",
    )


def test_ordinary_titles_produce_one_unchanged_form() -> None:
    assert search_title_forms("Song Name") == ("song name",)
    assert search_title_forms("Can't Stop (Live from Tokyo)") == ("can t stop live from tokyo",)


def test_titles_without_any_searchable_content_have_no_forms() -> None:
    assert search_title_forms("") == ()
    assert search_title_forms("   ") == ()
    assert search_title_forms("、、、") == ()
    assert search_title_forms(None) == ()


# ---------------------------------------------------------------------------
# Query generation
# ---------------------------------------------------------------------------


def test_ordinary_titles_keep_their_existing_queries() -> None:
    """The expansion layer must not change search for ordinary titles."""
    assert _query(Track("Song Name", ["Artist"])) == (
        "artist song name",
        "artist song name official audio",
        "artist song name audio",
        "artist song name official",
        "song name artist",
    )


def test_symbol_only_title_becomes_searchable_and_keeps_the_symbol() -> None:
    """Previously a symbol-only title produced no queries at all."""
    queries = _query(Track("❤️", ["Coldplay"]))

    assert queries[0] == "coldplay ❤"
    assert "coldplay heart" in queries
    assert "coldplay red heart" in queries


def test_expanded_queries_supplement_the_original() -> None:
    """The original title's queries stay first; expansions are appended."""
    queries = _query(Track("Higher Power ♾️", ["Coldplay"]))

    assert queries[0] == "coldplay higher power"
    assert queries.index("coldplay higher power") < queries.index("coldplay higher power infinity")
    # The literal Spotify title is searchable too, symbol included.
    assert "coldplay higher power ♾" in queries


def test_expanded_query_count_is_bounded() -> None:
    """A title full of symbols cannot flood the search with queries."""
    queries = _query(Track("My Universe ❤️", ["Coldplay"]))
    ordinary = _query(Track("My Universe", ["Coldplay"]))

    assert len(queries) == len(ordinary) + MAX_EXPANDED_TITLES
    assert "coldplay my universe ❤" in queries
    assert "coldplay my universe heart" in queries
    # The third expansion ("red heart") is beyond the cap and is not searched.
    assert "coldplay my universe red heart" not in queries


def test_expanded_queries_work_without_artist_information() -> None:
    assert _query(Track("❤️", [])) == (
        "❤",
        "❤ official audio",
        "❤ audio",
        "❤ official",
        "heart",
        "red heart",
    )


def test_queries_are_empty_when_the_title_has_no_searchable_form() -> None:
    assert _query(Track("、、、", ["Artist"])) == ()
