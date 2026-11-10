# Final presentation

Submit **[output/Final Presentation.pdf](output/Final%20Presentation.pdf)** on
Canvas by **1:30 PM Eastern, Wednesday, November 18, 2026**. The updated
[course guidelines](guidelines.pdf) require PDF only and a **10-minute hard stop**.
The Canvas upload link is to be provided by the course. This repository prepares
the deliverable; it does not submit it.

The editable [PowerPoint](output/Final%20Presentation.pptx) contains **12 slides**
and speaker notecards budgeted for **8 minutes of speaking plus 2 minutes of
questions**. The guidelines do not prescribe that split; it retains the deck's
existing question allowance. Rehearsal is still needed to confirm actual pacing.
The PDF contains the same slides, without presenter notes.

## Coverage of the updated guidelines

All six starred topic groups appear on the slides, not only in notes. Optional
topics are included where the project supports them. The final slide closes
with next steps as the guidelines recommend.

| Guideline | Slide(s) | What the audience sees |
|---|---|---|
| Project Recap* | 2 | Problem, target users, why supply risk and false trading signals matter, intended impact |
| Final Product Solution* | 3 | Implemented research package and explicit midsemester-to-final comparison |
| Data & Model* | 4–5 | Named sources, geographic aggregation, publication timing, training-only transformations, baseline creation and model selection |
| Evaluation & Performance* | 6–9 | R² versus trend, MAE, coverage, net returns/Sharpe and a buyer-cost example; improvements in evaluation since midsemester |
| Risks & Responsible AI* | 7, 10 | Coverage limits, geographic bias, calm-season failures, stale/revised inputs, human review and unsupported uses |
| Conclusion & Reflection* | 12 | Final outcome, lesson, why physical risk measurement matters and concrete next steps |
| Product Experience (optional) | 7, 9 | Actual interval outputs and a worked buyer-buffer example; no live demo dependency |
| Synthetic Data (if used) | 4 | No synthetic training data; distinguish historical resampling from simulated decision costs |
| Deployment & Real-World Feasibility | 11 | Implemented stack, proposed analyst-report integration, latency, costs, reliability and scaling limits |
| Business Value & Impact | 2, 9 | Physical buyers as users, estimated buffer-cost benefit, proposed pilot, untested adoption |
| What You'd Do Next | 12 | Prospective harvest, USDA benchmark, production weights and a buyer pilot with realistic costs |
| Logistics | Deliverables above | PDF-only submission; 10-minute maximum; November 18 deadline |

The comparison uses the submitted midsemester deck in
[`../mid-semester/output/`](../mid-semester/output/). Its GEFS and USDA trading
failures were already known; the final does not present them as new. The new
chronological R² versus trend differs from the old leave-one-year-out R² in
both folds and denominator, so the deck does not claim a comparable accuracy
increase. The advance is stronger evaluation, uncertainty and decision context.

## Speaking plan

| Slide | Topic | Speak for | Leave by |
|---|---|---|---|
| 1 | Title | 0:15 | 0:15 |
| 2 | Problem and users | 0:30 | 0:45 |
| 3 | Product and evolution | 0:50 | 1:35 |
| 4 | Data and preparation | 0:55 | 2:30 |
| 5 | Baselines and models | 0:50 | 3:20 |
| 6 | Yield evaluation | 0:50 | 4:10 |
| 7 | Uncertainty | 0:45 | 4:55 |
| 8 | Trading evaluation | 0:40 | 5:35 |
| 9 | Buyer example and value | 0:45 | 6:20 |
| 10 | Risks and responsible use | 0:50 | 7:10 |
| 11 | Production feasibility | 0:25 | 7:35 |
| 12 | Reflection and next steps | 0:25 | 8:00 |
| — | Questions | 2:00 | 10:00 |

The notecards start with a duration and cumulative leave-by mark. Sources and
optional Q&A details follow the speaking cues; they are not a script to read
verbatim. The builder derives order and timing from one `PLAN` and rejects a
schedule that differs from eight speaking minutes or exceeds the hard stop.

## Rebuild both deliverables

Use the project's Python environment (including pandas and pypdf), Node.js and
LibreOffice. LibreOffice's `soffice` must be on PATH, at the standard macOS app
location, or supplied through `SOFFICE=/path/to/soffice`.

```bash
# From presentation/final/
source ../../.venv/bin/activate
npm ci
npm run deliverable
```

This regenerates `deck_data.json` from committed analysis CSVs, builds the
PowerPoint, and exports the PDF. `export_pdf.py` uses an isolated temporary
LibreOffice profile and checks that the PDF has the same number of slides,
landscape pages and every visible text box from the PPTX. Only a successful
conversion replaces the submission PDF. Inspect the rendered PDF after edits:
text matching cannot detect clipped text, tiny chart labels or visual overlap.

Individual commands remain available:

```bash
python deck_data.py
npm run build       # PowerPoint only; does not refresh the submission PDF
npm run pdf         # PDF from the current PowerPoint
npm run previews    # single-slide PPTX files in output/previews/
```

The deck keeps the self-contained `pptxgenjs`/`sharp` toolchain from the prior
final version. It does not depend on a pinned Codex runtime. Figures are the
analysis scripts' own SVGs, rasterized at build time to retain their provenance.
They are image charts in PowerPoint; edit the analysis figure source to change
them. Text remains editable.

## Editing and evidence

- `PLAN` in `build_deck.js` controls slide order and speaking time.
- `SLIDE_FNS` controls slide content; `NOTECARDS` holds cues, sources and Q&A.
- `deck_data.py` derives numerical findings and rejects changed results that
  contradict the narrative. Do not insert replacement analysis numbers by hand.
- Source citations live in speaker notes, preserving the local PowerPoint
  edit that removed footer citations. Significant limits remain visible in the
  submission PDF.
- Direct PowerPoint edits are overwritten by a rebuild. Carry them into the
  builder and rerun `npm run deliverable` before submission.

The package is a research prototype. The county model uses revised full-season
history, the yield model has no untouched holdout, and the buyer example uses
the separate August point-weather variant with hypothetical costs. The slides
retain these distinctions. No analysis was retuned for this presentation.
