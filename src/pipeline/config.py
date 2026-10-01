"""Typed, validated access to config/sources.yaml and config/geography.yaml.

The two YAML files are the only place a source or a crop is described, so that
adding either needs no Python change. That only holds if the files are checked:
a silently ignored typo (`acccept`, an unquoted FIPS code that loses its leading
zero, a date object where a JSON-serialisable parameter was expected) would turn
into a wrong number much further downstream. So every key is either required or
explicitly optional, anything else is an error, and the error names the key path
that caused it.

Secrets never appear here. `SourceSpec.api_key()` reads the environment, loading
an untracked .env first if one exists, and raises a ConfigError naming the signup
URL when the variable is unset -- without ever putting the value in the message.

    from pipeline.config import load_sources, load_geography
    sources = load_sources()
    corn = load_geography().crop("corn")
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

import yaml

# config/ and src/ are siblings under the repository root, so a relative path in
# a default argument works from anywhere, not just from the repository root.
ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCES_PATH = "config/sources.yaml"
DEFAULT_GEOGRAPHY_PATH = "config/geography.yaml"
DEFAULT_DOTENV_PATH = ".env"

SCHEMA_VERSION = 1
SOURCE_KINDS = frozenset({"csv", "json"})

_SOURCE_REQUIRED = ("kind", "url_template", "params", "publication_lag_days", "notes")
_SOURCE_OPTIONAL = ("accept", "api_key_env", "api_key_signup_url")
_CROP_REQUIRED = ("label", "sensitive_months")
_CROP_OPTIONAL = ("countries", "states", "counties", "points", "notes")

_dotenv_loaded: set[Path] = set()


class ConfigError(Exception):
    """A config file is missing, malformed, or missing a secret it declares."""


# --------------------------------------------------------------------- dataclasses


@dataclass(frozen=True)
class SourceSpec:
    """One entry of config/sources.yaml.

    The first six fields are the contract every module codes against. The last
    three are optional and default to None so a keyless source needs none of
    them: `accept` because USDM returns CSV instead of JSON when the header is
    wrong, and the two api_key fields because a key has to be findable and
    requestable without hard-coding either in Python.
    """

    name: str
    kind: str
    url_template: str
    params: Mapping[str, Any]
    publication_lag_days: int | None
    notes: str
    accept: str | None = None
    api_key_env: str | None = None
    api_key_signup_url: str | None = None

    @property
    def needs_key(self) -> bool:
        return self.api_key_env is not None

    def format_url(self, **values: Any) -> str:
        """Fill url_template from `params`, overridden by `values`.

        Pass ints for year and month: some templates use a `02d` format spec.
        """
        merged = {**self.params, **values}
        try:
            return self.url_template.format(**merged)
        except (KeyError, IndexError) as exc:
            missing = str(exc).strip("'")
            raise ConfigError(
                f"sources.{self.name}.url_template needs a value for {missing!r}: "
                f"pass it to format_url() or give it a default in params"
            ) from exc
        except (ValueError, TypeError) as exc:
            raise ConfigError(
                f"sources.{self.name}.url_template could not be filled from "
                f"{sorted(merged)}: {exc} (year and month must be ints)"
            ) from exc

    def api_key(self, env: Mapping[str, str] | None = None) -> str | None:
        """The key for this source, or None when it needs none.

        Reads an untracked .env into the environment first, if one exists. The
        raised message names the variable and where to get a key; it never
        contains the value, so it is safe to print or log.
        """
        if self.api_key_env is None:
            return None
        if env is None:
            load_dotenv()
            env = os.environ
        value = (env.get(self.api_key_env) or "").strip()
        if not value:
            signup = self.api_key_signup_url or "the source's own documentation"
            raise ConfigError(
                f"source {self.name!r} needs an API key and {self.api_key_env} is not set. "
                f"Request a free key at {signup}, then either "
                f"`export {self.api_key_env}=...` or add a line "
                f"`{self.api_key_env}=...` to an untracked .env in the repository root. "
                f"Never commit the key; it travels in the query string, so a resolved "
                f"URL for this source is itself a secret."
            )
        return value


@dataclass(frozen=True)
class MonthWindow:
    """The months of the year when weather moves a crop, both ends inclusive.

    Wrapping is allowed (start=11, end=3) because a tree crop's season does not
    respect the calendar year.
    """

    start: int
    end: int
    label: str = ""

    @property
    def wraps(self) -> bool:
        return self.start > self.end

    def months(self) -> tuple[int, ...]:
        if self.wraps:
            return tuple(range(self.start, 13)) + tuple(range(1, self.end + 1))
        return tuple(range(self.start, self.end + 1))

    def contains(self, month: int) -> bool:
        return month in self.months()


@dataclass(frozen=True)
class StateRef:
    """A US state and the three incompatible codes it is known by.

    `fips` is the federal code used by USDA and Census; `ncei` is the NCEI code
    that nClimGrid county identifiers are built from. They differ (Iowa is FIPS
    19 and NCEI 13) and confusing them silently relocates a state's weather.
    """

    postal: str
    name: str
    fips: str
    ncei: str


@dataclass(frozen=True)
class CountyRef:
    """A county in scope, keyed by its 5-digit federal FIPS code."""

    fips: str
    name: str = ""
    weight: float | None = None


@dataclass(frozen=True)
class PointRef:
    """A representative growing location for crops with no US county geography."""

    name: str
    lat: float
    lon: float
    country: str = ""


@dataclass(frozen=True)
class CropGeography:
    """One entry of config/geography.yaml: where a crop grows and when it cares."""

    name: str
    label: str
    sensitive_months: MonthWindow
    countries: tuple[str, ...] = ()
    states: tuple[StateRef, ...] = ()
    counties: tuple[CountyRef, ...] = ()
    points: tuple[PointRef, ...] = ()
    notes: str = ""

    def postal_codes(self) -> tuple[str, ...]:
        return tuple(state.postal for state in self.states)

    def state_fips(self) -> tuple[str, ...]:
        return tuple(state.fips for state in self.states)

    def ncei_codes(self) -> tuple[str, ...]:
        return tuple(state.ncei for state in self.states)

    def state(self, postal: str) -> StateRef:
        for candidate in self.states:
            if candidate.postal == postal.upper():
                return candidate
        raise ConfigError(
            f"crop {self.name!r} has no state {postal!r} "
            f"(in scope: {', '.join(self.postal_codes()) or 'none'})"
        )

    def county_weights(self) -> dict[str, float]:
        """County FIPS -> weight, normalised to sum to 1.

        Empty while the county list is empty, and equal-weighted when counties
        are listed without weights, so a caller can use it unconditionally.
        """
        if not self.counties:
            return {}
        raw = {county.fips: (1.0 if county.weight is None else county.weight) for county in self.counties}
        total = sum(raw.values())
        return {fips: weight / total for fips, weight in raw.items()}


@dataclass(frozen=True)
class Geography:
    """config/geography.yaml as a whole: crop name -> CropGeography."""

    crops: Mapping[str, CropGeography] = field(default_factory=dict)

    def crop(self, name: str) -> CropGeography:
        try:
            return self.crops[name]
        except KeyError:
            raise ConfigError(
                f"unknown crop {name!r} (configured: {', '.join(sorted(self.crops))})"
            ) from None

    def names(self) -> tuple[str, ...]:
        return tuple(self.crops)

    def __iter__(self) -> Iterator[CropGeography]:
        return iter(self.crops.values())


# --------------------------------------------------------------------- validation


def _join(path: str, key: str) -> str:
    return f"{path}.{key}" if path else str(key)


def _mapping(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(
            f"{path or 'top level'}: expected a mapping, found {type(value).__name__}"
        )
    return value


def _sequence(value: Any, path: str) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ConfigError(f"{path}: expected a list, found {type(value).__name__}")
    return value


def _check_keys(mapping: Mapping[str, Any], path: str, required: Sequence[str],
                optional: Sequence[str] = ()) -> None:
    allowed = set(required) | set(optional)
    for key in mapping:
        if key not in allowed:
            raise ConfigError(
                f"{_join(path, str(key))}: unknown key (allowed: {', '.join(sorted(allowed))})"
            )
    for key in required:
        if key not in mapping:
            raise ConfigError(f"{_join(path, key)}: required key is missing")


def _text(mapping: Mapping[str, Any], path: str, key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{_join(path, key)}: expected a non-empty string, found {value!r}")
    return value


def _optional_text(mapping: Mapping[str, Any], path: str, key: str) -> str | None:
    value = mapping.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{_join(path, key)}: expected a string or null, found {value!r}")
    return value


def _optional_int(mapping: Mapping[str, Any], path: str, key: str, *, minimum: int) -> int | None:
    value = mapping.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{_join(path, key)}: expected a whole number or null, found {value!r}")
    if value < minimum:
        raise ConfigError(f"{_join(path, key)}: expected >= {minimum}, found {value}")
    return value


def _digits(value: Any, path: str, width: int) -> str:
    """A zero-padded code that must be quoted in YAML to survive as written."""
    if isinstance(value, int):
        raise ConfigError(
            f"{path}: quote the code as a string (\"{value:0{width}d}\"), or YAML "
            f"parses it as the number {value} and any leading zero is lost"
        )
    if not isinstance(value, str) or len(value) != width or not value.isdigit():
        raise ConfigError(f"{path}: expected a quoted {width}-digit code, found {value!r}")
    return value


def _number(value: Any, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{path}: expected a number, found {value!r}")
    return float(value)


def _json_safe(value: Any, path: str) -> None:
    """Params are copied into data/manifest.jsonl verbatim, so they must survive json.dumps.

    The usual offender is an unquoted date: YAML hands back a datetime.date and
    the failure would otherwise surface at the end of a long fetch.
    """
    try:
        json.dumps(value)
    except TypeError as exc:
        raise ConfigError(
            f"{path}: must be JSON-serialisable because it is written into the raw "
            f"manifest ({exc}); quote dates and other scalars YAML parses into objects"
        ) from exc


def _load_yaml(path: str | Path) -> Any:
    resolved = resolve_path(path)
    if not resolved.exists():
        raise ConfigError(f"{path}: no such config file (looked at {resolved})")
    try:
        return yaml.safe_load(resolved.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path}: not valid YAML: {exc}") from exc


def resolve_path(path: str | Path) -> Path:
    """Resolve a config path against the working directory, then the repository root."""
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    if candidate.exists():
        return candidate.resolve()
    return (ROOT / candidate).resolve()


# --------------------------------------------------------------------- loaders


def _source_spec(name: str, body: Any, path: str) -> SourceSpec:
    entry = _mapping(body, path)
    _check_keys(entry, path, _SOURCE_REQUIRED, _SOURCE_OPTIONAL)

    kind = _text(entry, path, "kind")
    if kind not in SOURCE_KINDS:
        raise ConfigError(
            f"{_join(path, 'kind')}: unknown kind {kind!r} "
            f"(allowed: {', '.join(sorted(SOURCE_KINDS))})"
        )

    params = _mapping(entry.get("params"), _join(path, "params"))
    _json_safe(params, _join(path, "params"))

    api_key_env = _optional_text(entry, path, "api_key_env")
    signup = _optional_text(entry, path, "api_key_signup_url")
    if api_key_env and not signup:
        raise ConfigError(
            f"{_join(path, 'api_key_signup_url')}: required when api_key_env is set, "
            f"so the error raised for a missing key can say where to get one"
        )

    return SourceSpec(
        name=name,
        kind=kind,
        url_template=_text(entry, path, "url_template"),
        params=params,
        publication_lag_days=_optional_int(entry, path, "publication_lag_days", minimum=0),
        notes=_text(entry, path, "notes"),
        accept=_optional_text(entry, path, "accept"),
        api_key_env=api_key_env,
        api_key_signup_url=signup,
    )


def load_sources(path: str | Path = DEFAULT_SOURCES_PATH) -> dict[str, SourceSpec]:
    """Every source in config/sources.yaml, keyed by name."""
    document = _load_yaml(path)
    try:
        top = _mapping(document, "")
        _check_keys(top, "", required=("sources",), optional=("version",))
        _check_version(top)
        sources = _mapping(top["sources"], "sources")
        if not sources:
            raise ConfigError("sources: no sources configured")
        return {
            name: _source_spec(name, body, _join("sources", str(name)))
            for name, body in sources.items()
        }
    except ConfigError as exc:
        raise ConfigError(f"{path}: {exc}") from exc


def _check_version(top: Mapping[str, Any]) -> None:
    version = top.get("version", SCHEMA_VERSION)
    if version != SCHEMA_VERSION:
        raise ConfigError(
            f"version: expected schema version {SCHEMA_VERSION}, found {version!r}"
        )


def _month_window(body: Any, path: str) -> MonthWindow:
    entry = _mapping(body, path)
    _check_keys(entry, path, required=("start", "end"), optional=("label",))
    bounds = []
    for key in ("start", "end"):
        value = entry[key]
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 12:
            raise ConfigError(f"{_join(path, key)}: expected a month 1-12, found {value!r}")
        bounds.append(value)
    return MonthWindow(start=bounds[0], end=bounds[1], label=entry.get("label", "") or "")


def _state_ref(body: Any, path: str) -> StateRef:
    entry = _mapping(body, path)
    _check_keys(entry, path, required=("postal", "name", "fips", "ncei"))
    postal = _text(entry, path, "postal")
    if len(postal) != 2 or not postal.isalpha() or postal != postal.upper():
        raise ConfigError(
            f"{_join(path, 'postal')}: expected a two-letter uppercase abbreviation, found {postal!r}"
        )
    return StateRef(
        postal=postal,
        name=_text(entry, path, "name"),
        fips=_digits(entry.get("fips"), _join(path, "fips"), 2),
        ncei=_digits(entry.get("ncei"), _join(path, "ncei"), 2),
    )


def _county_ref(body: Any, path: str) -> CountyRef:
    entry = _mapping(body, path)
    _check_keys(entry, path, required=("fips",), optional=("name", "weight"))
    weight = entry.get("weight")
    if weight is not None:
        weight = _number(weight, _join(path, "weight"))
        if weight <= 0:
            raise ConfigError(f"{_join(path, 'weight')}: expected a positive weight, found {weight}")
    return CountyRef(
        fips=_digits(entry.get("fips"), _join(path, "fips"), 5),
        name=entry.get("name", "") or "",
        weight=weight,
    )


def _point_ref(body: Any, path: str) -> PointRef:
    entry = _mapping(body, path)
    _check_keys(entry, path, required=("name", "lat", "lon"), optional=("country",))
    lat = _number(entry.get("lat"), _join(path, "lat"))
    lon = _number(entry.get("lon"), _join(path, "lon"))
    if not -90 <= lat <= 90:
        raise ConfigError(f"{_join(path, 'lat')}: expected -90..90, found {lat}")
    if not -180 <= lon <= 180:
        raise ConfigError(f"{_join(path, 'lon')}: expected -180..180, found {lon}")
    return PointRef(
        name=_text(entry, path, "name"),
        lat=lat,
        lon=lon,
        country=entry.get("country", "") or "",
    )


def _crop_geography(name: str, body: Any, path: str) -> CropGeography:
    entry = _mapping(body, path)
    _check_keys(entry, path, _CROP_REQUIRED, _CROP_OPTIONAL)

    counties = tuple(
        _county_ref(item, f"{_join(path, 'counties')}[{index}]")
        for index, item in enumerate(_sequence(entry.get("counties"), _join(path, "counties")))
    )
    weighted = [county for county in counties if county.weight is not None]
    if weighted and len(weighted) != len(counties):
        unweighted = ", ".join(county.fips for county in counties if county.weight is None)
        raise ConfigError(
            f"{_join(path, 'counties')}: weights are all-or-nothing, so a partial set "
            f"cannot be normalised; these have none: {unweighted}"
        )

    countries = _sequence(entry.get("countries"), _join(path, "countries"))
    for index, country in enumerate(countries):
        if not isinstance(country, str) or not country.strip():
            raise ConfigError(
                f"{_join(path, 'countries')}[{index}]: expected a non-empty string, found {country!r}"
            )

    return CropGeography(
        name=name,
        label=_text(entry, path, "label"),
        sensitive_months=_month_window(entry.get("sensitive_months"), _join(path, "sensitive_months")),
        countries=tuple(countries),
        states=tuple(
            _state_ref(item, f"{_join(path, 'states')}[{index}]")
            for index, item in enumerate(_sequence(entry.get("states"), _join(path, "states")))
        ),
        counties=counties,
        points=tuple(
            _point_ref(item, f"{_join(path, 'points')}[{index}]")
            for index, item in enumerate(_sequence(entry.get("points"), _join(path, "points")))
        ),
        notes=entry.get("notes", "") or "",
    )


def load_geography(path: str | Path = DEFAULT_GEOGRAPHY_PATH) -> Geography:
    """Every crop in config/geography.yaml, keyed by name, in file order."""
    document = _load_yaml(path)
    try:
        top = _mapping(document, "")
        _check_keys(top, "", required=("crops",), optional=("version",))
        _check_version(top)
        crops = _mapping(top["crops"], "crops")
        if not crops:
            raise ConfigError("crops: no crops configured")
        return Geography(crops={
            name: _crop_geography(name, body, _join("crops", str(name)))
            for name, body in crops.items()
        })
    except ConfigError as exc:
        raise ConfigError(f"{path}: {exc}") from exc


# --------------------------------------------------------------------- secrets


def load_dotenv(path: str | Path = DEFAULT_DOTENV_PATH, *, override: bool = False) -> list[str]:
    """Copy KEY=VALUE lines from an untracked .env into os.environ.

    Returns the variable names it set -- names only, never values, so the result
    is safe to print. A real environment variable wins unless override is set,
    and a missing file is not an error: every source but nass_production is
    keyless, so .env is optional.
    """
    resolved = resolve_path(path)
    if not resolved.exists():
        return []
    if resolved in _dotenv_loaded and not override:
        return []
    _dotenv_loaded.add(resolved)

    loaded: list[str] = []
    for line in resolved.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, _, value = stripped.removeprefix("export ").partition("=")
        name, value = name.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if not name or (name in os.environ and not override):
            continue
        os.environ[name] = value
        loaded.append(name)
    return loaded
