# ADR-001: Four shapes as separate functions, and `time.sleep` as the blocking call

Status: accepted

## Context

The comparison needs handler shapes that differ in exactly one property: whether their
work occupies the event loop. Two choices decide whether the result means anything.

## Decision one: four named functions, not one parameterised path

Each shape is its own function in `workload.py`, even though three of them share most of
their body.

A single function with a `mode` argument would be shorter and would hide the thing being
demonstrated. The whole claim is that these shapes are nearly identical in source and
wildly different in behaviour, so a reader has to be able to see all four side by side and
find the one character that matters. Deduplicating them would make the repository argue
less well while making it slightly tidier.

`sync_threadpool` earns its place specifically because it is the shape people are warned
away from. A comparison of "correct async" against "broken async" would have been easy and
would have missed the finding: a plain `def` beats a coroutine that blocks by a factor of
34.8, so the advice to prefer `async def` is wrong without a qualifier nobody attaches.

## Decision two: a sleeping thread, not a spin loop

`burn_blocking` calls `time.sleep` rather than looping on arithmetic.

Both hold the loop. They differ in what else they do. A spin loop also consumes a core,
and on a two core machine where the load generator shares the hardware with the thing it
measures, that would confound every number: throughput would fall partly because the loop
was held and partly because the CPU was busy, and the two could not be separated.

A sleeping thread isolates the property under test. It also models the common real cause
more closely than arithmetic does, because the usual way a handler blocks is a synchronous
network or disk call: a `requests.get`, a synchronous database driver, a file read. Those
hold the loop while using no CPU at all, which is exactly what `time.sleep` does.

## Consequence

CPU-bound blocking is untested here, and it is a real case: a handler doing heavy
computation starves the loop the same way and costs a core besides. The figures in this
repository are therefore a lower bound on what blocking costs. ADR-005 says so where a
reader will find it.
