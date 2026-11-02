"""E4: the same requests, timed three ways, on a real server.

Everything before this runs in one process against a model of a handler. This runs a real
uvicorn, over real sockets, with FastAPI's own routing and its own threadpool dispatch,
and times the identical requests from three places:

- inside the handler body, where a decorator or a span usually goes
- in an HTTP middleware, which wraps the application and therefore spans the task boundary
- at the client, which sees everything

For a healthy handler all three agree. The question is what they do when the loop is held,
and the answer decides whether the instrumentation most services actually have would catch
this in production.

The apparatus has its own ceiling, which is published first. An earlier version of this
experiment ran at a concurrency where every shape, healthy ones included, collapsed to the
same throughput: that was the HTTP stack and the load generator competing for two cores,
not the handler. Every figure below is taken well under that ceiling.
"""

from __future__ import annotations

import asyncio

import httpx
from _common import table, write

from event_loop_tax.live import LiveServer, drive_live

PORT = 8321
WORK_MS = 50.0
CONCURRENCY = 10
REQUESTS = 250
SHAPES = ("async_awaited", "sync_threadpool", "async_offloaded", "async_blocking")


async def run() -> dict:
    results, cells = {}, []
    with LiveServer(port=PORT, work_ms=WORK_MS) as server:
        async with httpx.AsyncClient(base_url=server.base_url, timeout=120) as client:
            ceiling = await drive_live(server.base_url, "noop",
                                       concurrency=CONCURRENCY, requests=400)
            print(f"\napparatus ceiling, a route that does no work: "
                  f"{ceiling.rps:.1f} rps at concurrency {CONCURRENCY}")
            print(f"every handler below is predicted at or under "
                  f"{CONCURRENCY / (WORK_MS / 1000):.0f} rps, which is "
                  f"{ceiling.rps / (CONCURRENCY / (WORK_MS / 1000)):.1f} times under it\n")

            for shape in SHAPES:
                result = await drive_live(server.base_url, shape,
                                          concurrency=CONCURRENCY, requests=REQUESTS)
                stats = (await client.get("/stats")).json()
                handler_p99 = stats["handler"]["p99_ms"]
                results[shape] = (result, handler_p99)
                cells.append({
                    "shape": shape, "rps": round(result.rps, 1),
                    "handler_p99_ms": round(handler_p99, 2),
                    "middleware_p99_ms": round(result.server_p99_ms, 2),
                    "client_p99_ms": round(result.client_p99_ms, 2),
                    "errors": result.errors,
                })
                print(f"  {shape:17s} {result.rps:7.1f} rps   handler p99 "
                      f"{handler_p99:8.2f}ms   middleware p99 "
                      f"{result.server_p99_ms:8.2f}ms   client p99 "
                      f"{result.client_p99_ms:8.2f}ms")
    return {"ceiling": ceiling, "results": results, "cells": cells}


def main() -> int:
    print(__doc__)
    print(f"{WORK_MS:g}ms of work, concurrency {CONCURRENCY}, {REQUESTS} requests.")

    data = asyncio.run(run())
    results, cells, ceiling = data["results"], data["cells"], data["ceiling"]

    print("\nthe same requests, three clocks\n")
    table(["shape", "rps", "handler p99", "middleware p99", "client p99"],
          [[c["shape"], f"{c['rps']:.1f}", f"{c['handler_p99_ms']:.2f}",
            f"{c['middleware_p99_ms']:.2f}", f"{c['client_p99_ms']:.2f}"] for c in cells],
          [18, 9, 14, 16, 12])

    blocking, blocking_handler = results["async_blocking"]
    awaited, awaited_handler = results["async_awaited"]

    blind_factor = blocking.client_p99_ms / blocking_handler if blocking_handler else 0.0
    healthy_factor = awaited.client_p99_ms / awaited_handler if awaited_handler else 0.0

    print(f"\nThe handler clock reads {blocking_handler:.2f}ms on requests the client "
          f"waited {blocking.client_p99_ms:.2f}ms for, a factor of {blind_factor:.1f}.")
    print(f"On the correct coroutine the same clock is accurate to a factor of "
          f"{healthy_factor:.2f}.")
    print("\nThat clock is not broken and it is not lying. It starts when the loop hands "
          "the request to the handler, so it cannot observe the request waiting to be "
          "handed over, and waiting to be handed over is the entire failure.")

    print(f"\nThe middleware clock does see it, reading "
          f"{blocking.server_p99_ms:.2f}ms, because it wraps the application across the "
          "task boundary rather than sitting inside the handler. Where the timer goes "
          "decides whether the bug is visible, and the two placements look equally "
          "reasonable in a code review.")

    throughput_drop = awaited.rps / blocking.rps if blocking.rps else 0.0
    print(f"\nThroughput tells the truth from anywhere: {blocking.rps:.1f} rps against "
          f"{awaited.rps:.1f}, a factor of {throughput_drop:.1f}, and it survives a real "
          "HTTP stack rather than being an artefact of the in process harness.")

    write("e4_three_clocks", {
        "experiment": "e4_three_clocks",
        "question": "Would the instrumentation a service already has catch this?",
        "work_ms": WORK_MS, "concurrency": CONCURRENCY, "requests": REQUESTS,
        "apparatus_ceiling_rps": round(ceiling.rps, 1),
        "predicted_handler_rps": round(CONCURRENCY / (WORK_MS / 1000), 1),
        "cells": cells,
        "blocking_handler_p99_ms": round(blocking_handler, 2),
        "blocking_client_p99_ms": round(blocking.client_p99_ms, 2),
        "blocking_middleware_p99_ms": round(blocking.server_p99_ms, 2),
        "handler_clock_blind_factor": round(blind_factor, 1),
        "handler_clock_healthy_factor": round(healthy_factor, 2),
        "live_throughput_drop": round(throughput_drop, 1),
        "errors": sum(c["errors"] for c in cells),
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
