"""Extracts quantity/recurrence/volume cues from a keyword mention using spaCy's dependency
parse, replacing the old fixed-window word-scanning heuristics that used to live in speech.py.

Dependency parsing finds the actual grammatical modifier of a keyword regardless of word order
or position in the sentence, which is what makes this generalize past specific phrasings the
old window-scan approach had to be hand-tuned against one at a time.
"""

from dataclasses import dataclass

import spacy

from dm_mixer import context_config

_NLP = None


def get_nlp():
    """Process-wide singleton - spacy.load() only actually runs once."""
    global _NLP
    if _NLP is None:
        _NLP = spacy.load("en_core_web_sm", disable=["ner", "lemmatizer", "attribute_ruler"])
    return _NLP


def parse_chunk(clean_text):
    """Run spaCy ONCE per transcribed chunk. Callers reuse the returned Doc for every keyword
    occurrence found in that chunk, rather than re-parsing per keyword."""
    return get_nlp()(clean_text)


@dataclass
class ContextCues:
    fire_count: int = 1
    periodic_seconds: float | None = None
    volume_multiplier: float = 1.0
    # The actual word(s) that produced fire_count/volume_multiplier, if any - kept alongside the
    # resolved numbers purely so callers can log a human-readable reason ("triggering 'explosion'
    # (faint)") instead of just a multiplier number.
    quantity_word: str | None = None
    volume_modifier_word: str | None = None


def analyze_occurrence(doc, match_start, match_end):
    """Given one regex match's char span for one keyword occurrence, walk the aligned token's
    syntactic neighborhood to produce quantity/periodic/volume cues."""
    cues = ContextCues()

    # alignment_mode="expand": the caller's keyword match is deliberately left-boundary-only
    # (matches "arrow" inside "arrows"), so the char span can end mid-token. "expand" snaps
    # outward to the enclosing token(s) instead of returning None the way strict mode would.
    span = doc.char_span(match_start, match_end, alignment_mode="expand")
    if span is None:
        return cues
    anchor = span.root

    periodic_seconds = _detect_periodic_seconds(doc)
    if periodic_seconds is not None:
        cues.periodic_seconds = periodic_seconds
        return cues  # periodic and burst-quantity are mutually exclusive, same as before

    fire_count, quantity_word = _detect_quantity(anchor)
    if fire_count is not None:
        cues.fire_count = min(context_config.MAX_FIRE_COUNT, fire_count)
        cues.quantity_word = quantity_word

    cues.volume_multiplier, cues.volume_modifier_word = _detect_volume_multiplier(anchor, doc)

    return cues


def _quantity_word_value(word):
    word = word.lower()
    if word.isdigit():
        return int(word)
    return context_config.QUANTITY_WORDS.get(word)


def _detect_quantity(anchor):
    """Looks for a numeral/quantifier modifier directly on the keyword ("three arrows",
    "several fireballs"), then for the partitive "QUANTITY of KEYWORD" construction where the
    keyword sits inside a prepositional phrase modifying the real quantity noun
    ("a couple of explosions" - "couple" is the actual syntactic subject, not "explosions").
    Returns (count, source_word) or (None, None) if nothing matched."""
    for child in anchor.children:
        if child.dep_ in ("nummod", "amod"):
            value = _quantity_word_value(child.text)
            if value is not None:
                return value, child.text.lower()

    if anchor.dep_ == "pobj" and anchor.head.dep_ == "prep" and anchor.head.text.lower() == "of":
        quantity_noun = anchor.head.head
        value = _quantity_word_value(quantity_noun.text)
        if value is not None:
            return value, quantity_noun.text.lower()

    return None, None


def _lookup_time_value(word):
    word = word.lower()
    if word.isdigit():
        return float(word)
    return context_config.RECURRENCE_TIME_WORDS.get(word)


def _detect_periodic_seconds(doc):
    """Sentence-wide, not tied to the keyword's position - a described recurring event applies
    to the whole utterance, matching the app's pre-existing (unchanged) behavior."""
    for token in doc:
        if token.text.lower() == "every":
            time_head = token.head
            seconds = _lookup_time_value(time_head.text)
            if seconds is None:
                for child in time_head.children:
                    seconds = _lookup_time_value(child.text)
                    if seconds is not None:
                        break
            return seconds if seconds is not None else context_config.DEFAULT_PERIODIC_SECONDS

    for token in doc:
        word = token.text.lower()
        if word in context_config.RECURRENCE_STANDALONE_WORDS:
            return context_config.RECURRENCE_STANDALONE_WORDS[word]

    text = doc.text.lower()
    for phrase, seconds in context_config.RECURRENCE_PHRASES.items():
        if phrase in text:
            return seconds

    return None


def _detect_volume_multiplier(anchor, doc):
    """Checks four sources real natural phrasing actually produces:
    (1) an adjective directly modifying the keyword ("a distant explosion", "a faint drip") -
    also accepts "compound", since spaCy's small model inconsistently tags some attributive
    present-participles ("a blaring explosion") as compound rather than amod even though
    grammatically they're playing the same role;
    (2) a prepositional phrase attached directly to the keyword ("rain outside my window");
    (3) an adverbial/prepositional modifier attached to the keyword's head verb as a SIBLING
    of the keyword, not a child of it (e.g. "an explosion rumbles outside" -> both "explosion"
    and "outside" attach to "rumbles", not to each other);
    (4) a multi-word spatial/intensity phrase matched as a substring of the whole chunk
    ("off in the distance", "right next to you") - these frequently don't reduce to a single
    amod/advmod/prep token the way (1)-(3) expect, so they're checked independently of the
    dependency parse, same as RECURRENCE_PHRASES.
    If more than one candidate matches, the strongest one (largest deviation from neutral 1.0)
    wins. Returns (multiplier, source_word) - source_word is None when nothing matched
    (multiplier 1.0)."""
    candidates = []
    for child in anchor.children:
        if child.dep_ in ("amod", "compound", "prep"):
            candidates.append(child.text.lower())

    if anchor.head is not anchor:
        for sibling in anchor.head.children:
            if sibling is not anchor and sibling.dep_ in ("advmod", "prep"):
                candidates.append(sibling.text.lower())

    best_multiplier = None
    best_word = None
    best_strength = 0.0
    for word in candidates:
        multiplier = context_config.VOLUME_MODIFIERS.get(word)
        if multiplier is None:
            continue
        strength = abs(multiplier - 1.0)
        if strength > best_strength:
            best_strength = strength
            best_multiplier = multiplier
            best_word = word

    text = doc.text.lower()
    for phrase, multiplier in context_config.VOLUME_PHRASES.items():
        if phrase in text:
            strength = abs(multiplier - 1.0)
            if strength > best_strength:
                best_strength = strength
                best_multiplier = multiplier
                best_word = phrase

    if best_multiplier is None:
        return 1.0, None
    clamped = max(context_config.VOLUME_MULTIPLIER_MIN, min(context_config.VOLUME_MULTIPLIER_MAX, best_multiplier))
    return clamped, best_word
