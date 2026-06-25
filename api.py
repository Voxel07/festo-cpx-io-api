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

from generate_system_config import generate_topology, save_topology, compare_topology

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


# Mount the Vite-built static assets (JS bundles, CSS, etc.) LAST so that all
# API routes take precedence.  Only activated when dist/ exists.
if _DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=str(_DIST / "assets")), name="assets")
