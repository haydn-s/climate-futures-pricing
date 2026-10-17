"""Evaluate fixed full-season corn-yield models using earlier harvests only."""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from pipeline.config import load_geography  # noqa: E402
from pipeline.storage import ProcessedStore, default_data_root  # noqa: E402
from pipeline.yield_validation import (  # noqa: E402
    FEATURE_SETS, INPUT_TABLES, MIN_TRAIN_YEARS,
    build_annual_table, expanding_predictions, score_predictions,
)


def input_hashes(store: ProcessedStore) -> dict[str, str]:
    hashes = {}
    for name in INPUT_TABLES:
        digest = hashlib.sha256()
        with store.path(name).open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        hashes[name] = digest.hexdigest()
    return hashes


def render_report(results: pd.DataFrame, *, min_train_years: int,
                  hashes: dict[str, str], complete_years: list[int]) -> str:
    lines = [
        "# Forward-year corn yield evaluation", "",
        "## Evaluation", "",
        "Each forecast for year t uses only complete crop years before t. "
        "The linear yield trend, feature scaling, and fixed Ridge(alpha=1) "
        "regression are fitted again inside every fold. The trend-only control "
        "predicts the extrapolated yield trend without a weather correction.", "",
        f"There are {len(complete_years)} complete seasons "
        f"({complete_years[0]}–{complete_years[-1]}). At least {min_train_years} "
        "earlier seasons are required. All four feature sets and the control "
        "use identical complete-case training and test years within a scenario.", "",
        "The feature sets and alpha are fixed to the original yield study; "
        "this evaluation does not tune or select a new model.", "",
        "## Results", "",
        "| Sample | Feature set | Test years | MAE (t/ha) | RMSE (t/ha) | "
        "R² vs trend | MAE improvement | Anomaly MAE (pp) |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in results.itertuples(index=False):
        lines.append(
            f"| {row.sample} | {row.feature_set} | {row.first_test_year}–"
            f"{row.last_test_year} (n={row.test_years}) | {row.mae_t_per_ha:.3f} | "
            f"{row.rmse_t_per_ha:.3f} | {row.r2_vs_trend:+.3f} | "
            f"{row.mae_improvement:+.1%} | {row.anomaly_mae_pp:.2f} |"
        )
    full = results.loc[(results["sample"] == "all_years")
                       & (results["feature_set"] == "counties_drought")].iloc[0]
    excluded = results.loc[(results["sample"] == "exclude_2012")
                           & (results["feature_set"] == "counties_drought")].iloc[0]
    lines += [
        "", "## Interpretation", "",
        f"The previously emphasized county-weather-plus-drought specification "
        f"has forward R² versus trend {full.r2_vs_trend:+.3f}, "
        f"or {excluded.r2_vs_trend:+.3f} when 2012 is removed from both training "
        "and evaluation and all folds are refitted. These are separate "
        "sensitivity runs, not a score obtained by merely hiding the 2012 error.", "",
        "R² versus trend is 1 − sum(model yield error²) / sum(fold-specific "
        "trend forecast error²). Positive values beat the trend forecast; zero "
        "matches it. This denominator differs from the original leave-one-year-out "
        "R², which used the mean of held-out anomalies. The scores should not be "
        "compared as if they were the same metric.", "",
        "This tests chronological generalization with full-season weather. It "
        "does not establish an in-season forecast, a futures-return signal, or "
        "performance on a new untouched holdout. The historical years have "
        "already informed this project, and the public weather and yield files "
        "may contain revisions unavailable at the original forecast date. "
        "The target is national US yield; the weather covers five states with "
        "equal state weighting, not national production weighting.", "",
        "## Reproduce", "",
        "```bash", "PYTHONPATH=src python analysis/corn_yield_forward_validation.py",
        "```", "",
        "Use `--data-root` for an archived data directory and `--output-dir` "
        "for a separate report destination. The CSV predictions expose every "
        "training boundary, trend baseline, forecast, and observed yield. "
        "Source table SHA-256 fingerprints:", "",
    ]
    lines.extend(f"- `{name}`: `{value}`" for name, value in hashes.items())
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=default_data_root())
    parser.add_argument("--output-dir", type=Path, default=ROOT / "analysis")
    parser.add_argument("--min-train-years", type=int, default=MIN_TRAIN_YEARS)
    args = parser.parse_args()
    store = ProcessedStore(args.data_root)
    table = build_annual_table(store, load_geography())
    all_predictions, all_results = [], []
    for sample, frame in [("all_years", table),
                          ("exclude_2012", table.drop(index=2012, errors="ignore"))]:
        predictions = expanding_predictions(frame, FEATURE_SETS,
                                            min_train_years=args.min_train_years)
        if predictions.empty:
            raise SystemExit(f"{sample}: insufficient complete seasons for a forward test")
        results = score_predictions(predictions)
        predictions.insert(0, "sample", sample)
        results.insert(0, "sample", sample)
        all_predictions.append(predictions)
        all_results.append(results)
    predictions = pd.concat(all_predictions, ignore_index=True)
    results = pd.concat(all_results, ignore_index=True)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    stem = args.output_dir / "corn_yield_forward_validation"
    predictions.to_csv(f"{stem}_predictions.csv", index=False, float_format="%.10g")
    results.to_csv(f"{stem}_results.csv", index=False, float_format="%.10g")
    Path(f"{stem}_report.md").write_text(render_report(
        results, min_train_years=args.min_train_years, hashes=input_hashes(store),
        complete_years=table.index.tolist(),
    ))
    print(results.to_string(index=False, float_format=lambda value: f"{value:.4f}"))
    print(f"Wrote {stem}_{{predictions.csv,results.csv,report.md}}")


if __name__ == "__main__":
    main()
