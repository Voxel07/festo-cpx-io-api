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
# app.mount("/svg", StaticFiles(directory="SVG"), name="svg")

# Serve the compiled Vite app (dist/) in production.
# Must be mounted AFTER the API routes so it only catches remaining paths.
_DIST = Path("dist")


class ConfigGenerateRequest(BaseModel):
    ip_address: str = Field(..., examples=["192.168.0.11"], description="IP address of the CPX-AP gateway")
    timeout: float = Field(0.0, ge=0, description="Modbus timeout in seconds")
    save_path: str = Field("bench_config.json", description="File path to save the generated BenchConfig")


class ConfigCompareRequest(BaseModel):
    ip_address: str = Field(..., examples=["192.168.0.11"], description="IP address of the CPX-AP gateway")
    timeout: float = Field(0.0, ge=0, description="Modbus timeout in seconds")
    config_path: str = Field("bench_config.json", description="Path to the stored bench_config.json to compare against")


class ConfigSavePayload(BaseModel):
    config: BenchConfig = Field(..., description="Full BenchConfig structure")
    save_path: str = Field("bench_config.json", description="File path to save the BenchConfig JSON")


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


@app.post("/config/generate")
async def generate_config(request: ConfigGenerateRequest):
    """Query live hardware to discover modules and generate a modern BenchConfig structure."""
    try:
        from hal import CpxApHardware, SafeSession
        hw = CpxApHardware()
        with SafeSession(hw, request.ip_address, timeout=request.timeout) as iface:
            modules = iface.read_topology()
        config = BenchConfig.from_hardware(modules, request.ip_address)

        # Preserve existing wiring and mounted_valves from the current file (if any)
        save_path = Path(request.save_path)
        if save_path.exists():
            try:
                existing = BenchConfig.model_validate_json(save_path.read_text(encoding="utf-8"))
                # Merge wiring
                if existing.wiring:
                    config.wiring = existing.wiring
                # Merge mounted_valves per module instance (match by address)
                existing_valves: dict[int, list[int]] = {}
                for inst in (existing.module_instances or []):
                    mv = inst.mounted_valves
                    if mv is not None and len(mv) >= 0:
                        existing_valves[inst.address] = list(mv)
                for inst in (config.module_instances or []):
                    if inst.address in existing_valves:
                        inst.mounted_valves = existing_valves[inst.address]
            except Exception:
                pass  # existing file is corrupt or missing — proceed with fresh config
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    try:
        save_path = Path(request.save_path)
        save_path.parent.mkdir(parents=True, exist_ok=True)
        save_path.write_text(config.model_dump_json(indent=2, exclude={"module_types", "test_definitions"}), encoding="utf-8")
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not save file: {exc}") from exc

    return JSONResponse({"config": config.model_dump(), "saved_to": str(save_path.resolve())})


@app.get("/config")
async def load_config(file_path: str = Query("bench_config.json", description="Path to the BenchConfig JSON file")):
    """Load a previously saved unified BenchConfig file."""
    path = Path(file_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {path.resolve()}")
    try:
        config = BenchConfig.model_validate_json(path.read_text(encoding="utf-8"))
        return JSONResponse(config.model_dump())
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid configuration format: {exc}")


@app.post("/config")
async def save_config(payload: ConfigSavePayload):
    """Persist a complete BenchConfig structure (topology + connections) to a JSON file."""
    path = Path(payload.save_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload.config.model_dump_json(indent=2, exclude={"module_types", "test_definitions"}), encoding="utf-8")
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not save config: {exc}") from exc
    return JSONResponse({"saved_to": str(path.resolve())})


@app.post("/config/compare")
async def compare_config(request: ConfigCompareRequest):
    """Compare a stored BenchConfig module instances against the live CPX-AP system."""
    stored_path = Path(request.config_path)
    if not stored_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Stored configuration file not found: {stored_path.resolve()}",
        )
    try:
        from hal import CpxApHardware, SafeSession
        from tests.compare_topology import run as run_compare
        hw = CpxApHardware()
        with SafeSession(hw, request.ip_address, timeout=request.timeout) as iface:
            result = run_compare(str(stored_path), iface)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return JSONResponse(result)


# ── Manual output auto-reset safety ──────────────────────────────────────────
# When an output is set HIGH via /io/set-output, a timer automatically resets
# it to LOW after _IO_AUTO_RESET_S seconds.  This prevents forgotten HIGH
# outputs during manual wire-test sessions.
import threading as _thr

_IO_AUTO_RESET_S = int(os.environ.get("IO_AUTO_RESET_S", "60"))
_io_timers: dict[str, _thr.Timer] = {}
_io_timers_lock = _thr.Lock()


def _auto_reset_output(
    ip: str, module_addr: int, channel: str, cpp: int, timeout: float,
) -> None:
    """Background callback that resets an output to LOW."""
    from hal import CpxApHardware, CrossProcessLock

    port_num = int(channel.lstrip("X"))
    hw = CpxApHardware()
    lock = CrossProcessLock(ip)
    try:
        lock.acquire(timeout=5.0)
    except Exception:
        return  # best-effort reset, skip if locked to avoid hangs
    try:
        hw.connect(ip, timeout)
        # On mixed DI+DO modules the SVG port IDs are sequential starting at X0
        # with input connectors first; write_output expects a 0-based index
        # within outputs only, so subtract the total number of input channels.
        mod = hw._get_module(module_addr)
        num_in = len([c for c in mod.channels.inputs if c.direction == "in"])
        out_base = port_num * cpp - num_in
        if out_base < 0:
            return  # port maps to an input channel — nothing to reset
        for i in range(cpp):
            hw.write_output(module_addr, out_base + i, False)
    except Exception:
        pass  # best-effort
    finally:
        try:
            hw.disconnect()
        except Exception:
            pass
        lock.release()


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

    When setting HIGH, a safety timer automatically resets the output to LOW
    after IO_AUTO_RESET_S seconds (default 60).  Setting LOW cancels any
    pending timer.
    """
    import concurrent.futures

    timer_key = f"{request.ip_address}:{request.module_addr}:{request.channel}"

    def _do():
        from hal import CpxApHardware, CrossProcessLock
        cpp = request.channels_per_port
        port_num = int(request.channel.lstrip("X"))
        hw = CpxApHardware()
        lock = CrossProcessLock(request.ip_address)
        lock.acquire(timeout=5.0)
        try:
            hw.connect(request.ip_address, request.timeout)
            # On mixed DI+DO modules the SVG port IDs are sequential starting at X0
            # with input connectors first; write_output expects a 0-based index
            # within outputs only, so subtract the total number of input channels.
            mod = hw._get_module(request.module_addr)
            num_in = len([c for c in mod.channels.inputs if c.direction == "in"])
            out_base = port_num * cpp - num_in
            if out_base < 0:
                raise ValueError(
                    f"Port X{port_num} maps to an input channel on module "
                    f"#{request.module_addr} (num_inputs={num_in})"
                )
            for i in range(cpp):
                hw.write_output(request.module_addr, out_base + i, request.value)
        finally:
            try:
                hw.disconnect()
            except Exception:
                pass
            lock.release()
        return {
            "ok": True,
            "module_addr": request.module_addr,
            "channel": request.channel,
            "value": request.value,
            "channels_written": list(range(out_base, out_base + cpp)),
            "auto_reset_s": _IO_AUTO_RESET_S if request.value else None,
        }

    loop = asyncio.get_running_loop()
    try:
        with concurrent.futures.ThreadPoolExecutor() as pool:
            result = await loop.run_in_executor(pool, _do)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    # ── Manage auto-reset timer ──
    with _io_timers_lock:
        # Cancel any existing timer for this output
        old = _io_timers.pop(timer_key, None)
        if old is not None:
            old.cancel()

        if request.value and _IO_AUTO_RESET_S > 0:
            timer = _thr.Timer(
                _IO_AUTO_RESET_S,
                _auto_reset_output,
                args=(request.ip_address, request.module_addr, request.channel,
                      request.channels_per_port, request.timeout),
            )
            timer.daemon = True
            timer.start()
            _io_timers[timer_key] = timer

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


class SetAllOutputsRequest(BaseModel):
    ip_address: str = Field(..., description="IP of the CPX-AP gateway")
    module_addr: int = Field(..., description="Module bus address (0-based position)")
    value: bool = Field(..., description="True = all HIGH, False = all LOW")
    timeout: float = Field(0.0, ge=0)
    channels: list[int] | None = Field(None, description="Specific hardware channel indices to set; omit to set all writable channels")
    valve_indices: list[int] | None = Field(None, description="0-based valve slot indices (VABX only); expanded to hardware channels via valve_channels mapping")
    module_name: str = Field("", description="Module display name, used with valve_indices to resolve channels-per-valve")


@app.post("/io/set-all-outputs")
async def io_set_all_outputs(request: SetAllOutputsRequest):
    """Set all writable output/inout channels of a module HIGH or LOW.

    When *channels* is provided, only those channel indices are set.
    When *valve_indices* is provided (VABX bodies), they are expanded to
    hardware channels using the per-product-family channel mapping
    (2 channels/valve for V4A/V4B/V4C, 1 for VEAM, etc.).
    Otherwise all ``outputs`` + ``inouts`` channels are discovered and set.

    Each channel set to HIGH spawns the usual auto-reset safety timer.
    Returns the list of channel indices that were written.
    """
    import concurrent.futures

    # ── Expand valve_indices → hardware channels ──
    if request.valve_indices is not None and request.module_name:
        from valve_channels import expand_valve_indices
        expanded = expand_valve_indices(request.valve_indices, request.module_name)
        if request.channels:
            request.channels = sorted(set(list(request.channels) + expanded))
        else:
            request.channels = expanded

    def _do():
        from hal import CpxApHardware, CrossProcessLock
        hw = CpxApHardware()
        lock = CrossProcessLock(request.ip_address)
        lock.acquire(timeout=5.0)
        try:
            hw.connect(request.ip_address, request.timeout)
            mod = hw._get_module(request.module_addr)

            if request.channels is not None:
                indices = list(request.channels)
            else:
                out_indices = [c.index for c in mod.channels.outputs if c.direction == "out"]
                inout_indices = [c.index for c in mod.channels.inouts]
                indices = sorted(set(out_indices + inout_indices))

            if not indices:
                raise ValueError(f"No writable channels found on module at #{request.module_addr}")

            for idx in indices:
                hw.write_output(request.module_addr, idx, request.value)

        finally:
            try:
                hw.disconnect()
            except Exception:
                pass
            lock.release()
        return {
            "ok": True,
            "module_addr": request.module_addr,
            "value": request.value,
            "channels_written": indices,
            "auto_reset_s": _IO_AUTO_RESET_S if request.value else None,
        }

    loop = asyncio.get_running_loop()
    try:
        with concurrent.futures.ThreadPoolExecutor() as pool:
            result = await loop.run_in_executor(pool, _do)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    # ── Manage auto-reset timers for each channel ──
    if request.value and _IO_AUTO_RESET_S > 0:
        with _io_timers_lock:
            for idx in result["channels_written"]:
                timer_key = f"{request.ip_address}:{request.module_addr}:X{idx}"
                old = _io_timers.pop(timer_key, None)
                if old is not None:
                    old.cancel()
                timer = _thr.Timer(
                    _IO_AUTO_RESET_S,
                    _auto_reset_output,
                    args=(request.ip_address, request.module_addr, f"X{idx}", 1, request.timeout),
                )
                timer.daemon = True
                timer.start()
                _io_timers[timer_key] = timer

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
    config_path: str = Field("bench_config.json", description="Path to unified bench configuration file")
    tests: list[str] = Field(..., description="List of test IDs to run")
    source: str = Field("web", description="Initiator: 'web' or 'ci'")
    test_parameters: dict[str, dict] = Field(
        default_factory=dict,
        description=(
            "Per-test parameter overrides keyed by test_id. "
            "Example: {\"remanent-params\": {\"power_supply_comport\": \"COM3\"}}"
        ),
    )


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
        request.config_path,
        request.tests,
        request.source,
        loop,  # <-- event loop passed explicitly
        request.test_parameters,
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


def _merge_sub_results(existing: list, incoming: list) -> None:
    """Merge *incoming* sub-results into *existing* by address/module key.

    Replaces matching entries (same ``address`` or ``module``) instead of
    appending, so live-pushed results don't duplicate when the final result
    arrives.
    """
    if not isinstance(existing, list) or not isinstance(incoming, list):
        return

    def _key(r: dict) -> str | None:
        """Stable lookup key for a sub-result dict."""
        addr = r.get("address") or r.get("module") or r.get("module_addr")
        return str(addr) if addr is not None else None

    # Build index of existing entries by key
    index: dict[str, int] = {}
    for i, r in enumerate(existing):
        if isinstance(r, dict):
            k = _key(r)
            if k is not None:
                index[k] = i

    for r in incoming:
        if not isinstance(r, dict):
            continue
        k = _key(r)
        if k is not None and k in index:
            existing[index[k]] = r   # replace in-place
        else:
            existing.append(r)





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


@app.delete("/test-run/{run_id}")
async def delete_test_run(run_id: str):
    """Delete a specific test run from history."""
    global _run_history
    _run_history = [r for r in _run_history if r.get("run_id") != run_id]

    from pocketbase_logger import pb_log
    pb_log.delete_run(run_id)
    return JSONResponse({"status": "deleted", "run_id": run_id})


@app.delete("/test-run")
async def clear_test_run_history():
    """Clear all test run history."""
    global _run_history
    _run_history.clear()

    from pocketbase_logger import pb_log
    pb_log.clear_history()
    return JSONResponse({"status": "cleared"})


class WriteParameterRequest(BaseModel):
    ip_address: str = Field(..., description="IP of the CPX-AP gateway")
    value: str = Field(..., description="Value to write (numeric string or enum string name)")
    timeout: float = Field(0.0, ge=0)
    instance: int | None = Field(None, description="Optional parameter instance index")


@app.get("/io/module/{address}/parameters")
async def get_module_parameters(
    address: int,
    ip_address: str = Query(..., description="IP of the CPX-AP gateway"),
    timeout: float = Query(0.0),
):
    """Retrieve metadata for all parameters available on the module at the given address."""
    import concurrent.futures
    import asyncio

    def _do():
        from hal import CpxApHardware, CrossProcessLock
        hw = CpxApHardware()
        lock = CrossProcessLock(ip_address)
        lock.acquire(timeout=5.0)
        try:
            hw.connect(ip_address, timeout)
            mod = hw._get_module(address)
            params = []
            for p in mod.module_dicts.parameters.values():
                first_index = p.parameter_instances.get("FirstIndex", 0) if p.parameter_instances else 0
                num_instances = p.parameter_instances.get("NumberOfInstances", 1) if p.parameter_instances else 1
                params.append({
                    "parameter_id": int(p.parameter_id),
                    "name": str(p.name),
                    "is_writable": bool(p.is_writable),
                    "data_type": str(p.data_type),
                    "enums": list(p.enums.enum_values.keys()) if p.enums else None,
                    "unit": str(p.unit) if p.unit else "",
                    "first_index": int(first_index),
                    "num_instances": int(num_instances),
                })
            return params
        finally:
            try:
                hw.disconnect()
            except Exception:
                pass
            lock.release()

    loop = asyncio.get_running_loop()
    try:
        with concurrent.futures.ThreadPoolExecutor() as pool:
            result = await loop.run_in_executor(pool, _do)
            return JSONResponse(result)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/io/module/{address}/parameter/{param_id}")
async def read_module_parameter(
    address: int,
    param_id: int,
    ip_address: str = Query(..., description="IP of the CPX-AP gateway"),
    timeout: float = Query(0.0),
    instance: int | None = Query(None, description="Optional parameter instance index"),
):
    """Read the current value of a module parameter."""
    import concurrent.futures
    import asyncio

    def _do():
        from hal import CpxApHardware, CrossProcessLock
        hw = CpxApHardware()
        lock = CrossProcessLock(ip_address)
        lock.acquire(timeout=5.0)
        try:
            hw.connect(ip_address, timeout)
            mod = hw._get_module(address)
            val = mod.read_module_parameter_enum_str(param_id, instances=instance)
            if isinstance(val, list):
                return {"value": [str(x) if x is not None else "" for x in val]}
            return {"value": str(val) if val is not None else ""}
        finally:
            try:
                hw.disconnect()
            except Exception:
                pass
            lock.release()

    loop = asyncio.get_running_loop()
    try:
        with concurrent.futures.ThreadPoolExecutor() as pool:
            result = await loop.run_in_executor(pool, _do)
            return JSONResponse(result)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/io/module/{address}/parameter/{param_id}")
async def write_module_parameter(
    address: int,
    param_id: int,
    request: WriteParameterRequest,
):
    """Write a new value to a module parameter and read it back."""
    import concurrent.futures
    import asyncio

    def _do():
        from hal import CpxApHardware, CrossProcessLock
        hw = CpxApHardware()
        lock = CrossProcessLock(request.ip_address)
        lock.acquire(timeout=5.0)
        try:
            hw.connect(request.ip_address, request.timeout)
            mod = hw._get_module(address)
            val = request.value
            try:
                if "." in val:
                    val = float(val)
                else:
                    val = int(val)
            except ValueError:
                pass  # keep as string (for enums etc)

            mod.write_module_parameter(param_id, val, instances=request.instance)
            time.sleep(0.05)
            new_val = mod.read_module_parameter_enum_str(param_id, instances=request.instance)
            return {"value": str(new_val) if new_val is not None else ""}
        finally:
            try:
                hw.disconnect()
            except Exception:
                pass
            lock.release()

    loop = asyncio.get_running_loop()
    try:
        with concurrent.futures.ThreadPoolExecutor() as pool:
            result = await loop.run_in_executor(pool, _do)
            return JSONResponse(result)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/io/diagnoses")
async def get_system_diagnoses(
    ip_address: str = Query(..., description="IP of the CPX-AP gateway"),
    timeout: float = Query(0.0),
):
    """Retrieve all active diagnoses raised in the system across all modules."""
    import concurrent.futures
    import asyncio

    def _do():
        from hal import CpxApHardware, CrossProcessLock
        hw = CpxApHardware()
        lock = CrossProcessLock(ip_address)
        lock.acquire(timeout=5.0)
        try:
            hw.connect(ip_address, timeout)
            active_diags = []
            for mod in hw._modules:
                try:
                    diag = mod.read_diagnosis_information()
                    if diag is not None:
                        active_diags.append({
                            "address": int(mod.position),
                            "module_name": getattr(mod.apdd_information, "order_text", "") or mod.name or f"Module {mod.position}",
                            "diagnosis_id": str(diag.diagnosis_id),
                            "name": str(diag.name),
                            "description": str(diag.description),
                            "guideline": str(diag.guideline),
                        })
                except Exception:
                    pass  # skip if module doesn't support diagnosis or fails
            return active_diags
        finally:
            try:
                hw.disconnect()
            except Exception:
                pass
            lock.release()

    loop = asyncio.get_running_loop()
    try:
        with concurrent.futures.ThreadPoolExecutor() as pool:
            result = await loop.run_in_executor(pool, _do)
            return JSONResponse(result)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


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
        with SafeSession(hw, request.ip_address, timeout=10.0) as iface:
            topology = iface.read_topology()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    bench_config = BenchConfig.from_hardware(topology, request.ip_address, request.bench_id)

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
            "POWER_SUPPLY_COMPORT": os.environ.get("POWER_SUPPLY_COMPORT", "(not set)"),
            "POWER_SUPPLY_CHANNELS": os.environ.get("POWER_SUPPLY_CHANNELS", "(not set)"),
            "POWER_SUPPLY_VOLTAGE": os.environ.get("POWER_SUPPLY_VOLTAGE", "(not set)"),
        }
    })


# ── Update test run execution to use SafeSession ─────────────────────────────

def _execute_test_run_safe(
    run_id: str,
    ip_address: str,
    config_path: str,
    tests: list[str],
    source: str,
    loop=None,  # asyncio event loop from caller (runs in thread)
    test_parameters: dict[str, dict] | None = None,
) -> None:
    """Run tests with SafeSession — guaranteed output reset on scope exit.

    Runs in a thread pool — *loop* must be passed from the async caller
    because ``asyncio.get_running_loop()`` doesn't work in threads.

    *test_parameters* is an optional ``{test_id: {param: value, ...}}`` dict
    that overrides resolved instance parameters.  The web UI uses this to
    forward the power-supply comport; CI uses env-vars (see below).
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

    try:
        # Load BenchConfig
        from config_models import BenchConfig
        bench_config = None
        try:
            if os.path.exists(config_path):
                import warnings
                with warnings.catch_warnings(record=True) as caught_warnings:
                    warnings.simplefilter("always")
                    bench_config = BenchConfig.model_validate_json(Path(config_path).read_text(encoding="utf-8"))
                for w in caught_warnings:
                    _log("warning", f"Config validation warning: {w.message}")
        except Exception as exc:
            _log("warning", f"Could not load BenchConfig: {exc}")

        # Build execution plan instances
        plan_instances = []
        if bench_config:
            try:
                from resolver import TestResolver, TestFilter
                resolver = TestResolver()
                for t_id in tests:
                    p = resolver.resolve(bench_config, TestFilter(test_id=t_id))
                    plan_instances.extend(p.instances)
            except Exception as exc:
                _log("error", f"Resolver failed to plan execution: {exc}")

        # ── Merge parameter overrides ────────────────────────────────────────────────────
        # Priority (high → low):
        #   1. test_parameters dict (from web UI or explicit API call)
        #   2. POWER_SUPPLY_COMPORT env-var (CI)
        #   3. Resolved-instance defaults (from bench_config / TEST_DEFINITION)
        ci_ps_comport = os.environ.get("POWER_SUPPLY_COMPORT", "").strip()
        ci_ps_channels_raw = os.environ.get("POWER_SUPPLY_CHANNELS", "").strip()
        ci_ps_channels = (
            [int(c) for c in ci_ps_channels_raw.split(",") if c.strip().isdigit()]
            if ci_ps_channels_raw else [1, 2, 4]
        )
        ci_ps_voltage = float(os.environ.get("POWER_SUPPLY_VOLTAGE", "24.0") or "24.0")

        _POWER_CYCLE_TEST_IDS = {"remanent-params", "condition-counter", "factory-reset"}

        # Get defaults from bench_config.power_supply
        cfg_ps_comport = None
        cfg_ps_channels = None
        if bench_config and getattr(bench_config, "power_supply", None) and bench_config.power_supply:
            ps = bench_config.power_supply
            cfg_ps_comport = ps.comport or ps.ip_address
            ch = []
            if ps.pl_channel is not None:
                ch.append(ps.pl_channel)
            if ps.ps_channel is not None:
                ch.append(ps.ps_channel)
            if ch:
                cfg_ps_channels = ch

        effective_params: dict[str, dict] = {}
        if test_parameters:
            effective_params.update(test_parameters)

        for inst in plan_instances:
            overrides = {}
            if inst.test_id in _POWER_CYCLE_TEST_IDS:
                if cfg_ps_comport:
                    overrides["power_supply_comport"] = cfg_ps_comport
                if cfg_ps_channels:
                    overrides["power_supply_channels"] = cfg_ps_channels

            # Explicit overrides take precedence
            web_overrides = effective_params.get(inst.test_id, {})
            overrides.update(web_overrides)

            # CI env-var fallback: inject comport only if not already set
            if inst.test_id in _POWER_CYCLE_TEST_IDS and ci_ps_comport:
                overrides.setdefault("power_supply_comport", ci_ps_comport)
                overrides.setdefault("power_supply_channels", ci_ps_channels)
                overrides.setdefault("power_supply_voltage", ci_ps_voltage)
            if overrides:
                inst.parameters = {**inst.parameters, **overrides}

        # Notify PocketBase
        commit_sha = os.environ.get("CI_COMMIT_SHA", "")
        config_commit = os.environ.get("CONFIG_COMMIT", "")
        _pb(
            pb_log.test_run_started,
            run_id,
            source,
            ip_address,
            tests,
            os.environ.get("TESTBENCH_ID", "default"),
            commit_sha,
            config_commit,
            os.environ.get("CI_PIPELINE_ID", ""),
            os.environ.get("CI_JOB_ID", ""),
            "", # resolved_plan_id
            "1.0" # schema_version
        )

        if not plan_instances:
            _log("warning", f"No modules matched the selected test(s): {', '.join(tests)}")
            _log("warning", f"Check configuration ({config_path}) and wiring for compatibility.")
            # Mark all selected tests as skipped
            for t_id in tests:
                if _current_test_run is not None:
                    _current_test_run["checkpoints"].append({
                        "test": t_id, "status": "skipped", "timestamp": time.time(),
                        "error": "No compatible module found",
                    })
                    _current_test_run["results"].append({
                        "test_id": t_id, "passed": None,
                        "error": "No compatible module found — skipped",
                    })
            return

        # Update progress total to reflect resolved instance count (not raw test-ID count)
        if _current_test_run is not None:
            _current_test_run["progress"]["total"] = len(plan_instances)

        # Pre-populate results with pending entries for every planned
        # (test, module) pair so the frontend can show module progress
        # before execution begins.
        if _current_test_run is not None and plan_instances:
            # Build address→name lookup from bench config
            addr_to_name: dict[int, str] = {}
            if bench_config:
                for mi in bench_config.module_instances:
                    addr_to_name[mi.address] = mi.display_name
            # Group instances by test_id
            seen_tests: dict[str, list] = {}
            for inst in plan_instances:
                seen_tests.setdefault(inst.test_id, []).append(inst)
            for t_id, instances in seen_tests.items():
                sub = []
                for inst in instances:
                    name = addr_to_name.get(inst.module_address, "")
                    sub.append({
                        "module": str(inst.module_address),
                        "module_name": name,
                        "address": inst.module_address,
                        "passed": None,
                        "status": "pending",
                    })
                _current_test_run["results"].append({
                    "test_id": t_id,
                    "passed": None,
                    "results": sub,
                    "duration_ms": 0,
                })

        _log("info", "Acquiring hardware lock...")
        hw = CpxApHardware()
        with SafeSession(hw, ip_address, timeout=10.0) as iface:
            for idx, inst in enumerate(plan_instances):
                test_id = inst.test_id
                _log("info", f"━━━ [{idx + 1}/{len(plan_instances)}] {test_id} (Module #{inst.module_address}) ━━━")
                if _current_test_run is not None:
                    _current_test_run["progress"]["current_test"] = test_id
                    _current_test_run["checkpoints"].append({
                        "test": test_id, "status": "running", "timestamp": time.time(),
                    })
                _pb(pb_log.checkpoint, run_id, test_id, "running")

                try:
                    # Per-test timeout (seconds) — prevents a stuck test
                    # from holding hardware indefinitely.
                    _TEST_TIMEOUT_S = int(os.environ.get("TEST_TIMEOUT_S", "300"))
                    import threading
                    _test_result_box: list[dict] = []
                    _test_exc_box: list[Exception] = []

                    def _run_test_target():
                        try:
                            r = _run_single_test_hw(
                                iface, inst, bench_config, config_path, _log,
                            )
                            _test_result_box.append(r)
                        except Exception as e:
                            _test_exc_box.append(e)

                    t = threading.Thread(target=_run_test_target, daemon=True)
                    t.start()
                    t.join(timeout=_TEST_TIMEOUT_S)

                    if t.is_alive():
                        _log("error", f"Test '{test_id}' timed out after {_TEST_TIMEOUT_S}s")
                        result = {"test_id": test_id, "passed": False,
                                  "error": f"Test timed out after {_TEST_TIMEOUT_S}s"}
                    elif _test_exc_box:
                        raise _test_exc_box[0]
                    elif _test_result_box:
                        result = _test_result_box[0]
                    else:
                        result = {"test_id": test_id, "passed": False,
                                  "error": "Test returned no result"}
                except Exception as exc:
                    tb = traceback.format_exc()
                    _log("error", f"Test '{test_id}' raised unhandled exception: {exc}")
                    _log("error", tb)
                    result = {"test_id": test_id, "passed": False, "error": str(exc),
                              "traceback": tb}

                if _current_test_run is not None:
                    _current_test_run["progress"]["completed"] = idx + 1
                    # Merge into existing live-results entry (e.g. from _push_live_module_result),
                    # deduplicating by module address instead of blindly extending.
                    results_list = _current_test_run["results"]
                    replaced_existing = False
                    for i, existing in enumerate(results_list):
                        if isinstance(existing, dict) and existing.get("test_id") == test_id:
                            if isinstance(existing, dict) and "results" in existing and "results" in result:
                                # Merge sub-results by address to avoid duplicates from live pushes
                                _merge_sub_results(existing["results"], result["results"])
                                existing["passed"] = existing.get("passed", True) and result.get("passed", True)
                                existing["all_passed"] = existing["passed"]
                                if "duration_ms" in existing and "duration_ms" in result and result["duration_ms"] is not None:
                                    existing["duration_ms"] = round((existing["duration_ms"] or 0) + result["duration_ms"], 1)
                            else:
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

            _log("info", f"All {len(plan_instances)} test(s) completed")
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
    resolved_instance,
    bench_config,
    config_path: str,
    log,
) -> dict:
    """Dispatch a single test using a pre-connected HardwareInterface."""
    test_id = resolved_instance.test_id
    addr = resolved_instance.module_address

    def _init_live_results(modules: list):
        """Update pre-populated pending entries with real topology module names.

        The global pre-population already created pending entries per
        (test_id, module_address).  This call enriches them with the
        actual module name from the hardware topology instead of
        appending duplicates.
        """
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
                m_addr = str(m.address)
                # Replace matching entry by address if it already exists
                replaced = False
                for i, existing in enumerate(sub):
                    if isinstance(existing, dict):
                        existing_addr = str(existing.get("address") or existing.get("module") or "")
                        if existing_addr == m_addr:
                            sub[i] = {
                                "module": m_addr,
                                "module_name": m.name,
                                "address": m.address,
                                "passed": existing.get("passed"),
                                "status": existing.get("status", "pending"),
                            }
                            replaced = True
                            break
                if not replaced:
                    sub.append({
                        "module": m_addr,
                        "module_name": m.name,
                        "address": m.address,
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

    raw = None

    if test_id == "connection-validation":
        wire = next((w for w in bench_config.wiring if w.id == resolved_instance.wiring_id), None)
        if wire:
            src_mod = next((m for m in bench_config.module_instances if m.instance_id == wire.source_instance_id), None)
            tgt_mod = next((m for m in bench_config.module_instances if m.instance_id == wire.target_instance_id), None)
            if src_mod and tgt_mod:
                conn = {
                    "source_module_addr": src_mod.address,
                    "source_channel": wire.source_channel,
                    "target_module_addr": tgt_mod.address,
                    "target_channel": wire.target_channel,
                }
                raw = run_validate(
                    hw_or_ip=hw,
                    log=log,
                    connections=[conn],
                    pulse_duration_s=resolved_instance.parameters.get("pulse_duration_s", 0.3)
                )

    elif test_id == "condition-counter":
        from tests.condition_counter import run_with_power_cycle as run_cc_pc
        conns = []
        if bench_config:
            for wire in bench_config.wiring:
                if wire.target_instance_id == resolved_instance.module_instance_id:
                    src_mod = next((m for m in bench_config.module_instances if m.instance_id == wire.source_instance_id), None)
                    tgt_mod = next((m for m in bench_config.module_instances if m.instance_id == wire.target_instance_id), None)
                    if src_mod and tgt_mod:
                        conns.append({
                            "source_module_addr": src_mod.address,
                            "source_channel": wire.source_channel,
                            "target_module_addr": tgt_mod.address,
                            "target_channel": wire.target_channel,
                        })
        if conns:
            comport = resolved_instance.parameters.get("power_supply_comport")
            if comport or resolved_instance.parameters.get("power_cycle"):
                raw = run_cc_pc(
                    hw=hw,
                    ip_address=bench_config.test_bench.ip_address if bench_config else "",
                    power_supply_comport=comport or "",
                    power_supply_channels=resolved_instance.parameters.get("power_supply_channels", [1, 2, 4]),
                    log=log,
                    cc_param_id=resolved_instance.parameters.get("cc_param_id", 20094),
                    cc_readback_param_id=resolved_instance.parameters.get("cc_readback_param_id", 20095),
                    toggle_cycles=resolved_instance.parameters.get("toggle_cycles", 3),
                    connections=conns,
                    power_supply_voltage=resolved_instance.parameters.get("power_supply_voltage", 24.0),
                    reconnect_wait=resolved_instance.parameters.get("reconnect_wait", 8.0),
                )
            else:
                raw = run_cc(
                    hw=hw,
                    log=log,
                    cc_param_id=resolved_instance.parameters.get("cc_param_id", 20094),
                    cc_readback_param_id=resolved_instance.parameters.get("cc_readback_param_id", 20095),
                    toggle_cycles=resolved_instance.parameters.get("toggle_cycles", 3),
                    connections=conns,
                )
        else:
            raw = {"test_id": test_id, "passed": None,
                   "error": f"No wiring targets module #{resolved_instance.module_address} — skipped"}

    elif test_id == "remanent-params":
        from tests.remanent_params import run_with_power_cycle as run_rem_pc
        comport = resolved_instance.parameters.get("power_supply_comport")
        if comport and bench_config:
            raw = run_rem_pc(
                hw=hw,
                ip_address=bench_config.test_bench.ip_address,
                power_supply_comport=comport,
                power_supply_channels=resolved_instance.parameters.get("power_supply_channels", [1, 2, 4]),
                log=log,
                param_id_1=resolved_instance.parameters.get("param_id_1", 20118),
                param_id_2=resolved_instance.parameters.get("param_id_2", 20119),
                module_address=resolved_instance.module_address,
                power_supply_voltage=resolved_instance.parameters.get("power_supply_voltage", 24.0),
                reconnect_wait=resolved_instance.parameters.get("reconnect_wait", 8.0),
            )
        else:
            raw = run_rem(
                hw=hw,
                log=log,
                param_id_1=resolved_instance.parameters.get("param_id_1", 20118),
                param_id_2=resolved_instance.parameters.get("param_id_2", 20119),
                module_address=resolved_instance.module_address,
            )

    elif test_id == "factory-reset":
        from tests.factory_reset import run as run_fr
        raw = run_fr(
            hw=hw,
            ip_address=bench_config.test_bench.ip_address if bench_config else "",
            log=log,
            module_address=resolved_instance.module_address,
            app_tag_param_id=resolved_instance.parameters.get("app_tag_param_id", 20118),
            cc_setpoint_out_param_id=resolved_instance.parameters.get("cc_setpoint_out_param_id", 20094),
            cc_actual_out_param_id=resolved_instance.parameters.get("cc_actual_out_param_id", 20095),
            cc_setpoint_in_param_id=resolved_instance.parameters.get("cc_setpoint_in_param_id", 20294),
            cc_actual_in_param_id=resolved_instance.parameters.get("cc_actual_in_param_id", 20295),
            device_reset_param_id=resolved_instance.parameters.get("device_reset_param_id", 20001),
            reset_reconnect_wait=resolved_instance.parameters.get("reset_reconnect_wait", 10.0),
            power_supply_comport=resolved_instance.parameters.get("power_supply_comport"),
            power_supply_channels=resolved_instance.parameters.get("power_supply_channels", [1, 2, 4]),
            power_supply_voltage=resolved_instance.parameters.get("power_supply_voltage", 24.0),
            reconnect_wait=resolved_instance.parameters.get("reconnect_wait", 8.0),
        )

    elif test_id == "open-load-diag":
        from tests.open_load_diag import run as run_old
        raw = run_old(
            hw=hw,
            log=log,
            module_address=resolved_instance.module_address,
            force_mask_param_id=resolved_instance.parameters.get("force_mask_param_id", 20081),
            force_value_param_id=resolved_instance.parameters.get("force_value_param_id", 20082),
            valve_defect_diag_enable_param_id=resolved_instance.parameters.get("valve_defect_diag_enable_param_id", 20021),
            openload_diag_enable_param_id=resolved_instance.parameters.get("openload_diag_enable_param_id", 20027),
            diag_settle_time=resolved_instance.parameters.get("diag_settle_time", 1.5),
            diag_clear_time=resolved_instance.parameters.get("diag_clear_time", 1.0),
        )

    elif test_id == "valve-condition-counter":
        raw = run_vcc(
            hw=hw,
            log=log,
            toggle_cycles=resolved_instance.parameters.get("toggle_cycles", 5),
            cc_param_id=resolved_instance.parameters.get("cc_param_id", 20094),
            cc_readback_param_id=resolved_instance.parameters.get("cc_readback_param_id", 20095),
            module_address=resolved_instance.module_address
        )

    elif test_id in ("valve-toggle", "output-toggle"):
        _init_live_results([m for m in hw.read_topology() if m.address == resolved_instance.module_address])
        _update_current_module(resolved_instance.module_address)
        raw = run_output_toggle(
            hw=hw,
            log=log,
            pulse_duration_s=resolved_instance.parameters.get("pulse_duration_s", 0.2),
            module_address=resolved_instance.module_address,
            on_result=lambda r: _push_live_module_result(r)
        )

    elif test_id == "compare-topology":
        raw = run_compare(
            topology_path=config_path,
            hw=hw,
            log=log
        )

    elif test_id == "system-diagnosis":
        try:
            diag = hw.read_diagnosis(addr)
            raw = {"passed": diag is not None, "diagnosis": str(diag), "results": [{"module": str(addr), "passed": diag is not None}]}
        except Exception as exc:
            raw = {"passed": False, "error": str(exc), "results": [{"module": str(addr), "passed": False, "error": str(exc)}]}

    if raw is None:
        raw = {"test_id": test_id, "passed": None, "error": f"Test '{test_id}' not implemented or skipped"}

    if isinstance(raw, list):
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


# Mount the Vite-built static assets (JS bundles, CSS, etc.) LAST so that all
# API routes take precedence.  Only activated when dist/ exists.
if _DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=str(_DIST / "assets")), name="assets")
