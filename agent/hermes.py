"""Hermes wiki + web gateway for the planner.

Env: HERMES_URL + HERMES_API_KEY (API key, sent as X-API-Key and Bearer).
Endpoints:
  GET {HERMES_URL}/wiki/search?q=...&limit=...
  GET {HERMES_URL}/wiki/fetch?ref=...
  GET {HERMES_URL}/web/search?q=...&limit=...
All functions return plain dicts and NEVER raise — when Hermes is
unreachable they return an offline stub so the planner keeps running.
"""
import json
import os
import urllib.parse
import urllib.request

TIMEOUT = 30


def _base():
    return os.environ.get("HERMES_URL", "").rstrip("/")


def _get(path, params):
    url = _base() + path + "?" + urllib.parse.urlencode(params)
    key = os.environ.get("HERMES_API_KEY", "")
    req = urllib.request.Request(url, headers={
        "X-API-Key": key,
        "Authorization": f"Bearer {key}",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.load(resp)


def search(query, limit=5):
    """Wiki search; offline stub {"ok": False, "stub": True, ...} on failure."""
    try:
        data = _get("/wiki/search", {"q": query, "limit": limit})
        results = data.get("results", data) if isinstance(data, dict) else data
        return {"ok": True, "query": query, "results": results}
    except Exception as exc:
        # ponytail: no Hermes server in dev — degrade to an empty stub, never crash
        return {"ok": False, "stub": True, "query": query, "results": [],
                "error": str(exc)[:200]}


def fetch(ref):
    """Fetch one wiki page by ref; offline stub on failure."""
    try:
        data = _get("/wiki/fetch", {"ref": ref})
        if isinstance(data, dict):
            return {"ok": True, "ref": ref, **data}
        return {"ok": True, "ref": ref, "content": data}
    except Exception as exc:
        return {"ok": False, "stub": True, "ref": ref, "content": "",
                "error": str(exc)[:200]}


def web_search(query, limit=5):
    """Web search via Hermes; offline stub {"ok": False, ...} on failure."""
    try:
        data = _get("/web/search", {"q": query, "limit": limit})
        results = data.get("results", data) if isinstance(data, dict) else data
        return {"ok": True, "query": query, "results": results}
    except Exception as exc:
        # ponytail: no Hermes server in dev — degrade to an empty stub, never crash
        return {"ok": False, "stub": True, "query": query, "results": [],
                "error": str(exc)[:200]}
