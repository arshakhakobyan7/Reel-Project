# T-503-fix — Wrap the thumbnail title by measured width

Follow `handoff/README.md`. Small fix of your T-503 work (reviewed by Claude: accepted, one issue).

## Issue
`_budget` turns the **widest** glyph of the text into a character count for every line, so normal
titles use only about half of the 1280-px frame and get an ellipsis too early. Real check: the title
«Սեփական ֆունկցիաներ, callable օբյեկտներ և hash աղյուսակներ Python-ում» (subtitle
«Python 12 · Դաս 14») was cut after «աղյուսակներ…», while each line was only ~650 px wide.

## Your files
- `src/reel/media/thumbnail.py`, `tests/unit/test_thumbnail_logic.py`,
  `tests/integration/test_thumbnail.py` (yours from T-503)

## What to change
- Wrap by **measured line width**: the sum of the glyph advances (`hmtx`, in font units → pixels
  at the font size) of each line must fit the text area (frame width minus both side margins), with a
  small safety margin for kerning. Words move to the next line; a single word wider than the line is
  broken by width. Still at most 3 title lines (1 subtitle line) with an ellipsis that also fits.
- Keep: glyph check before rendering, literal text via `textfile`, everything else from T-503.

## Tests
- The title above wraps into at most 3 lines **without** an ellipsis; every line's measured width
  fits the area.
- A title of only wide glyphs (e.g. «ՄՄՄ…» or «WWW…») still fits.
- A title that really is too long gets an ellipsis and fits.
- Existing T-503 tests still pass.

Write the report to `handoff/T-503-fix.report.md` (short: what changed, tests, check counts).
