"""Drive load at a real uvicorn process and compare what the client saw with what the
application recorded.

Two clocks on the same requests. The client's clock starts when it sends and stops when
the response arrives, so it includes every moment the request existed. The application's
clock starts when the event loop hands the request to the middleware, so it excludes any
time the request spent waiting to be handed over.

For a healthy handler the two agree. For a handler that holds the loop they cannot, and
the size of the disagreement is how much of the problem an application metric is blind to.
"""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass

import httpx

HOST = "127.0.0.1"
STARTUP_TIMEOUT_SECONDS = 60


@dataclass(frozen=True)
class LiveResult:
    """One load run against a real server, timed from both ends."""

    shape: str
    concurrency: int
    requests: int
    wall_seconds: float
    rps: float
    client_p50_ms: float
    client_p99_ms: float
    client_max_ms: float
    server_p50_ms: float
    server_p99_ms: float
    server_max_ms: float
    errors: int

    @property
    def hidden_p99_ms(self) -> float:
        """Tail latency the application's own metric did not see."""
        return self.client_p99_ms - self.server_p99_ms

    @property
    def hidden_share(self) -> float:
        """Share of the client observed tail that is invisible from inside the app."""
        if self.client_p99_ms <= 0:
            return 0.0
        return self.hidden_p99_ms / self.client_p99_ms

    def as_dict(self) -> dict:
        return {
            "shape": self.shape, "concurrency": self.concurrency,
            "requests": self.requests, "wall_seconds": round(self.wall_seconds, 4),
            "rps": round(self.rps, 2),
            "client_p50_ms": round(self.client_p50_ms, 3),
            "client_p99_ms": round(self.client_p99_ms, 3),
            "client_max_ms": round(self.client_max_ms, 3),
            "server_p50_ms": round(self.server_p50_ms, 3),
            "server_p99_ms": round(self.server_p99_ms, 3),
            "server_max_ms": round(self.server_max_ms, 3),
            "hidden_p99_ms": round(self.hidden_p99_ms, 3),
            "hidden_share": round(self.hidden_share, 4),
            "errors": self.errors,
        }


class LiveServer:
    """A uvicorn process, started and stopped around a measurement.

    A subprocess rather than an in-process server task. Running the server on the same
    event loop as the load generator would mean the generator competes with the thing it
    is measuring for the very resource under test, which would make every number here a
    measurement of the harness.
    """

    def __init__(self, port: int = 8111, work_ms: float = 5.0,
                 blocking_fraction: float = 1.0, threadpool_size: int = 40) -> None:
        self.port = port
        self._env = dict(
            os.environ,
            ELT_WORK_MS=str(work_ms),
            ELT_BLOCKING_FRACTION=str(blocking_fraction),
            ELT_THREADPOOL_SIZE=str(threadpool_size),
        )
        self._process: subprocess.Popen | None = None

    @property
    def base_url(self) -> str:
        return f"http://{HOST}:{self.port}"

    def __enter__(self) -> LiveServer:
        self._process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "event_loop_tax.server:app",
             "--host", HOST, "--port", str(self.port), "--log-level", "error"],
            env=self._env,
        )
        deadline = time.time() + STARTUP_TIMEOUT_SECONDS
        while time.time() < deadline:
            try:
                httpx.get(f"{self.base_url}/stats", timeout=1.0)
                return self
            except Exception:  # noqa: BLE001
                time.sleep(0.2)
        self.__exit__(None, None, None)
        raise RuntimeError(f"server did not come up within {STARTUP_TIMEOUT_SECONDS}s")

    def __exit__(self, *exc) -> None:
        if self._process is not None:
            self._process.send_signal(signal.SIGINT)
            try:
                self._process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=10)
            self._process = None


def _percentile(ordered: list[float], q: float) -> float:
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
    return ordered[index]


async def drive_live(base_url: str, shape: str, concurrency: int, requests: int,
                     warmup: int = 20) -> LiveResult:
    """Send `requests` requests at `concurrency`, then read the server's own numbers."""
    limits = httpx.Limits(max_connections=concurrency + 10,
                          max_keepalive_connections=concurrency + 10)
    latencies: list[float] = []
    errors = 0

    async with httpx.AsyncClient(base_url=base_url, limits=limits, timeout=120.0) as client:
        for _ in range(warmup):
            await client.get(f"/{shape}")
        await client.post("/reset")

        gate = asyncio.Semaphore(concurrency)

        async def one() -> None:
            nonlocal errors
            async with gate:
                started = time.perf_counter()
                try:
                    response = await client.get(f"/{shape}")
                    if response.status_code != 200:
                        errors += 1
                except Exception:  # noqa: BLE001
                    errors += 1
                    return
                latencies.append((time.perf_counter() - started) * 1000.0)

        started = time.perf_counter()
        await asyncio.gather(*[one() for _ in range(requests)])
        wall = time.perf_counter() - started

        server = (await client.get("/stats")).json()

    ordered = sorted(latencies)
    return LiveResult(
        shape=shape, concurrency=concurrency, requests=requests,
        wall_seconds=wall, rps=requests / wall if wall > 0 else 0.0,
        client_p50_ms=_percentile(ordered, 0.50),
        client_p99_ms=_percentile(ordered, 0.99),
        client_max_ms=ordered[-1] if ordered else 0.0,
        server_p50_ms=server["p50_ms"], server_p99_ms=server["p99_ms"],
        server_max_ms=server["max_ms"], errors=errors,
    )
