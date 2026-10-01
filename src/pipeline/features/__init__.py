"""Feature construction: cleaned tables in, point-in-time design matrix out.

`clean` is where a source stops being bytes; this is where it stops being a
source and becomes a column. The split matters because every feature here is
built under an as-of cutoff -- see pipeline.features.panel on why the
information set, not the model, is this project's experimental variable.
"""

from .panel import HEAT_THRESHOLD_C, PanelSpec, belt_drought, belt_weather, build

__all__ = ["HEAT_THRESHOLD_C", "PanelSpec", "belt_drought", "belt_weather", "build"]
