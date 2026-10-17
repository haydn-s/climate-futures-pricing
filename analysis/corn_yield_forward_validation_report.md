# Forward-year corn yield evaluation

## Evaluation

Each forecast for year t uses only complete crop years before t. The linear yield trend, feature scaling, and fixed Ridge(alpha=1) regression are fitted again inside every fold. The trend-only control predicts the extrapolated yield trend without a weather correction.

There are 26 complete seasons (2000–2025). At least 10 earlier seasons are required. All four feature sets and the control use identical complete-case training and test years within a scenario.

The feature sets and alpha are fixed to the original yield study; this evaluation does not tune or select a new model.

## Results

| Sample | Feature set | Test years | MAE (t/ha) | RMSE (t/ha) | R² vs trend | MAE improvement | Anomaly MAE (pp) |
|---|---|---:|---:|---:|---:|---:|---:|
| all_years | trend_only | 2010–2025 (n=16) | 0.579 | 0.787 | +0.000 | +0.0% | 5.70 |
| all_years | points | 2010–2025 (n=16) | 0.448 | 0.588 | +0.441 | +22.8% | 4.31 |
| all_years | counties | 2010–2025 (n=16) | 0.387 | 0.457 | +0.662 | +33.2% | 3.71 |
| all_years | counties_drought | 2010–2025 (n=16) | 0.314 | 0.395 | +0.748 | +45.8% | 3.05 |
| all_years | points_drought | 2010–2025 (n=16) | 0.423 | 0.555 | +0.503 | +27.0% | 4.11 |
| exclude_2012 | trend_only | 2010–2025 (n=15) | 0.360 | 0.434 | +0.000 | +0.0% | 3.37 |
| exclude_2012 | points | 2010–2025 (n=15) | 0.346 | 0.464 | -0.142 | +3.7% | 3.26 |
| exclude_2012 | counties | 2010–2025 (n=15) | 0.307 | 0.354 | +0.335 | +14.6% | 2.84 |
| exclude_2012 | counties_drought | 2010–2025 (n=15) | 0.273 | 0.314 | +0.476 | +24.2% | 2.53 |
| exclude_2012 | points_drought | 2010–2025 (n=15) | 0.352 | 0.482 | -0.230 | +2.1% | 3.32 |

## Interpretation

The previously emphasized county-weather-plus-drought specification has forward R² versus trend +0.748, or +0.476 when 2012 is removed from both training and evaluation and all folds are refitted. These are separate sensitivity runs, not a score obtained by merely hiding the 2012 error.

R² versus trend is 1 − sum(model yield error²) / sum(fold-specific trend forecast error²). Positive values beat the trend forecast; zero matches it. This denominator differs from the original leave-one-year-out R², which used the mean of held-out anomalies. The scores should not be compared as if they were the same metric.

This tests chronological generalization with full-season weather. It does not establish an in-season forecast, a futures-return signal, or performance on a new untouched holdout. The historical years have already informed this project, and the public weather and yield files may contain revisions unavailable at the original forecast date. The target is national US yield; the weather covers five states with equal state weighting, not national production weighting.

## Reproduce

```bash
PYTHONPATH=src python analysis/corn_yield_forward_validation.py
```

Use `--data-root` for an archived data directory and `--output-dir` for a separate report destination. The CSV predictions expose every training boundary, trend baseline, forecast, and observed yield. Source table SHA-256 fingerprints:

- `nasa_power_point_daily`: `bcc37518341aac582cb60980a81e125f2501241388d7b2e1089cfba210a99c04`
- `nclimgrid_county_daily`: `cacd6bf7ea731e330ae238e20cae08cab88924929da00c28380e41e6813ea04c`
- `usdm_county_drought`: `7a95532a26652e00566fe32d82e3fdbe7c95513c876581e11ea71be9dce215fa`
- `owid_yields_annual`: `d7f57022ae166e30c136ba81527060b9fccca075e59ccd8d3796f3b783c32a0d`
