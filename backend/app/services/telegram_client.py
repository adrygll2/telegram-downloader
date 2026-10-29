import asyncio
import logging
import os
import re
from datetime import datetime, timedelta

from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

from app.services import descarga_paralela
from app.services.storage import _detect_multipart, suggest_folder_name, create_movie_folder

# Lo que se esta bajando lleva esto detras hasta que esta entero.
SUFIJO_PARCIAL = ".parcial"

# Cuanto se espera a Telegram al buscar las partes de un archivo en un canal.
BUSQUEDA_PARTES_S = 8


async def _partes_en_catalogo(channel_id, base):
    """Mensajes del catalogo de ese canal cuyo nombre empieza por `base`."""
    from app.database.connection import get_pool
    pool = get_pool()
    if not pool or not channel_id:
        return []
    patron = base.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    try:
        async with pool.acquire() as conn:
            return await conn.fetch("""
                SELECT channel_id, message_id, file_name, file_size FROM media_items
                WHERE channel_id = $1 AND file_name ILIKE $2 LIMIT 200
            """, channel_id, patron)
    except Exception as e:
        logger.warning("Buscando partes de %s en el catalogo: %s", base, e)
        return []

logger = logging.getLogger("tmd")


class TelegramDownloader:
    def __init__(self, config):
        self.api_id = config["api_id"]
        self.api_hash = config["api_hash"]
        self.phone = config["phone"]
        self.download_path = config["download_path"]
        # Conexiones por archivo (ver services/descarga_paralela.py). 1 = como
        # antes, un trozo detras de otro.
        self.conexiones = max(1, config.get("download_conexiones", 6))
        self.client = None
        self.clients = {}
        self.accounts = {}
        self.primary_account_id = None
        # None hasta preguntarselo a Telegram al arrancar. Es el de la cuenta
        # principal; el de cada cuenta va en `self.accounts[id]["premium"]`.
        self.premium = None
        self.active_batches = {}
        self.channel_ids = []
        self.channels = {}
        self._config_channels = config.get("channels") or []
        self._config_accounts = config.get("telegram_accounts") or [{
            "id": 1, "name": "Principal", "phone": self.phone,
            "session_name": "user", "is_primary": True, "active": True,
        }]
        self.session_dir = None
        self._message_callback = None
        self._event_handlers = set()
        self.configure_accounts(self._config_accounts)

    def configure_accounts(self, accounts):
        self.accounts = {
            int(account["id"]): dict(account)
            for account in accounts if account.get("active", True)
        }
        primary = next((account for account in self.accounts.values() if account.get("is_primary")), None)
        if primary is None and self.accounts:
            primary = next(iter(self.accounts.values()))
        if primary:
            self.primary_account_id = int(primary["id"])
            self.phone = primary.get("phone", self.phone)
        self.client = self.clients.get(self.primary_account_id)

    def _session_path(self, session_name=None):
        session_name = session_name or self.accounts.get(self.primary_account_id, {}).get("session_name", "user")
        if self.session_dir:
            return os.path.join(self.session_dir, session_name)
        for base in [os.getcwd(), os.path.join(os.path.dirname(__file__), "..", "..", "..")]:
            d = os.path.join(base, "session")
            if os.path.isdir(d):
                return os.path.join(d, session_name)
        d = os.path.join(os.getcwd(), "session")
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, session_name)

    async def ensure_client(self, account_id):
        account_id = int(account_id)
        account = self.accounts.get(account_id)
        if not account:
            raise ValueError(f"Cuenta Telegram {account_id} no encontrada")
        client = self.clients.get(account_id)
        if client is None:
            session_file = self._session_path(account.get("session_name"))
            os.makedirs(os.path.dirname(session_file), exist_ok=True)
            client = TelegramClient(session_file, self.api_id, self.api_hash)
            self.clients[account_id] = client
            if account_id == self.primary_account_id:
                self.client = client
        if not client.is_connected():
            await client.connect()
        return client

    def client_for_account(self, account_id):
        return self.clients.get(int(account_id)) if account_id is not None else None

    def account_for_channel(self, channel_id):
        channel = next((ch for ch in self.channel_ids if ch["id"] == channel_id), None)
        return channel.get("account_id", self.primary_account_id) if channel else self.primary_account_id

    def client_for_channel(self, channel_id):
        account_id = self.account_for_channel(channel_id)
        return self.client_for_account(account_id)

    def any_connected(self) -> bool:
        """Si alguna de las cuentas tiene el cliente conectado."""
        return any(c is not None and c.is_connected() for c in self.clients.values())

    async def load_channels_from_list(self, channels: list[dict]):
        if not channels:
            channels = self._config_channels
        self.channel_ids = [{
            "id": ch["id"],
            "name": ch["name"],
            "account_id": int(ch.get("account_id") or self.primary_account_id),
            "account_name": ch.get("account_name") or self.accounts.get(
                int(ch.get("account_id") or self.primary_account_id), {}
            ).get("name", "Principal"),
        } for ch in channels]
        self.channels = {}
        for ch in self.channel_ids:
            account_id = ch["account_id"]
            if account_id not in self.accounts:
                logger.warning("Canal %s asignado a cuenta Telegram desconocida %s", ch["name"], account_id)
                continue
            try:
                client = await self.ensure_client(account_id)
                if not await client.is_user_authorized():
                    continue
                entity = await client.get_entity(self._resolve_channel_id(ch["id"]))
                self.channels[ch["id"]] = {
                    "entity": entity, "name": ch["name"], "account_id": account_id,
                    "account_name": ch["account_name"],
                }
            except Exception as e:
                logger.warning("No se pudo resolver canal %d con cuenta %s: %s",
                               ch["id"], account_id, e)

    def _resolve_channel_id(self, ch_id):
        if ch_id > 0:
            return int(f"-100{ch_id}")
        return ch_id

    async def add_account(self, account):
        account_id = int(account["id"])
        self.accounts[account_id] = dict(account)

    async def remove_account(self, account_id, archive_session=False):
        account_id = int(account_id)
        client = self.clients.pop(account_id, None)
        if client:
            await client.disconnect()
        self._event_handlers.discard(account_id)
        account = self.accounts.get(account_id)
        if account:
            if archive_session:
                self._archive_session(account)
            else:
                self._delete_session(account)
        self.accounts.pop(account_id, None)
        self.channels = {cid: ch for cid, ch in self.channels.items() if ch.get("account_id") != account_id}
        self.channel_ids = [ch for ch in self.channel_ids if ch.get("account_id") != account_id]
        if account_id == self.primary_account_id:
            self.primary_account_id = None
            self.client = None

    def _archive_session(self, account):
        import time
        session_file = f"{self._session_path(account.get('session_name'))}.session"
        if not os.path.isfile(session_file):
            return
        backup = f"{session_file}.bak-{int(time.time())}"
        try:
            os.rename(session_file, backup)
            logger.info("Sesion Telegram archivada como %s", os.path.basename(backup))
        except OSError as e:
            logger.warning("No se pudo archivar la sesion Telegram %s: %s", account.get("name"), e)

    def _delete_session(self, account):
        session_file = f"{self._session_path(account.get('session_name'))}.session"
        directory = os.path.dirname(session_file)
        basename = os.path.basename(session_file)
        try:
            paths = [os.path.join(directory, name) for name in os.listdir(directory)
                     if name == basename or name.startswith(basename + "-")
                     or name.startswith(basename + ".bak-")]
        except OSError:
            paths = [session_file]
        for path in paths:
            try:
                if os.path.isfile(path):
                    os.remove(path)
            except OSError as e:
                logger.warning("No se pudo borrar la sesion Telegram %s: %s", account.get("name"), e)

    async def reset_account(self, account_id, archive_session=False):
        account_id = int(account_id)
        client = self.clients.pop(account_id, None)
        if client:
            await client.disconnect()
        self._event_handlers.discard(account_id)
        if archive_session and account_id in self.accounts:
            self._archive_session(self.accounts[account_id])
        if account_id == self.primary_account_id:
            self.client = None
        return await self.ensure_client(account_id)

    async def get_total_messages(self, channel_id: int):
        ch_data = self.channels.get(channel_id)
        if not ch_data:
            return 0
        client = self.client_for_account(ch_data["account_id"])
        if not client:
            return 0
        try:
            msgs = await client.get_messages(ch_data["entity"], limit=1)
            if msgs:
                if hasattr(msgs, 'id'):
                    return msgs.id
                if isinstance(msgs, (list, tuple)) and len(msgs) > 0:
                    return msgs[0].id
        except Exception as e:
            logger.warning("No se pudo obtener total de mensajes del canal %d: %s", channel_id, e)
        return 0

    async def start(self):
        """Conecta cada sesión persistida. Las cuentas no autorizadas quedan
        conectadas para que el panel pueda completar su inicio de sesión.

        Antes esto llamaba a `client.start(phone=...)`, que en un contenedor
        hacia dos destrozos: pedia el codigo por consola (y moria con un EOF,
        sin sesion igualmente) y, de camino, **gastaba un envio de codigo en
        cada arranque**. Con varios redespliegues seguidos Telegram acababa
        respondiendo "all available options for this type of number were
        already used", y el hash que dejaba cacheado convertia la siguiente
        peticion del panel en un reenvio en vez de un envio nuevo.
        """
        if descarga_paralela.motor_cifrado() == "python":
            logger.warning("Telethon esta descifrando en Python puro: las descargas no pasaran "
                           "de ~1 MB/s. Falta el paquete cryptg en la imagen.")
        for account_id, account in self.accounts.items():
            try:
                client = await self.ensure_client(account_id)
                if not await client.is_user_authorized():
                    logger.warning("Sin sesion de Telegram para %s (%s); inicia sesion en administracion",
                                   account.get("name", account_id), account.get("phone", ""))
                    continue
            except Exception as e:
                logger.warning("No se pudo conectar la cuenta Telegram %s: %s",
                               account.get("name", account_id), e)
                continue
            await self._comprobar_premium(account_id)

        channels = self.channel_ids or self._config_channels
        await self.load_channels_from_list(channels)
        self.watch_new_messages()

    async def _comprobar_premium(self, account_id):
        """Sin Premium Telegram limita la velocidad de descarga de la cuenta; con
        Premium no. No hay nada que ajustar aqui, pero si las descargas van
        lentas lo primero es saber cual de las dos es."""
        account = self.accounts.get(int(account_id), {})
        client = self.clients.get(int(account_id))
        if client is None:
            return None
        try:
            yo = await client.get_me()
            premium = bool(getattr(yo, "premium", False))
        except Exception as e:
            logger.warning("No se pudo saber si la cuenta de Telegram %s es Premium: %s",
                           account.get("name", account_id), e)
            return None
        account["premium"] = premium
        if int(account_id) == self.primary_account_id:
            self.premium = premium
        logger.info("Cuenta de Telegram %s: %s | Premium: %s | Cifrado: %s | Conexiones por archivo: %d",
                    account.get("name", account_id),
                    getattr(yo, "username", None) or getattr(yo, "phone", "?"),
                    "si" if premium else "no", descarga_paralela.motor_cifrado(), self.conexiones)
        return premium

    async def add_channel(self, ch_id, name, account_id=None):
        account_id = int(account_id or self.primary_account_id)
        existing = next((ch for ch in self.channel_ids if ch["id"] == ch_id), None)
        changed = not existing or existing.get("account_id") != account_id
        if account_id not in self.accounts:
            raise ValueError(f"Cuenta Telegram {account_id} no encontrada")
        client = await self.ensure_client(account_id)
        if not await client.is_user_authorized():
            raise ValueError(f"La cuenta {self.accounts[account_id]['name']} no tiene una sesion activa")
        entity = await client.get_entity(self._resolve_channel_id(ch_id))
        if existing:
            existing.update(name=name, account_id=account_id,
                            account_name=self.accounts[account_id]["name"])
        else:
            existing = {"id": ch_id, "name": name, "account_id": account_id,
                        "account_name": self.accounts[account_id]["name"]}
            self.channel_ids.append(existing)
        self.channels[ch_id] = {"entity": entity, "name": name, "account_id": account_id,
                                 "account_name": self.accounts[account_id]["name"]}
        return changed

    async def refresh_from_db(self, db_channels: list[dict]):
        await self.load_channels_from_list(db_channels)

    def channel_name(self, ch_id: int) -> str:
        """Nombre del canal, resuelto o no. `self.channels` solo tiene los que
        Telethon ha conseguido resolver; `self.channel_ids` los tiene todos."""
        resolved = self.channels.get(ch_id, {}).get("name")
        if resolved:
            return resolved
        for ch in self.channel_ids:
            if ch["id"] == ch_id:
                return ch["name"]
        return ""

    def watch_new_messages(self, callback=None, account_id=None):
        """Llama a callback(channel_id, msg) por cada mensaje nuevo en un canal
        activo. Telethon ya mantiene la conexion abierta para las descargas,
        asi que escuchar actualizaciones no cuesta peticiones extra. El filtro
        se evalua en cada evento para que los canales añadidos despues cuenten.

        Se compara contra `channel_ids` (todos los canales activos) y no contra
        `channels` (solo los que Telethon ha resuelto): si `get_entity` fallaba
        para un canal, sus mensajes nuevos se descartaban en silencio y lo que
        se publicara ahi no se indexaba nunca.
        """
        from telethon import events

        if callback is not None:
            self._message_callback = callback
        if self._message_callback is None:
            return

        for current_id, client in self.clients.items():
            if account_id is not None and current_id != int(account_id):
                continue
            if current_id in self._event_handlers or not client.is_connected():
                continue

            async def _handler(event, telegram_account_id=current_id):
                chat_id = event.chat_id
                for ch in list(self.channel_ids):
                    ch_id = ch["id"]
                    if (ch.get("account_id") == telegram_account_id
                            and self._resolve_channel_id(ch_id) == chat_id):
                        try:
                            await self._message_callback(ch_id, event.message)
                        except Exception as e:
                            logger.warning("Error procesando mensaje nuevo del canal %d: %s", ch_id, e)
                        return

            client.add_event_handler(_handler, events.NewMessage())
            self._event_handlers.add(current_id)
            logger.info("Escuchando mensajes nuevos con cuenta Telegram %s (%d canales)",
                        self.accounts.get(current_id, {}).get("name", current_id),
                        sum(ch.get("account_id") == current_id for ch in self.channel_ids))

    async def stop(self):
        await asyncio.gather(*(client.disconnect() for client in self.clients.values()),
                             return_exceptions=True)
        self.clients.clear()
        self.client = None
        self._event_handlers.clear()

    async def login_interactive(self):
        account_id = self.primary_account_id
        client = await self.ensure_client(account_id)
        account = self.accounts[account_id]
        phone = account.get("phone") or self.phone
        if not await client.is_user_authorized():
            await client.send_code_request(phone)
            code = input("Introduce el codigo de verificacion de Telegram: ").strip()
            try:
                await client.sign_in(phone, code)
            except SessionPasswordNeededError:
                password = input("Introduce tu contrasena de verificacion en dos pasos: ").strip()
                await client.sign_in(password=password)

        for ch in self.channel_ids:
            if ch["account_id"] == account_id:
                await self.add_channel(ch["id"], ch["name"], account_id)
        self.watch_new_messages(account_id=account_id)
        print(f"Login correcto. Sesion guardada en {account['session_name']}.session")

    async def list_dialogs(self, account_id=None):
        dialogs = []
        account_ids = [int(account_id)] if account_id is not None else list(self.accounts)
        for current_id in account_ids:
            client = await self.ensure_client(current_id)
            if not await client.is_user_authorized():
                continue
            account_name = self.accounts[current_id]["name"]
            async for dialog in client.iter_dialogs():
                if dialog.is_user:
                    continue
                username = getattr(dialog.entity, "username", None)
                dialogs.append({
                    "id": dialog.id,
                    "name": dialog.name,
                    "username": username,
                    "url": f"https://t.me/{username}" if username else "",
                    "account_id": current_id,
                    "account_name": account_name,
                    "is_channel": dialog.is_channel,
                    "is_group": dialog.is_group,
                    "is_megagroup": dialog.is_channel and getattr(dialog.entity, "megagroup", False),
                })
        return dialogs

    async def search_messages(self, query, limit=50, offset_id=0, reverse=False, channel_ids=None):
        if channel_ids is None:
            channel_ids = list(self.channels.keys())

        async def _search_one(ch_id):
            ch = self.channels.get(ch_id)
            if not ch:
                return []
            client = self.client_for_account(ch["account_id"])
            if not client:
                return []
            msgs = []
            kwargs = {"search": query, "limit": limit, "reverse": reverse}
            if offset_id > 0:
                kwargs["offset_id"] = offset_id
            async for msg in client.iter_messages(ch["entity"], **kwargs):
                if msg.media:
                    size = self._get_file_size(msg)
                    if size == 0:
                        continue
                    file_name = self._get_file_name(msg)
                    if not file_name or not self._is_downloadable(file_name):
                        continue
                    msgs.append({
                        "id": msg.id,
                        "date": str(msg.date) if msg.date else "",
                        "text": (msg.text or "")[:200],
                        "file_name": file_name,
                        "size": size,
                        "size_str": self._format_size(size),
                        "channel_id": ch_id,
                        "channel_name": ch["name"],
                        "account_id": ch["account_id"],
                        "account_name": ch.get("account_name", ""),
                    })
            return msgs

        tasks = [_search_one(cid) for cid in channel_ids]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        merged = []
        for r in results:
            if isinstance(r, list):
                merged.extend(r)
        merged.sort(key=lambda x: x["id"], reverse=not reverse)
        return merged[:limit]

    async def get_message(self, message_id, channel_id=None):
        if channel_id is not None and channel_id not in self.channels:
            return None
        channel_ids = [channel_id] if channel_id is not None else list(self.channels)
        for ch_id in channel_ids:
            ch = self.channels[ch_id]
            client = self.client_for_account(ch["account_id"])
            if not client:
                continue
            try:
                msg = await client.get_messages(ch["entity"], ids=message_id)
                if msg:
                    size = self._get_file_size(msg)
                    return {
                        "id": msg.id,
                        "date": str(msg.date) if msg.date else "",
                        "text": (msg.text or "")[:200],
                        "file_name": self._get_file_name(msg),
                        "size": size,
                        "size_str": self._format_size(size),
                        "channel_id": ch_id,
                        "channel_name": ch["name"],
                        "account_id": ch["account_id"],
                        "account_name": ch.get("account_name", ""),
                    }
            except Exception:
                continue
        return None

    async def find_related_parts(self, message_id, channel_id=None):
        msg = None
        ch_data = None
        initial_channel_id = None
        if channel_id is not None and channel_id not in self.channels:
            return None, None, []
        candidate_channels = [channel_id] if channel_id is not None else list(self.channels)
        for ch_id in candidate_channels:
            ch = self.channels[ch_id]
            client = self.client_for_account(ch["account_id"])
            if not client:
                continue
            try:
                msg = await client.get_messages(ch["entity"], ids=message_id)
                if msg:
                    ch_data = ch
                    initial_channel_id = ch_id
                    break
            except Exception:
                continue
        if not msg:
            return None, None, []

        file_name = self._get_file_name(msg)
        if not file_name:
            return None, None, []

        base, part_num, archive_type = _detect_multipart(file_name)
        own_channel = initial_channel_id
        own_account = ch_data["account_id"]

        if base is None:
            folder_name = suggest_folder_name(file_name)
            return file_name, folder_name, [{
                "message_id": message_id,
                "file_name": file_name,
                "part_num": 0,
                "size": self._get_file_size(msg),
                "channel_id": own_channel,
                "account_id": own_account,
            }]

        parts_found = {}
        size = self._get_file_size(msg)
        parts_found[part_num] = {
            "message_id": message_id,
            "file_name": file_name,
            "part_num": part_num,
            "size": size,
            "channel_id": own_channel,
            "account_id": own_account,
        }

        base_lower = base.lower()

        # Las demas partes, primero en el catalogo. Antes se buscaban siempre
        # en Telegram, una busqueda por canal y una detras de otra: Telegram
        # raciona mucho las busquedas y, al pasarse, Telethon se duerme en
        # silencio hasta un minuto por llamada (FloodWait). El boton de
        # "Descargar" se quedaba sin contestar y parecia que no hacia nada.
        for r in await _partes_en_catalogo(own_channel, base):
            b, p, a = _detect_multipart(r["file_name"])
            if b and b.lower() == base_lower and p not in parts_found:
                parts_found[p] = {
                    "message_id": r["message_id"],
                    "file_name": r["file_name"],
                    "part_num": p,
                    "size": r["file_size"] or 0,
                    "channel_id": r["channel_id"],
                    "account_id": self.channels.get(r["channel_id"], {}).get("account_id"),
                }
        if len(parts_found) > 1:
            parts = [parts_found[p] for p in sorted(parts_found.keys())]
            return base, suggest_folder_name(base), parts

        # Si el catalogo no las tiene (canal sin indexar todavia), Telegram;
        # con tiempo limitado por canal para no colgar la peticion.
        for ch_id, ch in self.channels.items():
            client = self.client_for_account(ch["account_id"])
            if not client:
                continue
            try:
                await asyncio.wait_for(self._buscar_partes(client, ch_id, ch, base, base_lower,
                                                           initial_channel_id, message_id, parts_found),
                                       timeout=BUSQUEDA_PARTES_S)
            except asyncio.TimeoutError:
                logger.warning("Buscando partes de %s: el canal %s no contesta a tiempo", base, ch.get("name", ch_id))
            except Exception:
                continue

        parts = [parts_found[p] for p in sorted(parts_found.keys())]
        folder_name = suggest_folder_name(base)
        return base, folder_name, parts

    async def _buscar_partes(self, client, ch_id, ch, base, base_lower, initial_channel_id, message_id,
                             parts_found):
        async for m in client.iter_messages(ch["entity"], search=base, limit=100):
            fname = self._get_file_name(m)
            if not fname or (ch_id == initial_channel_id and m.id == message_id):
                continue
            b, p, a = _detect_multipart(fname)
            if b and b.lower() == base_lower and p not in parts_found:
                parts_found[p] = {
                    "message_id": m.id,
                    "file_name": fname,
                    "part_num": p,
                    "size": self._get_file_size(m),
                    "channel_id": ch_id,
                    "account_id": ch["account_id"],
                }

    async def download_to_folder(self, message_id, folder, progress_callback=None, channel_id=None,
                                 account_id=None):
        msg = None
        client = None
        if channel_id is not None and channel_id not in self.channels:
            raise ValueError("El canal del mensaje no esta resuelto")
        if channel_id is not None:
            ch = self.channels[channel_id]
            if account_id is None or ch["account_id"] == int(account_id):
                client = self.client_for_account(ch["account_id"])
                if client:
                    msg = await client.get_messages(ch["entity"], ids=message_id)
        elif not msg:
            for ch_id, ch in self.channels.items():
                if account_id is not None and ch["account_id"] != int(account_id):
                    continue
                candidate = self.client_for_account(ch["account_id"])
                if not candidate:
                    continue
                try:
                    msg = await candidate.get_messages(ch["entity"], ids=message_id)
                    if msg and msg.media:
                        client = candidate
                        break
                except Exception:
                    continue
        if not msg or not msg.media:
            raise ValueError("El mensaje no tiene archivo adjunto")
        if client is None:
            raise ValueError("No hay una sesion Telegram asociada al canal")

        file_name = self._get_file_name(msg) or f"file_{message_id}"
        file_path = os.path.join(folder, file_name)
        # Ya bajado solo si mide lo que dice Telegram. Bastaba con que pesara
        # algo, y un archivo a medias tambien pesa: una segunda descarga del
        # mismo lote encontraba el que la primera estaba escribiendo, lo daba
        # por hecho y a los tres segundos dejaba en la biblioteca una pelicula
        # cortada.
        esperado = self._get_file_size(msg)
        if os.path.isfile(file_path):
            if esperado and os.path.getsize(file_path) == esperado:
                return file_path
            os.remove(file_path)

        os.makedirs(folder, exist_ok=True)

        def progress(current, total):
            if progress_callback:
                progress_callback(message_id, current, total)

        # Se baja a `<nombre>.parcial` y solo se renombra al acabar: en paralelo
        # el ultimo trozo puede escribirse antes que los del medio, y entonces
        # el archivo mide lo que tiene que medir sin estar entero. Con el
        # nombre final solo existe lo que esta completo.
        parcial = file_path + SUFIJO_PARCIAL
        if self.conexiones > 1:
            try:
                await descarga_paralela.bajar(client, msg, parcial, self.conexiones, progress)
                os.replace(parcial, file_path)
                return file_path
            except descarga_paralela.SinParalelo as e:
                logger.warning("Descarga en paralelo no disponible para %s (%s): se baja de la forma normal",
                               file_name, e)

        await client.download_media(msg, file=parcial, progress_callback=progress)
        os.replace(parcial, file_path)
        return file_path

    def _get_file_name(self, msg):
        if hasattr(msg, "file") and msg.file and msg.file.name:
            return msg.file.name
        if hasattr(msg, "document") and msg.document:
            for attr in msg.document.attributes:
                if hasattr(attr, "file_name") and attr.file_name:
                    return attr.file_name
        return None

    def _get_file_size(self, msg):
        if hasattr(msg, "document") and msg.document:
            return getattr(msg.document, "size", 0)
        if hasattr(msg, "file") and msg.file:
            return getattr(msg.file, "size", 0)
        return 0

    @staticmethod
    def _is_downloadable(file_name):
        f = file_name.lower()
        if f.endswith(('.mkv', '.mp4', '.avi', '.mov', '.wmv', '.flv', '.webm', '.m4v', '.ts',
                       '.rar', '.zip', '.7z',
                       '.tar.gz', '.tar.bz2', '.tar', '.tgz', '.tbz2')):
            return True
        if '.part' in f and f.endswith('.rar'):
            return True
        if re.search(r'\.r\d{2,}$', f):
            return True
        if re.search(r'\.\d{3,}$', f):
            return True
        return False

    @staticmethod
    def _format_size(bytes_val):
        if bytes_val is None or bytes_val == 0:
            return "0 B"
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if bytes_val < 1024:
                return f"{bytes_val:.1f} {unit}"
            bytes_val /= 1024
        return f"{bytes_val:.1f} PB"
