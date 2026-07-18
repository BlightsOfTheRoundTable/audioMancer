"""Regression/tuning table for spaCy-based context extraction.

This is the file to grow when tuning dm_mixer/context_config.py: add a (sentence, keyword)
pair and the cues it should produce, run the suite, and adjust the config until it passes.
"""

import re

import pytest

from dm_mixer import context_analysis


@pytest.fixture(scope="session", autouse=True)
def _warm_nlp():
    context_analysis.get_nlp()  # pay the model-load cost once for the whole test session


def _cues_for(sentence, keyword):
    clean_text = sentence.lower()
    doc = context_analysis.parse_chunk(clean_text)
    match = re.search(r'\b' + re.escape(keyword), clean_text)
    assert match is not None, f"{keyword!r} not found in {sentence!r}"
    return context_analysis.analyze_occurrence(doc, match.start(), match.end())


@pytest.mark.parametrize(
    "sentence, keyword, expected_fire_count, expected_periodic_seconds, expected_volume_multiplier",
    [
        # --- quantity: direct modifiers ---
        ("three arrows fly through the air", "arrow", 3, None, 1.0),
        ("many arrows fly through the air", "arrow", 5, None, 1.0),
        ("a dozen arrows fly through the air", "arrow", 12, None, 1.0),
        ("20 arrows fly through the air", "arrow", 15, None, 1.0),  # capped at MAX_FIRE_COUNT
        ("several fireballs streak overhead", "fireball", 3, None, 1.0),
        ("two explosions rock the tower", "explosion", 2, None, 1.0),
        # --- quantity: partitive "N of KEYWORD" construction ---
        ("a couple of explosions rock the tower", "explosion", 2, None, 1.0),
        ("a pair of arrows fly by", "arrow", 2, None, 1.0),
        ("a swarm of goblins approaches", "goblin", 6, None, 1.0),
        ("a horde of goblins approaches", "goblin", 8, None, 1.0),
        # --- quantity: higher numbers (found missing during vocabulary review - only up to
        # "six"/"dozen" were covered, "seven" through "twenty" produced no quantity cue at all) ---
        ("seven arrows fly through the air", "arrow", 7, None, 1.0),
        # --- periodic: explicit "every ..." ---
        ("thunder rumbles every fifteen seconds", "thunder", 1, 15.0, 1.0),
        ("thunder rumbles every minute", "thunder", 1, 60.0, 1.0),
        ("thunder rumbles every 20 seconds", "thunder", 1, 20.0, 1.0),
        ("thunder rumbles every twenty seconds", "thunder", 1, 20.0, 1.0),
        ("a drip occurs every so often", "drip", 1, 15.0, 1.0),
        # --- periodic: idioms without the word "every" ---
        ("you hear a drip once and a while", "drip", 1, 20.0, 1.0),
        ("you hear thunder occasionally", "thunder", 1, 20.0, 1.0),
        ("you hear thunder repeatedly", "thunder", 1, 15.0, 1.0),
        ("you hear a drip on and off", "drip", 1, 15.0, 1.0),
        # --- volume/intensity: modifier attached to the verb, not the keyword directly ---
        ("there's an explosion outside", "explosion", 1, None, 0.2),
        ("you hear an explosion outside", "explosion", 1, None, 0.2),
        ("the dragon roars nearby", "dragon", 1, None, 1.6),
        ("you hear rain outside", "rain", 1, None, 0.2),
        # --- volume/intensity: adjective directly on the keyword ---
        ("a faint drip echoes in the hall", "drip", 1, None, 0.12),
        ("a distant explosion rumbles", "explosion", 1, None, 0.2),
        ("a muted explosion rumbles", "explosion", 1, None, 0.35),
        ("a blaring explosion rocks the tower", "explosion", 1, None, 2.0),
        # --- volume/intensity: multiple modifiers, strongest wins ---
        ("a distant, muffled explosion rumbles", "explosion", 1, None, 0.2),
        # --- volume/intensity: louder/size descriptors (found missing during manual testing -
        # "massive explosion" produced no volume cue at all until these were added) ---
        ("massive explosion", "explosion", 1, None, 2.4),
        ("suddenly there is a massive explosion", "explosion", 1, None, 2.4),
        ("a huge explosion rocks the tower", "explosion", 1, None, 2.0),
        ("a tiny drip echoes", "drip", 1, None, 0.12),
        # --- volume/intensity: multi-word spatial phrases that don't reduce to a single
        # amod/advmod/prep token - matched as substrings instead of via the dependency parse ---
        ("thunder rumbles off in the distance", "thunder", 1, None, 0.2),
        ("the dragon roars right next to you", "dragon", 1, None, 1.6),
        # --- no cues present at all ---
        ("i hear rain outside my window", "rain", 1, None, 0.2),
        ("the goblins are approaching", "goblin", 1, None, 1.0),
    ],
)
def test_context_cues_extraction(sentence, keyword, expected_fire_count,
                                  expected_periodic_seconds, expected_volume_multiplier):
    cues = _cues_for(sentence, keyword)
    assert cues.fire_count == expected_fire_count
    assert cues.periodic_seconds == expected_periodic_seconds
    assert cues.volume_multiplier == pytest.approx(expected_volume_multiplier)


def test_periodic_and_burst_quantity_are_mutually_exclusive():
    """A periodic phrase must suppress quantity detection entirely, matching the pre-existing
    (unchanged) behavior where a keyword is either a recurring re-fire or a quantity burst,
    never both."""
    cues = _cues_for("three explosions every ten seconds", "explosion")

    assert cues.periodic_seconds == 10.0
    assert cues.fire_count == 1


def test_unrecognized_modifier_words_are_ignored():
    cues = _cues_for("a wobbly explosion happens", "explosion")

    assert cues.volume_multiplier == 1.0
    assert cues.fire_count == 1


def test_cues_expose_the_source_word_for_logging():
    """The actual matched word is carried alongside the resolved number specifically so
    callers can log a human-readable reason, e.g. "triggering 'explosion' (faint)"."""
    volume_cues = _cues_for("a faint explosion rumbles", "explosion")
    assert volume_cues.volume_modifier_word == "faint"

    quantity_cues = _cues_for("three arrows fly through the air", "arrow")
    assert quantity_cues.quantity_word == "three"


def test_multi_word_volume_phrase_exposes_the_matched_phrase_for_logging():
    cues = _cues_for("thunder rumbles off in the distance", "thunder")
    assert cues.volume_modifier_word == "off in the distance"


def test_source_words_are_none_when_nothing_matched():
    cues = _cues_for("an explosion happens", "explosion")

    assert cues.volume_modifier_word is None
    assert cues.quantity_word is None


def test_no_keyword_match_returns_neutral_cues():
    doc = context_analysis.parse_chunk("nothing relevant here")
    cues = context_analysis.analyze_occurrence(doc, 0, 0)

    assert cues.fire_count == 1
    assert cues.periodic_seconds is None
    assert cues.volume_multiplier == 1.0
