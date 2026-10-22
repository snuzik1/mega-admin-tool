"""HTTP API наборов конфигов (см. megatool/bundles.py).

Сервис создаётся лениво при первом обращении и кэшируется на runtime
(``request.app.state.rt._bundles``), чтобы хранилище не пересоздавалось
на каждый запрос. Формат ответов общий для проекта: {"ok": True, ...}
или {"ok": False, "error": "..."}.
"""

from __future__ import annotations

import io
from typing import Any, Dict

from fastapi import APIRouter, Body, Request
from fastapi.responses import StreamingResponse

from .bundles import BundleStore

router = APIRouter(prefix="/api")


def _store(request: Request) -> BundleStore:
    """Ленивый экземпляр хранилища, привязанный к runtime приложения."""
    runtime = request.app.state.rt
    store = getattr(runtime, "_bundles", None)
    if store is None:
        store = BundleStore(runtime.cfg)
        runtime._bundles = store
    return store


def _as_dict(body: Any) -> Dict[str, Any]:
    return body if isinstance(body, dict) else {}


# --------------------------------------------------------------------------- #
#  Наборы
# --------------------------------------------------------------------------- #

@router.get("/bundles")
def bundles_list(request: Request) -> Dict[str, Any]:
    items = _store(request).list()
    return {"ok": True, "items": items, "count": len(items)}


@router.get("/bundles/presets")
def bundles_presets(request: Request) -> Dict[str, Any]:
    items = _store(request).presets()
    return {"ok": True, "items": items, "count": len(items)}


@router.post("/bundles")
def bundles_create(request: Request, body: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    return _store(request).create(_as_dict(body))


@router.get("/bundles/{bid}")
def bundles_get(request: Request, bid: str) -> Dict[str, Any]:
    return _store(request).get(bid)


@router.patch("/bundles/{bid}")
def bundles_update(request: Request, bid: str,
                   body: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    return _store(request).save_meta(bid, _as_dict(body))


@router.delete("/bundles/{bid}")
def bundles_delete(request: Request, bid: str) -> Dict[str, Any]:
    return _store(request).delete(bid)


# --------------------------------------------------------------------------- #
#  Файлы набора
# --------------------------------------------------------------------------- #

@router.get("/bundles/{bid}/file")
def bundle_file_read(request: Request, bid: str, path: str = "") -> Dict[str, Any]:
    return _store(request).read_file(bid, path)


@router.post("/bundles/{bid}/file")
def bundle_file_write(request: Request, bid: str,
                      body: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    data = _as_dict(body)
    return _store(request).write_file(bid, data.get("path", ""), data.get("content", ""))


@router.post("/bundles/{bid}/files")
def bundle_file_add(request: Request, bid: str,
                    body: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    return _store(request).add_file(bid, _as_dict(body).get("path", ""))


@router.delete("/bundles/{bid}/file")
def bundle_file_delete(request: Request, bid: str, path: str = "") -> Dict[str, Any]:
    return _store(request).delete_file(bid, path)


# --------------------------------------------------------------------------- #
#  Экспорт
# --------------------------------------------------------------------------- #

@router.get("/bundles/{bid}/export")
def bundle_export(request: Request, bid: str):
    store = _store(request)
    payload = store.export_zip(bid)
    if isinstance(payload, dict):
        return payload
    return StreamingResponse(
        io.BytesIO(payload),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{bid}.zip"',
            "Content-Length": str(len(payload)),
        },
    )


__all__ = ["router"]
