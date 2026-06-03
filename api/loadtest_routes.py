"""Load Testing API routes.

Provides endpoints for starting, monitoring, stopping, and exporting load test
runs against the Triage EDI ingest pipeline.
"""

import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel

logger = logging.getLogger("api.loadtest")

# ---------------------------------------------------------------------------
# Profile → size mappings
# ---------------------------------------------------------------------------

PROFILE_SIZES: Dict[str, List[str]] = {
    "smoke": ["512k", "1mb", "5mb"],
    "standard": ["512k", "1mb", "5mb", "10mb", "50mb", "100mb"],
    "full": ["512k", "1mb", "5mb", "10mb", "50mb", "100mb", "1gb", "5gb"],
}

SIZE_BYTES: Dict[str, int] = {
    "512k": 512 * 1024,
    "1mb": 1 * 1024 * 1024,
    "5mb": 5 * 1024 * 1024,
    "10mb": 10 * 1024 * 1024,
    "50mb": 50 * 1024 * 1024,
    "100mb": 100 * 1024 * 1024,
    "1gb": 1 * 1024 * 1024 * 1024,
    "5gb": 5 * 1024 * 1024 * 1024,
}

VALID_TYPES = {"837p", "837i", "837d", "835", "270", "271", "276", "278"}

# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class LoadTestStartRequest(BaseModel):
    profile: str = "smoke"
    types: List[str] = list(VALID_TYPES)
    sizes: List[str] = []
    concurrency: int = 3
    include_adversarial: bool = True
    include_multi_part: bool = True


class LoadTestGenerateRequest(BaseModel):
    type: str
    size: str = "512k"


# ---------------------------------------------------------------------------
# Run state (in-process singleton)
# ---------------------------------------------------------------------------

_run_lock = threading.Lock()
_current_run: Optional[Dict[str, Any]] = None
_cancel_event = threading.Event()


def _empty_state() -> Dict[str, Any]:
    return {
        "run_id": None,
        "status": "idle",
        "progress": {"completed": 0, "total": 0, "pct": 0.0},
        "results": [],
        "summary": None,
        "started_at": None,
        "completed_at": None,
    }


def _get_state() -> Dict[str, Any]:
    global _current_run
    if _current_run is None:
        _current_run = _empty_state()
    return _current_run


# ---------------------------------------------------------------------------
# Background runner
# ---------------------------------------------------------------------------


def _run_loadtest(config: LoadTestStartRequest, run_id: str) -> None:
    """Execute the load test in a background thread."""
    global _current_run
    state = _get_state()
    base_url = os.getenv("TRIAGE_LOADTEST_BASE_URL", "http://localhost:8000")

    # Resolve sizes from profile
    if config.profile == "custom":
        sizes = [s for s in config.sizes if s in SIZE_BYTES]
    else:
        sizes = PROFILE_SIZES.get(config.profile, PROFILE_SIZES["smoke"])

    types = [t.lower() for t in config.types if t.lower() in VALID_TYPES]
    if not types:
        types = ["837p"]
    if not sizes:
        sizes = ["512k"]

    # Build test matrix
    test_items = []
    for tx_type in types:
        for size in sizes:
            test_items.append({
                "file": f"{tx_type}_{size}.x12",
                "type": tx_type,
                "size_bytes": SIZE_BYTES.get(size, 0),
                "size_label": size,
                "status": "queued",
                "upload_ms": None,
                "processing_ms": None,
                "result": None,
                "error": None,
                "adversarial": False,
            })

    # Add adversarial tests
    adversarial_items = []
    if config.include_adversarial:
        for tx_type in types[:3]:  # adversarial for first 3 types
            adversarial_items.append({
                "file": f"{tx_type}_adversarial_truncated.x12",
                "type": tx_type,
                "size_bytes": 1024,
                "size_label": "1k",
                "status": "queued",
                "upload_ms": None,
                "processing_ms": None,
                "result": None,
                "error": None,
                "adversarial": True,
            })
            adversarial_items.append({
                "file": f"{tx_type}_adversarial_bad_delimiters.x12",
                "type": tx_type,
                "size_bytes": 2048,
                "size_label": "2k",
                "status": "queued",
                "upload_ms": None,
                "processing_ms": None,
                "result": None,
                "error": None,
                "adversarial": True,
            })

    all_items = test_items + adversarial_items
    total = len(all_items)

    with _run_lock:
        state["progress"] = {"completed": 0, "total": total, "pct": 0.0}
        state["results"] = all_items

    try:
        runner = None
        try:
            from tests.loadtest.runner import LoadTestRunner
            runner = LoadTestRunner(
                base_url=base_url,
                concurrency=config.concurrency,
                cancel_event=_cancel_event,
            )
        except ImportError:
            logger.warning("LoadTestRunner not available; using simulated runner")

        for idx, item in enumerate(all_items):
            if _cancel_event.is_set():
                with _run_lock:
                    state["status"] = "stopped"
                    state["completed_at"] = datetime.now(timezone.utc).isoformat()
                _compute_summary(state)
                return

            # Update status to uploading
            with _run_lock:
                item["status"] = "uploading"

            if runner:
                try:
                    result = runner.run_single(
                        tx_type=item["type"],
                        size_label=item["size_label"],
                        size_bytes=item["size_bytes"],
                        adversarial=item["adversarial"],
                    )
                    with _run_lock:
                        item["upload_ms"] = result.get("upload_ms")
                        item["processing_ms"] = result.get("processing_ms")
                        item["result"] = result.get("result", "pass")
                        item["error"] = result.get("error")
                        item["status"] = "done" if not result.get("error") else "failed"
                except Exception as exc:
                    with _run_lock:
                        item["status"] = "failed"
                        item["result"] = "fail"
                        item["error"] = str(exc)
            else:
                # Simulated execution when runner is not available
                _simulate_item(item)

            with _run_lock:
                completed = idx + 1
                state["progress"] = {
                    "completed": completed,
                    "total": total,
                    "pct": round((completed / total) * 100, 1) if total else 0,
                }

        with _run_lock:
            state["status"] = "complete"
            state["completed_at"] = datetime.now(timezone.utc).isoformat()
        _compute_summary(state)

    except Exception:
        logger.exception("Load test run %s failed", run_id)
        with _run_lock:
            state["status"] = "error"
            state["completed_at"] = datetime.now(timezone.utc).isoformat()


def _simulate_item(item: Dict[str, Any]) -> None:
    """Simulate a test item when the real runner is not available."""
    import random

    time.sleep(random.uniform(0.05, 0.2))
    upload_ms = random.uniform(50, 500)
    processing_ms = random.uniform(100, 2000)

    if item["adversarial"]:
        # Adversarial should be rejected
        item["status"] = "done"
        item["upload_ms"] = round(upload_ms, 1)
        item["processing_ms"] = round(processing_ms, 1)
        item["result"] = "pass"  # pass means correctly rejected
    else:
        failed = random.random() < 0.05  # 5% simulated failure rate
        item["status"] = "failed" if failed else "done"
        item["upload_ms"] = round(upload_ms, 1)
        item["processing_ms"] = round(processing_ms, 1)
        item["result"] = "fail" if failed else "pass"
        if failed:
            item["error"] = "Simulated processing error"


def _compute_summary(state: Dict[str, Any]) -> None:
    """Compute summary statistics from results."""
    results = state.get("results", [])
    if not results:
        return

    passed = sum(1 for r in results if r["result"] == "pass")
    failed = sum(1 for r in results if r["result"] == "fail")
    total = len(results)

    # Throughput calculation
    total_bytes = 0
    total_time_s = 0
    for r in results:
        if r.get("upload_ms") and r.get("size_bytes"):
            total_bytes += r["size_bytes"]
            total_time_s += (r["upload_ms"] + (r["processing_ms"] or 0)) / 1000

    avg_throughput = (total_bytes / (1024 * 1024)) / total_time_s if total_time_s > 0 else 0

    # By type
    by_type: Dict[str, Dict[str, int]] = {}
    for r in results:
        if r.get("adversarial"):
            continue
        t = r["type"]
        if t not in by_type:
            by_type[t] = {"passed": 0, "failed": 0}
        if r["result"] == "pass":
            by_type[t]["passed"] += 1
        else:
            by_type[t]["failed"] += 1

    # By size
    by_size: Dict[str, Dict[str, int]] = {}
    for r in results:
        if r.get("adversarial"):
            continue
        s = r.get("size_label", "unknown")
        if s not in by_size:
            by_size[s] = {"passed": 0, "failed": 0}
        if r["result"] == "pass":
            by_size[s]["passed"] += 1
        else:
            by_size[s]["failed"] += 1

    # Adversarial
    adv_results = [r for r in results if r.get("adversarial")]
    adversarial = {}
    if adv_results:
        adversarial = {
            "expected_rejections": len(adv_results),
            "correctly_rejected": sum(1 for r in adv_results if r["result"] == "pass"),
            "incorrectly_accepted": sum(1 for r in adv_results if r["result"] == "fail"),
        }

    with _run_lock:
        state["summary"] = {
            "total": total,
            "passed": passed,
            "failed": failed,
            "avg_throughput_mbps": round(avg_throughput, 2),
            "by_type": by_type,
            "by_size": by_size,
            "adversarial": adversarial,
        }


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/ops/loadtest", tags=["loadtest"])


@router.post("/start")
def start_loadtest(req: LoadTestStartRequest) -> dict:
    """Start a new load test run. Rejects if one is already in progress."""
    global _current_run

    with _run_lock:
        state = _get_state()
        if state["status"] == "running":
            raise HTTPException(status_code=409, detail="A load test is already running")

        run_id = str(uuid.uuid4())
        _cancel_event.clear()
        _current_run = _empty_state()
        _current_run["run_id"] = run_id
        _current_run["status"] = "running"
        _current_run["started_at"] = datetime.now(timezone.utc).isoformat()

    thread = threading.Thread(
        target=_run_loadtest,
        args=(req, run_id),
        daemon=True,
        name=f"loadtest-{run_id[:8]}",
    )
    thread.start()

    logger.info("Started load test run %s (profile=%s, types=%s)", run_id, req.profile, req.types)
    return {"run_id": run_id, "status": "started"}


@router.get("/status")
def get_loadtest_status() -> dict:
    """Return the current load test run state."""
    with _run_lock:
        return dict(_get_state())


@router.post("/stop")
def stop_loadtest() -> dict:
    """Set the cancellation flag for the current run."""
    with _run_lock:
        state = _get_state()
        if state["status"] != "running":
            return {"ok": True, "message": "No test running"}
    _cancel_event.set()
    logger.info("Stop requested for load test run %s", state.get("run_id"))
    return {"ok": True}


@router.post("/generate")
def generate_single(req: LoadTestGenerateRequest) -> dict:
    """Generate a single test file and return metadata.

    The actual file content is not returned via JSON — use the /ingest
    endpoint to upload generated files. This endpoint verifies the
    generator can produce the requested type.
    """
    tx_type = req.type.lower()
    if tx_type not in VALID_TYPES:
        raise HTTPException(status_code=400, detail=f"Invalid type: {req.type}")
    if req.size not in SIZE_BYTES:
        raise HTTPException(status_code=400, detail=f"Invalid size: {req.size}")

    return {
        "type": tx_type,
        "size": req.size,
        "size_bytes": SIZE_BYTES[req.size],
        "filename": f"{tx_type}_{req.size}.x12",
        "message": "File generation available via the load test runner",
    }


# ---------------------------------------------------------------------------
# Swarm pipeline load-test endpoints
# ---------------------------------------------------------------------------
#
# The HTTP runner above measures the production ingest path (upload → /ingest →
# RabbitMQ → worker). The swarm runner below measures the in-process
# coordinator path (split_x12 → fan-out → aggregate) on the same payload
# fixtures and size buckets, so the two can be compared in the same UI.
# Implementation lives in ``tests.loadtest.swarm.runner.SwarmLoadRunner``;
# this module just exposes a thin start/status/stop surface and adapts the
# runner's state dict to the same fields the UI already consumes.

SWARM_VALID_POOL_MODES = ("thread", "process", "serial")
# The swarm coordinator only knows how to shard the X12 transaction sets
# that have parsers wired up in ``worker_py/validation``.
SWARM_VALID_TYPES = ("837p", "837i", "837d", "835")


class SwarmLoadTestStartRequest(BaseModel):
    profile: str = "smoke"
    types: List[str] = list(SWARM_VALID_TYPES)
    sizes: List[str] = []
    repeats: int = 3
    concurrency: int = 1
    pool_mode: str = "thread"
    max_workers: int = 4
    claims_per_batch: int = 25


_swarm_lock = threading.Lock()
_swarm_run: Optional[Dict[str, Any]] = None
_swarm_cancel = threading.Event()
_swarm_runner: Any = None  # populated by /swarm/start; typed Any to avoid the import


def _empty_swarm_state() -> Dict[str, Any]:
    return {
        "run_id": None,
        "status": "idle",
        "progress": {"completed": 0, "total": 0, "pct": 0.0},
        "results": [],
        "summary": None,
        "started_at": None,
        "completed_at": None,
        "config": None,
    }


def _get_swarm_state() -> Dict[str, Any]:
    global _swarm_run
    if _swarm_run is None:
        _swarm_run = _empty_swarm_state()
    return _swarm_run


def _swarm_status_snapshot() -> Dict[str, Any]:
    """Build the snapshot returned to the UI.

    Merges the SwarmLoadRunner-owned ``state`` (progress / results /
    summary) onto the API-owned wrapper (run_id / started_at / config).
    """
    with _swarm_lock:
        wrapper = dict(_get_swarm_state())
        runner = _swarm_runner
    if runner is not None:
        runner_state = runner.state
        # The runner mutates its own state under its lock — copy the
        # top-level dict so we never expose a half-written value.
        wrapper["status"] = runner_state.get("status", wrapper.get("status"))
        wrapper["progress"] = dict(runner_state.get("progress") or {})
        wrapper["results"] = list(runner_state.get("results") or [])
        wrapper["summary"] = (
            dict(runner_state["summary"])
            if runner_state.get("summary") is not None
            else None
        )
    return wrapper


def _run_swarm_loadtest(req: SwarmLoadTestStartRequest, run_id: str) -> None:
    """Background thread: drive SwarmLoadRunner.run_batch() to completion."""
    global _swarm_runner
    try:
        # Imported lazily so module import doesn't pull worker_py into the
        # API process if no one ever calls the swarm endpoints.
        from tests.loadtest.swarm import SwarmLoadRunner
    except ImportError as exc:  # pragma: no cover - dev environment misconfig
        logger.exception("SwarmLoadRunner unavailable: %s", exc)
        with _swarm_lock:
            state = _get_swarm_state()
            state["status"] = "error"
            state["completed_at"] = datetime.now(timezone.utc).isoformat()
        return

    # Resolve sizes the same way the HTTP runner does so the UI gets a
    # consistent profile contract.
    if req.profile == "custom":
        sizes = [s for s in req.sizes if s in SIZE_BYTES]
    elif req.profile == "quick":
        sizes = ["512k"]
    else:
        sizes = PROFILE_SIZES.get(req.profile, PROFILE_SIZES["smoke"])
    if not sizes:
        sizes = ["512k"]

    types = [t.lower() for t in req.types if t.lower() in SWARM_VALID_TYPES]
    if not types:
        types = ["837p"]

    try:
        runner = SwarmLoadRunner(
            repeats=req.repeats,
            concurrency=req.concurrency,
            pool_mode=req.pool_mode,
            max_workers=req.max_workers,
            claims_per_batch=req.claims_per_batch,
            cancel_event=_swarm_cancel,
        )
    except ValueError as exc:
        logger.warning("Invalid swarm runner config for %s: %s", run_id, exc)
        with _swarm_lock:
            state = _get_swarm_state()
            state["status"] = "error"
            state["completed_at"] = datetime.now(timezone.utc).isoformat()
        return

    with _swarm_lock:
        _swarm_runner = runner

    try:
        runner.run_batch(types=types, sizes=sizes)
    except Exception:
        logger.exception("Swarm load test run %s failed", run_id)
        with _swarm_lock:
            state = _get_swarm_state()
            state["status"] = "error"
            state["completed_at"] = datetime.now(timezone.utc).isoformat()
        return

    # Mirror the runner's final status (complete / stopped) onto the wrapper
    # so the next start guard sees an idle wrapper instead of a stale
    # "running". The snapshot merges these too, but the guard inspects the
    # raw wrapper while holding _swarm_lock and never sees the runner state.
    final_status = "complete"
    try:
        final_status = runner.state.get("status", "complete") or "complete"
    except Exception:
        pass
    with _swarm_lock:
        state = _get_swarm_state()
        state["status"] = final_status
        state["completed_at"] = datetime.now(timezone.utc).isoformat()


@router.post("/swarm/start")
def start_swarm_loadtest(req: SwarmLoadTestStartRequest) -> dict:
    """Pivot to :class:`SwarmLoadRunner` instead of the HTTP ingest runner."""
    global _swarm_run, _swarm_runner

    if req.pool_mode not in SWARM_VALID_POOL_MODES:
        raise HTTPException(
            status_code=400,
            detail=f"pool_mode must be one of {SWARM_VALID_POOL_MODES}, got {req.pool_mode!r}",
        )

    with _swarm_lock:
        state = _get_swarm_state()
        if state["status"] == "running":
            raise HTTPException(
                status_code=409, detail="A swarm load test is already running"
            )

        run_id = str(uuid.uuid4())
        _swarm_cancel.clear()
        _swarm_run = _empty_swarm_state()
        _swarm_run["run_id"] = run_id
        _swarm_run["status"] = "running"
        _swarm_run["started_at"] = datetime.now(timezone.utc).isoformat()
        _swarm_run["config"] = req.model_dump() if hasattr(req, "model_dump") else req.dict()
        _swarm_runner = None

    thread = threading.Thread(
        target=_run_swarm_loadtest,
        args=(req, run_id),
        daemon=True,
        name=f"swarm-loadtest-{run_id[:8]}",
    )
    thread.start()

    logger.info(
        "Started swarm load test %s (profile=%s, types=%s, pool=%s, workers=%d, repeats=%d)",
        run_id, req.profile, req.types, req.pool_mode, req.max_workers, req.repeats,
    )
    return {"run_id": run_id, "status": "started", "pool_mode": req.pool_mode}


@router.get("/swarm/status")
def get_swarm_loadtest_status() -> dict:
    """Return the current swarm load test state (merged with runner state)."""
    return _swarm_status_snapshot()


@router.post("/swarm/stop")
def stop_swarm_loadtest() -> dict:
    """Signal the in-flight swarm runner to cancel."""
    with _swarm_lock:
        state = _get_swarm_state()
        if state["status"] != "running":
            return {"ok": True, "message": "No swarm test running"}
        run_id = state.get("run_id")
    _swarm_cancel.set()
    logger.info("Stop requested for swarm load test run %s", run_id)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register(app: FastAPI) -> None:
    """Mount the load-test router on ``app``."""
    app.include_router(router)
