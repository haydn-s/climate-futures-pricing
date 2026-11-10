# Mid-semester deck

`output/Midterm Presentation.pptx` and `output/Midterm Presentation.pdf` — the
9 slides presented on October 14, 2026. These are kept as submitted.

## The builder no longer runs

`build_deck.mjs` cannot be executed. It was written against the Codex
presentations skill and hardcodes two paths into that runtime:

- `~/.codex/plugins/cache/openai-primary-runtime/presentations/26.1011.11113/…`,
  a pinned skill version that no longer exists — the installed version has
  since moved to `26.1017.11052`
- a runtime-specific Python under `~/.cache/codex-runtimes/`

There is also no `package.json` in this directory, so `@oai/artifact-tool`
cannot be reinstalled. Running `node build_deck.mjs` fails at import resolution
before any of its own code executes.

The file is kept because it is the record of how a submitted artifact was
produced, and because its layout and palette were the basis for the final deck.
It is not maintained and should not be repaired — if the mid-semester deck ever
needs regenerating, port it onto the final deck's toolchain rather than reviving
the Codex dependency.

## Build the final deck instead

[`../final/`](../final/) uses plain npm (`pptxgenjs`, `sharp`), declares
everything in `package.json`, and derives every quoted number from the committed
analysis CSVs so slides cannot drift from the repository:

```bash
cd ../final
../../.venv/bin/python deck_data.py
npm install
npm run build
```

## One caveat about these artifacts

The committed `.pptx` is the October 14 rebuild; the committed `.pdf` was
exported on October 13 from the previous build. They differ by one rebuild, so
the PDF is not a faithful render of the PowerPoint file beside it. Neither was
regenerated afterwards, because the deck had already been presented.
