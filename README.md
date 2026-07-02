# cpx-ap-topology-manager

FastAPI backend and helper scripts for the CPX-AP Topology Manager.
The hardware library ([festo-cpx-io](https://github.com/Festo-se/festo-cpx-io)) is kept as a
separate, swappable dependency so it can be updated independently.

## Repository layout

```
api.py                    # FastAPI application (backend)
generate_system_config.py # Topology generation & comparison helpers
outputs.py                # Example: write digital outputs on a CPX-AP system
SVG/                      # Product SVG icons served at /svg/<filename>
topology.jsonc            # Last saved topology (auto-generated)
test.jsonc                # Example / test topology
requirements.txt          # Python dependencies
pyproject.toml            # Package metadata
```

## API Stuff

You can see all available endpoints by visiting:
<http://localhost:8000/docs#/>
assuming this is the port of your local deployment.

## Setup

### 1 – Install the CPX-IO library

**Option A – Local editable install** (tracks your local clone of festo-cpx-io)

```bash
pip install -e ../festo-cpx-io
```

**Option B – PyPI release** (once the library is published)

```bash
pip install festo-cpx-io==<version>
```

### 2 – Install this project's dependencies

```bash
pip install -r requirements.txt
```

### 3 – Start the API

```bash
uvicorn api:app --reload
```

The API runs on `http://localhost:8000`.

### 4 – Frontend (development)

The Vite dev server (`fe/basicTesting`) proxies `/topology`, `/compare`, `/svg` and
`/svg-map` requests to the API.

```bash
cd ../fe/basicTesting
npm run dev          # http://localhost:5173
```

### 4 – Frontend (production)

Build the React app so the API can serve it directly:

```bash
cd ../fe/basicTesting
npm run build        # writes compiled assets to ../cpx-ap-topology-manager/dist/
```

Then just start the API – it serves the SPA at `/`.

## Swapping the CPX-IO library

Because `festo-cpx-io` is an ordinary pip dependency you can update it without touching
this project:

```bash
# Pull the latest changes in the library repo and re-install
cd ../festo-cpx-io
git pull
pip install -e .     # if using editable install – no reinstall needed
```

Or pin to a specific release:

```bash
pip install festo-cpx-io==<new_version>
```
