from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Literal
import numpy as np


WeatherEventType = Literal[
    "rain_event",
    "cold_spell",
    "heat_wave",
    "wind_event",
    "dry_spell",
]


@dataclass
class MonthlyClimate:
    """
    Monthly climate parameters for one location.

    These are the small set of monthly inputs required by this reduced
    weather generator. The design follows the WGEN/Richardson family of
    daily stochastic weather generators, which generate daily precipitation,
    temperature, and radiation from monthly statistics.

    References / modeling basis:
      - Richardson and Wright's WGEN generates daily precipitation,
        maximum/minimum temperature, and solar radiation.
      - Richardson-style weather generators commonly use a wet/dry Markov
        chain for precipitation occurrence and a distribution, often gamma,
        for precipitation amounts on wet days.
      - This implementation uses monthly parameters rather than historical
        station records so scenarios can be parameterized easily.
    """
    temp_mean_c: float
    precip_mm: float
    wet_day_prob: float
    solar_rad_mj_m2: float
    wind_mean_ms: float = 3.5
    wind_sigma_ms: float = 1.5


@dataclass
class WeatherEvent:
    """
    Explicit scenario-level weather override.

    Background weather is stochastic but seedable. WeatherEvent objects are
    deterministic scenario injections used to force controlled disturbances
    such as a cold spell after planting or a heavy-rain event before spraying.
    """
    event_type: WeatherEventType
    start_date: date
    duration_days: int

    # For rain_event.
    total_rain_mm: float | None = None

    # For cold_spell / heat_wave.
    temp_delta_c: float | None = None

    # For wind_event.
    wind_ms: float | None = None

    # Optional label stored in daily weather tags.
    label: str | None = None


@dataclass
class WeatherDay:
    """
    Daily weather state emitted by the generator.

    This is the exogenous input consumed by downstream soil, crop-growth,
    pest/disease, and operation-feasibility modules.
    """
    day: date
    air_temp_mean_c: float
    air_temp_min_c: float
    air_temp_max_c: float
    rain_mm: float
    wind_ms: float
    solar_rad_mj_m2: float
    is_raining: bool
    weather_tags: list[str] = field(default_factory=list)


@dataclass
class WeatherGeneratorConfig:
    """
    Configuration for the reduced daily weather generator.

    Scientific basis:
        The implementation follows the structure of WGEN/Richardson-style
        daily stochastic weather generation:
          1. precipitation occurrence is represented as a two-state wet/dry
             process with persistence;
          2. wet-day rainfall amount is sampled from a gamma distribution;
          3. daily temperature is represented as a monthly seasonal mean plus
             an autocorrelated anomaly;
          4. solar radiation is sampled around a monthly baseline and reduced
             on wet days.

    Engineering simplification:
        This is not a calibrated station weather generator. It uses monthly
        scenario parameters, a simple AR(1) temperature anomaly, and a compact
        wet/dry persistence rule instead of estimating full transition
        probabilities from station records. The purpose is reproducible
        scenario forcing for Farm-ARE, not climate reanalysis.
    """
    monthly: dict[int, MonthlyClimate]

    # Temperature process.
    # AR(1) anomaly preserves day-to-day temperature persistence.
    temp_ar1_phi: float = 0.75
    temp_noise_sigma_c: float = 2.5

    # Rain process.
    # wet_persistence_bonus and dry_after_dry_penalty approximate a first-order
    # wet/dry Markov process without explicitly storing P(W|W) and P(W|D).
    wet_persistence_bonus: float = 0.15
    dry_after_dry_penalty: float = 0.05

    # Wet-day rainfall amount distribution.
    # A gamma distribution is commonly used for wet-day precipitation amounts
    # in stochastic weather generators.
    rain_gamma_shape: float = 1.6

    # Diurnal temperature range.
    # Used to derive min/max temperature from daily mean temperature.
    diurnal_range_mean_c: float = 10.0
    diurnal_range_sigma_c: float = 2.0
    rainy_diurnal_reduction_c: float = 3.0

    # Solar radiation.
    rainy_solar_min_multiplier: float = 0.45
    rainy_solar_max_multiplier: float = 0.75
    solar_noise_sigma_frac: float = 0.08

    # Wind.
    # Wind is represented as bounded daily mean speed. This is sufficient for
    # operation feasibility checks such as drone flight and spraying.
    wind_min_ms: float = 0.0
    wind_max_ms: float = 14.0

    # Hard physical clipping.
    temp_min_clip_c: float = -10.0
    temp_max_clip_c: float = 40.0


def default_harbin_soybean_config() -> WeatherGeneratorConfig:
    """
    Harbin / Heilongjiang-oriented default weather parameters for May-Sep soybean scenarios.

    Parameter choice:
        The defaults are not station-calibrated. They are selected to provide a
        realistic soybean-season scale for Harbin/Heilongjiang:
          - May-Sep daily mean temperature rises from cool spring to warm July,
            then declines into September.
          - May-Sep cumulative precipitation is approximately 430 mm, matching
            the scale of reported growing-season precipitation in Heilongjiang
            soybean regions.
          - July/August are wetter than May/September.
          - Solar radiation is highest near June/July and declines into September.

    These values should be treated as scenario defaults and can be replaced by
    station-derived monthly statistics for another location.
    """
    monthly = {
        5: MonthlyClimate(temp_mean_c=14.5, precip_mm=55.0,  wet_day_prob=0.25, solar_rad_mj_m2=18.0),
        6: MonthlyClimate(temp_mean_c=20.0, precip_mm=90.0,  wet_day_prob=0.35, solar_rad_mj_m2=20.0),
        7: MonthlyClimate(temp_mean_c=23.5, precip_mm=135.0, wet_day_prob=0.45, solar_rad_mj_m2=19.0),
        8: MonthlyClimate(temp_mean_c=21.5, precip_mm=105.0, wet_day_prob=0.38, solar_rad_mj_m2=17.0),
        9: MonthlyClimate(temp_mean_c=15.0, precip_mm=45.0,  wet_day_prob=0.25, solar_rad_mj_m2=13.0),
    }
    return WeatherGeneratorConfig(monthly=monthly)


class WeatherGenerator:
    """
    Reduced daily weather generator for Farm-ARE.

    Purpose:
        Provide a seedable exogenous weather trace for closed-loop farm
        scenarios. The generated trace can be reused across oracle and agent
        runs so that differences in outcome are caused by management actions,
        not different weather.

    Scope:
        Daily stochastic generation plus deterministic scenario overrides.

    Non-scope:
        This is not numerical weather prediction, not climate downscaling, and
        not a calibrated weather generator for a specific meteorological station.
    """

    def __init__(self, config: WeatherGeneratorConfig, seed: int = 0) -> None:
        self.config = config
        self.rng = np.random.default_rng(seed)

    def generate(
        self,
        start_date: date,
        end_date: date,
        events: list[WeatherEvent] | None = None,
    ) -> list[WeatherDay]:
        """
        Generate inclusive daily weather from start_date to end_date.

        Determinism:
            For a fixed seed, config, date range, and event list, the generated
            weather trace is deterministic.

        Generation order:
            1. generate stochastic background weather;
            2. apply deterministic scenario weather events.
        """
        if end_date < start_date:
            raise ValueError("end_date must be >= start_date")

        events = events or []
        days = list(self._date_range(start_date, end_date))

        trace: list[WeatherDay] = []
        temp_anomaly = 0.0
        was_wet_yesterday = False

        for d in days:
            clim = self._monthly_climate(d)

            # Temperature: monthly mean + AR(1) daily anomaly.
            eps = self.rng.normal(0.0, self.config.temp_noise_sigma_c)
            temp_anomaly = self.config.temp_ar1_phi * temp_anomaly + eps
            temp_mean = clim.temp_mean_c + temp_anomaly

            # Precipitation occurrence: compact wet/dry persistence model.
            # This approximates a first-order two-state Markov chain:
            # wet days make another wet day more likely, dry days make wet
            # occurrence slightly less likely.
            p_wet = clim.wet_day_prob
            if was_wet_yesterday:
                p_wet += self.config.wet_persistence_bonus
            else:
                p_wet -= self.config.dry_after_dry_penalty
            p_wet = float(np.clip(p_wet, 0.02, 0.90))

            is_wet = bool(self.rng.random() < p_wet)

            # Precipitation amount: gamma-distributed wet-day rainfall.
            if is_wet:
                days_in_month = self._days_in_month(d.year, d.month)
                expected_wet_days = max(1.0, clim.wet_day_prob * days_in_month)
                mean_wet_day_rain = clim.precip_mm / expected_wet_days
                rain_mm = self._sample_gamma(mean=mean_wet_day_rain, shape=self.config.rain_gamma_shape)
            else:
                rain_mm = 0.0

            was_wet_yesterday = is_wet

            # Diurnal range: generate min/max around mean.
            # Wet days reduce the temperature range because of cloud/rain effects.
            dtr = self.rng.normal(
                self.config.diurnal_range_mean_c,
                self.config.diurnal_range_sigma_c,
            )
            if is_wet:
                dtr -= self.config.rainy_diurnal_reduction_c
            dtr = float(np.clip(dtr, 4.0, 16.0))

            temp_min = temp_mean - dtr / 2.0
            temp_max = temp_mean + dtr / 2.0

            # Wind: bounded daily mean wind speed.
            wind = self.rng.normal(clim.wind_mean_ms, clim.wind_sigma_ms)
            wind = float(np.clip(wind, self.config.wind_min_ms, self.config.wind_max_ms))

            # Solar radiation: monthly baseline with stochastic perturbation.
            # Wet days reduce solar radiation to represent cloud/rain conditions.
            solar = clim.solar_rad_mj_m2
            solar *= self.rng.normal(1.0, self.config.solar_noise_sigma_frac)
            if is_wet:
                solar *= self.rng.uniform(
                    self.config.rainy_solar_min_multiplier,
                    self.config.rainy_solar_max_multiplier,
                )
            solar = max(0.0, float(solar))

            weather_day = WeatherDay(
                day=d,
                air_temp_mean_c=round(float(np.clip(temp_mean, self.config.temp_min_clip_c, self.config.temp_max_clip_c)), 2),
                air_temp_min_c=round(float(np.clip(temp_min, self.config.temp_min_clip_c, self.config.temp_max_clip_c)), 2),
                air_temp_max_c=round(float(np.clip(temp_max, self.config.temp_min_clip_c, self.config.temp_max_clip_c)), 2),
                rain_mm=round(float(rain_mm), 2),
                wind_ms=round(wind, 2),
                solar_rad_mj_m2=round(solar, 2),
                is_raining=rain_mm > 0.1,
                weather_tags=[],
            )
            trace.append(weather_day)

        self._apply_events(trace, events)
        return trace

    def _apply_events(self, trace: list[WeatherDay], events: list[WeatherEvent]) -> None:
        """
        Apply deterministic scenario-level overrides.

        Event overrides are intentionally applied after background generation so
        a scenario author can force specific disturbances while preserving the
        remaining stochastic weather context.
        """
        by_day = {w.day: w for w in trace}

        for event in events:
            if event.duration_days <= 0:
                raise ValueError("WeatherEvent.duration_days must be positive")

            affected_days = [
                event.start_date + timedelta(days=i)
                for i in range(event.duration_days)
                if event.start_date + timedelta(days=i) in by_day
            ]
            if not affected_days:
                continue

            tag = event.label or event.event_type

            if event.event_type == "rain_event":
                if event.total_rain_mm is None:
                    raise ValueError("rain_event requires total_rain_mm")
                daily_amounts = self._split_rain_event(event.total_rain_mm, len(affected_days))
                for d, rain in zip(affected_days, daily_amounts):
                    w = by_day[d]
                    w.rain_mm = round(w.rain_mm + rain, 2)
                    w.is_raining = w.rain_mm > 0.1
                    w.solar_rad_mj_m2 = round(w.solar_rad_mj_m2 * 0.55, 2)
                    w.air_temp_max_c = round(w.air_temp_max_c - 1.5, 2)
                    w.air_temp_mean_c = round((w.air_temp_min_c + w.air_temp_max_c) / 2.0, 2)
                    w.weather_tags.append(tag)

            elif event.event_type == "cold_spell":
                if event.temp_delta_c is None:
                    raise ValueError("cold_spell requires temp_delta_c")
                delta = -abs(event.temp_delta_c)
                for d in affected_days:
                    self._shift_temperature(by_day[d], delta)
                    by_day[d].weather_tags.append(tag)

            elif event.event_type == "heat_wave":
                if event.temp_delta_c is None:
                    raise ValueError("heat_wave requires temp_delta_c")
                delta = abs(event.temp_delta_c)
                for d in affected_days:
                    self._shift_temperature(by_day[d], delta)
                    by_day[d].weather_tags.append(tag)

            elif event.event_type == "wind_event":
                if event.wind_ms is None:
                    raise ValueError("wind_event requires wind_ms")
                for d in affected_days:
                    by_day[d].wind_ms = round(float(event.wind_ms), 2)
                    by_day[d].weather_tags.append(tag)

            elif event.event_type == "dry_spell":
                for d in affected_days:
                    w = by_day[d]
                    w.rain_mm = 0.0
                    w.is_raining = False
                    w.solar_rad_mj_m2 = round(w.solar_rad_mj_m2 * 1.10, 2)
                    w.weather_tags.append(tag)

            else:
                raise ValueError(f"Unsupported event_type: {event.event_type}")

    def _split_rain_event(self, total_rain_mm: float, n_days: int) -> list[float]:
        """
        Split programmed rainfall across event days.

        A one-day event receives the full amount. Multi-day events are split
        using a Dirichlet draw so total rainfall is preserved while daily
        amounts vary.
        """
        if n_days == 1:
            return [round(float(total_rain_mm), 2)]

        weights = self.rng.dirichlet(np.ones(n_days))
        amounts = weights * total_rain_mm
        return [round(float(x), 2) for x in amounts]

    def _shift_temperature(self, w: WeatherDay, delta_c: float) -> None:
        """Shift daily mean/min/max temperature by the same delta."""
        w.air_temp_mean_c = round(w.air_temp_mean_c + delta_c, 2)
        w.air_temp_min_c = round(w.air_temp_min_c + delta_c, 2)
        w.air_temp_max_c = round(w.air_temp_max_c + delta_c, 2)

    def _sample_gamma(self, mean: float, shape: float) -> float:
        """
        Sample wet-day rainfall from a gamma distribution.

        The gamma distribution is parameterized by shape and scale, with
        scale = mean / shape.
        """
        scale = mean / shape
        return float(self.rng.gamma(shape=shape, scale=scale))

    def _monthly_climate(self, d: date) -> MonthlyClimate:
        if d.month not in self.config.monthly:
            raise ValueError(
                f"No monthly climate parameters for month={d.month}. "
                "Add parameters or restrict generation to configured months."
            )
        return self.config.monthly[d.month]

    @staticmethod
    def _date_range(start_date: date, end_date: date):
        cur = start_date
        while cur <= end_date:
            yield cur
            cur += timedelta(days=1)

    @staticmethod
    def _days_in_month(year: int, month: int) -> int:
        if month == 12:
            next_month = date(year + 1, 1, 1)
        else:
            next_month = date(year, month + 1, 1)
        return (next_month - date(year, month, 1)).days


def summarize_weather(trace: list[WeatherDay]) -> dict[str, float]:
    """Return basic seasonal diagnostics for a generated trace."""
    rain_total = sum(w.rain_mm for w in trace)
    mean_temp = sum(w.air_temp_mean_c for w in trace) / len(trace)
    mean_wind = sum(w.wind_ms for w in trace) / len(trace)
    mean_solar = sum(w.solar_rad_mj_m2 for w in trace) / len(trace)
    wet_days = sum(1 for w in trace if w.is_raining)

    return {
        "days": len(trace),
        "rain_total_mm": round(rain_total, 1),
        "mean_temp_c": round(mean_temp, 2),
        "wet_days": wet_days,
        "mean_wind_ms": round(mean_wind, 2),
        "mean_solar_rad_mj_m2": round(mean_solar, 2),
    }


if __name__ == "__main__":
    config = default_harbin_soybean_config()
    generator = WeatherGenerator(config=config, seed=42)

    events = [
        WeatherEvent(
            event_type="cold_spell",
            start_date=date(2026, 5, 12),
            duration_days=3,
            temp_delta_c=5.0,
            label="post_planting_cold_spell",
        ),
        WeatherEvent(
            event_type="rain_event",
            start_date=date(2026, 6, 25),
            duration_days=2,
            total_rain_mm=35.0,
            label="heavy_rain_event",
        ),
        WeatherEvent(
            event_type="wind_event",
            start_date=date(2026, 7, 10),
            duration_days=1,
            wind_ms=11.5,
            label="spraying_blocked_high_wind",
        ),
        WeatherEvent(
            event_type="dry_spell",
            start_date=date(2026, 8, 1),
            duration_days=8,
            label="pod_fill_dry_spell",
        ),
    ]

    trace = generator.generate(
        start_date=date(2026, 5, 1),
        end_date=date(2026, 9, 30),
        events=events,
    )

    print(summarize_weather(trace))

    for w in trace:
        if w.weather_tags:
            print(w)
