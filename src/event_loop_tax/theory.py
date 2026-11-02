"""What each handler shape's throughput should be, derived before anything was measured.

These are the predictions the experiments are checked against. They are written here, in
their own module, so that the measurement cannot be quietly fitted to them: a law that is
adjusted after seeing the result is not a law, it is a curve fit with an opinion.

Each one is elementary queueing arithmetic, which is the point. The claim of this
repository is not that the behaviour is surprising once stated, it is that nobody states
it, and that the resulting mistake is invisible in code review and in a profiler at low
load.
"""

from __future__ import annotations

from event_loop_tax.config import ScenarioConfig

# Measured throughput is allowed to fall this far short of the ceiling before the
# agreement is called a disagreement. The gap is real work the model does not include:
# scheduler wakeups, executor handoff, and the harness's own bookkeeping.
AGREEMENT_TOLERANCE = 0.25


def predicted_rps(config: ScenarioConfig) -> float:
    """The ceiling this handler shape can reach at this concurrency.

    `async_awaited`: every request is in flight simultaneously, so the limit is simply how
    many are allowed at once divided by how long each takes.

    `async_blocking`: the blocking portion of every request is executed one at a time,
    because the loop runs on one thread and the handler does not yield. The ceiling is
    therefore one over the blocking time per request, and raising concurrency cannot move
    it. This is the invariant worth remembering: throughput times blocking seconds equals
    one, whatever the load.

    `sync_threadpool` and `async_offloaded`: the blocking work runs on worker threads, so
    the limit is the smaller of the concurrency and the pool size.
    """
    work = config.work_seconds
    if config.handler == "async_awaited":
        return config.concurrency / work

    if config.handler == "async_blocking":
        blocking = config.blocking_seconds
        if blocking <= 0:
            return config.concurrency / work
        serial_ceiling = 1.0 / blocking
        return min(config.concurrency / work, serial_ceiling)

    parallel = min(config.concurrency, config.threadpool_size)
    return parallel / work


def serial_floor_rps(config: ScenarioConfig) -> float:
    """Throughput of a fully serialised handler: one over its blocking time.

    Published on its own because it is the number that makes the result memorable. A
    handler blocking for five milliseconds cannot exceed two hundred requests per second
    on one process, no matter how many clients are waiting or how many cores are idle.
    """
    blocking = config.blocking_seconds
    return float("inf") if blocking <= 0 else 1.0 / blocking


def agrees(measured_rps: float, predicted: float, tolerance: float = AGREEMENT_TOLERANCE) -> bool:
    """Whether a measurement lands near its prediction.

    One sided on purpose. Measured throughput below the ceiling is expected and is
    accounted for by overhead the model omits. Measured throughput *above* the ceiling
    means the model is wrong, or the harness is not measuring what it claims, and that is
    a failure rather than a pleasant surprise.
    """
    if predicted == float("inf"):
        return True
    if measured_rps > predicted * 1.05:
        return False
    return measured_rps >= predicted * (1.0 - tolerance)
