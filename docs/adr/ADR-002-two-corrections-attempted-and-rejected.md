# ADR-002: Two corrections attempted, measured, and rejected

Status: accepted

## Context

Measured throughput sits below the theoretical ceiling, and the gap shrinks as the work
grows: 0.819 of the ceiling at 2ms of work, 0.958 at 20ms. That is the signature of a
fixed per request cost, not of noise. Twice it looked worth correcting for, and twice the
correction was dropped.

Both attempts are recorded because the rejections are the useful part. Either correction
would have produced a tighter, more precise looking number that was less true.

## Attempt one: a calibrated harness cost

A test began failing intermittently. The cause was not flakiness in the usual sense: at
2ms of work the harness cost is a tenth of the budget, so the measurement sat near the
edge of the agreement band and a loaded machine pushed it over.

The fix seemed obvious. Model per request wall time as work plus a constant, measure the
constant, and tighten the band around a corrected expectation.

`calibrate_overhead` estimates that constant at two work sizes, 4ms and 32ms, which gives
two equations in one unknown. The two estimates should agree. They differ by nearly
fourfold, because a short `asyncio.sleep` overshoots proportionally more than a long one,
so the cost is not a constant and the model that assumes it is does not hold.

**Decision.** The calibration stays in the codebase and is gated by its own consistency
check. `Report.overhead_applied` is true only when the two estimates agree within
`CONSISTENCY_LIMIT`, and on this machine they do not, so the uncorrected ceiling is used
with a generous one sided band and the report records that the tighter check was available
and declined rather than silently skipped.

The test was fixed by raising its work to 10ms, where the harness cost is a rounding
error, with the reason written into the test.

## Attempt two: a loop side cost per request

The fraction sweep in E3 predicts the knee at one over the concurrency. Measured, the loss
starts earlier than that. The same reasoning applied: a request costs the loop something
even when the handler does not block, that cost serialises exactly as blocking does, and
recovering it from the serialised end of the sweep would give a corrected budget.

It recovers a figure that implies a blocking budget of zero at both concurrencies tested.
The sweep directly contradicts that: at concurrency 50 a blocking fraction of 0.005
retains most of the throughput. The recovered number mixes the loop's own cost with the
overshoot of a short `time.sleep` and cannot separate them.

**Decision.** Not published as a correction. E3 reports the clean formula as a marker of
where loss becomes catastrophic, the measured losses beside it, and the finding that
there is no knee at all.

## Why this is in version control

Because the alternative was available and tempting in both cases: average two estimates
that disagree, or publish a correction that makes a table look more rigorous. Both would
have survived review. Neither would have been true, and the second would have told a
reader there is a safe amount of blocking when the measurement says there is not.

## Consequence

The agreement band is wider than it could be, and that is the honest cost. What makes it
tolerable is that the findings this repository actually publishes are factors of 10 to 44,
which no tolerance question touches.
