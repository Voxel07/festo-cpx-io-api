"""FastAPI backend for the CPX-AP Topology Manager.

Development workflow
--------------------
1. Start the API:      uvicorn api:app --reload   (from festo-cpx-io/)
2. Start the dev UI:   cd C:/workspace/repos/fe/basicTesting && npm run dev
   The Vite dev server runs on http://localhost:5173 and proxies all
   /topology, /compare, /svg and /svg-map requests to FastAPI on :8000.

Production workflow
-------------------
1. Build the frontend:  cd C:/workspace/repos/fe/basicTesting && npm run build
   This writes the compiled assets into festo-cpx-io/dist/ (via vite.config.ts outDir).
2. Start the API:       uvicorn api:app
   FastAPI serves the built React app at http://localhost:8000.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
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


@app.post("/connections")
async def save_connections(payload: ConnectionsPayload):
    """Persist I/O connections drawn in the topology editor to a JSON file."""
    data = {
        "version": "1.0",
        "topology_name": payload.topology_name,
        "connections": payload.connections,
    }
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


# ─── Test Run Lock ──────────────────────────────────────────────────────────

import asyncio

_test_run_lock = asyncio.Lock()
_current_test_run: dict | None = None  # {run_id, status, progress, results, ...}


@app.get("/test-run/status")
async def test_run_status():
    """Return the current test-run state (id, status, progress, results)."""
    return JSONResponse(_current_test_run or {"status": "idle"})


class StartTestRunRequest(BaseModel):
    ip_address: str = Field(..., description="IP address of the CPX-AP gateway")
    connections_path: str = Field("connections.jsonc", description="Path to connections file")
    topology_path: str = Field("topology.jsonc", description="Path to topology file")
    tests: list[str] = Field(..., description="List of test IDs to run")
    source: str = Field("web", description="Initiator: 'web' or 'ci'")


@app.post("/test-run/start")
async def start_test_run(request: StartTestRunRequest):
    """Start a test run.  Blocks if another run is already in progress."""
    global _current_test_run

    if _test_run_lock.locked():
        raise HTTPException(
            status_code=409,
            detail=f"Another test run is in progress (source: {_current_test_run.get('source','unknown')}). Try again later.",
        )

    # Acquire the lock manually — the background task will release it
    await _test_run_lock.acquire()

    run_id = f"run-{int(__import__('time').time())}"
    _current_test_run = {
        "run_id": run_id,
        "status": "running",
        "source": request.source,
        "ip_address": request.ip_address,
        "tests": request.tests,
        "progress": {"completed": 0, "total": len(request.tests), "current_test": None},
        "results": [],
        "checkpoints": [],
    }

    # Fire-and-forget: run tests in background
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


async def _execute_test_run(
    run_id: str,
    ip_address: str,
    connections_path: str,
    topology_path: str,
    tests: list[str],
    source: str,
):
    """Background coroutine that runs the selected tests and updates _current_test_run."""
    global _current_test_run
    import time
    from pocketbase_logger import pb_log

    def _log(level: str, msg: str):
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        entry = {"level": level, "message": msg, "timestamp": ts}
        if _current_test_run is not None:
            _current_test_run.setdefault("logs", []).append(entry)

    _log("info", f"Test run {run_id} started (source={source}, ip={ip_address})")
    pb_log.test_run_started(run_id, source, ip_address, tests)

    try:
        for idx, test_id in enumerate(tests):
            _log("info", f"Starting test: {test_id}")
            _current_test_run["progress"]["current_test"] = test_id
            _current_test_run["checkpoints"].append({
                "test": test_id,
                "status": "running",
                "timestamp": time.time(),
            })
            pb_log.checkpoint(run_id, test_id, "running")

            try:
                result = await _run_single_test(test_id, ip_address, connections_path, topology_path)
            except Exception as exc:
                _log("error", f"Test '{test_id}' raised: {exc}")
                result = {"test_id": test_id, "passed": False, "error": str(exc)}

            _current_test_run["progress"]["completed"] = idx + 1
            _current_test_run["results"].append(result)
            cp = _current_test_run["checkpoints"][-1]
            passed = result.get("passed", False)

            if passed:
                cp["status"] = "passed"
                _log("info", f"Test '{test_id}' PASSED")
                pb_log.checkpoint(run_id, test_id, "passed")
            else:
                err = result.get("error", "Unknown error")
                # Extract cpx_io error details if present
                if isinstance(result.get("error"), dict):
                    err = json.dumps(result["error"], default=str)
                cp["status"] = "failed"
                cp["error"] = str(err)[:500]
                _log("error", f"Test '{test_id}' FAILED: {err}")
                pb_log.checkpoint(run_id, test_id, "failed", str(err)[:500])
                pb_log.error(run_id, f"Test '{test_id}' failed: {err}")

        _current_test_run["status"] = "completed"
        _log("info", "All tests completed")
        pb_log.test_run_completed(run_id, _current_test_run["results"])
    except Exception as exc:
        _current_test_run["status"] = "error"
        _current_test_run["error"] = str(exc)
        _log("error", f"Test run crashed: {exc}")
        pb_log.error(run_id, f"Test run crashed: {exc}")
    finally:
        _test_run_lock.release()


async def _run_single_test(test_id: str, ip_address: str, connections_path: str, topology_path: str) -> dict:
    """Dispatch a single test by ID.  Runs synchronously via run_in_executor."""
    import concurrent.futures
    from test_runner import (
        test_condition_counter,
        test_valve_condition_counter,
        test_remanent_params,
    )

    loop = asyncio.get_running_loop()

    if test_id == "validate-connections":
        with concurrent.futures.ThreadPoolExecutor() as pool:
            return await loop.run_in_executor(
                pool,
                lambda: validate_connections(ip_address, connections_path),
            )

    if test_id == "compare-topology":
        with concurrent.futures.ThreadPoolExecutor() as pool:
            return await loop.run_in_executor(
                pool,
                lambda: compare_topology(topology_path, ip_address),
            )

    if test_id == "condition-counter":
        with concurrent.futures.ThreadPoolExecutor() as pool:
            return await loop.run_in_executor(
                pool,
                lambda: _run_test_with_connection(
                    ip_address, connections_path, test_condition_counter
                ),
            )

    if test_id == "valve-condition-counter":
        with concurrent.futures.ThreadPoolExecutor() as pool:
            return await loop.run_in_executor(
                pool,
                lambda: _run_test_with_connection(
                    ip_address, connections_path, test_valve_condition_counter
                ),
            )

    if test_id == "remanent-params":
        with concurrent.futures.ThreadPoolExecutor() as pool:
            return await loop.run_in_executor(
                pool,
                lambda: _run_test_with_connection(
                    ip_address, connections_path, test_remanent_params
                ),
            )

    return {"test_id": test_id, "passed": None, "error": f"Test '{test_id}' not yet implemented"}


def _run_test_with_connection(ip_address: str, connections_path: str, test_fn) -> dict:
    """Helper: connect to CPX-AP, run *test_fn*, return aggregated result.

    Captures and returns cpx_io errors as structured dicts so they reach the UI.
    """
    from cpx_io.cpx_system.cpx_ap.cpx_ap import CpxAp

    try:
        with CpxAp(ip_address=ip_address, timeout=0) as cpx_ap:
            raw = test_fn(cpx_ap, connections_path)
    except Exception as exc:
        return {"passed": False, "error": f"{type(exc).__name__}: {exc}", "cpx_io_error": True}

    if isinstance(raw, list):
        passed = all(r.get("passed", False) for r in raw if r.get("passed") is not None)
        return {"results": raw, "all_passed": passed, "passed": passed}
    return raw


# ─── PocketBase History ────────────────────────────────────────────────────

@app.get("/test-run/history")
async def test_run_history(limit: int = 50):
    """Return the most recent test runs from PocketBase."""
    from pocketbase_logger import pb_log
    return JSONResponse(pb_log.get_run_history(limit))


@app.get("/test-run/{run_id}")
async def test_run_detail(run_id: str):
    """Return full detail for a specific test run."""
    from pocketbase_logger import pb_log
    detail = pb_log.get_run_detail(run_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")
    return JSONResponse(detail)


# Mount the Vite-built static assets (JS bundles, CSS, etc.) LAST so that all
# API routes take precedence.  Only activated when dist/ exists.
if _DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=str(_DIST / "assets")), name="assets")
