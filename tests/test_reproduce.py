"""The top-level reproduction plan should remain complete and safe to resume."""

from __future__ import annotations

import reproduce


def test_default_plan_fetches_every_keyless_source_and_runs_every_analysis() -> None:
    plan = reproduce.build_plan(reproduce.STAGE_ORDER)

    fetched = [step.command[step.command.index("--source") + 1]
               for step in plan if step.stage == "fetch"]
    analyses = [step.label for step in plan if step.stage == "analysis"]

    assert fetched == list(reproduce.KEYLESS_SOURCES)
    assert "nass_production" not in fetched
    assert analyses == list(reproduce.ANALYSES)
    assert plan[-1].stage == "test"


def test_refresh_and_nass_are_explicit_opt_ins() -> None:
    plan = reproduce.build_plan(("fetch", "clean"), refresh=True, include_nass=True)
    fetches = [step for step in plan if step.stage == "fetch"]
    cleans = [step for step in plan if step.stage == "clean"]

    assert all("--force" in step.command for step in fetches)
    assert any(step.command[step.command.index("--source") + 1] == "nass_production"
               for step in fetches)
    assert any(step.command[-1] == "nass_production" for step in cleans)


def test_stage_order_is_canonical_even_if_arguments_arrive_out_of_order() -> None:
    args = reproduce.parser().parse_args(["--stages", "test", "analysis", "features"])
    stages = tuple(stage for stage in reproduce.STAGE_ORDER if stage in args.stages)

    assert stages == ("features", "analysis", "test")
