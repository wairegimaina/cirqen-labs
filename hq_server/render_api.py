"""Render's API, so the admin panel can deploy a hospital's HQ and put its
identity on it without anyone opening Render.

Needs RENDER_API_KEY on Control (Render > Account settings > API keys), set
once. A hospital is linked to its Render services (web and worker) on its
page; the panel then shows their last deploy, deploys the latest commit, and
puts a newly issued identity in their environment before redeploying.
Creating a new hospital's services stays with its Blueprint file
(hq_server tools/new_hospital.py): Render's API can't apply a Blueprint.
"""
from __future__ import annotations

import os

import httpx

API = "https://api.render.com/v1"
TIMEOUT = 20


class RenderError(Exception):
    """Render refused or couldn't be reached; the message is safe to show."""


def configured() -> bool:
    return bool(os.getenv("RENDER_API_KEY", "").strip())


def _client(transport=None) -> httpx.Client:
    key = os.getenv("RENDER_API_KEY", "").strip()
    if not key:
        raise RenderError("Render is not connected: set RENDER_API_KEY on Cirqen Control once.")
    return httpx.Client(base_url=API, timeout=TIMEOUT, transport=transport,
                        headers={"Authorization": f"Bearer {key}", "Accept": "application/json"})


def _call(method: str, path: str, transport=None, **kwargs):
    try:
        with _client(transport) as client:
            resp = client.request(method, path, **kwargs)
    except httpx.HTTPError as exc:
        raise RenderError(f"Render could not be reached ({exc.__class__.__name__}).") from exc
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("message", "")
        except ValueError:
            detail = ""
        raise RenderError(f"Render refused ({resp.status_code}){': ' + detail if detail else ''}.")
    return resp.json() if resp.content else {}


def services(transport=None) -> list[dict]:
    """Every service on the account: [{"id", "name", "type", "url"}], by name."""
    found, cursor = [], None
    for _ in range(20):                               # 20 pages of 100 is plenty
        params = {"limit": 100, **({"cursor": cursor} if cursor else {})}
        page = _call("GET", "/services", transport, params=params)
        if not page:
            break
        for item in page:
            svc = item.get("service") or {}
            found.append({"id": svc.get("id"), "name": svc.get("name"), "type": svc.get("type"),
                          "url": (svc.get("serviceDetails") or {}).get("url")})
        cursor = page[-1].get("cursor")
        if len(page) < 100 or not cursor:
            break
    return sorted(found, key=lambda s: s["name"] or "")


def latest_deploy(service_id: str, transport=None) -> dict | None:
    """{"id", "status", "created_at", "finished_at", "commit"} or None."""
    page = _call("GET", f"/services/{service_id}/deploys", transport, params={"limit": 1})
    if not page:
        return None
    d = page[0].get("deploy") or {}
    commit = d.get("commit") or {}
    return {"id": d.get("id"), "status": d.get("status"), "created_at": d.get("createdAt"),
            "finished_at": d.get("finishedAt"),
            "commit": ((commit.get("id") or "")[:7] + " " + (commit.get("message") or "").split("\n")[0]).strip()}


def deploy(service_id: str, transport=None) -> dict:
    d = _call("POST", f"/services/{service_id}/deploys", transport, json={"clearCache": "do_not_clear"})
    return {"id": d.get("id"), "status": d.get("status")}


def set_env(service_id: str, key: str, value: str, transport=None) -> None:
    _call("PUT", f"/services/{service_id}/env-vars/{key}", transport, json={"value": value})
