# ADR-005: What this repository cannot measure

Status: accepted

## Context

Every figure here comes from one process on a two core machine, with a model of a
handler rather than a handler. A reader is entitled to know how far that generalises.

## The limits

**One worker process.** Production FastAPI runs several uvicorn workers behind a load
balancer, so a blocked loop takes out one worker rather than the service. That changes the
blast radius and none of the arithmetic: each worker still serialises its own blocking
portion, so a fleet of eight workers has eight times the ceiling and the same shape of
failure. The figures here should be read per worker.

**`time.sleep` stands in for a synchronous call.** It models a blocking network or disk
call, which is the common real cause: a `requests.get`, a synchronous database driver, a
file read. It does not model CPU-bound blocking, which also burns a core and on a two core
machine would confound the comparison with contention. A handler doing heavy computation
starves the loop the same way and costs more besides, and that case is untested here.

**The client is closed loop.** A fixed number of simulated clients, each sending again
only once its previous request returned. When the server stalls the client stops sending,
so requests that would have arrived during the stall are never counted as slow. This is
why the broken handler posts a better p99 than the healthy ones, which is a real finding
about latency metrics and also a real limitation: the true tail under a stall is worse
than anything printed here. The bias flatters the blocking handler, which is to say it
runs against this repository's own argument.

**Generated work, not a real workload.** Every request does the same amount of work. Real
traffic has a distribution, and a long tail of expensive requests interacts with a blocked
loop in ways this does not model.

**Two cores.** The apparatus ceiling is 726 rps, which is low, and it constrains the live
experiment to concurrencies and work sizes well under it. On a larger machine the HTTP
stack would stop binding first and the live figures would change. The in process figures
would not, because they do not go through a socket.

**One framework.** FastAPI and uvicorn. The threadpool size, the dispatch rule for `def`
endpoints and the middleware's position relative to the task boundary are all specific to
Starlette, and the three clock result depends on that last one. Another framework's
middleware may sit on the other side of the boundary and see nothing.

**Two corrections were attempted and rejected**, and neither is in the published figures.
A per request harness cost, calibrated at two work sizes, gave estimates differing by
nearly fourfold, so it is not a constant and the correction was declined. A loop side cost
recovered from the serialised end of the fraction sweep implied no blocking budget at all
at either concurrency, which the sweep itself contradicts. ADR-002 carries both.

## Consequence

The claims this repository supports are narrow and worth making: a coroutine that holds
the loop serialises, its ceiling is one over its blocking time and concurrency cannot move
it, the loss begins at the smallest fraction of blocking rather than at a threshold, a
plain `def` handler is safer than a badly written coroutine, and a timer inside a handler
body cannot see any of it while throughput and loop lag both can.

Anything broader needs a real service and a real workload, which is what the CLI is for.
