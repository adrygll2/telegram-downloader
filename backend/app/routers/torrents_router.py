import asyncio
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.auth.dependencies import get_current_admin, get_current_user
from app.services.animetosho import search_animetosho
from app.services.leetx import get_magnet, search_leetx
from app.services.nyaa import search_nyaa
from app.services.transmission_client import TransmissionManager

logger = logging.getLogger("tmd")
router = APIRouter(prefix="/api", tags=["torrents"])
manager: TransmissionManager | None = None


def init_torrents_router(config):
    global manager
    manager = TransmissionManager(config)


class AddTorrentRequest(BaseModel):
    magnet: str = Field(min_length=10, max_length=8192)
    title: str = Field(default="", max_length=500)


class TorrentActionRequest(BaseModel):
    torrent_id: int = Field(gt=0)


def _manager():
    if manager is None:
        raise HTTPException(status_code=503, detail="Transmission no configurado")
    return manager


@router.get("/nyaa/search")
async def nyaa_search(user: Annotated[str, Depends(get_current_user)], query: str = Query(min_length=2, max_length=200),
                      page: int = Query(default=1, ge=1, le=1000), spanish_only: bool = False):
    results = await asyncio.to_thread(search_nyaa, query, page, "1_2", spanish_only)
    return {"results": results, "count": len(results)}


@router.get("/leetx/search")
async def leetx_search(user: Annotated[str, Depends(get_current_user)], query: str = Query(min_length=2, max_length=200),
                       page: int = Query(default=1, ge=1, le=1000)):
    results = await asyncio.to_thread(search_leetx, query, page)
    return {"results": results, "count": len(results)}


@router.get("/leetx/magnet")
async def leetx_magnet(user: Annotated[str, Depends(get_current_user)], id: int = Query(gt=0)):
    magnet = await asyncio.to_thread(get_magnet, f"https://1337x.to/torrent/{id}/")
    if not magnet:
        raise HTTPException(status_code=404, detail="No se encontro magnet")
    return {"magnet": magnet}


@router.get("/animetosho/search")
async def animetosho_search(user: Annotated[str, Depends(get_current_user)], query: str = Query(min_length=2, max_length=200),
                            page: int = Query(default=1, ge=1, le=1000), spanish_only: bool = False):
    results = await asyncio.to_thread(search_animetosho, query, page, spanish_only)
    return {"results": results, "count": len(results)}


@router.get("/torrents")
async def list_torrents(user: Annotated[str, Depends(get_current_user)]):
    try:
        return {"torrents": await asyncio.to_thread(_manager().get_torrents)}
    except Exception as error:
        logger.warning("Transmission no disponible: %s", error)
        raise HTTPException(status_code=503, detail="Transmission no disponible") from error


@router.post("/torrents")
async def add_torrent(req: AddTorrentRequest, admin: Annotated[str, Depends(get_current_admin)]):
    if not req.magnet.startswith("magnet:?xt=urn:btih:"):
        raise HTTPException(status_code=400, detail="URI magnet no valida")
    try:
        torrent_id = await asyncio.to_thread(_manager().add_magnet, req.magnet)
    except Exception as error:
        logger.warning("No se pudo anadir torrent: %s", error)
        raise HTTPException(status_code=503, detail="No se pudo conectar con Transmission") from error
    return {"status": "added", "torrent_id": torrent_id}


@router.post("/torrent/pause")
async def pause_torrent(req: TorrentActionRequest, admin: Annotated[str, Depends(get_current_admin)]):
    try:
        await asyncio.to_thread(_manager().pause, req.torrent_id)
    except Exception as error:
        raise HTTPException(status_code=503, detail="No se pudo pausar el torrent") from error
    return {"status": "paused"}


@router.post("/torrent/resume")
async def resume_torrent(req: TorrentActionRequest, admin: Annotated[str, Depends(get_current_admin)]):
    try:
        await asyncio.to_thread(_manager().resume, req.torrent_id)
    except Exception as error:
        raise HTTPException(status_code=503, detail="No se pudo reanudar el torrent") from error
    return {"status": "resumed"}


@router.delete("/torrent/{torrent_id}")
async def remove_torrent(torrent_id: int, admin: Annotated[str, Depends(get_current_admin)]):
    if torrent_id <= 0:
        raise HTTPException(status_code=400, detail="Id de torrent no valido")
    try:
        await asyncio.to_thread(_manager().remove, torrent_id, True)
    except Exception as error:
        raise HTTPException(status_code=503, detail="No se pudo eliminar el torrent") from error
    return {"status": "removed"}
