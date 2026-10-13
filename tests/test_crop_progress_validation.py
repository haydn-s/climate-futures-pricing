"""The USDA development evaluator cannot inspect its model-specific 2026 holdout."""

from __future__ import annotations

import numpy as np
import pandas as pd
from pandas.tseries.offsets import BDay

from pipeline.crop_progress_validation import (
    FEATURE_SETS,
    OUTER_TEST_YEARS,
    evaluate_development,
    evaluate_holdout,
    evaluate_simple_rule,
    load_protocol,
    outer_year_folds,
)


def model_frame() -> pd.DataFrame:
    rng = np.random.default_rng(590)
    rows = []
    for year in range(2012, 2027):
        dates = pd.date_range(f"{year}-04-01", periods=20, freq="W-MON")
        for number, release in enumerate(dates):
            deterioration = float(np.sin(number / 3) + rng.normal(0, 0.1))
            row = {
                "date": release,
                "year": year,
                "entry_date": release + BDay(1),
                "exit_date": release + BDay(6),
                "target_corn_gross_return": float(
                    0.01 * np.sign(deterioration) + rng.normal(0, 0.01)
                ),
                "target_corn_cost_return": 0.001,
            }
            for feature in FEATURE_SETS["all_usda_plus_market"]:
                row[feature] = float(rng.normal())
            row["condition_balance_change_1w"] = -deterioration
            rows.append(row)
    return pd.DataFrame(rows)


def test_saved_protocol_matches_implemented_boundary() -> None:
    protocol = load_protocol()
    assert tuple(protocol["information_boundary"]["outer_test_years"]) == OUTER_TEST_YEARS
    assert list(protocol["feature_sets"]) == list(FEATURE_SETS)


def test_outer_folds_expand_and_never_return_2026() -> None:
    frame = model_frame()
    folds = outer_year_folds(frame)
    assert len(folds) == len(OUTER_TEST_YEARS)
    assert [int(frame.loc[test, "year"].iloc[0]) for _, test in folds] == list(OUTER_TEST_YEARS)
    assert all(2026 not in set(frame.loc[index, "year"]) for fold in folds for index in fold)


def test_development_never_scores_holdout() -> None:
    bundle = evaluate_development(model_frame())
    assert len(bundle.results) == len(FEATURE_SETS) * 3
    assert set(bundle.headline_predictions["year"]) == set(OUTER_TEST_YEARS)
    assert 2026 not in set(bundle.headline_predictions["year"])


def test_simple_rule_holds_when_condition_is_not_reported() -> None:
    frame = model_frame()
    frame.loc[frame["year"].eq(2017), "condition_balance_change_1w"] = np.nan
    result = evaluate_simple_rule(frame)
    rows = result.predictions[result.predictions["year"].eq(2017)]
    assert rows["signal"].eq(0).all()


def test_holdout_scores_only_2026_after_headline_freeze() -> None:
    frame = model_frame()
    development = evaluate_development(frame)
    holdout = evaluate_holdout(frame, headline=development.headline)
    assert set(holdout.predictions["year"]) == {2026}
    assert holdout.headline == development.headline
