"""The four shapes a request handler can take, and the one line that separates them.

Every shape below does the same amount of work for the same wall-clock duration when run
alone. They differ only in whether that work occupies the event loop, and that difference
is the entire subject of this repository.

The naming is deliberate. Three of these are things people write on purpose believing
they are equivalent:

- `async_awaited` is the reference: a coroutine that yields while it waits.
- `async_blocking` is the bug: a coroutine that holds the loop. In code it differs from
  the reference by one character of import and no characters of structure.
- `sync_threadpool` is a plain `def`. Frameworks run it on a worker thread, so it cannot
  block the loop at all. It is the shape people are told to avoid and it is safer than a
  badly written coroutine.
- `async_offloaded` is the fix: a coroutine that hands its blocking part to a thread.
"""

from __future__ import annotations

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor

from event_loop_tax.config import ScenarioConfig


def burn_blocking(seconds: float) -> None:
    """Occupy this thread for a duration without yielding.

    `time.sleep` rather than a spin loop. A spin loop would also burn CPU, which would
    confound the measurement with contention on a two core machine: the question here is
    what happens when the loop is not yielded to, not what happens when the CPU is busy.
    A sleeping thread models the common real cause, a synchronous network or disk call,
    far better than arithmetic does.
    """
    if seconds > 0:
        time.sleep(seconds)


async def wait_awaited(seconds: float) -> None:
    """Yield to the loop for a duration, the way a correct async client would."""
    if seconds > 0:
        await asyncio.sleep(seconds)


async def async_awaited(config: ScenarioConfig, executor: ThreadPoolExecutor) -> None:
    """The reference. Awaits the whole of its work and holds the loop for none of it."""
    await wait_awaited(config.work_seconds)


async def async_blocking(config: ScenarioConfig, executor: ThreadPoolExecutor) -> None:
    """The bug. Holds the loop for `blocking_fraction` of its work.

    At a fraction of 1.0 this is the textbook mistake. The interesting values are the
    small ones, because a handler that is 99 percent correct async and 1 percent blocking
    still serialises that 1 percent across every concurrent request, and nothing in the
    code review flags it.
    """
    burn_blocking(config.blocking_seconds)
    await wait_awaited(config.awaited_seconds)


def sync_body(config: ScenarioConfig) -> None:
    """The body of a plain `def` handler. A sync function cannot await, so all of its
    work is blocking; it is simply blocking a worker thread rather than the loop."""
    burn_blocking(config.work_seconds)


async def sync_threadpool(config: ScenarioConfig, executor: ThreadPoolExecutor) -> None:
    """A plain `def` handler, as a framework runs it: dispatched to a worker thread.

    Modelled here by an explicit executor rather than by the framework's own, so the pool
    size is a measured variable instead of a hidden default. The default in Starlette is
    40 threads, which is a ceiling most people have never looked up.
    """
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(executor, sync_body, config)


async def async_offloaded(config: ScenarioConfig, executor: ThreadPoolExecutor) -> None:
    """The fix. A coroutine that hands its blocking part to a thread and awaits the rest."""
    loop = asyncio.get_running_loop()
    if config.blocking_seconds > 0:
        await loop.run_in_executor(executor, burn_blocking, config.blocking_seconds)
    await wait_awaited(config.awaited_seconds)


HANDLERS = {
    "async_awaited": async_awaited,
    "async_blocking": async_blocking,
    "sync_threadpool": sync_threadpool,
    "async_offloaded": async_offloaded,
}


def build(name: str):
    if name not in HANDLERS:
        raise ValueError(f"unknown handler {name!r}, expected one of {sorted(HANDLERS)}")
    return HANDLERS[name]
