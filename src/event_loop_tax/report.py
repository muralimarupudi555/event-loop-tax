"""Turn a measurement into a verdict by comparing it against what theory allows.

A throughput number on its own says nothing. The same figure can be excellent or a
disaster depending on the concurrency it was taken at and the work each request does, so
every reading here is reported next to the ceiling it was supposed to reach.
"""

from __future__ import annotations

from dataclasses import dataclass

from event_loop_tax.config import ScenarioConfig
from event_loop_tax.harness import Measurement, calibrate_overhead, measure
from event_loop_tax.theory import (
    AGREEMENT_TOLERANCE,
    CONSISTENCY_LIMIT,
    CORRECTED_TOLERANCE,
    agrees,
    expected_rps,
    predicted_rps,
    serial_floor_rps,
)

# A chosen budget, not a discovered constant. The loop is called blocked when it was at
# least this late to run a task that asked to run. Five probe intervals is far outside
# anything a healthy loop produces here, where the median lag is under a millisecond, and
# far inside what a blocking handler produces, where it runs to seconds. A reader who
# wants a different budget should set one; the tool makes the choice visible rather than
# making it for them.
DEFAULT_LAG_BUDGET_MULTIPLE = 5.0


@dataclass(frozen=True)
class Report:
    """One scenario: what it achieved, what it could have, and what the loop was doing."""

    measurement: Measurement
    predicted_rps: float
    serial_floor_rps: float
    lag_budget_ms: float
    overhead_seconds: float = 0.0
    overhead_consistency: float = float("inf")

    @property
    def overhead_applied(self) -> bool:
        """Whether the harness cost correction was shown to be valid on this machine.

        It is applied only when the two point calibration agreed with itself. When it did
        not, the uncorrected ceiling is used with a wider band, and this flag records that
        the tighter check was available and declined rather than silently skipped.
        """
        return self.overhead_consistency <= CONSISTENCY_LIMIT

    @property
    def comparison_rps(self) -> float:
        """The number the measurement is actually judged against."""
        if self.overhead_applied:
            return expected_rps(self.config, self.overhead_seconds)
        return self.predicted_rps

    @property
    def tolerance(self) -> float:
        return CORRECTED_TOLERANCE if self.overhead_applied else AGREEMENT_TOLERANCE

    @property
    def config(self) -> ScenarioConfig:
        return self.measurement.config

    @property
    def achieved_share(self) -> float:
        """Measured throughput as a share of the ceiling for this shape."""
        if self.predicted_rps in (0.0, float("inf")):
            return 1.0
        return self.measurement.rps / self.predicted_rps

    @property
    def agrees_with_theory(self) -> bool:
        return agrees(self.measurement.rps, self.comparison_rps, self.tolerance)

    @property
    def exceeds_ceiling(self) -> bool:
        """Measured faster than the model says is possible.

        Not a good result. It means the model is wrong or the harness is not measuring
        what it claims, and either way the number should not be published.
        """
        if self.predicted_rps == float("inf"):
            return False
        return self.measurement.rps > self.predicted_rps * 1.05

    @property
    def loop_blocked(self) -> bool:
        return self.measurement.lag_p99_ms > self.lag_budget_ms

    @property
    def serial_floor_ratio(self) -> float:
        """Throughput divided by the fully serialised ceiling.

        Near one means the handler is serialising: every request's blocking portion is
        running one at a time, and no amount of concurrency will change that.
        """
        floor = self.serial_floor_rps
        if floor in (0.0, float("inf")):
            return 0.0
        return self.measurement.rps / floor

    def as_dict(self) -> dict:
        return {
            **self.measurement.as_dict(),
            "predicted_rps": round(self.predicted_rps, 2),
            "comparison_rps": round(self.comparison_rps, 2),
            "overhead_ms": round(self.overhead_seconds * 1000, 4),
            "overhead_consistency": (None if self.overhead_consistency == float("inf")
                                     else round(self.overhead_consistency, 3)),
            "overhead_applied": self.overhead_applied,
            "tolerance": self.tolerance,
            "serial_floor_rps": (None if self.serial_floor_rps == float("inf")
                                 else round(self.serial_floor_rps, 2)),
            "achieved_share": round(self.achieved_share, 4),
            "agrees_with_theory": self.agrees_with_theory,
            "exceeds_ceiling": self.exceeds_ceiling,
            "loop_blocked": self.loop_blocked,
            "lag_budget_ms": round(self.lag_budget_ms, 4),
            "serial_floor_ratio": round(self.serial_floor_ratio, 4),
        }


def build_report(config: ScenarioConfig,
                 lag_budget_multiple: float = DEFAULT_LAG_BUDGET_MULTIPLE,
                 calibrate: bool = False) -> Report:
    """Measure the scenario, and optionally calibrate the harness first.

    Calibration is off by default because it costs two extra measurements and, on the
    machine this was developed on, is rejected by its own consistency check anyway. It is
    exposed so a reader on different hardware can find out whether it holds for them.
    """
    overhead, consistency = (0.0, float("inf"))
    if calibrate:
        overhead, consistency = calibrate_overhead(config.concurrency)

    return Report(
        measurement=measure(config),
        predicted_rps=predicted_rps(config),
        serial_floor_rps=serial_floor_rps(config),
        lag_budget_ms=config.lag_probe_interval_ms * lag_budget_multiple,
        overhead_seconds=overhead,
        overhead_consistency=consistency,
    )
