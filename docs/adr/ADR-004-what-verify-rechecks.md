# ADR-004: `make verify` re-derives relationships, not timings

Status: accepted

## Context

This repository publishes throughput and latency figures. Rerunning them produces
different numbers: in E1 the run to run spread reaches a factor of 1.54 on one shape. A
checker that re-measured them and demanded they match the published figure would fail at
random.

That leaves an unattractive choice. Either check nothing, and let the published figures
rot, or check something that fails for reasons nobody can act on.

## Decision

Three checks, and only the deterministic ones re-measure.

**The suite.** Deterministic, re-measured on every verify, with a coverage floor.

**The figures.** Read from the recorded run in `docs/experiments`, regenerated only by
`make bench`. Because that directory is in version control, rerunning the benchmarks
produces a diff showing exactly which published figures moved and by how much, which is
more informative than a green tick.

**The relationships.** `tools/check_invariant.py` recomputes every ceiling from
`theory.py` and checks the recorded data against it. No recorded throughput may exceed the
ceiling its shape allows. The serialised invariant, throughput times blocking seconds,
must sit in its band at every concurrency in the sweep. The live experiment must have run
under the apparatus ceiling.

## Why the third one matters

It is the part that makes the recorded figures worth anything. A relationship is not
machine dependent: a measurement above its own physical ceiling is wrong on any hardware,
and the invariant holds wherever the arithmetic holds. So the check runs on every verify,
in CI, on a reader's laptop, and it fails on data that has drifted away from the model
even though it never re-measures a timing.

It was verified by tripling one recorded figure, which makes it exit 1 and name the cell.

## Consequence

The published timings belong to one machine on one day, and the README says so near the
top rather than at the bottom. A reader who wants numbers for their own hardware runs
`make bench`.

The risk accepted is that a change could alter the timings without anything failing, as
long as the result stayed physically consistent. What covers the serious version of that
is the suite: the shapes are asserted to do what they claim against a live lag probe, so a
change that broke the premise fails regardless of timing.
