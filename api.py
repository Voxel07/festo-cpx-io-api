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

import asyncio
import json
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

app = FastAPI(
    title="CPX-AP Topology Manager",
    description="Generate and compare CPX-AP hardware topology with a React/MUI frontend.",
    version="2.0.0",
)

# Serve SVG product images at /svg/<filename>
app.mount("/svg", StaticFiles(directory="SVG"), name="svg")

# Serve the compiled Vite app (dist/) in production.
# Must be mounted AFTER the API routes so it only catches remaining paths.
_DIST = Path("dist")


class TopologyRequest(BaseModel):
    ip_address: str = Field(..., examples=["192.168.1.11"], description="IP address of the CPX-AP gateway")
    timeout: float = Field(0.0, ge=0, description="Modbus timeout in seconds (0 = keep device setting)")
    save_path: str | None = Field(None, description="Optional file path to save topology.jsonc")


class CompareRequest(BaseModel):
    ip_address: str = Field(..., examples=["192.168.1.11"], description="IP address of the CPX-AP gateway")
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

    Tries ``write_channel`` first; falls back to ``write_channels`` (all other
    channels LOW) so valve terminals (VABX) are also supported.
    """
    import concurrent.futures

    def _do():
        from cpx_io.cpx_system.cpx_ap.cpx_ap import CpxAp
        from generate_system_config import _find_module_by_addr
        cpp = request.channels_per_port
        port_num = int(request.channel.lstrip("X"))
        base_idx = port_num * cpp
        with CpxAp(ip_address=request.ip_address, timeout=request.timeout) as cpx_ap:
            mod = _find_module_by_addr(cpx_ap, request.module_addr)
            write_failed = False
            for i in range(cpp):
                try:
                    mod.write_channel(base_idx + i, request.value)
                except Exception:
                    write_failed = True
                    break
            if write_failed:
                num_out = len(mod.channels.outputs)
                vals = [False] * max(num_out, base_idx + cpp)
                for i in range(cpp):
                    vals[base_idx + i] = request.value
                mod.write_channels(vals)
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
        from cpx_io.cpx_system.cpx_ap.cpx_ap import CpxAp
        from generate_system_config import _find_module_by_addr
        port_num = int(channel.lstrip("X"))
        base_idx = port_num * channels_per_port
        with CpxAp(ip_address=ip_address, timeout=timeout) as cpx_ap:
            mod = _find_module_by_addr(cpx_ap, module_addr)
            values = [bool(mod.read_channel(base_idx + i)) for i in range(channels_per_port)]
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
        "progress": {"completed": 0, "total": len(request.tests), "current_test": None},
        "results": [],
        "checkpoints": [],
        "logs": [],
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    asyncio.create_task(
        _execute_test_run(
            run_id=run_id,
            ip_address=request.ip_address,
            connections_path=request.connections_path,
            topology_path=request.topology_path,
            tests=request.tests,
            source=request.source,
        )
    )

    return JSONResponse({"run_id": run_id, "status": "started"})


def _extract_error_summary(result: dict) -> str:
    """Build a human-readable error string from a result dict.

    Walks nested ``results`` lists so that per-connection/per-module errors
    are surfaced instead of returning a generic "Unknown error".
    """
    # Top-level error key is set by some tests on device-level failure
    if result.get("error"):
        return str(result["error"])

    # Collect errors from sub-results (validate_connections, condition_counter…)
    sub_errors: list[str] = []
    for r in result.get("results", []):
        if r.get("passed") is False and r.get("error"):
            src = r.get("source_addr") or r.get("address")
            tgt = r.get("target_addr")
            loc = f"#{src}→#{tgt}" if tgt else (f"#{src}" if src else "")
            msg = r.get("error", "")
            sub_errors.append(f"{loc}: {msg}" if loc else msg)

    if sub_errors:
        return " | ".join(sub_errors[:5])  # cap to first 5 so log stays readable

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
            passed = bool(result.get("passed", False))

            if passed:
                cp["status"] = "passed"
                _log("info", f"✓ {test_id} PASSED")
                pb_log.checkpoint(run_id, test_id, "passed")
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

    if test_id == "condition-counter":
        with concurrent.futures.ThreadPoolExecutor() as pool:
            return await loop.run_in_executor(
                pool,
                lambda: _run_with_cpx(ip_address, connections_path, run_cc, log),
            )

    if test_id == "valve-condition-counter":
        with concurrent.futures.ThreadPoolExecutor() as pool:
            return await loop.run_in_executor(
                pool,
                lambda: _run_with_cpx(ip_address, connections_path, run_vcc, log),
            )

    if test_id == "remanent-params":
        with concurrent.futures.ThreadPoolExecutor() as pool:
            return await loop.run_in_executor(
                pool,
                lambda: _run_with_cpx(ip_address, connections_path, run_rem, log),
            )

    return {"test_id": test_id, "passed": None,
            "error": f"Test '{test_id}' not implemented"}


def _run_with_cpx(ip_address: str, connections_path: str, test_fn, log) -> dict:
    """Open a CpxAp connection, run *test_fn(cpx_ap, connections_path, log=log)*.

    Returns an aggregated result dict with a top-level ``passed`` bool.
    """
    from cpx_io.cpx_system.cpx_ap.cpx_ap import CpxAp

    log("info", f"Connecting to {ip_address} …")
    try:
        with CpxAp(ip_address=ip_address, timeout=0) as cpx_ap:
            log("info", f"Connected — {len(cpx_ap.modules)} module(s) on bus")
            raw = test_fn(cpx_ap, connections_path, log=log)
    except Exception as exc:
        err = f"{type(exc).__name__}: {exc}"
        log("error", f"Device connection failed: {err}")
        return {"passed": False, "error": err, "cpx_io_error": True}

    if isinstance(raw, list):
        passed = all(r.get("passed", False) for r in raw if r.get("passed") is not None)
        return {"results": raw, "all_passed": passed, "passed": passed}
    return raw


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
    import requests as _req
    from pocketbase_logger import PB_URL
    try:
        r = _req.get(f"{PB_URL}/api/health", timeout=3)
        return JSONResponse({"status": "ok", "url": PB_URL, "http_status": r.status_code})
    except Exception as exc:
        return JSONResponse(
            {"status": "unreachable", "url": PB_URL, "error": str(exc)},
            status_code=503,
        )


# Mount the Vite-built static assets (JS bundles, CSS, etc.) LAST so that all
# API routes take precedence.  Only activated when dist/ exists.
if _DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=str(_DIST / "assets")), name="assets")
