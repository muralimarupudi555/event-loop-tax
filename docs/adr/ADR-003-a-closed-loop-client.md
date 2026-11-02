# ADR-003: A closed loop client, with the bias published

Status: accepted

## Context

A load harness is either open loop, where requests arrive on a schedule regardless of what
the server is doing, or closed loop, where a fixed number of clients each send again only
once their previous request returned.

## Decision

Closed loop, in both the in process harness and the live driver.

## Why

It is what almost every load test does, including the ones a reader will have run, so the
numbers are comparable to numbers they already have. It also models a real bounded client
population: a connection pool, a fixed set of upstream services, a browser with six
connections per host.

## The bias, which is also a finding

Closed loop measurement suffers from coordinated omission. When the server stalls, the
client stops sending, so the requests that would have arrived during the stall are never
issued and never counted as slow. The measured tail is therefore optimistic, and the true
tail under a stall is worse than anything printed here.

In this repository that bias produces the result in E1 that is most worth looking at: the
blocking handler posts a p99 of 5.93ms, better than all three healthy shapes. It is not an
artefact to apologise for, it is a demonstration of why latency percentiles do not catch
this. The requests that completed were fine. The ones that were never sent are not in the
distribution.

It is tolerable because it runs against this repository's own argument. A bias that made
the blocking handler look worse would be an argument for the conclusion; one that makes it
look better is an argument the conclusion survives.

## Consequence

Every latency figure here is optimistic for the blocking shape and roughly correct for the
others. Throughput is unaffected, which is why throughput is what the conclusions rest on,
and loop lag is unaffected, which is why it is the diagnostic.

An open loop harness would measure the true tail and is the obvious next thing. It needs a
rate schedule, a way to record requests that were never served, and a decision about what
to do when the server cannot keep up at all, none of which this repository has.
