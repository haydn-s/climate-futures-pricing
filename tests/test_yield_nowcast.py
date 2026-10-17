"""Fixed-date yield features must respect publication, completeness, and cohorts."""

import numpy as np
import pandas as pd
import pytest

from pipeline.yield_nowcast import build_issue_table, evaluate_issues


@pytest.fixture
def nowcast_inputs(geography):
    crop = geography.crop("corn")
    days = pd.DatetimeIndex(np.concatenate([
        pd.date_range(f"{year}-04-01", f"{year}-09-30").to_numpy()
        for year in range(2000, 2014)
    ]))
    daily = pd.DataFrame({
        "date": days, "tmax_c": np.where(days.month == 7, 32.0, 27.0),
        "precip_mm": 2.0, "publication_date": days + pd.Timedelta(5, unit="D"),
    })
    power = pd.concat([daily.assign(crop="corn", point=point.name)
                       for point in crop.points], ignore_index=True)
    map_days = pd.DatetimeIndex(np.concatenate([
        pd.date_range(f"{year}-04-01", f"{year}-09-30", freq="W-TUE").to_numpy()
        for year in range(2000, 2014)
    ]))
    drought = pd.concat([
        pd.DataFrame({"map_date": map_days, "state": state.postal,
                      "fips": state.fips + "001", "d2": map_days.month.astype(float),
                      "publication_date": map_days + pd.Timedelta(2, unit="D")})
        for state in crop.states
    ], ignore_index=True)
    counties = pd.concat([
        pd.DataFrame({"date": days, "state": state.postal,
                      "publication_date": pd.Timestamp("2030-01-01")})
        for state in crop.states
    ], ignore_index=True)
    yields = pd.DataFrame({"crop": "corn", "entity": "United States",
                           "year": np.arange(2000, 2014),
                           "yield_t_per_ha": np.linspace(8.0, 10.0, 14)})
    return power, drought, counties, yields, geography


def august_2012(issues):
    return issues.loc[(issues.year == 2012) & (issues.issue_month == 8)].iloc[0]


def test_august_uses_only_published_july_weather_and_released_maps(nowcast_inputs):
    row = august_2012(build_issue_table(*nowcast_inputs))
    assert row.weather_complete and row.drought_complete
    assert row.weather_through == pd.Timestamp("2012-07-27")
    assert row.weather_latest_publication == row.issue_date
    assert row.heat_dd_july == 27 * 3.0
    assert row.precip_summer == (30 + 27) * 2.0
    # The July 31 map was released on August 2, after this issue.
    assert row.drought_through == pd.Timestamp("2012-07-24")
    assert row.drought_latest_publication <= row.issue_date
    assert row.peak_d2 == 7.0


def test_unpublished_observations_cannot_change_the_earlier_features(nowcast_inputs):
    power, drought, counties, yields, geography = nowcast_inputs
    expected = august_2012(build_issue_table(*nowcast_inputs))
    power.loc[(power.date >= "2012-07-28") & (power.date < "2013-01-01"), "tmax_c"] = 1000.0
    drought.loc[drought.map_date == "2012-07-31", "d2"] = 99.0
    actual = august_2012(build_issue_table(power, drought, counties, yields, geography))
    pd.testing.assert_series_equal(expected, actual)


@pytest.mark.parametrize("publication", [pd.Timestamp("2012-08-02"), pd.NaT])
def test_late_or_unknown_point_publication_blocks_the_day(nowcast_inputs, publication):
    power, drought, counties, yields, geography = nowcast_inputs
    mask = (power.date == "2012-07-27") & (power.point == geography.crop("corn").points[0].name)
    power.loc[mask, "publication_date"] = publication
    row = august_2012(build_issue_table(power, drought, counties, yields, geography))
    assert not row.weather_complete
    assert row.weather_days == row.weather_expected_days - 1
    assert np.isnan(row.heat_dd_july)


def test_missing_point_value_does_not_become_a_smaller_belt_average(nowcast_inputs):
    power, drought, counties, yields, geography = nowcast_inputs
    power.loc[(power.date == "2012-07-20") & (power.point == "Iowa"), "tmax_c"] = np.nan
    row = august_2012(build_issue_table(power, drought, counties, yields, geography))
    assert not row.weather_complete
    assert np.isnan(row.precip_summer)


def test_one_missing_county_blocks_an_expected_drought_map(nowcast_inputs):
    power, drought, counties, yields, geography = nowcast_inputs
    drought = drought.loc[~((drought.map_date == "2012-07-24") & (drought.state == "IA"))]
    row = august_2012(build_issue_table(power, drought, counties, yields, geography))
    assert not row.drought_complete
    assert row.drought_maps == row.drought_expected_maps - 1
    assert np.isnan(row.peak_d2)


def test_missing_drought_publication_cannot_be_hidden_by_other_counties(nowcast_inputs):
    power, drought, counties, yields, geography = nowcast_inputs
    drought.loc[(drought.map_date == "2012-07-24") & (drought.state == "IA"), "publication_date"] = pd.NaT
    row = august_2012(build_issue_table(power, drought, counties, yields, geography))
    assert not row.drought_complete
    assert np.isnan(row.peak_d2)


def test_county_rebuild_dates_are_preserved_and_do_not_enter_models(nowcast_inputs):
    power, drought, counties, yields, geography = nowcast_inputs
    expected = august_2012(build_issue_table(*nowcast_inputs))
    assert expected.county_published_days == 0
    counties["publication_date"] = counties.date + pd.Timedelta(7, unit="D")
    actual = august_2012(build_issue_table(power, drought, counties, yields, geography))
    assert actual.county_published_days > 0
    for feature in ["heat_dd_july", "tmax_july", "precip_summer", "peak_d2"]:
        assert actual[feature] == expected[feature]


def test_all_issue_dates_and_models_use_the_same_training_and_test_years(nowcast_inputs):
    issues = build_issue_table(*nowcast_inputs)
    issues.loc[(issues.year == 2002) & (issues.issue_month == 8), "peak_d2"] = np.nan
    predictions, results, common_years = evaluate_issues(issues)
    assert 2002 not in common_years
    for (_, _, _), group in predictions.groupby(["sample", "issue_month", "feature_set"]):
        expected = [2011, 2012, 2013] if group["sample"].iloc[0] == "all_years" else [2011, 2013]
        assert group.year.tolist() == expected
        assert (group.train_end < group.year).all()
    assert results.groupby("sample").test_years.nunique().eq(1).all()


def test_duplicate_inputs_and_missing_issue_cohorts_fail_explicitly(nowcast_inputs):
    power, drought, counties, yields, geography = nowcast_inputs
    with pytest.raises(ValueError, match="duplicate corn point-days"):
        build_issue_table(pd.concat([power, power.iloc[:1]]), drought, counties, yields, geography)
    issues = build_issue_table(*nowcast_inputs)
    with pytest.raises(ValueError, match="insufficient common"):
        evaluate_issues(issues.loc[issues.year < 2005])
