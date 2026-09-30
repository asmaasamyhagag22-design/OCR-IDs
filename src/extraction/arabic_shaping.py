# Research history: not part of the shipped service and never copied into its image
# (docker/app_code_files.txt, phase 4 final Stage 7).
"""Render Arabic strings into a form Pillow can draw, for diagnostic images only.

This Pillow build has no Raqm, so ``ImageDraw.text`` neither joins Arabic
letters nor reorders right-to-left runs: an extracted name drawn directly comes
out as disconnected, backwards glyphs. A contact sheet whose whole purpose is
"compare the printed field with what we read" is useless in that state.

So the joining is done here, from Python's own Unicode database rather than
from a hand-typed table. Every Arabic presentation form in U+FE70..U+FEFC
declares its base letter and its form in its compatibility decomposition
(``<initial> 0628``), and a letter's joining behaviour follows from which forms
exist for it: a letter with initial and medial forms joins on both sides, one
with only isolated and final forms joins on the right only, one with neither
does not join. Deriving both from the database means there is no table to get
wrong.

**This module is for drawing pictures.** No comparison, no check and no stored
value passes through it. Field values are always stored and emitted as the raw
logical string; see ``text_norm``.
"""
import unicodedata

ISOLATED, INITIAL, MEDIAL, FINAL = 'isolated', 'initial', 'medial', 'final'
_TAGS = {'<isolated>': ISOLATED, '<initial>': INITIAL,
         '<medial>': MEDIAL, '<final>': FINAL}

#: Marks that sit on a letter without breaking a join: tashkeel and tatweel.
TRANSPARENT = frozenset(
    [chr(c) for c in range(0x064B, 0x0660)] + [chr(0x0670), chr(0x0640)])

ARABIC_RANGE = range(0x0600, 0x0700)


def _build_tables():
    forms, ligatures = {}, {}
    for code in range(0xFE70, 0xFEFD):
        char = chr(code)
        decomposition = unicodedata.decomposition(char).split()
        if len(decomposition) < 2 or decomposition[0] not in _TAGS:
            continue
        tag = _TAGS[decomposition[0]]
        bases = [chr(int(part, 16)) for part in decomposition[1:]]
        if len(bases) == 1:
            forms.setdefault(bases[0], {})[tag] = char
        else:
            ligatures.setdefault(tuple(bases), {})[tag] = char
    return forms, ligatures


FORMS, LIGATURES = _build_tables()

#: Letters that connect to the letter after them (they have an initial form).
DUAL_JOINING = frozenset(base for base, table in FORMS.items() if INITIAL in table)
#: Letters that connect only to the letter before them.
RIGHT_JOINING = frozenset(base for base, table in FORMS.items()
                          if INITIAL not in table and FINAL in table)
JOINING = DUAL_JOINING | RIGHT_JOINING


def _pick(base, previous_joins, next_joins):
    """Choose the presentation form for ``base`` given its two neighbours."""
    table = FORMS.get(base)
    if not table:
        return base
    if previous_joins and next_joins and MEDIAL in table:
        return table[MEDIAL]
    if previous_joins and FINAL in table:
        return table[FINAL]
    if next_joins and INITIAL in table:
        return table[INITIAL]
    return table.get(ISOLATED, base)


def _apply_ligatures(text):
    """Fold lam + alef pairs into their single presentation glyph."""
    output, index = [], 0
    while index < len(text):
        pair = (text[index], text[index + 1]) if index + 1 < len(text) else None
        if pair in LIGATURES:
            output.append(('ligature', pair))
            index += 2
        else:
            output.append(('char', text[index]))
            index += 1
    return output


def shape(text):
    """Join an Arabic string into presentation forms, keeping logical order."""
    if not text:
        return ''
    tokens = _apply_ligatures(str(text))
    result = []
    for position, (kind, token) in enumerate(tokens):
        base = token[0] if kind == 'ligature' else token

        previous_joins = False
        for earlier in range(position - 1, -1, -1):
            candidate = tokens[earlier][1]
            candidate = candidate[0] if tokens[earlier][0] == 'ligature' else candidate
            if candidate in TRANSPARENT:
                continue
            previous_joins = candidate in DUAL_JOINING
            break

        next_joins = False
        for later in range(position + 1, len(tokens)):
            candidate = tokens[later][1]
            candidate = candidate[0] if tokens[later][0] == 'ligature' else candidate
            if candidate in TRANSPARENT:
                continue
            next_joins = candidate in JOINING
            break

        if kind == 'ligature':
            table = LIGATURES[token]
            result.append(table.get(FINAL if previous_joins else ISOLATED,
                                    table.get(ISOLATED, ''.join(token))))
        else:
            result.append(_pick(base, previous_joins, next_joins))
    return ''.join(result)


def _is_rtl(char):
    return 0x0590 <= ord(char) <= 0x08FF or 0xFB1D <= ord(char) <= 0xFEFC


def reorder_for_display(text):
    """Reverse right-to-left runs so a non-bidi renderer draws them correctly.

    A deliberately small subset of the bidi algorithm: it handles one paragraph
    direction and keeps digit and Latin runs in their own left-to-right order.
    That covers every field on this card. It is only ever used to draw a
    diagnostic image.
    """
    if not text:
        return ''
    characters = list(text)
    if not any(_is_rtl(c) for c in characters):
        return text

    # Each character is right-to-left, left-to-right, or neutral. Neutrals -
    # spaces, slashes, hyphens - take the direction of what came before them, so
    # a date embedded in an Arabic line keeps its own digit order.
    def direction_of(char):
        if _is_rtl(char):
            return 'R'
        if char.isspace():
            # Whitespace is its own run so it stays between the two runs it
            # separates instead of being carried to one end by the reversal.
            return 'S'
        if char.isalnum():
            return 'L'
        return None

    runs, current, current_direction = [], [], None
    for char in characters:
        direction = direction_of(char) or current_direction or 'R'
        if current_direction is None or direction == current_direction:
            current.append(char)
            current_direction = direction
        else:
            runs.append((current_direction, current))
            current, current_direction = [char], direction
    if current:
        runs.append((current_direction, current))

    rendered = [''.join(run[::-1] if direction == 'R' else run)
                for direction, run in runs]
    return ''.join(rendered[::-1])


def for_display(text):
    """Shape and reorder ``text`` for a non-bidi renderer. Drawing only."""
    return reorder_for_display(shape(text))
