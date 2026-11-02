"""A real FastAPI application with one route per handler shape.

The in-process harness isolates the event loop effect. This exists to check the effect
survives a real stack: a real ASGI server, real sockets, real HTTP parsing, and the
framework's own threadpool dispatch rather than a model of it.

It also measures the thing the harness structurally cannot. The server records how long
each request took *from inside the application*, which is what an ASGI middleware, an APM
agent or a `@app.middleware("http")` timer would record in production. The client records
how long the same request took from the outside. When those two numbers disagree, the
difference is time the request existed but the application had not been scheduled to see
it, and that gap is the reason this bug hides from latency dashboards.
"""

from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from event_loop_tax.config import ScenarioConfig
from event_loop_tax.workload import (
    async_awaited,
    async_blocking,
    async_offloaded,
    burn_blocking,
)

_server_durations_ms: list[float] = []
_handler_durations_ms: list[float] = []
_executor: ThreadPoolExecutor | None = None


@asynccontextmanager
async def timed_handler():
    """Time only the body of the handler, the way a decorator or a span inside a view
    function would.

    This clock starts after the event loop has already chosen to run this request, so by
    construction it cannot observe the request waiting to be chosen. It is the narrowest
    of the three clocks in this repository and the one most instrumentation actually
    uses.
    """
    started = time.perf_counter()
    try:
        yield
    finally:
        _handler_durations_ms.append((time.perf_counter() - started) * 1000.0)


def config_from_env() -> ScenarioConfig:
    return ScenarioConfig(
        handler="async_awaited",
        work_ms=float(os.environ.get("ELT_WORK_MS", "5")),
        blocking_fraction=float(os.environ.get("ELT_BLOCKING_FRACTION", "1.0")),
        threadpool_size=int(os.environ.get("ELT_THREADPOOL_SIZE", "40")),
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _executor
    config = config_from_env()
    _executor = ThreadPoolExecutor(max_workers=config.threadpool_size)
    app.state.config = config
    yield
    _executor.shutdown(wait=False)


def create_app() -> FastAPI:
    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None)

    @app.middleware("http")
    async def record_duration(request: Request, call_next):
        """Time the request the way production does: from inside the application.

        This timer starts when the event loop hands the request to the application. Any
        time the request spent waiting to be handed over is already gone, and no
        application level metric can see it. That omission is the measurement, not a flaw
        in it.
        """
        started = time.perf_counter()
        response = await call_next(request)
        elapsed = (time.perf_counter() - started) * 1000.0
        if request.url.path not in ("/stats", "/reset"):
            _server_durations_ms.append(elapsed)
        response.headers["x-server-ms"] = f"{elapsed:.4f}"
        return response

    @app.get("/async_awaited")
    async def route_async_awaited(request: Request):
        async with timed_handler():
            await async_awaited(request.app.state.config, _executor)
        return {"shape": "async_awaited"}

    @app.get("/async_blocking")
    async def route_async_blocking(request: Request):
        async with timed_handler():
            await async_blocking(request.app.state.config, _executor)
        return {"shape": "async_blocking"}

    @app.get("/sync_threadpool")
    def route_sync_threadpool(request: Request):
        """Declared `def` on purpose.

        Starlette inspects the signature and dispatches a non-coroutine endpoint to its
        own threadpool. Writing this as `async def` with a blocking body would be the bug;
        writing it as `def` is the shape people are warned away from, and it is the safe
        one.
        """
        started = time.perf_counter()
        burn_blocking(request.app.state.config.work_seconds)
        _handler_durations_ms.append((time.perf_counter() - started) * 1000.0)
        return {"shape": "sync_threadpool"}

    @app.get("/async_offloaded")
    async def route_async_offloaded(request: Request):
        async with timed_handler():
            await async_offloaded(request.app.state.config, _executor)
        return {"shape": "async_offloaded"}

    @app.get("/noop")
    async def noop():
        """A route that does no work at all.

        Its throughput is the ceiling of the measurement apparatus: HTTP parsing, JSON
        encoding, sockets, and the load generator's own cost, all on the same machine.
        Any handler comparison run near this number is measuring the apparatus rather
        than the handler, which is a mistake this repository made once and now guards
        against by publishing the ceiling beside every live result.
        """
        return {"shape": "noop"}

    @app.get("/stats")
    async def stats():
        def summarise(values: list[float]) -> dict:
            ordered = sorted(values)
            def pct(q: float) -> float:
                if not ordered:
                    return 0.0
                index = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
                return ordered[index]
            return {
                "count": len(ordered), "p50_ms": pct(0.50), "p95_ms": pct(0.95),
                "p99_ms": pct(0.99), "max_ms": ordered[-1] if ordered else 0.0,
            }

        middleware = summarise(_server_durations_ms)
        return {**middleware, "middleware": middleware,
                "handler": summarise(_handler_durations_ms)}

    @app.post("/reset")
    async def reset():
        _server_durations_ms.clear()
        _handler_durations_ms.clear()
        return {"ok": True}

    return app


app = create_app()
