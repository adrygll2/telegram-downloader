"""Alta de la sesion de Telegram desde la interfaz.

Hasta ahora esto solo se podia hacer con `python main.py setup`, que pide el
codigo de verificacion por consola. En un contenedor no hay consola: Telethon
escribia "Please enter the code you received", se encontraba con un EOF y el
backend se quedaba con un cliente conectado pero sin autenticar. A partir de
ahi todo lo que tocaba Telegram fallaba con "The key is not registered in the
system" y la indexacion encontraba cero mensajes.

Estos endpoints parten aquel dialogo en dos peticiones (mandar codigo, canjear
codigo) y reutilizan el cliente de Telethon asociado a cada cuenta. Al terminar
no hace falta reiniciar: se resuelven sus canales y se inicia la indexacion.
"""
import logging
import os
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from telethon.errors import (
    AuthRestartError,
    FloodWaitError,
    PhoneCodeEmptyError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    PhoneNumberInvalidError,
    SendCodeUnavailableError,
    SessionPasswordNeededError,
)

from app.auth.dependencies import get_current_admin
from app.services.descarga_paralela import motor_cifrado

logger = logging.getLogger("tmd")

router = APIRouter(prefix="/api/admin/telegram", tags=["telegram"])

async def _get_pending(account_id: int) -> dict:
    from app.database.telegram_accounts import get_telegram_account
    account = await get_telegram_account(account_id)
    if account and account.get("pending_code_hash"):
        return {"phone": account["phone"], "hash": account["pending_code_hash"],
                "at": account.get("pending_code_at")}
    return {}


async def _set_pending(account_id: int, code_hash: str) -> None:
    import time
    from app.database.telegram_accounts import set_account_pending
    await set_account_pending(account_id, code_hash, int(time.time()))


async def _clear_pending(account_id: int) -> None:
    from app.database.telegram_accounts import clear_account_pending
    await clear_account_pending(account_id)


class SignInRequest(BaseModel):
    code: str
    password: str | None = None
    account_id: int | None = None


class AddAccountRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    phone: str = Field(min_length=8, max_length=32)


class CredentialsRequest(BaseModel):
    api_id: int
    api_hash: str
    phone: str


def _mask_phone(phone: str) -> str:
    """+34633453385 -> +34·····3385. El numero ya esta en el .env, pero no hay
    razon para repetirlo entero en cada respuesta de la API."""
    if len(phone) < 8:
        return phone
    return f"{phone[:3]}{'·' * (len(phone) - 7)}{phone[-4:]}"


def _humanize(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds} segundos"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} minuto{'s' if minutes != 1 else ''}"
    hours = minutes // 60
    return f"{hours} hora{'s' if hours != 1 else ''}"


def _mask_hash(value: str) -> str:
    """Igual que en /api/config: se enmascara en vez de ocultarse para que la
    interfaz sepa que hay algo puesto sin llegar a enseñarlo."""
    return f"{'*' * 8}{value[-4:]}" if value else ""


def _credentials_out(config: dict) -> dict:
    return {
        "api_id": config.get("api_id") or 0,
        "api_hash": _mask_hash(config.get("api_hash", "")),
        "phone": config.get("phone", ""),
    }


def _ctx():
    from app.routers.download import downloader, config
    if downloader is None:
        raise HTTPException(status_code=503, detail="El backend aun esta arrancando")
    return downloader, config


def _configured(config: dict, phone: str | None = None) -> bool:
    return bool(config.get("api_id") and config.get("api_hash") and (phone or config.get("phone")))


def _selected_account_id(downloader, account_id=None):
    selected = int(account_id or downloader.primary_account_id or 0)
    if selected not in downloader.accounts:
        raise HTTPException(status_code=404, detail="Cuenta Telegram no encontrada")
    return selected


async def _ensure_client(downloader, config, account_id=None):
    """Obtiene el cliente de la cuenta elegida y señala si acaba de crearse."""
    account_id = _selected_account_id(downloader, account_id)
    created = account_id not in downloader.clients
    return await downloader.ensure_client(account_id), created


@router.get("/status")
async def telegram_status(user: Annotated[str, Depends(get_current_admin)], account_id: int | None = None):
    downloader, config = _ctx()
    account_id = _selected_account_id(downloader, account_id)
    account = downloader.accounts[account_id]
    if not _configured(config, account.get("phone")):
        return {
            "configured": False, "authorized": False, "code_sent": False,
            "account_id": account_id, "account_name": account["name"],
            "phone": _mask_phone(account.get("phone", "")), "channels_resolved": 0,
            "credentials": _credentials_out(config),
            "detail": "Faltan el API ID o el API Hash global de Telegram.",
        }

    authorized = False
    try:
        client, _ = await _ensure_client(downloader, config, account_id)
        authorized = await client.is_user_authorized()
        # Se pregunta cada vez: quien acaba de pagar Premium quiere verlo aqui
        # sin tener que reiniciar nada.
        if authorized:
            premium = bool(getattr(await client.get_me(), "premium", False))
            account["premium"] = premium
            if account_id == downloader.primary_account_id:
                downloader.premium = premium
    except Exception as e:
        logger.warning("No se pudo comprobar la sesion de Telegram: %s", e)

    return {
        "configured": True,
        "authorized": authorized,
        "account_id": account_id,
        "account_name": account["name"],
        "is_primary": account.get("is_primary", False),
        "code_sent": bool(await _get_pending(account_id)),
        "phone": _mask_phone(account.get("phone", "")),
        "channels_resolved": sum(ch.get("account_id") == account_id for ch in downloader.channels.values()),
        "credentials": _credentials_out(config),
        # Lo que decide la velocidad de descarga: si Telegram limita la cuenta,
        # con que se descifra y cuantas conexiones se abren por archivo.
        "premium": account.get("premium") if authorized else None,
        "cifrado": motor_cifrado(),
        "conexiones": downloader.conexiones,
    }


@router.get("/accounts")
async def telegram_accounts(user: Annotated[str, Depends(get_current_admin)]):
    downloader, _config = _ctx()
    from app.database.telegram_accounts import list_telegram_accounts
    accounts = await list_telegram_accounts()
    output = []
    for account in accounts:
        authorized = False
        client = downloader.client_for_account(account["id"])
        if client and client.is_connected():
            try:
                authorized = await client.is_user_authorized()
            except Exception as e:
                logger.warning("No se pudo consultar la cuenta Telegram %s: %s", account["name"], e)
        output.append({
            "id": account["id"], "name": account["name"],
            "phone": _mask_phone(account.get("phone", "")),
            "is_primary": account["is_primary"], "active": account["active"],
            "channels_count": account["channels_count"], "authorized": authorized,
            "code_sent": bool(await _get_pending(account["id"])),
        })
    return {"accounts": output}


@router.post("/accounts")
async def create_telegram_account(req: AddAccountRequest, user: Annotated[str, Depends(get_current_admin)]):
    name = req.name.strip()
    phone = req.phone.strip().replace(" ", "")
    if not name or not phone.startswith("+"):
        raise HTTPException(status_code=400, detail="Indica un nombre y un teléfono con prefijo internacional.")
    downloader, config = _ctx()
    from app.database.telegram_accounts import create_telegram_account as create_account
    try:
        account = await create_account(name, phone)
        await downloader.add_account(account)
        from app.database.telegram_accounts import list_telegram_accounts
        config["telegram_accounts"] = await list_telegram_accounts()
        downloader.configure_accounts(config["telegram_accounts"])
    except Exception as e:
        logger.warning("No se pudo crear la cuenta Telegram %s: %s", name, e)
        raise HTTPException(status_code=409, detail="No se pudo crear la cuenta; comprueba si ya existe ese teléfono.")
    return {"account": {"id": account["id"], "name": account["name"],
                         "phone": _mask_phone(account["phone"]), "is_primary": False,
                         "active": True, "authorized": False, "channels_count": 0}}


@router.delete("/accounts/{account_id}")
async def remove_telegram_account(account_id: int, user: Annotated[str, Depends(get_current_admin)]):
    from app.database.telegram_accounts import delete_telegram_account, list_telegram_accounts
    downloader, _config = _ctx()
    try:
        account = await delete_telegram_account(account_id)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    if not account:
        raise HTTPException(status_code=404, detail="Cuenta Telegram no encontrada")
    await downloader.remove_account(account_id)
    remaining = await list_telegram_accounts()
    downloader.configure_accounts(remaining)
    from app.routers.download import config
    config["telegram_accounts"] = remaining
    return {"deleted": True, "account_id": account_id}


@router.post("/send-code")
async def send_code(user: Annotated[str, Depends(get_current_admin)], account_id: int | None = None):
    downloader, config = _ctx()
    account_id = _selected_account_id(downloader, account_id)
    account = downloader.accounts[account_id]
    if not _configured(config, account.get("phone")):
        raise HTTPException(status_code=400, detail="Faltan el API ID o el API Hash global de Telegram.")
    client, _ = await _ensure_client(downloader, config, account_id)

    if await client.is_user_authorized():
        return {"authorized": True, "code_sent": False,
                "detail": "Esta sesion ya estaba iniciada."}

    phone = account["phone"]

    # Telethon guarda el phone_code_hash y, si lo tiene, send_code_request deja
    # de mandar un codigo nuevo y llama a ResendCodeRequest. Pulsando el boton
    # dos veces se agotaban las vias de reenvio y Telegram respondia "all
    # available options for this type of number were already used". Lo tiramos
    # para que siempre sea un envio nuevo.
    from telethon import utils as tl_utils
    client._phone_code_hash.pop(tl_utils.parse_phone(phone) or phone, None)

    try:
        sent = await client.send_code_request(phone)
    except PhoneNumberInvalidError:
        raise HTTPException(status_code=400, detail=f"Telegram no acepta el numero {_mask_phone(phone)}.")
    except FloodWaitError as e:
        raise HTTPException(
            status_code=429,
            detail=f"Telegram ha limitado los intentos. Hay que esperar {_humanize(e.seconds)} "
                   "antes de pedir otro codigo.",
        )
    except SendCodeUnavailableError:
        raise HTTPException(
            status_code=429,
            detail="Telegram ya ha gastado todas las vias de envio para este numero (SMS, llamada). "
                   "Espera unos minutos y vuelve a intentarlo, o inicia sesion desde la app de "
                   "Telegram en otro dispositivo para que te llegue por ahi.",
        )
    except AuthRestartError:
        raise HTTPException(status_code=503,
                            detail="Telegram ha pedido reiniciar el proceso. Vuelve a darle al boton.")
    except Exception as e:
        logger.error("Error pidiendo el codigo de Telegram: %s", e)
        raise HTTPException(status_code=502, detail=f"Telegram no ha aceptado la peticion: {e}")

    await _set_pending(account_id, sent.phone_code_hash)
    logger.info("Codigo de Telegram enviado a %s (%s)", account["name"], _mask_phone(phone))

    return {"authorized": False, "code_sent": True, "account_id": account_id, "phone": _mask_phone(phone),
            "detail": "Telegram te ha mandado un codigo. Miralo en la app y escribelo aqui."}


@router.post("/sign-in")
async def sign_in(req: SignInRequest, user: Annotated[str, Depends(get_current_admin)]):
    downloader, config = _ctx()

    account_id = _selected_account_id(downloader, req.account_id)
    account = downloader.accounts[account_id]
    pending = await _get_pending(account_id)
    if not pending:
        raise HTTPException(status_code=409,
                            detail="No hay ningun codigo pendiente. Pide uno nuevo.")

    client, _ = await _ensure_client(downloader, config, account_id)
    code = req.code.strip().replace(" ", "").replace("-", "")

    try:
        await client.sign_in(pending["phone"], code, phone_code_hash=pending["hash"])
    except SessionPasswordNeededError:
        # Cuenta con verificacion en dos pasos: el codigo era correcto pero
        # falta la contrasena. Se pide en una segunda vuelta del formulario.
        if not req.password:
            raise HTTPException(
                status_code=428,
                detail="Esta cuenta tiene verificacion en dos pasos. Escribe tambien tu contrasena.",
            )
        try:
            await client.sign_in(password=req.password)
        except Exception as e:
            raise HTTPException(status_code=401, detail=f"Contrasena de dos pasos incorrecta: {e}")
    except (PhoneCodeInvalidError, PhoneCodeEmptyError):
        raise HTTPException(status_code=400, detail="El codigo no es correcto.")
    except PhoneCodeExpiredError:
        await _clear_pending(account_id)
        raise HTTPException(status_code=400, detail="El codigo ha caducado. Pide uno nuevo.")
    except Exception as e:
        logger.error("Error iniciando sesion en Telegram: %s", e)
        raise HTTPException(status_code=502, detail=f"Telegram ha rechazado el acceso: {e}")

    await _clear_pending(account_id)
    logger.info("Sesion de Telegram iniciada para %s desde el panel de administracion", account["name"])

    resolved, indexing = await _after_login(downloader, config, account_id)

    return {"authorized": True, "account_id": account_id, "channels_resolved": resolved, "indexing": indexing,
            "detail": "Sesion iniciada. Ya se estan resolviendo los canales."}


async def _after_login(downloader, config, account_id: int) -> tuple[int, bool]:
    """Deja el backend como si hubiera arrancado con la sesion ya puesta:
    canales resueltos, escucha de mensajes nuevos e indexacion en marcha."""
    from app.database.connection import get_active_channels

    try:
        channels = await get_active_channels()
        if channels:
            config["channels"] = channels
            await downloader.load_channels_from_list(channels)
    except Exception as e:
        logger.error("No se pudieron resolver los canales tras el login: %s", e)

    # main registra el callback incluso si aun no habia canales; al iniciar una
    # cuenta nueva solo hace falta enganchar su cliente.
    try:
        downloader.watch_new_messages(account_id=account_id)
    except Exception as e:
        logger.warning("No se pudo enganchar la escucha de mensajes nuevos: %s", e)

    indexing = False
    try:
        from app.routers.index_router import _do_start
        result = await _do_start(force=False)
        indexing = result.get("status") == "started"
    except Exception as e:
        logger.error("No se pudo arrancar la indexacion tras el login: %s", e)

    resolved = sum(ch.get("account_id") == account_id for ch in downloader.channels.values())
    return resolved, indexing


@router.post("/credentials")
async def save_credentials(req: CredentialsRequest, user: Annotated[str, Depends(get_current_admin)]):
    """Guarda API ID, API Hash y telefono en la base de datos.

    No se tocan variables de entorno: en un despliegue tipo Coolify el .env lo
    regenera la plataforma en cada redespliegue y lo escrito aqui se perderia.
    La tabla `app_settings` manda sobre el .env al arrancar.
    """
    downloader, config = _ctx()

    api_hash = req.api_hash.strip()
    # La interfaz sirve el hash enmascarado; si vuelve tal cual es que no lo
    # han tocado. Sin esto se guardaria "********1204" como credencial.
    if api_hash.startswith("****"):
        api_hash = config.get("api_hash", "")

    phone = req.phone.strip().replace(" ", "")

    if req.api_id <= 0:
        raise HTTPException(status_code=400, detail="El API ID tiene que ser un numero positivo.")
    if not api_hash:
        raise HTTPException(status_code=400, detail="Falta el API Hash.")
    if not phone.startswith("+"):
        raise HTTPException(status_code=400,
                            detail="El telefono va con prefijo internacional, por ejemplo +34600112233.")

    # Cambiar de aplicacion de Telegram invalida la sesion: la clave guardada
    # pertenece al api_id anterior y Telegram la rechazaria con el mismo
    # "The key is not registered in the system" de siempre.
    app_changed = (req.api_id != config.get("api_id")) or (api_hash != config.get("api_hash"))
    phone_changed = phone != config.get("phone")

    from app.database.connection import set_app_settings
    await set_app_settings({"api_id": req.api_id, "api_hash": api_hash, "phone": phone})

    config["api_id"] = req.api_id
    config["api_hash"] = api_hash
    config["phone"] = phone
    # El downloader se quedo con una copia al construirse.
    downloader.api_id = req.api_id
    downloader.api_hash = api_hash
    downloader.phone = phone
    downloader.accounts[downloader.primary_account_id]["phone"] = phone
    from app.database.telegram_accounts import update_primary_phone, list_telegram_accounts
    await update_primary_phone(phone)
    config["telegram_accounts"] = await list_telegram_accounts()

    await _clear_pending(downloader.primary_account_id)

    if app_changed:
        for account_id in list(downloader.accounts):
            await _clear_pending(account_id)
            await _reset_client(downloader, account_id, archive=True)
    elif phone_changed:
        await _reset_client(downloader, downloader.primary_account_id, archive=True)

    logger.info("Credenciales de Telegram actualizadas por %s (app nueva: %s)", user, app_changed)

    return {
        "saved": True,
        "session_reset": app_changed or phone_changed,
        "credentials": _credentials_out(config),
        "detail": ("Credenciales guardadas. La sesion afectada debe volver a iniciar sesion.")
        if app_changed or phone_changed else "Credenciales guardadas.",
    }


@router.post("/logout")
async def logout(user: Annotated[str, Depends(get_current_admin)], account_id: int | None = None):
    """Cierra la sesion de Telegram de una cuenta para volver a entrar con el codigo.

    Existe porque Telegram puede tratar una sesion vieja peor que una nueva:
    con la cuenta ya Premium y cryptg, esta bajaba a 5,3 MB/s con miles de
    esperas forzosas mientras Telegram Desktop, misma cuenta y misma casa,
    bajaba la misma pelicula en un minuto. Se cierra tambien en Telegram
    (log_out), para que no quede un dispositivo fantasma en la cuenta.
    """
    downloader, config = _ctx()
    account_id = _selected_account_id(downloader, account_id)
    if downloader.active_batches and any(
            b.get("status") in ("downloading", "extracting") for b in downloader.active_batches.values()):
        raise HTTPException(status_code=409,
                            detail="Hay descargas en marcha. Espera a que acaben o pausalas antes de cerrar la sesion.")

    cerrada_en_telegram = False
    client = downloader.clients.get(account_id)
    if client is not None:
        try:
            if not client.is_connected():
                await client.connect()
            cerrada_en_telegram = bool(await client.log_out())
        except Exception as e:
            logger.warning("No se pudo cerrar la sesion en Telegram (se aparta igualmente): %s", e)
    await _clear_pending(account_id)
    # log_out ya borra el fichero; si fallo, se aparta como al cambiar de app.
    await _reset_client(downloader, account_id, archive=True)
    downloader.accounts[account_id].pop("premium", None)
    if account_id == downloader.primary_account_id:
        downloader.premium = None
    logger.info("Sesion de Telegram %s cerrada por %s (en Telegram: %s)",
                downloader.accounts[account_id].get("name", account_id), user,
                "si" if cerrada_en_telegram else "no")
    return {"authorized": False, "account_id": account_id,
            "detail": "Sesion cerrada. Pide un codigo para volver a entrar."}


async def _reset_client(downloader, account_id: int, archive: bool = False) -> None:
    """Desconecta un cliente y, si cambió su identidad/API, aparta su sesión.
    Se renombra en vez de borrarse: si el cambio fue un error, el fichero
    sigue ahi."""
    import time

    account_id = int(account_id)
    client = downloader.clients.pop(account_id, None)
    if client is not None:
        try:
            await client.disconnect()
        except Exception as e:
            logger.warning("No se pudo desconectar el cliente anterior: %s", e)
    downloader._event_handlers.discard(account_id)
    if account_id == downloader.primary_account_id:
        downloader.client = None

    downloader.channels = {cid: ch for cid, ch in downloader.channels.items()
                           if ch.get("account_id") != account_id}

    session_file = f"{downloader._session_path(downloader.accounts[account_id]['session_name'])}.session"
    if archive and os.path.isfile(session_file):
        backup = f"{session_file}.bak-{int(time.time())}"
        try:
            os.rename(session_file, backup)
            logger.info("Sesion anterior apartada en %s", os.path.basename(backup))
        except OSError as e:
            logger.warning("No se pudo apartar la sesion anterior: %s", e)


# --- Diagnostico: ver los mensajes de un canal tal cual -------------------------

def _peer_de(channel_id: int) -> int:
    """Los canales se guardan con su id positivo; Telethon quiere el marcado
    (-100...)."""
    return channel_id if channel_id < 0 else int(f"-100{channel_id}")


def _resumen(m) -> dict:
    """Lo que interesa de un mensaje para entender como publica un canal: el
    texto, que lleva adjunto, a que responde y los enlaces. Solo lectura."""
    from telethon.tl import types as tl

    from app.services.contexto import texto_de

    medio = None
    doc = getattr(m, "document", None)
    if getattr(m, "photo", None):
        medio = {"tipo": "foto"}
    elif doc is not None:
        medio = {"tipo": "documento", "mime": getattr(doc, "mime_type", None),
                 "tamano": getattr(doc, "size", None)}
        for a in getattr(doc, "attributes", []) or []:
            if isinstance(a, tl.DocumentAttributeFilename):
                medio["nombre"] = a.file_name
            elif isinstance(a, tl.DocumentAttributeVideo):
                medio.update(tipo="video", duracion=a.duration, ancho=a.w, alto=a.h,
                             streaming=bool(getattr(a, "supports_streaming", False)))
    elif isinstance(getattr(m, "media", None), tl.MessageMediaWebPage):
        wp = m.media.webpage
        medio = {"tipo": "web", "url": getattr(wp, "url", None), "titulo": getattr(wp, "title", None)}

    enlaces = []
    for ent, texto in (m.get_entities_text() if m.message else []):
        url = getattr(ent, "url", None) or (texto if isinstance(ent, tl.MessageEntityUrl) else None)
        if url:
            enlaces.append(url)
    botones = []
    for fila in (getattr(getattr(m, "reply_markup", None), "rows", None) or []):
        for b in fila.buttons:
            if getattr(b, "url", None):
                botones.append({"texto": b.text, "url": b.url})

    r = getattr(m, "reply_to", None)
    return {
        "id": m.id,
        "fecha": m.date.isoformat() if m.date else None,
        # Las fichas "enriquecidas" no traen nada en `message`: su texto sale
        # de `rich_message` (ver contexto._texto_rico).
        "texto": (m.message or texto_de(m) or "")[:800],
        # Para lo que llega vacio sin serlo (hay fichas que en la app de
        # Telegram se ven enteras y aqui no traen ni texto ni adjunto): de que
        # tipo es el mensaje y su adjunto, tal como los entiende Telethon.
        "clase": type(m).__name__,
        "clase_medio": type(m.media).__name__ if getattr(m, "media", None) else None,
        "medio": medio,
        "album": m.grouped_id,
        "responde_a": getattr(r, "reply_to_msg_id", None),
        "tema": getattr(r, "reply_to_top_id", None) or (getattr(r, "reply_to_msg_id", None)
                                                        if getattr(r, "forum_topic", False) else None),
        "enlaces": enlaces,
        "botones": botones,
    }


@router.get("/mensajes")
async def mensajes_de_canal(user: Annotated[str, Depends(get_current_admin)], channel_id: int,
                            limite: int = 40, tema: int | None = None, antes_de: int = 0):
    """Los ultimos mensajes de un canal (o de un tema suyo), resumidos. Es para
    entender como publica cada canal (que va en el texto, que en el archivo,
    como se relacionan) y afinar el indexado. **Solo lee**."""
    downloader, config = _ctx()
    cliente, _ = await _ensure_client(downloader, config, downloader.account_for_channel(channel_id))
    if not await cliente.is_user_authorized():
        raise HTTPException(status_code=409, detail="Telegram no tiene sesion")
    entidad = await cliente.get_input_entity(_peer_de(channel_id))
    kw = {"limit": max(1, min(limite, 200)), "offset_id": antes_de}
    if tema:
        kw["reply_to"] = tema
    return {"mensajes": [_resumen(m) async for m in cliente.iter_messages(entidad, **kw)]}


@router.get("/temas")
async def temas_de_canal(user: Annotated[str, Depends(get_current_admin)], channel_id: int):
    """Los temas de un canal con foro (para encontrar el de "Peliculas")."""
    from app.services.peticiones import PeticionError, temas

    try:
        downloader, _config = _ctx()
        return {"temas": await temas(_peer_de(channel_id), cliente=downloader.client_for_channel(channel_id))}
    except PeticionError as e:
        raise HTTPException(status_code=e.codigo, detail=str(e))
