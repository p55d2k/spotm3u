"""Search-term normalization and symbolic-title expansion.

A Spotify title is not always searchable text. A track can be titled ``❤️`` or
``♾️`` (Coldplay's singles are), an emoji used as decoration adds tokens a
search engine ignores, and full-width or typographic variants (``Ａｖｉｃｉｉ``)
survive into a query verbatim while a search engine only indexes their plain
form. This layer turns one title into a short, ordered list of *searchable title
forms*.

The first form is the normalized title the query builder already used, so an
ordinary title yields exactly one form and keeps the queries it had. Alternative
forms are only ever *added*:

- the **symbol-preserving** form, because ``{artist} ❤️`` is what the title
  actually says and a search engine may index it,
- up to two **named** forms, where each symbol is replaced by its textual
  meaning (``heart``, ``red heart``, ``infinity``).

Expansion is deliberately small and maintainable rather than an exhaustive
Unicode dictionary: a curated table for the symbols that really appear in song
titles, a generic fallback that derives a phrase from the character's Unicode
name, and a noise filter that drops derived names with no search value
(``musical note``). Non-Latin text is kept as-is — it is never transliterated or
replaced by an approximation. The track's metadata is never modified.

This module only *describes* a title. Query generation, candidate ranking and
source validation stay where they are; a candidate found through an expanded
form goes through exactly the same scoring and validation as any other.
"""

from __future__ import annotations

import unicodedata

from ..normalization import normalize

# Curated search terms for symbols that appear in song titles. Keys are matched
# with presentation selectors removed (``❤️`` matches ``❤``); the first term is
# the preferred query and a second, genuinely different term feeds one more
# query (``heart`` vs ``red heart``). Hearts dominate real song titles, so they
# get the explicit entries; everything else falls back to its Unicode name.
_SYMBOL_TERMS: dict[str, tuple[str, ...]] = {
    "❤": ("heart", "red heart"),
    "♥": ("heart", "red heart"),
    "♡": ("heart", "red heart"),
    "❣": ("heart",),
    "💕": ("hearts", "two hearts"),
    "💔": ("heart", "broken heart"),
    "💖": ("heart",),
    "💗": ("heart",),
    "💘": ("heart", "cupid heart"),
    "💙": ("heart", "blue heart"),
    "🖤": ("heart", "black heart"),
    "🤍": ("heart", "white heart"),
    "♾": ("infinity",),
    "∞": ("infinity",),
    "★": ("star",),
    "☆": ("star",),
    "⭐": ("star",),
    "🌟": ("star",),
    "✨": ("sparkles",),
    "⚡": ("lightning",),
    "🔥": ("fire",),
    "☀": ("sun",),
    "🌙": ("moon",),
    "👑": ("crown",),
}

# Symbols that mean nothing as a search term: note glyphs, legal/typographic
# signs. They are dropped from the named forms (the symbol-preserving form still
# carries them) instead of producing queries like ``song musical note``.
_SILENT_SYMBOLS = frozenset("♪♫♬🎵🎶🎼©®™°℗℠")

# Words that carry no search value on their own. A name derived from Unicode
# that is made only of these is dropped rather than searched for.
_NAME_NOISE_WORDS = frozenset(
    {
        "beamed",
        "digit",
        "eighth",
        "emoji",
        "keycap",
        "letter",
        "mark",
        "marks",
        "modifier",
        "musical",
        "note",
        "notes",
        "quarter",
        "score",
        "selector",
        "sign",
        "signs",
        "sixteenth",
        "symbol",
        "symbols",
        "variation",
    }
)

# Filler words dropped from a derived name ("SMILING FACE WITH SMILING EYES" ->
# "smiling face smiling eyes"), which stays a plausible search phrase.
_NAME_FILLER_WORDS = frozenset({"and", "of", "the", "with"})

# Presentation selectors carry no text (``❤️`` and ``❤`` are the same heart), so
# they are removed rather than turned into a separator.
_VARIATION_SELECTORS = ("\ufe0e", "\ufe0f")


def search_title_forms(title: str | None) -> tuple[str, ...]:
    """Return the searchable forms of ``title``, most preferred first.

    The first form is :func:`spotm3u.normalization.normalize` of the title — the
    query text used before this layer existed, so ordinary titles produce one
    form and search exactly as they did. A title containing symbols adds the
    symbol-preserving form and up to two named forms, each only when it differs
    from every form before it. An empty title (or one that normalizes to
    nothing, e.g. a title of only separators) still contributes its
    symbol-preserving form when there is one, so a symbol-only title becomes
    searchable instead of searching for nothing. Returns ``()`` when there is no
    form at all.
    """
    text = (title or "").strip()
    if not text:
        return ()

    tidy = _tidy(text)
    candidates = (
        normalize(text),
        tidy,
        _named_form(tidy, variant=0),
        _named_form(tidy, variant=1),
    )

    forms: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = candidate.casefold()
        if candidate and key not in seen:
            seen.add(key)
            forms.append(candidate)
    return tuple(forms)


def _tidy(text: str) -> str:
    """Collapse a title into search tokens, **keeping** symbols and emoji.

    ``normalize`` treats symbols as separators; this keeps them so the form the
    title literally shows can still be searched for. Compatibility forms are
    folded (full-width and mathematical letters, ``№``, ``①``), punctuation and
    unusual separators become spaces, and runs of whitespace collapse.
    """
    folded = unicodedata.normalize("NFKC", text)
    characters: list[str] = []
    for character in folded:
        if character in _VARIATION_SELECTORS:
            continue
        keep = character.isalnum() or _symbol_terms(character) is not None
        characters.append(character if keep else " ")
    return " ".join("".join(characters).casefold().split())


def _named_form(tidy_text: str, *, variant: int) -> str:
    """Replace every symbol in ``tidy_text`` with its term for ``variant``.

    ``variant`` 0 uses each symbol's preferred term, ``variant`` 1 uses its
    second term when it has one. Repeated terms collapse, so ``❤️❤️`` names one
    heart rather than two. Returns ``""`` when the title holds no symbol.
    """
    parts: list[str] = []
    word: list[str] = []
    found_symbol = False

    def flush() -> None:
        """Finish the ordinary word being collected, if any."""
        if word:
            parts.append("".join(word))
            word.clear()

    for character in tidy_text:
        if character.isspace():
            flush()
            continue
        terms = _symbol_terms(character)
        if terms is None:
            word.append(character)
            continue
        found_symbol = True
        flush()
        if not terms:
            continue
        term = terms[variant] if variant < len(terms) else terms[0]
        if parts and parts[-1] == term:
            continue
        parts.append(term)
    flush()
    if not found_symbol:
        return ""
    return " ".join(parts)


def _symbol_terms(character: str) -> tuple[str, ...] | None:
    """Return the search terms for one character, or ``None`` when it is not a symbol.

    ``()`` means the character is a symbol with no useful search term, so named
    forms drop it. Anything that is not a symbol is left to the caller.
    """
    key = character
    for selector in _VARIATION_SELECTORS:
        key = key.replace(selector, "")
    if key in _SYMBOL_TERMS:
        return _SYMBOL_TERMS[key]
    if key in _SILENT_SYMBOLS:
        return ()
    # Emoji and pictographs ("So") without a curated term: derive one from the
    # Unicode name, so an unlisted emoji still expands instead of silently
    # dropping out of the alternative queries.
    if unicodedata.category(character) == "So":
        return _derived_terms(character)
    return None


def _derived_terms(character: str) -> tuple[str, ...]:
    """Derive a search phrase from a symbol's Unicode name, or drop it."""
    name = unicodedata.name(character, "")
    if not name:
        return ()
    words = [
        word for word in name.casefold().replace("-", " ").split() if word not in _NAME_FILLER_WORDS
    ]
    if not words or all(word in _NAME_NOISE_WORDS for word in words):
        return ()
    return (" ".join(words),)


__all__ = ["search_title_forms"]
