"""The implemented GEFS validation boundary must match its saved protocol."""

from __future__ import annotations

import numpy as np
import pandas as pd
from pandas.tseries.offsets import BDay

from pipeline.gefs_validation import (
    ABSOLUTE_FEATURES,
    FEATURE_SETS,
    MARKET_FEATURES,
    REVISION_FEATURES,
    apply_no_overlap,
    evaluate_development,
    load_protocol,
    outer_year_folds,
)


def test_saved_protocol_matches_implemented_features_and_years() -> None:
    protocol = load_protocol()
    assert list(protocol["feature_sets"]) == list(FEATURE_SETS)
    assert protocol["information_boundary"]["outer_test_years"] == [2023, 2024, 2025]
    assert protocol["information_boundary"]["candidate_selection_outer_years"] == [2023, 2024]
    assert protocol["information_boundary"]["final_development_confirmation_year"] == 2025
    assert protocol["information_boundary"]["untouched_holdout_year"] == 2026


def test_outer_folds_expand_and_never_expose_the_holdout() -> None:
    frame = pd.DataFrame({"year": [2021, 2022, 2023, 2024, 2025, 2026]})
    folds = outer_year_folds(frame)
    assert [frame.loc[test, "year"].unique().tolist() for _, test in folds] == [
        [2023], [2024], [2025],
    ]
    assert [frame.loc[train, "year"].max() for train, _ in folds] == [2022, 2023, 2024]
    assert all(2026 not in set(frame.loc[index, "year"]) for fold in folds for index in fold)


def model_frame() -> pd.DataFrame:
    rng = np.random.default_rng(590)
    rows = []
    for year in range(2021, 2027):
        dates = pd.bdate_range(f"{year}-05-03", periods=60)
        for number, date in enumerate(dates):
            heat_revision = float(np.sin(number / 3) + rng.normal(0, 0.1))
            rain_revision = float(rng.normal(0, 1))
            direction = np.sign(heat_revision - 0.1 * rain_revision)
            row = {
                "date": date,
                "year": year,
                "entry_date": date + BDay(1),
                "exit_date": date + BDay(6),
                "target_corn_gross_return": float(
                    0.01 * direction + rng.normal(0, 0.01)
                ),
                "target_corn_cost_return": 0.001,
            }
            row.update({column: float(rng.normal()) for column in MARKET_FEATURES})
            row.update({column: float(rng.normal()) for column in ABSOLUTE_FEATURES})
            row.update({column: float(rng.normal()) for column in REVISION_FEATURES})
            row["rev_d1_3_heat_dd29_sum"] = heat_revision
            row["rev_d1_3_precip_mm_sum"] = rain_revision
            rows.append(row)
    return pd.DataFrame(rows)


def test_no_overlap_keeps_only_the_next_executable_signal() -> None:
    frame = pd.DataFrame({
        "entry_date": pd.to_datetime(["2024-07-01", "2024-07-02", "2024-07-08"]),
        "exit_date": pd.to_datetime(["2024-07-05", "2024-07-08", "2024-07-12"]),
    })
    assert apply_no_overlap(frame, np.ones(3, dtype=int)).tolist() == [1, 0, 1]


def test_development_evaluation_never_scores_the_2026_holdout() -> None:
    bundle = evaluate_development(model_frame())
    assert len(bundle.results) == len(FEATURE_SETS) * 3
    assert set(bundle.headline_predictions["year"]) == {2023, 2024, 2025}
    assert 2026 not in set(bundle.headline_predictions["year"])
    assert bundle.simple_rule.folds == 3
    assert bundle.placebo_forward.folds == 3
    assert bundle.placebo_backward.folds == 3
