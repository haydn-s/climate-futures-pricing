# Final deck

`output/Final Presentation.pptx` — 9 slides for the November 18 final
presentation, with a speaker notecard on every slide (a time budget, a
cumulative "leave by" mark, and short cues) budgeted for **8 minutes of
speaking followed by 2 minutes of questions**.

At that budget a slide averages under a minute, so the notecards are cues
rather than sentences. Five of them carry an `IF ASKED` block answering the
challenge that slide invites, and the closing card indexes all five: with a
dedicated Q&A, most of the risk sits in the two minutes after the talk.

## Why this replaces the mid-semester builder

`presentation/mid-semester/build_deck.mjs` hardcodes two paths into the Codex
runtime: a presentations skill pinned to version `26.1011.11113`, and a
runtime-specific Python. The pinned skill version no longer exists — only
`26.1017.11052` does — so that deck can no longer be rebuilt, and it had no
`package.json` to reinstall from either.

This deck instead uses the same self-contained toolchain as the proposal deck:
plain npm, `pptxgenjs`, `sharp`. Everything it needs is declared in
`package.json`, so `npm install` is enough on any machine.

## The numbers cannot drift

The mid-semester deck hardcoded its figures inside the builder, which let slides
and reports disagree silently. Here `deck_data.py` derives every quoted number
from the **committed analysis CSVs** and writes `deck_data.json`, which the
builder reads. A slide can only say what the repository says.

Each extraction carries an assertion about what it found. If a feature set is
renamed, a column disappears, or a result moves far enough to contradict a claim
on a slide, the build stops with a named error instead of emitting a deck full
of quietly wrong numbers:

```
DeckDataError: the full-season result is no longer positive in both samples;
the deck's claim must be rewritten before it can be rebuilt
```

The tripwires currently assert that the full-season result stays positive in
both samples, that no single withheld harvest flips its sign, that 2012 stays
inside its 90% interval, that the model remains the *narrower* distribution in
the provisioning test, that the saving without 2012 still straddles zero, that
the sample still cannot detect a profitable ten-basis-point signal, and that the
locked trading results are still negative. Each one guards a specific sentence
on a specific slide.

## Figures

The four analysis figures are the analysis scripts' own SVGs, rasterised at
build time rather than redrawn, so a chart cannot drift from the report it came
from either. They are rendered at 300 dpi and downsampled to 2400 px wide —
about 200 dpi at full-slide width, crisp on a projector without carrying a
6000-pixel bitmap. Re-running an analysis script updates the slide.

## Rebuild

```bash
../../.venv/bin/python deck_data.py   # derives deck_data.json from ../../analysis
npm install                           # pptxgenjs, sharp, react-icons
npm run build                         # writes output/Final Presentation.pptx
```

`npm run previews` writes one single-slide file per slide into
`output/previews/`, which is useful for checking layout without scrolling a
full deck.

## Editing

- **Slide order** is the `ORDER` list near the bottom of `build_deck.js`.
- **Speaker notes** are the `NOTECARDS` object; keys match `ORDER`.
- **Numbers** are not edited here. Change the analysis, rerun it, rerun
  `deck_data.py`. If a number needs to change on a slide but not in the
  repository, the slide is wrong.
- Editing the `.pptx` directly in PowerPoint works, but a rebuild overwrites it
  — move anything worth keeping back into `build_deck.js`.

## Status

The structure, the data wiring, the figures and the notecards are in place and
the build is proven end to end. **The wording is a first draft**: the argument
is correct and sourced, but the phrasing, emphasis and the balance between
slides are presentation decisions still worth a pass before submission.
