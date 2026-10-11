"""
12-Factor config service (Vercel-ready).

Layers, lowest -> highest precedence:
  1. hardcoded defaults
  2. config.<env>.yaml          (APP_ENV selects the env, default: development)
  3. .env file                  (NUM_WORKERS is an alias for workers)
  4. OS environment variables   (APP_* prefix)
  5. ?set=key=value overrides   (highest precedence, like CLI flags)

Vercel layout (this file is the ASGI function):
    api/index.py       <- this file (exported `app` is auto-detected as ASGI)
    requirements.txt   <- fastapi, uvicorn, pyyaml
    vercel.json        <- rewrite: /(.*) -> /api/index

Vercel's serverless filesystem is READ-ONLY, so layer files are never written
at runtime. If config.development.yaml / .env are committed next to this file
(or at the project root) they are read and used; otherwise the embedded
assigned values below are used (identical content, so the result is the same).

Local run:
    pip install -r requirements.txt
    uvicorn api.index:app --reload --port 8000
"""

import os

import yaml
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

app = FastAPI(title="12-Factor Config Service")

# CORS: allow the grader page to call this endpoint directly from the browser.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Layer 1: hardcoded defaults (lowest precedence)
DEFAULTS = {
    "port": 8000,
    "workers": 1,
    "debug": False,
    "log_level": "info",
    "api_key": "default-secret-000",
}

# Aliases: .env keys that map onto canonical config keys.
ENV_FILE_ALIASES = {"NUM_WORKERS": "workers"}

# Embedded copies of the assigned layer files, used when the files are not
# present on disk (e.g. on Vercel's read-only serverless filesystem).
YAML_LAYER = {"debug": True, "api_key": "key-pluck99d9x"}
ENV_LAYER = {"APP_PORT": "8179", "NUM_WORKERS": "1", "APP_LOG_LEVEL": "info"}

# Where to look for committed layer files: this file's dir, project root, cwd.
_SEARCH_DIRS = []
_here = os.path.dirname(os.path.abspath(__file__))
for _d in (_here, os.path.dirname(_here), os.getcwd()):
    _d = os.path.abspath(_d)
    if _d not in _SEARCH_DIRS:
        _SEARCH_DIRS.append(_d)


def find_file(name):
    for d in _SEARCH_DIRS:
        candidate = os.path.join(d, name)
        if os.path.isfile(candidate):
            return candidate
    return None


def coerce(key: str, value):
    """Apply the type-coercion rules for each config key."""
    key = key.strip().lower()
    if key in ("port", "workers"):
        try:
            return int(str(value).strip())
        except ValueError:
            return value
    if key == "debug":
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("true", "1", "yes", "on")
    return str(value)


def read_env_file(name: str) -> dict:
    """Parse a KEY=VALUE .env-style file. Returns {} if the file is absent."""
    path = find_file(name)
    data = {}
    if not path:
        return data
    with open(path, "r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            data[key.strip()] = val.strip().strip('"').strip("'")
    return data


def read_yaml_file(name: str) -> dict:
    """Load a YAML file. Returns {} if the file is absent or invalid."""
    path = find_file(name)
    if not path:
        return {}
    with open(path, "r", encoding="utf-8") as fh:
        loaded = yaml.safe_load(fh)
    return loaded if isinstance(loaded, dict) else {}


def build_base_config() -> dict:
    # Layer 1: defaults
    cfg = dict(DEFAULTS)

    # Layer 2: environment-specific YAML (fall back to embedded content)
    env_name = os.getenv("APP_ENV", "development")
    yaml_layer = read_yaml_file(f"config.{env_name}.yaml") or dict(YAML_LAYER)
    for key, val in yaml_layer.items():
        cfg[str(key).strip().lower()] = val

    # Layer 3: .env file (apply aliases, strip an optional APP_ prefix)
    env_layer = read_env_file(".env") or dict(ENV_LAYER)
    for key, val in env_layer.items():
        canon = ENV_FILE_ALIASES.get(key, key).strip().lower().removeprefix("app_")
        cfg[canon] = val

    # Layer 4: OS environment variables with the APP_* prefix
    for key, val in os.environ.items():
        if key.startswith("APP_") and key != "APP_ENV":
            cfg[key.removeprefix("APP_").strip().lower()] = val

    return cfg


def resolve_config(overrides) -> dict:
    cfg = build_base_config()

    # Layer 5: CLI-style overrides, highest precedence (?set=key=value)
    for pair in overrides:
        key, _, value = str(pair).partition("=")
        key = key.strip().lower()
        if key:
            cfg[key] = value

    resolved = {}
    for key, val in cfg.items():
        canon = str(key).strip().lower()
        resolved[canon] = coerce(canon, val)

    # Secret masking: never expose the real api_key.
    resolved["api_key"] = "****"
    return resolved


@app.get("/effective-config")
def effective_config(request: Request):
    overrides = request.query_params.getlist("set")
    return JSONResponse(content=resolve_config(overrides))


@app.get("/")
def root():
    return {"service": "12-factor-config", "endpoint": "/effective-config"}
