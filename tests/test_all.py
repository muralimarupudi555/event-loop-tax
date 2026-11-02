"""Tests for event-loop-tax.

The weight is on the claims the repository publishes: that each handler shape does what
its name says to the event loop, that the throughput laws are arithmetic rather than
aspiration, and that the harness excludes what it says it excludes.

The load bearing one is TestTheShapesDoWhatTheyClaim. Every number in this repository
rests on `async_blocking` really starving the loop and `async_awaited` really not, so that
is asserted rather than assumed.
"""

from __future__ import annotations

import asyncio
import json
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from event_loop_tax import cli, theory
from event_loop_tax.config import HANDLERS, ConfigError, ScenarioConfig
from event_loop_tax.harness import Measurement, RunResult, drive_once, measure
from event_loop_tax.lag import LagProfile, LoopLagProbe
from event_loop_tax.report import build_report
from event_loop_tax.workload import build, burn_blocking, wait_awaited

FAST = dict(work_ms=2.0, requests=200, repeats=3, warmup_requests=5, concurrency=10)


class TestConfigRefusesUntrustworthyScenarios:
    def test_unknown_handler(self):
        with pytest.raises(ConfigError, match="handler must be one of"):
            ScenarioConfig(handler="goroutine")

    def test_zero_work(self):
        with pytest.raises(ConfigError, match="must be positive"):
            ScenarioConfig(work_ms=0)

    def test_blocking_fraction_outside_zero_to_one(self):
        with pytest.raises(ConfigError, match="between 0 and 1"):
            ScenarioConfig(blocking_fraction=1.5)

    def test_zero_concurrency(self):
        with pytest.raises(ConfigError, match="at least 1"):
            ScenarioConfig(concurrency=0)

    def test_too_few_requests_for_a_tail(self):
        with pytest.raises(ConfigError, match="wearing a percentile"):
            ScenarioConfig(requests=50)

    def test_too_few_repeats_for_a_median(self):
        with pytest.raises(ConfigError, match="one run with extra steps"):
            ScenarioConfig(repeats=2)

    def test_no_warmup(self):
        with pytest.raises(ConfigError, match="at least one warmup"):
            ScenarioConfig(warmup_requests=0)

    def test_zero_threadpool(self):
        with pytest.raises(ConfigError, match="at least 1"):
            ScenarioConfig(threadpool_size=0)

    def test_zero_probe_interval(self):
        with pytest.raises(ConfigError, match="must be positive"):
            ScenarioConfig(lag_probe_interval_ms=0)

    def test_work_splits_into_blocking_and_awaited(self):
        config = ScenarioConfig(work_ms=10, blocking_fraction=0.25)
        assert config.blocking_seconds == pytest.approx(0.0025)
        assert config.awaited_seconds == pytest.approx(0.0075)
        assert config.blocking_seconds + config.awaited_seconds == pytest.approx(
            config.work_seconds
        )

    def test_as_dict_round_trips(self):
        assert ScenarioConfig(work_ms=7).as_dict()["work_ms"] == 7


class TestTheShapesDoWhatTheyClaim:
    """The load bearing property. Every published number depends on it."""

    async def _lag_while_running(self, handler_name: str, work_ms: float = 40.0) -> float:
        config = ScenarioConfig(handler=handler_name, work_ms=work_ms, requests=200,
                                repeats=3, warmup_requests=1, lag_probe_interval_ms=5.0)
        handler = build(handler_name)
        probe = LoopLagProbe(config.lag_probe_interval_ms)
        with ThreadPoolExecutor(max_workers=4) as executor:
            probe.start()
            await asyncio.sleep(0)
            await handler(config, executor)
            profile = await probe.stop()
        return profile.max_ms

    async def test_a_blocking_coroutine_starves_the_loop(self):
        """40ms of blocking must leave the probe at least tens of milliseconds late."""
        assert await self._lag_while_running("async_blocking") > 20.0

    async def test_an_awaited_coroutine_does_not(self):
        assert await self._lag_while_running("async_awaited") < 20.0

    async def test_a_sync_handler_on_a_thread_does_not_starve_the_loop(self):
        """The shape people are warned away from is the one that cannot block the loop."""
        assert await self._lag_while_running("sync_threadpool") < 20.0

    async def test_offloading_the_blocking_part_fixes_it(self):
        assert await self._lag_while_running("async_offloaded") < 20.0

    async def test_every_shape_takes_about_the_same_wall_time_alone(self):
        """Alone, all four cost the same. The difference only appears under concurrency,
        which is why this bug ships."""
        durations = {}
        for name in HANDLERS:
            config = ScenarioConfig(handler=name, work_ms=30.0)
            handler = build(name)
            with ThreadPoolExecutor(max_workers=4) as executor:
                await handler(config, executor)  # warm the pool
                started = time.perf_counter()
                await handler(config, executor)
                durations[name] = time.perf_counter() - started
        assert max(durations.values()) < 3 * min(durations.values())

    def test_an_unknown_shape_is_refused_by_name(self):
        with pytest.raises(ValueError, match="unknown handler"):
            build("virtual_thread")

    def test_zero_durations_are_no_ops(self):
        burn_blocking(0)
        burn_blocking(-1)

    async def test_zero_awaited_duration_is_a_no_op(self):
        await wait_awaited(0)


class TestTheThroughputLaws:
    """Arithmetic, asserted exactly, because these are the predictions the experiments
    are checked against."""

    def test_awaited_scales_with_concurrency(self):
        config = ScenarioConfig(handler="async_awaited", work_ms=5, concurrency=50)
        assert theory.predicted_rps(config) == pytest.approx(10_000.0)

    def test_blocking_is_one_over_the_blocking_time(self):
        config = ScenarioConfig(handler="async_blocking", work_ms=5, concurrency=50)
        assert theory.predicted_rps(config) == pytest.approx(200.0)

    def test_blocking_does_not_improve_with_concurrency(self):
        """The invariant. Raising load cannot move a serialised ceiling."""
        low = ScenarioConfig(handler="async_blocking", work_ms=5, concurrency=10)
        high = ScenarioConfig(handler="async_blocking", work_ms=5, concurrency=500)
        assert theory.predicted_rps(low) == theory.predicted_rps(high) == 200.0

    def test_partial_blocking_raises_the_ceiling_proportionally(self):
        config = ScenarioConfig(handler="async_blocking", work_ms=10,
                                blocking_fraction=0.1, concurrency=1000)
        assert theory.predicted_rps(config) == pytest.approx(1000.0)

    def test_a_coroutine_that_blocks_for_nothing_is_just_awaited(self):
        config = ScenarioConfig(handler="async_blocking", work_ms=5,
                                blocking_fraction=0.0, concurrency=50)
        assert theory.predicted_rps(config) == pytest.approx(10_000.0)

    def test_threadpool_is_capped_by_the_pool(self):
        config = ScenarioConfig(handler="sync_threadpool", work_ms=5, concurrency=500,
                                threadpool_size=40)
        assert theory.predicted_rps(config) == pytest.approx(8_000.0)

    def test_threadpool_is_capped_by_concurrency_when_that_is_smaller(self):
        config = ScenarioConfig(handler="sync_threadpool", work_ms=5, concurrency=10,
                                threadpool_size=40)
        assert theory.predicted_rps(config) == pytest.approx(2_000.0)

    def test_offloaded_obeys_the_same_law_as_the_threadpool(self):
        kwargs = dict(work_ms=5, concurrency=100, threadpool_size=25)
        assert theory.predicted_rps(ScenarioConfig(handler="async_offloaded", **kwargs)) == (
            theory.predicted_rps(ScenarioConfig(handler="sync_threadpool", **kwargs))
        )

    def test_serial_floor_is_infinite_when_nothing_blocks(self):
        config = ScenarioConfig(handler="async_awaited", blocking_fraction=0.0)
        assert theory.serial_floor_rps(config) == float("inf")

    def test_agreement_accepts_overhead_below_the_ceiling(self):
        assert theory.agrees(measured_rps=850.0, predicted=1000.0)

    def test_agreement_rejects_throughput_far_below_the_ceiling(self):
        assert not theory.agrees(measured_rps=400.0, predicted=1000.0)

    def test_agreement_rejects_exceeding_the_ceiling(self):
        """Faster than possible is a harness failure, not a pleasant surprise."""
        assert not theory.agrees(measured_rps=1200.0, predicted=1000.0)

    def test_an_infinite_ceiling_always_agrees(self):
        assert theory.agrees(measured_rps=5.0, predicted=float("inf"))


class TestLagProbe:
    async def test_it_records_lag_while_the_loop_is_held(self):
        probe = LoopLagProbe(interval_ms=5.0)
        probe.start()
        await asyncio.sleep(0)
        time.sleep(0.08)
        await asyncio.sleep(0)
        profile = await probe.stop()
        assert profile.samples >= 1
        assert profile.max_ms > 20.0

    async def test_a_quiet_loop_reports_little_lag(self):
        probe = LoopLagProbe(interval_ms=5.0)
        probe.start()
        await asyncio.sleep(0.1)
        profile = await probe.stop()
        assert profile.median_ms < 20.0

    def test_an_unused_probe_reports_zeroes_rather_than_crashing(self):
        assert LoopLagProbe().profile() == LagProfile(0, 0.0, 0.0, 0.0)

    async def test_stop_is_safe_before_start(self):
        assert (await LoopLagProbe().stop()).samples == 0

    def test_profile_is_json_serialisable(self):
        json.dumps(LagProfile(3, 1.0, 2.0, 3.0).as_dict())


class TestHarness:
    async def test_warmups_are_excluded_from_the_samples(self):
        config = ScenarioConfig(handler="async_awaited", **FAST)
        with ThreadPoolExecutor(max_workers=4) as executor:
            result = await drive_once(config, executor)
        assert result.requests == config.requests

    def test_percentiles_are_ordered(self):
        m = measure(ScenarioConfig(handler="async_awaited", **FAST))
        assert m.runs[0].p50_ms <= m.runs[0].p95_ms <= m.runs[0].p99_ms <= m.runs[0].max_ms

    def test_repeats_each_produce_a_run(self):
        m = measure(ScenarioConfig(handler="async_awaited", **FAST))
        assert len(m.runs) == FAST["repeats"]

    def test_spread_is_reported_beside_the_median(self):
        m = measure(ScenarioConfig(handler="async_awaited", **FAST))
        assert m.rps_spread >= 1.0

    def test_a_zero_wall_time_does_not_divide_by_zero(self):
        empty = RunResult(wall_seconds=0.0, requests=0, rps=0.0, p50_ms=0.0, p95_ms=0.0,
                          p99_ms=0.0, max_ms=0.0, lag=LagProfile(0, 0.0, 0.0, 0.0))
        m = Measurement(config=ScenarioConfig(), runs=[empty, empty, empty])
        assert m.rps_spread == 0.0

    def test_measurement_is_json_serialisable(self):
        json.dumps(measure(ScenarioConfig(handler="async_awaited", **FAST)).as_dict())


class TestReportVerdicts:
    def test_a_healthy_handler_agrees_with_theory_and_does_not_block(self):
        report = build_report(ScenarioConfig(handler="async_awaited", **FAST))
        assert not report.loop_blocked
        assert not report.exceeds_ceiling

    def test_a_blocking_handler_is_detected(self):
        report = build_report(ScenarioConfig(handler="async_blocking", **FAST))
        assert report.loop_blocked
        assert report.serial_floor_ratio > 0.5

    def test_the_blocking_handler_sits_near_its_serial_floor(self):
        """The claim in one assertion: it is not slow, it is serialised."""
        report = build_report(ScenarioConfig(handler="async_blocking", **FAST))
        assert 0.7 < report.serial_floor_ratio < 1.1

    def test_report_is_json_serialisable(self):
        json.dumps(build_report(ScenarioConfig(handler="async_awaited", **FAST)).as_dict())

    def test_an_infinite_ceiling_reports_a_full_share(self):
        report = build_report(
            ScenarioConfig(handler="async_awaited", blocking_fraction=0.0, **FAST)
        )
        assert report.serial_floor_rps == float("inf")
        assert report.as_dict()["serial_floor_rps"] is None
        assert report.serial_floor_ratio == 0.0


class TestExitCodes:
    def _report(self, **overrides):
        from event_loop_tax.harness import Measurement as M
        from event_loop_tax.report import Report

        run = RunResult(wall_seconds=1.0, requests=100, rps=100.0, p50_ms=1.0,
                        p95_ms=2.0, p99_ms=3.0, max_ms=4.0,
                        lag=LagProfile(10, 0.1, overrides.pop("lag_p99", 0.2), 0.3))
        defaults = dict(measurement=M(config=ScenarioConfig(), runs=[run, run, run]),
                        predicted_rps=100.0, serial_floor_rps=200.0, lag_budget_ms=50.0)
        defaults.update(overrides)
        return Report(**defaults)

    def test_healthy(self):
        assert cli.classify(self._report()) == cli.EXIT_HEALTHY

    def test_blocked_loop(self):
        assert cli.classify(self._report(lag_p99=900.0)) == cli.EXIT_LOOP_BLOCKED

    def test_degraded(self):
        assert cli.classify(self._report(predicted_rps=1000.0)) == cli.EXIT_DEGRADED

    def test_impossible_beats_every_other_verdict(self):
        """A measurement above the ceiling must not be reported as any kind of result."""
        assert cli.classify(self._report(predicted_rps=10.0, lag_p99=900.0)) == (
            cli.EXIT_IMPOSSIBLE
        )

    def test_a_blocked_loop_outranks_merely_degraded(self):
        code = cli.classify(self._report(predicted_rps=1000.0, lag_p99=900.0))
        assert code == cli.EXIT_LOOP_BLOCKED

    def test_render_names_the_diagnosis_when_the_loop_is_blocked(self):
        rendered = cli.render(self._report(lag_p99=900.0))
        assert "not yielding" in rendered

    def test_render_warns_when_the_measurement_is_impossible(self):
        assert "should not be used" in cli.render(self._report(predicted_rps=10.0))

    def test_render_says_concurrency_will_not_help_when_serialised(self):
        rendered = cli.render(self._report(lag_p99=900.0, predicted_rps=100.0,
                                           serial_floor_rps=105.0))
        assert "adding concurrency will not help" in rendered


class TestCliEndToEnd:
    def test_a_rejected_scenario_exits_two(self, capsys):
        assert cli.main(["--work-ms", "0"]) == cli.EXIT_BAD_CONFIG
        assert "invalid scenario" in capsys.readouterr().err

    def test_a_healthy_run_exits_zero(self, capsys):
        """Ten milliseconds of work, not two, and the reason is the subject of ADR-002.

        The harness costs a fraction of a millisecond per request. Against two
        milliseconds of work that is a tenth of the budget and the measurement lands
        near the edge of the agreement band, so this test failed intermittently under a
        loaded machine. Against ten it is a rounding error. The flakiness was a real
        signal about the model, not a reason to widen the band until nothing fails.
        """
        code = cli.main(["--handler", "async_awaited", "--work-ms", "10",
                         "--requests", "200", "--repeats", "3", "--concurrency", "10"])
        assert code == cli.EXIT_HEALTHY
        assert "throughput" in capsys.readouterr().out

    def test_a_blocking_run_exits_four(self):
        code = cli.main(["--handler", "async_blocking", "--work-ms", "2",
                         "--requests", "200", "--repeats", "3", "--concurrency", "10"])
        assert code == cli.EXIT_LOOP_BLOCKED

    def test_json_output_parses(self, capsys):
        cli.main(["--handler", "async_awaited", "--work-ms", "2", "--requests", "200",
                  "--repeats", "3", "--concurrency", "10", "--json"])
        payload = json.loads(capsys.readouterr().out)
        assert "predicted_rps" in payload and "loop_blocked" in payload

    def test_an_internal_failure_exits_three(self, monkeypatch, capsys):
        def boom(*args, **kwargs):
            raise RuntimeError("loop exploded")

        monkeypatch.setattr(cli, "build_report", boom)
        assert cli.main(["--requests", "200", "--repeats", "3"]) == cli.EXIT_INTERNAL_ERROR
        assert "failed to complete" in capsys.readouterr().err


class TestSmallSurfaces:
    def test_an_empty_percentile_is_zero_rather_than_an_index_error(self):
        from event_loop_tax.harness import _percentile

        assert _percentile([], 0.99) == 0.0

    def test_an_infinite_ceiling_counts_as_fully_achieved(self):
        """Nothing blocks, so there is no ceiling to fall short of."""
        from event_loop_tax.harness import Measurement as M
        from event_loop_tax.report import Report

        run = RunResult(wall_seconds=1.0, requests=100, rps=100.0, p50_ms=1.0, p95_ms=2.0,
                        p99_ms=3.0, max_ms=4.0, lag=LagProfile(1, 0.1, 0.2, 0.3))
        report = Report(measurement=M(config=ScenarioConfig(), runs=[run]),
                        predicted_rps=float("inf"), serial_floor_rps=float("inf"),
                        lag_budget_ms=50.0)
        assert report.achieved_share == 1.0
        assert report.exceeds_ceiling is False


class TestTheServerApp:
    """The real FastAPI app, exercised in process through an ASGI transport.

    In process rather than over a socket because the subject here is the application's
    routing, middleware and the three clocks, not the network. The socket path is covered
    by the slow tests below, which start a real uvicorn.
    """

    async def _client(self):
        import httpx

        from event_loop_tax.server import create_app

        app = create_app()
        transport = httpx.ASGITransport(app=app)
        return httpx.AsyncClient(transport=transport, base_url="http://app"), app

    async def test_every_shape_has_a_route_that_answers(self):
        client, app = await self._client()
        async with client, app.router.lifespan_context(app):
            for shape in HANDLERS:
                response = await client.get(f"/{shape}")
                assert response.status_code == 200
                assert response.json()["shape"] == shape

    async def test_the_middleware_stamps_a_server_duration(self):
        client, app = await self._client()
        async with client, app.router.lifespan_context(app):
            response = await client.get("/async_awaited")
            assert float(response.headers["x-server-ms"]) > 0

    async def test_stats_reports_both_clocks(self):
        client, app = await self._client()
        async with client, app.router.lifespan_context(app):
            await client.post("/reset")
            for _ in range(3):
                await client.get("/async_awaited")
            stats = (await client.get("/stats")).json()
        assert stats["handler"]["count"] == 3
        assert stats["middleware"]["count"] == 3
        assert stats["p99_ms"] == stats["middleware"]["p99_ms"]

    async def test_the_noop_route_does_no_work(self):
        """It exists to measure the apparatus, so it must not be part of the apparatus."""
        client, app = await self._client()
        async with client, app.router.lifespan_context(app):
            await client.post("/reset")
            response = await client.get("/noop")
            stats = (await client.get("/stats")).json()
        assert response.json()["shape"] == "noop"
        assert stats["middleware"]["p99_ms"] < 5.0

    async def test_reset_clears_both_clocks(self):
        client, app = await self._client()
        async with client, app.router.lifespan_context(app):
            await client.get("/async_awaited")
            await client.post("/reset")
            stats = (await client.get("/stats")).json()
        assert stats["handler"]["count"] == 0 and stats["middleware"]["count"] == 0

    async def test_stats_and_reset_are_not_counted_as_requests(self):
        client, app = await self._client()
        async with client, app.router.lifespan_context(app):
            await client.post("/reset")
            await client.get("/stats")
            stats = (await client.get("/stats")).json()
        assert stats["middleware"]["count"] == 0

    async def test_empty_stats_do_not_divide_by_zero(self):
        client, app = await self._client()
        async with client, app.router.lifespan_context(app):
            await client.post("/reset")
            stats = (await client.get("/stats")).json()
        assert stats["p50_ms"] == 0.0 and stats["max_ms"] == 0.0

    def test_config_from_env_reads_the_knobs(self, monkeypatch):
        from event_loop_tax.server import config_from_env

        monkeypatch.setenv("ELT_WORK_MS", "12.5")
        monkeypatch.setenv("ELT_BLOCKING_FRACTION", "0.25")
        monkeypatch.setenv("ELT_THREADPOOL_SIZE", "7")
        config = config_from_env()
        assert config.work_ms == 12.5
        assert config.blocking_fraction == 0.25
        assert config.threadpool_size == 7


class TestLiveResultArithmetic:
    def _result(self, **overrides):
        from event_loop_tax.live import LiveResult

        defaults = dict(shape="async_blocking", concurrency=10, requests=200,
                        wall_seconds=10.0, rps=20.0, client_p50_ms=50.0,
                        client_p99_ms=500.0, client_max_ms=600.0, server_p50_ms=50.0,
                        server_p99_ms=100.0, server_max_ms=120.0, errors=0)
        defaults.update(overrides)
        return LiveResult(**defaults)

    def test_hidden_latency_is_the_gap_between_the_clocks(self):
        assert self._result().hidden_p99_ms == pytest.approx(400.0)

    def test_hidden_share_is_a_fraction_of_what_the_client_saw(self):
        assert self._result().hidden_share == pytest.approx(0.8)

    def test_a_zero_client_tail_does_not_divide_by_zero(self):
        assert self._result(client_p99_ms=0.0).hidden_share == 0.0

    def test_it_is_json_serialisable(self):
        json.dumps(self._result().as_dict())


@pytest.mark.slow
class TestAgainstARealServer:
    """A real uvicorn process, real sockets. Marked slow: it costs seconds, and the
    property it proves is the one the whole repository rests on, so it is proved once."""

    async def test_the_handler_clock_cannot_see_a_blocked_loop(self):
        import httpx

        from event_loop_tax.live import LiveServer, drive_live

        with LiveServer(port=8231, work_ms=40.0) as server:
            result = await drive_live(server.base_url, "async_blocking",
                                      concurrency=10, requests=200, warmup=5)
            async with httpx.AsyncClient(base_url=server.base_url, timeout=60) as client:
                stats = (await client.get("/stats")).json()

        assert result.errors == 0
        # The handler clock starts after the loop has already chosen to run the request,
        # so it reports roughly the work time however long the request really waited.
        assert stats["handler"]["p99_ms"] < 4 * 40.0
        assert result.client_p99_ms > 3 * stats["handler"]["p99_ms"]

    async def test_a_healthy_handler_agrees_across_all_three_clocks(self):
        import httpx

        from event_loop_tax.live import LiveServer, drive_live

        with LiveServer(port=8232, work_ms=40.0) as server:
            result = await drive_live(server.base_url, "async_awaited",
                                      concurrency=10, requests=200, warmup=5)
            async with httpx.AsyncClient(base_url=server.base_url, timeout=60) as client:
                stats = (await client.get("/stats")).json()

        assert result.errors == 0
        assert result.client_p99_ms < 4 * stats["handler"]["p99_ms"]

    def test_a_server_that_never_comes_up_is_an_error_not_a_hang(self, monkeypatch):
        """The port is held by this test, so uvicorn cannot bind it.

        An earlier version used port 1 and expected the bind to fail. It does not when
        the tests run as root, which privileged ports permit, so the server came up and
        the test asserted the opposite of what happened.
        """
        import socket

        from event_loop_tax import live

        holder = socket.socket()
        holder.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        holder.bind((live.HOST, 0))
        holder.listen(1)
        port = holder.getsockname()[1]
        try:
            monkeypatch.setattr(live, "STARTUP_TIMEOUT_SECONDS", 2)
            with pytest.raises(RuntimeError, match="did not come up"), live.LiveServer(
                port=port
            ):
                pass
        finally:
            holder.close()


class TestTheOverheadCorrection:
    """The correction exists, and the point of it is that it is allowed to be rejected."""

    def test_zero_overhead_leaves_the_law_untouched(self):
        config = ScenarioConfig(handler="async_awaited", work_ms=5, concurrency=50)
        assert theory.expected_rps(config, 0.0) == theory.predicted_rps(config)

    def test_overhead_lowers_the_awaited_ceiling(self):
        config = ScenarioConfig(handler="async_awaited", work_ms=5, concurrency=10)
        assert theory.expected_rps(config, 0.001) == pytest.approx(10 / 0.006)

    def test_overhead_lowers_the_serial_ceiling_too(self):
        """It runs on the same thread as the handler, so it serialises with it."""
        config = ScenarioConfig(handler="async_blocking", work_ms=5, concurrency=100)
        assert theory.expected_rps(config, 0.001) == pytest.approx(1 / 0.006)

    def test_overhead_lowers_the_threadpool_ceiling(self):
        config = ScenarioConfig(handler="sync_threadpool", work_ms=5, concurrency=10,
                                threadpool_size=40)
        assert theory.expected_rps(config, 0.001) == pytest.approx(10 / 0.006)

    def test_a_consistent_calibration_is_applied(self):
        from event_loop_tax.harness import Measurement as M
        from event_loop_tax.report import Report

        config = ScenarioConfig(handler="async_awaited", work_ms=5.0, concurrency=10)
        run = RunResult(wall_seconds=1.0, requests=100, rps=100.0, p50_ms=1.0, p95_ms=2.0,
                        p99_ms=3.0, max_ms=4.0, lag=LagProfile(1, 0.1, 0.2, 0.3))
        report = Report(measurement=M(config=config, runs=[run]),
                        predicted_rps=theory.predicted_rps(config),
                        serial_floor_rps=200.0, lag_budget_ms=50.0,
                        overhead_seconds=0.001, overhead_consistency=1.1)
        assert report.overhead_applied
        assert report.comparison_rps < report.predicted_rps
        assert report.tolerance == theory.CORRECTED_TOLERANCE

    def test_an_inconsistent_calibration_is_declined(self):
        """Two estimates of one constant that disagree mean it is not a constant."""
        from event_loop_tax.harness import Measurement as M
        from event_loop_tax.report import Report

        config = ScenarioConfig(handler="async_awaited", work_ms=5.0, concurrency=10)
        run = RunResult(wall_seconds=1.0, requests=100, rps=100.0, p50_ms=1.0, p95_ms=2.0,
                        p99_ms=3.0, max_ms=4.0, lag=LagProfile(1, 0.1, 0.2, 0.3))
        report = Report(measurement=M(config=config, runs=[run]),
                        predicted_rps=theory.predicted_rps(config),
                        serial_floor_rps=200.0, lag_budget_ms=50.0,
                        overhead_seconds=0.001, overhead_consistency=3.8)
        assert not report.overhead_applied
        assert report.comparison_rps == report.predicted_rps
        assert report.tolerance == theory.AGREEMENT_TOLERANCE
        assert report.as_dict()["overhead_applied"] is False

    def test_an_uncalibrated_report_declines_by_default(self):
        report = build_report(ScenarioConfig(handler="async_awaited", **FAST))
        assert not report.overhead_applied
        assert report.as_dict()["overhead_consistency"] is None

    @pytest.mark.slow
    def test_calibration_returns_an_estimate_and_its_consistency(self):
        from event_loop_tax.harness import calibrate_overhead

        overhead, consistency = calibrate_overhead(concurrency=10, requests=200, repeats=3)
        assert overhead >= 0.0
        assert consistency >= 1.0

    @pytest.mark.slow
    def test_the_cli_accepts_the_calibrate_flag(self):
        code = cli.main(["--handler", "async_awaited", "--work-ms", "5",
                         "--requests", "200", "--repeats", "3", "--concurrency", "10",
                         "--calibrate"])
        assert code in {cli.EXIT_HEALTHY, cli.EXIT_DEGRADED}


class TestLiveDriverFailurePaths:
    async def test_a_non_200_response_is_counted_as_an_error(self, monkeypatch):
        import httpx

        from event_loop_tax import live
        from event_loop_tax.server import create_app

        app = create_app()
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)

            class Client(httpx.AsyncClient):
                async def get(self, url, *a, **kw):
                    if url.endswith("/missing"):
                        return httpx.Response(404, request=httpx.Request("GET", url))
                    return await super().get(url, *a, **kw)

            monkeypatch.setattr(live.httpx, "AsyncClient",
                                lambda **kw: Client(transport=transport,
                                                    base_url="http://app"))
            result = await live.drive_live("http://app", "missing", concurrency=2,
                                           requests=4, warmup=0)
        assert result.errors == 4

    async def test_a_transport_failure_is_counted_rather_than_raised(self, monkeypatch):
        import httpx

        from event_loop_tax import live
        from event_loop_tax.server import create_app

        app = create_app()
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)

            class Client(httpx.AsyncClient):
                async def get(self, url, *a, **kw):
                    if url.endswith("/boom"):
                        raise httpx.ConnectError("refused")
                    return await super().get(url, *a, **kw)

            monkeypatch.setattr(live.httpx, "AsyncClient",
                                lambda **kw: Client(transport=transport,
                                                    base_url="http://app"))
            result = await live.drive_live("http://app", "boom", concurrency=2,
                                           requests=4, warmup=0)
        assert result.errors == 4 and result.client_p99_ms == 0.0

    def test_a_server_that_ignores_sigint_is_killed(self, monkeypatch):
        import subprocess

        from event_loop_tax import live

        killed = {"value": False}

        class Stubborn:
            def send_signal(self, sig): pass
            def wait(self, timeout=None):
                if not killed["value"]:
                    raise subprocess.TimeoutExpired(cmd="uvicorn", timeout=timeout)
            def kill(self): killed["value"] = True

        server = live.LiveServer(port=8299)
        server._process = Stubborn()
        server.__exit__(None, None, None)
        assert killed["value"] is True
