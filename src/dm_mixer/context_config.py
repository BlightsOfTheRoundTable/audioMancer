"""Tunable word/phrase tables for spaCy-based context extraction (context_analysis.py).

This is where the interpretation of natural speech gets refined over time. Add words/phrases
and adjust numbers here; tests/test_context_analysis.py is the place to add example sentences
that lock in what the new/changed entries should do.
"""

# Word/digit -> integer count. Used for both direct quantity modifiers ("three arrows",
# "several fireballs") and as the quantity noun in "N of KEYWORD" constructions ("a couple
# of explosions").
QUANTITY_WORDS = {
    "a": 1, "an": 1, "one": 1, "single": 1,
    "two": 2, "couple": 2, "twin": 2, "double": 2,
    "three": 3, "triple": 3, "several": 3, "multiple": 3,
    "four": 4, "few": 4, "many": 5, "handful": 5, "bunch": 4,
    "five": 5, "six": 6, "half-dozen": 6, "half dozen": 6, "ton": 6, "tons": 8,
    "dozen": 12, "countless": 5, "barrage": 6, "volley": 5, "plenty": 5, "lots": 5,
}
MAX_FIRE_COUNT = 15

# Word/digit -> seconds, for the value attached to an explicit "every ___" phrase
# (e.g. "every fifteen seconds" -> 15.0, "every minute" -> 60.0, "every so often" -> 15.0).
RECURRENCE_TIME_WORDS = {
    "few": 4.0, "couple": 4.0, "so": 15.0, "often": 15.0,
    "five": 5.0, "ten": 10.0, "fifteen": 15.0, "thirty": 30.0,
    "minute": 60.0, "dozen": 12.0,
}

# Single-word recurrence idioms with no "every" present at all ("thunder occasionally").
RECURRENCE_STANDALONE_WORDS = {
    "occasionally": 20.0, "periodically": 15.0, "intermittently": 15.0, "sporadically": 25.0,
}

# Multi-word recurrence idioms, matched as substrings of the transcript rather than via the
# dependency parse - these are distinctive enough phrases that false-positive risk (unlike a
# short single-word keyword) is low. Includes "once and a while", a common colloquial variant
# of "once in a while".
RECURRENCE_PHRASES = {
    "once in a while": 20.0, "once and a while": 20.0,
    "from time to time": 20.0, "now and then": 20.0, "now and again": 20.0,
}

DEFAULT_PERIODIC_SECONDS = 8.0

# Spatial/intensity descriptor -> volume multiplier.
# 1.0 = neutral. <1.0 = quieter/farther. >1.0 = louder/closer.
# If more than one modifier matches, the one with the largest deviation from 1.0 wins - see
# analyze_occurrence in context_analysis.py.
#
# These were originally much closer to neutral (0.4x-1.5x) but manual testing showed that range
# is too subtle to reliably notice during actual play - especially against other background
# loops, or on short one-shot sounds where perception is dominated by the attack transient.
# A direct A/B test at extreme values (~0.02x vs 1.0x) confirmed the underlying volume mechanism
# itself is fine and clearly audible - so the fix is a much more dramatic word-to-multiplier
# scale, not a code change.
VOLUME_MODIFIERS = {
    # extreme quiet
    "faint": 0.12, "tiny": 0.12,
    # strong quiet / farther away
    "distant": 0.2, "far": 0.2, "muffled": 0.2, "outside": 0.2, "weak": 0.2, "hushed": 0.2,
    # mild quiet
    "quiet": 0.35, "soft": 0.35, "gentle": 0.35, "subtle": 0.35, "small": 0.35, "slight": 0.35,
    # mild loud / closer
    "nearby": 1.6, "close": 1.6, "near": 1.6, "sudden": 1.5,
    # strong loud
    "loud": 2.0, "roaring": 2.0, "powerful": 2.0, "violent": 2.0, "intense": 2.0,
    "giant": 2.0, "huge": 2.0,
    # extreme loud
    "thunderous": 2.4, "deafening": 2.4, "booming": 2.4, "massive": 2.4, "enormous": 2.4,
    "gigantic": 2.4, "colossal": 2.4, "immense": 2.4, "tremendous": 2.4,
}
VOLUME_MULTIPLIER_MIN = 0.1
VOLUME_MULTIPLIER_MAX = 2.5
