"""Comprueba que los canales se resuelven y descargan con su sesion asignada."""
import asyncio
import os
import tempfile
from types import SimpleNamespace

from app.services import telegram_client as modulo


class Mensaje:
    def __init__(self, message_id, name):
        self.id = message_id
        self.date = None
        self.text = name
        self.media = True
        self.file = SimpleNamespace(name=name, size=5)
        self.document = SimpleNamespace(size=5, attributes=[])


class ClienteFalso:
    def __init__(self, session, api_id, api_hash):
        self.session = session
        self.account = os.path.basename(session)
        self.connected = False
        self.handlers = []
        self.downloaded = []

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.connected = False

    def is_connected(self):
        return self.connected

    async def is_user_authorized(self):
        return True

    async def get_entity(self, entity_id):
        return SimpleNamespace(id=entity_id, entity_account=self.account)

    async def get_messages(self, entity, ids):
        return Mensaje(ids, f"{self.account}.mkv")

    async def iter_messages(self, entity, **kwargs):
        # Los IDs de mensaje se repiten entre canales; el channel_id debe
        # determinar qué cliente se usa para cada resultado.
        yield Mensaje(100, f"{self.account}.mkv")

    def add_event_handler(self, handler, event):
        self.handlers.append(handler)

    async def download_media(self, message, file, progress_callback=None):
        self.downloaded.append(message.id)
        with open(file, "wb") as output:
            output.write(b"video")
        if progress_callback:
            progress_callback(5, 5)
        return file


async def comprobar():
    real_client = modulo.TelegramClient
    modulo.TelegramClient = ClienteFalso
    try:
        with tempfile.TemporaryDirectory() as temporal:
            config = {
                "api_id": 123,
                "api_hash": "test",
                "phone": "+10000000000",
                "download_path": temporal,
                "telegram_accounts": [
                    {"id": 7, "name": "Principal", "phone": "+10000000000",
                     "session_name": "user", "is_primary": True, "active": True},
                    {"id": 8, "name": "Secundaria", "phone": "+20000000000",
                     "session_name": "secondary", "is_primary": False, "active": True},
                ],
                "channels": [
                    {"id": -1007001, "name": "Canal principal", "account_id": 7},
                    {"id": -1008001, "name": "Canal secundario", "account_id": 8},
                ],
            }
            downloader = modulo.TelegramDownloader(config)
            downloader.session_dir = os.path.join(temporal, "session")
            await downloader.load_channels_from_list(config["channels"])
            await downloader.start()

            principal = downloader.client_for_channel(-1007001)
            secundaria = downloader.client_for_channel(-1008001)
            assert principal is downloader.clients[7]
            assert secundaria is downloader.clients[8]
            assert principal is not secundaria
            assert principal.session.endswith("user")
            assert secundaria.session.endswith("secondary")

            results = await downloader.search_messages("video", channel_ids=[-1007001, -1008001])
            assert {(result["id"], result["channel_id"], result["account_id"]) for result in results} == {
                (100, -1007001, 7), (100, -1008001, 8),
            }

            target = os.path.join(temporal, "download")
            await downloader.download_to_folder(100, target, channel_id=-1008001, account_id=8)
            assert secundaria.downloaded == [100]
            assert principal.downloaded == []
            await downloader.stop()
    finally:
        modulo.TelegramClient = real_client


asyncio.run(comprobar())
print("Multi-cuenta: entidades, búsqueda y descarga usan la sesión asignada")
