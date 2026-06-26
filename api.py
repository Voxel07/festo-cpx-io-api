"""FastAPI backend for the CPX-AP Topology Manager.

Development workflow
--------------------
1. Start the API:      uvicorn api:app --reload   (from festo-cpx-io-api/)
2. Start the dev UI:   cd C:/workspace/repos/fe/basicTesting && npm run dev
   The Vite dev server runs on http://localhost:5173 and proxies all
   /topology, /compare, /svg and /svg-map requests to FastAPI on :8000.

Production workflow
-------------------
1. Build the frontend:  cd C:/workspace/repos/fe/basicTesting && npm run build
   This writes the compiled assets into festo-cpx-io-api/dist/ (via vite.config.ts outDir).
2. Start the API:       uvicorn api:app
   FastAPI serves the built React app at http://localhost:8000.
"""

from __future__ import annotations

# Load .env before any other imports that read os.environ
from dotenv import load_dotenv
load_dotenv()

import asyncio
import json
import os
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from generate_system_config import (
    generate_topology,
    save_topology,
    save_topology_with_valves,
    compare_topology,
    validate_connections,
)

# ── New architecture components ───────────────────────────────────────────────
try:
    from hal import CpxApHardware, SafeSession
    from resolver import TestResolver, TestFilter, create_basic_test_definitions
    from resolver import ExecutionPlan as ResolvedPlan
    from config_models import (
        BenchConfig,
        SafetyClass,
        ModuleCategory,
        ConnectionType,
        TestDefinition,
    )
    from repository import (
        PocketBaseRepository,
        TestRunRecord,
        TestResultRecord,
        ResultRepository,
    )
    _NEW_COMPONENTS = True
except ImportError:
    _NEW_COMPONENTS = False

app = FastAPI(
    title="CPX-AP Topology Manager",
    description="Generate and compare CPX-AP hardware topology with a React/MUI frontend.",
    version="3.0.0",
)

# Serve SVG product images at /svg/<filename>
app.mount("/svg", StaticFiles(directory="SVG"), name="svg")

# Serve the compiled Vite app (dist/) in production.
# Must be mounted AFTER the API routes so it only catches remaining paths.
_DIST = Path("dist")


class TopologyRequest(BaseModel):
    ip_address: str = Field(..., examples=["192.168.0.11"], description="IP address of the CPX-AP gateway")
    timeout: float = Field(0.0, ge=0, description="Modbus timeout in seconds (0 = keep device setting)")
    save_path: str | None = Field(None, description="Optional file path to save topology.jsonc")


class CompareRequest(BaseModel):
    ip_address: str = Field(..., examples=["192.168.0.11"], description="IP address of the CPX-AP gateway")
    timeout: float = Field(0.0, ge=0, description="Modbus timeout in seconds (0 = keep device setting)")
    stored_path: str = Field(..., description="Path to the stored topology.jsonc file to compare against")


@app.get("/", response_class=FileResponse, include_in_schema=False)
async def ui():
    """Serve the built React SPA (production). During development use the Vite dev server."""
    index = _DIST / "index.html"
    if not index.exists():
        return JSONResponse(
            {"detail": "Frontend not built. Run: cd frontend && npm run build"},
            status_code=503,
        )
    return FileResponse(str(index))


@app.get("/svg-map", include_in_schema=False)
async def svg_map():
    """Return the SVG icon file mapping (OrderCode -> filename)."""
    with open("SVG/IconFileMapping.json", encoding="utf-8") as f:
        return JSONResponse(json.load(f))


@app.post("/topology")
async def get_topology(request: TopologyRequest):
    """Generate the topology for the given CPX-AP system.

    Returns the topology JSON and optionally saves it to a file.
    """
    try:
        topology = generate_topology(request.ip_address, request.timeout)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    saved_to: str | None = None
    if request.save_path:
        try:
            save_path = Path(request.save_path)
            save_path.parent.mkdir(parents=True, exist_ok=True)
            save_topology(topology, str(save_path))
            saved_to = str(save_path.resolve())
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"Could not save file: {exc}") from exc

    return JSONResponse({"topology": topology, "saved_to": saved_to})


@app.get("/topology")
async def get_topology_query(
    ip_address: str = Query(..., description="IP address of the CPX-AP gateway"),
    timeout: float = Query(0.0, ge=0, description="Modbus timeout in seconds"),
    save_path: str | None = Query(None, description="Optional path to save topology.jsonc"),
):
    """Generate topology via GET request (useful for quick testing in a browser)."""
    return await get_topology(TopologyRequest(ip_address=ip_address, timeout=timeout, save_path=save_path))


@app.post("/compare")
async def compare(request: CompareRequest):
    """Compare a stored topology file against the live CPX-AP system.

    Returns stored and live topologies plus a structured diff:
    * ``changes``  - field-level differences for modules present in both
    * ``added``    - modules present in live but absent in the stored file
    * ``removed``  - modules present in the stored file but absent in live
    * ``has_diff`` - True when any difference was found
    """
    stored_path = Path(request.stored_path)
    if not stored_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Stored topology file not found: {stored_path.resolve()}",
        )
    try:
        result = compare_topology(str(stored_path), request.ip_address, request.timeout)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return JSONResponse(result)


class ConnectionsPayload(BaseModel):
    topology_name: str | None = Field(None, description="Name of the topology these connections belong to")
    connections: list[dict] = Field(..., description="List of I/O connection objects")
    save_path: str = Field(..., description="File path to save the connections JSON")
    mounted_valves: dict | None = Field(None, description="Mounted valve indices per module address")


@app.post("/connections")
async def save_connections(payload: ConnectionsPayload):
    """Persist I/O connections drawn in the topology editor to a JSON file."""
    data = {
        "version": "1.0",
        "topology_name": payload.topology_name,
        "connections": payload.connections,
    }
    if payload.mounted_valves:
        data["mounted_valves"] = payload.mounted_valves
    path = Path(payload.save_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not save: {exc}") from exc
    return JSONResponse({"saved_to": str(path.resolve())})


@app.get("/connections")
async def load_connections(file_path: str = Query(..., description="Path to the connections JSON file")):
    """Load a previously saved connections file."""
    path = Path(file_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {path.resolve()}")
    with open(path, encoding="utf-8") as f:
        return JSONResponse(json.load(f))


class SaveTopologyPayload(BaseModel):
    topology: dict = Field(..., description="Full topology JSON including MountedValves fields")
    save_path: str = Field("topology.jsonc", description="File path to save")


@app.post("/topology/save-with-valves")
async def save_topology_valves(payload: SaveTopologyPayload):
    """Save an in-memory topology (with valve-mount edits) to a JSON file.

    Unlike ``POST /topology``, this does NOT re-read from the device — it
    persists the topology exactly as provided so that valve-config edits
    (``MountedValves``) are kept.
    """
    try:
        path = Path(payload.save_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        save_topology_with_valves(payload.topology, str(path))
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not save: {exc}") from exc
    return JSONResponse({"saved_to": str(path.resolve())})


class ValidateConnectionsRequest(BaseModel):
    ip_address: str = Field(..., description="IP address of the CPX-AP gateway")
    connections_path: str = Field("connections.jsonc", description="Path to connections JSON file")
    timeout: float = Field(0.0, ge=0, description="Modbus timeout in seconds")
    pulse_duration_s: float = Field(0.3, ge=0.1, le=5.0,
                                    description="How long to hold output HIGH for each connection")


@app.post("/validate-connections")
async def validate_connections_endpoint(request: ValidateConnectionsRequest):
    """Validate I/O connections by pulsing each source output and reading the target input.

    Returns a detailed per-connection pass/fail report.
    """
    try:
        result = validate_connections(
            ip_address=request.ip_address,
            connections_path=request.connections_path,
            timeout=request.timeout,
            pulse_duration_s=request.pulse_duration_s,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return JSONResponse(result)


# ─── IO Direct Control ─────────────────────────────────────────────────────


class SetOutputRequest(BaseModel):
    ip_address: str = Field(..., description="IP of the CPX-AP gateway")
    module_addr: int = Field(..., description="Module bus address (0-based position)")
    channel: str = Field(..., description="Port label, e.g. 'X0'")
    value: bool = Field(..., description="True = HIGH, False = LOW")
    timeout: float = Field(0.0, ge=0)
    channels_per_port: int = Field(1, ge=1, le=4, description="2 for M12-5P (2 channels per connector), 1 for M8 or single-channel")


@app.post("/io/set-output")
async def io_set_output(request: SetOutputRequest):
    """Set a single output channel on a module HIGH or LOW.

    Connects, sets the output, and disconnects.  Does NOT use SafeSession
    because outputs should persist for manual testing from the frontend.
    """
    import concurrent.futures

    def _do():
        from hal import CpxApHardware
        cpp = request.channels_per_port
        port_num = int(request.channel.lstrip("X"))
        base_idx = port_num * cpp
        hw = CpxApHardware()
        try:
            hw.connect(request.ip_address, request.timeout)
            for i in range(cpp):
                hw.write_output(request.module_addr, base_idx + i, request.value)
        finally:
            hw.disconnect()
        return {
            "ok": True,
            "module_addr": request.module_addr,
            "channel": request.channel,
            "value": request.value,
            "channels_written": list(range(base_idx, base_idx + cpp)),
        }

    loop = asyncio.get_running_loop()
    try:
        with concurrent.futures.ThreadPoolExecutor() as pool:
            result = await loop.run_in_executor(pool, _do)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return JSONResponse(result)


@app.get("/io/read-input")
async def io_read_input(
    ip_address: str = Query(..., description="IP of the CPX-AP gateway"),
    module_addr: int = Query(..., description="Module bus address"),
    channel: str = Query(..., description="Port label, e.g. 'X0'"),
    timeout: float = Query(0.0, ge=0),
    channels_per_port: int = Query(1, ge=1, le=4, description="2 for M12-5P, 1 for M8 / single-channel"),
):
    """Read one or more input channels from a module (all channels of an M12 connector).

    Returns ``{"values": [bool, ...], "value": bool, "module_addr": int, "channel": str}``
    where ``value`` is ``True`` only when all channels read HIGH.
    """
    import concurrent.futures

    def _do():
        from hal import CpxApHardware
        port_num = int(channel.lstrip("X"))
        base_idx = port_num * channels_per_port
        hw = CpxApHardware()
        try:
            hw.connect(ip_address, timeout)
            values = [hw.read_input(module_addr, base_idx + i) for i in range(channels_per_port)]
        finally:
            hw.disconnect()
        return {"values": values, "value": all(values), "module_addr": module_addr, "channel": channel}

    loop = asyncio.get_running_loop()
    try:
        with concurrent.futures.ThreadPoolExecutor() as pool:
            result = await loop.run_in_executor(pool, _do)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return JSONResponse(result)


# ─── Test Run Lock + SSE streaming ─────────────────────────────────────────

_test_run_lock = asyncio.Lock()
_current_test_run: dict | None = None  # {run_id, status, progress, results, ...}

# In-memory history — newest first, max 200 entries.
# Populated when PocketBase is unavailable so HistoryTab always has data.
_run_history: list[dict] = []

# Per-run SSE queues: run_id → asyncio.Queue of log-entry dicts.
# None is sent as a sentinel when the run ends.
_log_queues: dict[str, asyncio.Queue] = {}


# ─── SSE helpers ───────────────────────────────────────────────────────────

async def _sse_generator(run_id: str, request: Request):
    """Yield SSE frames for *run_id*, starting with any buffered log entries."""
    # Replay existing logs so a late-connecting client catches up
    if _current_test_run and _current_test_run.get("run_id") == run_id:
        for entry in (_current_test_run.get("logs") or []):
            yield f"data: {json.dumps(entry)}\n\n"

    queue: asyncio.Queue = asyncio.Queue()
    _log_queues[run_id] = queue
    try:
        while True:
            if await request.is_disconnected():
                break
            try:
                entry = await asyncio.wait_for(queue.get(), timeout=25.0)
                if entry is None:  # sentinel — run finished
                    yield f"data: {json.dumps({'type': 'done'})}\n\n"
                    break
                yield f"data: {json.dumps(entry)}\n\n"
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"
    finally:
        _log_queues.pop(run_id, None)


@app.get("/test-run/status")
async def test_run_status():
    """Return the current test-run state (id, status, progress, results)."""
    return JSONResponse(_current_test_run or {"status": "idle"})


@app.get("/test-run/{run_id}/stream")
async def stream_run_logs(run_id: str, request: Request):
    """Server-Sent Events stream of log entries for *run_id*.

    Connect with ``EventSource('/test-run/<run_id>/stream')`` from the
    frontend.  The stream replays any already-emitted log entries so a
    late connection still sees the full history.  A ``{"type":"done"}``
    message is sent when the run ends, after which the stream closes.
    """
    return StreamingResponse(
        _sse_generator(run_id, request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


class StartTestRunRequest(BaseModel):
    ip_address: str = Field(..., description="IP address of the CPX-AP gateway")
    connections_path: str = Field("connections.jsonc", description="Path to connections file")
    topology_path: str = Field("topology.jsonc", description="Path to topology file")
    tests: list[str] = Field(..., description="List of test IDs to run")
    source: str = Field("web", description="Initiator: 'web' or 'ci'")


@app.post("/test-run/start")
async def start_test_run(request: StartTestRunRequest):
    """Start a test run.  Returns 409 if another run is already in progress."""
    global _current_test_run

    if _test_run_lock.locked():
        raise HTTPException(
            status_code=409,
            detail=f"Another test run is in progress (source: {(_current_test_run or {}).get('source','unknown')}). Try again later.",
        )

    await _test_run_lock.acquire()

    run_id = f"run-{int(time.time())}"
    _current_test_run = {
        "run_id": run_id,
        "status": "running",
        "source": request.source,
        "ip_address": request.ip_address,
        "tests": request.tests,
        "progress": {"completed": 0, "total": len(request.tests), "current_test": None, "current_module": None},
        "results": [],
        "checkpoints": [],
        "logs": [],
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    # Use SafeSession-based executor in background (output reset guaranteed).
    # Pass the event loop explicitly — the function runs in a thread pool
    # where asyncio.get_running_loop() would fail.
    import concurrent.futures
    loop = asyncio.get_running_loop()
    loop.run_in_executor(
        None,
        _execute_test_run_safe,
        run_id,
        request.ip_address,
        request.connections_path,
        request.topology_path,
        request.tests,
        request.source,
        loop,  # <-- event loop passed explicitly
    )

    return JSONResponse({"run_id": run_id, "status": "started"})


def _extract_error_summary(result: dict) -> str:
    """Build a human-readable error string from a result dict.

    Handles nested ``results`` lists and plain list results.
    """
    if not isinstance(result, dict):
        return "unexpected result format"

    if result.get("error"):
        return str(result["error"])

    sub_results = result.get("results", [])
    if not isinstance(sub_results, list):
        sub_results = []

    sub_errors: list[str] = []
    for r in sub_results:
        if not isinstance(r, dict):
            continue
        if r.get("passed") is False and r.get("error"):
            src = r.get("source_addr") or r.get("address")
            tgt = r.get("target_addr")
            loc = f"#{src}→#{tgt}" if tgt else (f"#{src}" if src else "")
            msg = r.get("error", "")
            sub_errors.append(f"{loc}: {msg}" if loc else msg)

    if sub_errors:
        return " | ".join(sub_errors[:5])
    return "no details available"


async def _execute_test_run(
    run_id: str,
    ip_address: str,
    connections_path: str,
    topology_path: str,
    tests: list[str],
    source: str,
) -> None:
    """Background coroutine: runs selected tests, streams logs, updates history."""
    global _current_test_run

    from pocketbase_logger import pb_log

    loop = asyncio.get_running_loop()

    def _log(level: str, msg: str) -> None:
        """Thread-safe log that appends to run-state AND pushes to any SSE clients."""
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        entry = {"level": level, "message": msg, "timestamp": ts}
        if _current_test_run is not None:
            _current_test_run["logs"].append(entry)
        # Thread-safe push to SSE queue (may be called from executor thread)
        if run_id in _log_queues:
            loop.call_soon_threadsafe(_log_queues[run_id].put_nowait, entry)

    _log("info", f"Test run {run_id} started  source={source}  ip={ip_address}")
    pb_log.test_run_started(run_id, source, ip_address, tests)

    try:
        for idx, test_id in enumerate(tests):
            _log("info", f"━━━ [{idx + 1}/{len(tests)}] {test_id} ━━━")
            _current_test_run["progress"]["current_test"] = test_id
            _current_test_run["checkpoints"].append({
                "test": test_id,
                "status": "running",
                "timestamp": time.time(),
            })
            pb_log.checkpoint(run_id, test_id, "running")

            try:
                result = await _run_single_test(
                    test_id, ip_address, connections_path, topology_path, _log,
                )
            except Exception as exc:
                import traceback
                tb = traceback.format_exc()
                _log("error", f"Test '{test_id}' raised unhandled exception: {exc}")
                _log("error", tb)
                result = {"test_id": test_id, "passed": False, "error": str(exc),
                          "traceback": tb}

            _current_test_run["progress"]["completed"] = idx + 1
            _current_test_run["results"].append(result)
            cp = _current_test_run["checkpoints"][-1]
            passed = result.get("passed")

            if passed is True:
                cp["status"] = "passed"
                _log("info", f"✓ {test_id} PASSED")
                pb_log.checkpoint(run_id, test_id, "passed")
            elif passed is None:
                # All sub-results were skipped (no compatible modules)
                cp["status"] = "skipped"
                cp["note"] = "No compatible modules found"
                _log("warning", f"⚠ {test_id} SKIPPED — no compatible modules")
                pb_log.checkpoint(run_id, test_id, "skipped")
            else:
                err = _extract_error_summary(result)
                cp["status"] = "failed"
                cp["error"] = err[:500]
                _log("error", f"✗ {test_id} FAILED — {err}")
                pb_log.checkpoint(run_id, test_id, "failed", err[:500])
                pb_log.error(run_id, f"Test '{test_id}' failed: {err}")

        _current_test_run["status"] = "completed"
        _log("info", f"All {len(tests)} test(s) completed")
        pb_log.test_run_completed(run_id, _current_test_run["results"])

    except Exception as exc:
        _current_test_run["status"] = "error"
        _current_test_run["error"] = str(exc)
        _log("error", f"Test run crashed: {exc}")
        pb_log.error(run_id, f"Test run crashed: {exc}")
    finally:
        # ── Persist to in-memory history ─────────────────────────────
        if _current_test_run:
            history_entry = {
                "id": run_id,
                "run_id": run_id,
                "source": source,
                "ip_address": ip_address,
                "status": _current_test_run.get("status", "error"),
                "tests": json.dumps(tests),
                "results": json.dumps(_current_test_run.get("results", []), default=str),
                "checkpoints": _current_test_run.get("checkpoints", []),
                "logs": _current_test_run.get("logs", []),
                "started_at": _current_test_run.get("started_at", ""),
                "completed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            _run_history.insert(0, history_entry)
            if len(_run_history) > 200:
                _run_history.pop()

        # ── Signal SSE stream end then release lock ───────────────────
        if run_id in _log_queues:
            loop.call_soon_threadsafe(_log_queues[run_id].put_nowait, None)
        _test_run_lock.release()


async def _run_single_test(
    test_id: str,
    ip_address: str,
    connections_path: str,
    topology_path: str,
    log,
) -> dict:
    """Dispatch a single test by ID.  Runs blocking code via run_in_executor."""
    import concurrent.futures

    from tests.validate_connections import run as run_validate
    from tests.compare_topology import run as run_compare
    from tests.condition_counter import run as run_cc
    from tests.valve_condition_counter import run as run_vcc
    from tests.remanent_params import run as run_rem

    loop = asyncio.get_running_loop()

    if test_id == "validate-connections":
        with concurrent.futures.ThreadPoolExecutor() as pool:
            raw = await loop.run_in_executor(
                pool,
                lambda: run_validate(ip_address, connections_path, log=log),
            )
        raw["passed"] = bool(raw.get("all_passed", False))
        return raw

    if test_id == "compare-topology":
        with concurrent.futures.ThreadPoolExecutor() as pool:
            raw = await loop.run_in_executor(
                pool,
                lambda: run_compare(topology_path, ip_address, log=log),
            )
        return raw  # run_compare already sets "passed"

    hal_tests = {
        "condition-counter": run_cc,
        "valve-condition-counter": run_vcc,
        "remanent-params": run_rem,
    }
    if test_id in hal_tests:
        fn = hal_tests[test_id]
        with concurrent.futures.ThreadPoolExecutor() as pool:
            return await loop.run_in_executor(
                pool,
                lambda: _run_with_hw(ip_address, connections_path, fn, log),
            )

    return {"test_id": test_id, "passed": None,
            "error": f"Test '{test_id}' not implemented"}


def _run_with_hw(ip_address: str, connections_path: str, test_fn, log) -> dict:
    """Open a SafeSession, run *test_fn(hw, connections_path, log=log)*.

    Uses :class:`SafeSession` for guaranteed output reset.  Accepts either
    a HardwareInterface-accepting test function or a legacy CpxAp-accepting one.
    """
    from hal import CpxApHardware, SafeSession

    log("info", f"Connecting to {ip_address} …")
    try:
        hw = CpxApHardware()
        with SafeSession(hw, ip_address) as iface:
            topology = iface.read_topology()
            log("info", f"Connected — {len(topology)} module(s) on bus")
            # Try HAL signature first: test_fn(hw, connections_path, log=log)
            import inspect
            sig = inspect.signature(test_fn)
            params = list(sig.parameters.keys())
            if len(params) >= 1 and params[0] in ("hw", "iface", "cpx_ap"):
                raw = test_fn(iface, connections_path, log=log)
            else:
                raw = test_fn(iface, connections_path, log=log)
    except Exception as exc:
        err = f"{type(exc).__name__}: {exc}"
        log("error", f"Device connection failed: {err}")
        return {"passed": False, "error": err, "cpx_io_error": True}

    if isinstance(raw, list):
        valid = [r for r in raw if r.get("passed") is not None]
        if not valid:
            passed = None  # all skipped — no actual test ran
        else:
            passed = all(r.get("passed", False) for r in valid)
        return {"results": raw, "all_passed": passed, "passed": passed}
    return raw


# Legacy alias
_run_with_cpx = _run_with_hw


# ─── History ──────────────────────────────────────────────────────────────

@app.get("/test-run/history")
async def test_run_history(limit: int = 50):
    """Return recent test runs.

    Tries PocketBase first; falls back to in-memory history when PocketBase
    is unavailable so the History tab always shows data.
    """
    from pocketbase_logger import pb_log
    runs = pb_log.get_run_history(limit)
    if not runs:
        runs = _run_history[:limit]
    return JSONResponse(runs)


@app.get("/test-run/{run_id}")
async def test_run_detail(run_id: str):
    """Return full detail for a specific test run.

    Checks in-memory history first, then PocketBase.
    """
    # In-memory lookup
    mem = next((r for r in _run_history if r["run_id"] == run_id), None)
    if mem:
        return JSONResponse(mem)
    # Currently active run
    if _current_test_run and _current_test_run.get("run_id") == run_id:
        return JSONResponse(_current_test_run)
    # PocketBase
    from pocketbase_logger import pb_log
    detail = pb_log.get_run_detail(run_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")
    return JSONResponse(detail)


@app.get("/pocketbase/health")
async def pocketbase_health():
    """Check whether the PocketBase logging service is reachable."""
    import os as _os
    import requests as _req
    pb_url = _os.environ.get("PB_URL") or _os.environ.get("POCKETBASE_URL", "http://localhost:8090")
    try:
        r = _req.get(f"{pb_url}/api/health", timeout=(1.5, 3))
        return JSONResponse({"status": "ok", "url": pb_url, "http_status": r.status_code})
    except Exception as exc:
        return JSONResponse(
            {"status": "unreachable", "url": pb_url, "error": str(exc)},
            status_code=503,
        )


# ─── Dry-Run / Resolve Endpoints ──────────────────────────────────────────────

class DryRunRequest(BaseModel):
    """Request body for dry-run / resolve operations."""
    ip_address: str = Field(..., description="IP address of the CPX-AP gateway")
    bench_id: str = Field("default", description="Test bench identifier")
    test_filter: str | None = Field(None, description="Optional test_id filter")
    safety_class_filter: str | None = Field(None, description="Optional safety class filter (safe/caution/destructive)")
    export_path: str | None = Field(None, description="Path to export resolved plan JSON")


@app.post("/test-run/dry-run")
async def dry_run(request: DryRunRequest):
    """Resolve which tests would run for a given bench without touching hardware.

    Returns the execution plan: test_id, module, channel, wiring assignments.
    """
    if not _NEW_COMPONENTS:
        raise HTTPException(status_code=501, detail="Resolver not available — check imports")

    try:
        # Build a minimal bench config from live topology
        hw = CpxApHardware()
        with SafeSession(hw, request.ip_address) as iface:
            topology = iface.read_topology()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    # Convert live modules to config models
    from config_models import (
        ModuleInstance, ModuleTypeDefinition, TestBenchMetadata, ChannelDefinition,
    )

    instances: list[ModuleInstance] = []
    type_defs: dict[str, ModuleTypeDefinition] = {}

    for i, m in enumerate(topology):
        inst_id = f"mod-{m.address:03d}"
        type_ref = f"type-{m.module_code}"

        instances.append(ModuleInstance(
            instance_id=inst_id,
            display_name=m.name,
            module_code=m.module_code,
            product_key=m.product_key,
            address=m.address,
            category=_infer_category(m),
            module_type_ref=type_ref,
        ))

        if type_ref not in type_defs:
            caps = _infer_capabilities(m)
            type_defs[type_ref] = ModuleTypeDefinition(
                module_code=m.module_code,
                product_family=m.series,
                capabilities=caps,
                num_inputs=m.num_inputs,
                num_outputs=m.num_outputs,
                num_configurable=m.num_inouts,
                valve_count=0,
            )

    bench_config = BenchConfig(
        schema_version="1.0",
        test_bench=TestBenchMetadata(
            id=request.bench_id,
            name=f"Bench {request.bench_id}",
            ip_address=request.ip_address,
        ),
        module_types=type_defs,
        module_instances=instances,
        test_definitions=create_basic_test_definitions(),
    )

    # Build filters
    filters = TestFilter(test_id=request.test_filter)
    if request.safety_class_filter:
        try:
            filters.safety_class = SafetyClass(request.safety_class_filter)
        except ValueError:
            pass

    resolver = TestResolver()
    plan = resolver.dry_run(bench_config, filters)

    if request.export_path:
        resolver.export_plan(plan, request.export_path)

    return JSONResponse(plan.to_dict())


@app.get("/test-run/env")
async def ci_environment():
    """Return the CI environment variables recognized by this service.

    Useful for debugging GitLab CI configuration.
    """
    return JSONResponse({
        "variables": {
            "CONFIG_REPO_PATH": os.environ.get("CONFIG_REPO_PATH", "(not set)"),
            "CONFIG_REF": os.environ.get("CONFIG_REF", "(not set)"),
            "TESTBENCH_ID": os.environ.get("TESTBENCH_ID", "(not set)"),
            "POCKETBASE_URL": os.environ.get("POCKETBASE_URL", os.environ.get("PB_URL", "(not set)")),
            "DRY_RUN": os.environ.get("DRY_RUN", "false"),
            "TEST_FILTER": os.environ.get("TEST_FILTER", ""),
            "SAFETY_CLASS_FILTER": os.environ.get("SAFETY_CLASS_FILTER", "safe"),
            "GITLAB_PIPELINE_ID": os.environ.get("CI_PIPELINE_ID", "(not set)"),
            "GITLAB_JOB_ID": os.environ.get("CI_JOB_ID", "(not set)"),
        }
    })


# ── Helpers ───────────────────────────────────────────────────────────────────

def _infer_category(m) -> ModuleCategory:
    if m.is_valve:
        return ModuleCategory.VALVE
    if m.is_inout:
        return ModuleCategory.INOUT
    if m.is_output:
        return ModuleCategory.OUTPUT
    if m.is_input:
        return ModuleCategory.INPUT
    return ModuleCategory.BUS


def _infer_capabilities(m) -> list[str]:
    caps: list[str] = []
    if m.num_inputs > 0:
        caps.append("digital_input")
    if m.num_outputs > 0:
        caps.append("digital_output")
    if m.num_inouts > 0:
        caps.append("configurable_io")
    if m.is_valve:
        caps.extend(["valve_output", "condition_counter", "remanent_params"])
    # Most CPX-AP modules support CC and remanent params
    name_upper = m.name.upper()
    if any(x in name_upper for x in ("DI", "DO", "DIO", "HDO", "AI", "IOL", "VABX")):
        caps.append("condition_counter")
        caps.append("remanent_params")
    if "EP" in name_upper or "EC" in name_upper or "PN" in name_upper or "PB" in name_upper:
        caps.append("system_diagnosis")
    return list(set(caps))


# ── Update test run execution to use SafeSession ─────────────────────────────

def _execute_test_run_safe(
    run_id: str,
    ip_address: str,
    connections_path: str,
    topology_path: str,
    tests: list[str],
    source: str,
    loop=None,  # asyncio event loop from caller (runs in thread)
) -> None:
    """Run tests with SafeSession — guaranteed output reset on scope exit.

    Runs in a thread pool — *loop* must be passed from the async caller
    because ``asyncio.get_running_loop()`` doesn't work in threads.
    """
    global _current_test_run
    from pocketbase_logger import pb_log
    from hal import CpxApHardware, SafeSession
    import traceback

    if loop is None:
        loop = asyncio.get_event_loop()

    # Fire-and-forget PocketBase logger — never blocks test execution.
    # Uses a shared thread pool so unreachable PB doesn't stall tests.
    import concurrent.futures as _cf
    _pb_pool = _cf.ThreadPoolExecutor(max_workers=2, thread_name_prefix="pblog")

    def _pb(call, *args):
        """Submit a PocketBase call to a background thread.  Best-effort only."""
        try:
            f = _pb_pool.submit(call, *args)
            # Log failures after a short delay (non-blocking)
            def _check():
                try:
                    result = f.result(timeout=5)
                    if result is False:
                        _log("warning", f"PocketBase write failed ({call.__name__})")
                except Exception:
                    pass
            _pb_pool.submit(_check)
        except Exception:
            pass

    def _log(level: str, msg: str) -> None:
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        entry = {"level": level, "message": msg, "timestamp": ts}
        if _current_test_run is not None:
            _current_test_run["logs"].append(entry)
        if run_id in _log_queues:
            loop.call_soon_threadsafe(_log_queues[run_id].put_nowait, entry)

    _log("info", f"Test run {run_id} started  source={source}  ip={ip_address}")
    _pb(pb_log.test_run_started, run_id, source, ip_address, tests)

    hw = CpxApHardware()
    try:
        with SafeSession(hw, ip_address) as iface:
            for idx, test_id in enumerate(tests):
                _log("info", f"━━━ [{idx + 1}/{len(tests)}] {test_id} ━━━")
                if _current_test_run is not None:
                    _current_test_run["progress"]["current_test"] = test_id
                    _current_test_run["checkpoints"].append({
                        "test": test_id, "status": "running", "timestamp": time.time(),
                    })
                _pb(pb_log.checkpoint, run_id, test_id, "running")

                try:
                    result = _run_single_test_hw(
                        iface, test_id, ip_address, connections_path, topology_path, _log,
                    )
                except Exception as exc:
                    tb = traceback.format_exc()
                    _log("error", f"Test '{test_id}' raised unhandled exception: {exc}")
                    _log("error", tb)
                    result = {"test_id": test_id, "passed": False, "error": str(exc),
                              "traceback": tb}

                if _current_test_run is not None:
                    _current_test_run["progress"]["completed"] = idx + 1
                    # Replace existing live-results entry if present (e.g. from _init_live_results),
                    # otherwise append
                    results_list = _current_test_run["results"]
                    replaced_existing = False
                    for i, existing in enumerate(results_list):
                        if isinstance(existing, dict) and existing.get("test_id") == test_id:
                            results_list[i] = result
                            replaced_existing = True
                            break
                    if not replaced_existing:
                        results_list.append(result)
                    if _current_test_run["checkpoints"]:
                        cp = _current_test_run["checkpoints"][-1]
                        passed = bool(result.get("passed", False))
                        if passed:
                            cp["status"] = "passed"
                            _log("info", f"✓ {test_id} PASSED")
                            _pb(pb_log.checkpoint, run_id, test_id, "passed")
                        else:
                            err = _extract_error_summary(result)
                            cp["status"] = "failed"
                            cp["error"] = err[:500]
                            _log("error", f"✗ {test_id} FAILED — {err}")
                            _pb(pb_log.checkpoint, run_id, test_id, "failed", err[:500])
                            _pb(pb_log.error, run_id, f"Test '{test_id}' failed: {err}")

            _log("info", f"All {len(tests)} test(s) completed")
    except Exception as exc:
        if _current_test_run is not None:
            _current_test_run["status"] = "error"
            _current_test_run["error"] = str(exc)
        _log("error", f"Test run crashed: {exc}")
        _pb(pb_log.error, run_id, f"Test run crashed: {exc}")
    finally:
        if _current_test_run is not None:
            # Only keep "error" if the run crashed; otherwise → "completed"
            if _current_test_run.get("status") != "error":
                _current_test_run["status"] = "completed"
            history_entry = {
                "id": run_id, "run_id": run_id, "source": source,
                "ip_address": ip_address,
                "status": _current_test_run.get("status", "error"),
                "tests": json.dumps(tests),
                "results": json.dumps(_current_test_run.get("results", []), default=str),
                "checkpoints": _current_test_run.get("checkpoints", []),
                "logs": _current_test_run.get("logs", []),
                "started_at": _current_test_run.get("started_at", ""),
                "completed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            _run_history.insert(0, history_entry)
            if len(_run_history) > 200:
                _run_history.pop()
        if run_id in _log_queues:
            loop.call_soon_threadsafe(_log_queues[run_id].put_nowait, None)
        _test_run_lock.release()
        _pb(pb_log.test_run_completed, run_id, _current_test_run["results"] if _current_test_run else [])


def _run_single_test_hw(
    hw,
    test_id: str,
    ip_address: str,
    connections_path: str,
    topology_path: str,
    log,
) -> dict:
    """Dispatch a single test using a pre-connected HardwareInterface."""

    def _init_live_results(modules: list):
        """Pre-populate all modules as pending so the UI shows them immediately."""
        if _current_test_run is None:
            return
        results = _current_test_run.get("results")
        if not isinstance(results, list):
            return
        entry = None
        for r in results:
            if isinstance(r, dict) and r.get("test_id") == test_id:
                entry = r
                break
        if entry is None:
            entry = {"test_id": test_id, "passed": None, "results": [], "duration_ms": 0}
            results.append(entry)
        sub = entry.get("results")
        if isinstance(sub, list):
            for m in modules:
                sub.append({
                    "module": str(m.address),
                    "module_name": m.name,
                    "passed": None,
                    "status": "pending",
                })

    def _update_current_module(addr):
        """Thread-safe update of current module in progress."""
        if _current_test_run is not None:
            _current_test_run["progress"]["current_module"] = str(addr)

    def _push_live_module_result(mod_result: dict):
        """Push a per-module result live while the test is still running.
        Replaces a pending entry (matched by 'module' key) if one exists,
        otherwise appends."""
        if _current_test_run is None:
            return
        results = _current_test_run.get("results")
        if not isinstance(results, list):
            return
        # Find or create the current test's entry
        entry = None
        for r in results:
            if isinstance(r, dict) and r.get("test_id") == test_id:
                entry = r
                break
        if entry is None:
            entry = {"test_id": test_id, "passed": None, "results": [], "duration_ms": 0}
            results.append(entry)
        sub = entry.get("results")
        if isinstance(sub, list):
            # Replace pending entry for this module if it exists, else append
            mod_addr = mod_result.get("address")
            replaced = False
            if mod_addr is not None:
                mod_addr = str(mod_addr)
                for i, existing in enumerate(sub):
                    if isinstance(existing, dict) and existing.get("module") == mod_addr:
                        sub[i] = mod_result
                        replaced = True
                        break
            if not replaced:
                sub.append(mod_result)
            # Update running totals
            all_ok = all(
                r.get("passed", False)
                for r in sub
                if isinstance(r, dict) and r.get("passed") is not None
            )
            entry["passed"] = all_ok
            entry["all_passed"] = all_ok
            total_ms = sum(
                r.get("duration_ms", 0)
                for r in sub
                if isinstance(r, dict)
            )
            entry["duration_ms"] = round(total_ms, 1)

    from tests.validate_connections import run as run_validate
    from tests.compare_topology import run as run_compare
    from tests.condition_counter import run as run_cc
    from tests.valve_condition_counter import run as run_vcc
    from tests.remanent_params import run as run_rem
    from tests.output_toggle import run as run_output_toggle

    test_map: dict[str, callable] = {
        "validate-connections": lambda: run_validate(hw, connections_path, log=log),
        "compare-topology": lambda: run_compare(topology_path, hw, log=log),
        "condition-counter": lambda: run_cc(hw, connections_path, log=log),
        "valve-condition-counter": lambda: run_vcc(hw, log=log),
        "remanent-params": lambda: run_rem(hw, connections_path, log=log),
        "output-toggle": lambda: (
            _init_live_results([m for m in hw.read_topology() if m.num_outputs > 0 or m.num_inouts > 0]),
            run_output_toggle(hw, connections_path, log=log,
                on_module=lambda addr: _update_current_module(addr),
                on_result=lambda r: _push_live_module_result(r))
        )[1],
    }

    if test_id in test_map:
        raw = test_map[test_id]()
        # Normalize: wrap plain lists so all results are uniform dicts
        if isinstance(raw, list):
            # Ensure every sub-result has an 'address' for the frontend (#? fix)
            for r in raw:
                if isinstance(r, dict) and "address" not in r:
                    if "source_addr" in r:
                        r["address"] = r["source_addr"]
                    elif "module_addr" in r:
                        r["address"] = r["module_addr"]
            passed = all(
                r.get("passed", False)
                for r in raw
                if isinstance(r, dict) and r.get("passed") is not None
            )
            # Sum sub-result durations for the test-level total
            total_ms = sum(
                r.get("duration_ms", 0)
                for r in raw
                if isinstance(r, dict)
            )
            raw = {
                "results": raw,
                "all_passed": passed,
                "passed": passed,
                "test_id": test_id,
                "duration_ms": round(total_ms, 1) if total_ms > 0 else None,
            }
        elif isinstance(raw, dict):
            if "all_passed" in raw:
                raw["passed"] = bool(raw.get("all_passed", False))
            raw.setdefault("test_id", test_id)
            # Sum sub-result durations if present, and patch missing 'address'
            sub = raw.get("results", [])
            if isinstance(sub, list):
                for r in sub:
                    if isinstance(r, dict) and "address" not in r:
                        if "source_addr" in r:
                            r["address"] = r["source_addr"]
                        elif "module_addr" in r:
                            r["address"] = r["module_addr"]
                if "duration_ms" not in raw:
                    total_ms = sum(
                        r.get("duration_ms", 0)
                        for r in sub
                        if isinstance(r, dict)
                    )
                    if total_ms > 0:
                        raw["duration_ms"] = round(total_ms, 1)
        return raw

    return {"test_id": test_id, "passed": None, "error": f"Test '{test_id}' not implemented"}


# Mount the Vite-built static assets (JS bundles, CSS, etc.) LAST so that all
# API routes take precedence.  Only activated when dist/ exists.
if _DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=str(_DIST / "assets")), name="assets")
