# Proposal deck

`climate-futures-pricing-proposal.pptx` — 13 slides for the 10-minute project proposal, with a speaker notecard on every slide (a time budget, a cumulative "leave by" mark, and short bullets).

The three chart slides are built from the same public data as [`analysis/initial_analysis.py`](../analysis/initial_analysis.py), so the deck cannot drift from the numbers in the [README](../README.md). Charts are native PowerPoint charts, not images, so they stay editable.

## Rebuild

```bash
source ../.venv/bin/activate   # pandas and numpy, per ../requirements.txt
python chart_data.py           # derives chart_data.json from the cached pulls in ../data/raw
npm install                    # pptxgenjs, sharp and react-icons
npm run build                  # writes climate-futures-pricing-proposal.pptx
```

`npm run previews` writes one single-slide file per slide, which is useful for checking layout.

## Editing

- **Slide order** is the `ORDER` list near the bottom of `build_deck.js`.
- **Speaker notes** are the `NOTECARDS` object; keys match `ORDER`.
- Editing the `.pptx` directly in PowerPoint works fine, but a rebuild overwrites it — move changes worth keeping back into `build_deck.js`.

Note that macOS Quick Look cannot render PowerPoint charts, so chart slides appear blank in Finder previews. Open the file in PowerPoint to check them.
