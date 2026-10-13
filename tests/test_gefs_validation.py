"""The implemented GEFS validation boundary must match its saved protocol."""

from __future__ import annotations

import pandas as pd

from pipeline.gefs_validation import (
    FEATURE_SETS,
    load_protocol,
    outer_year_folds,
)


def test_saved_protocol_matches_implemented_features_and_years() -> None:
    protocol = load_protocol()
    assert list(protocol["feature_sets"]) == list(FEATURE_SETS)
    assert protocol["information_boundary"]["outer_test_years"] == [2023, 2024, 2025]
    assert protocol["information_boundary"]["untouched_holdout_year"] == 2026


def test_outer_folds_expand_and_never_expose_the_holdout() -> None:
    frame = pd.DataFrame({"year": [2021, 2022, 2023, 2024, 2025, 2026]})
    folds = outer_year_folds(frame)
    assert [frame.loc[test, "year"].unique().tolist() for _, test in folds] == [
        [2023], [2024], [2025],
    ]
    assert [frame.loc[train, "year"].max() for train, _ in folds] == [2022, 2023, 2024]
    assert all(2026 not in set(frame.loc[index, "year"]) for fold in folds for index in fold)
