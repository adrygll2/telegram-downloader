"""Comprueba que "Ver" sobrevive a un mensaje borrado del canal.

Existe por un fallo concreto: el canal borro y volvio a subir los episodios de
VisionQuest, el catalogo seguia teniendo los mensajes borrados y la ficha
ofrecia justo esos. "Ver" acababa en "una de las partes ya no esta en el
canal" —guardado ademas para siempre en `reproduccion`— con otras dos copias
buenas del mismo episodio al lado.

Telegram y la base de datos son de mentira: lo que se prueba es la decision.
"""
import asyncio
import sys
import types

from app.services import reproduccion as rp

fallos = 0


def comprobar(nombre, ok):
    global fallos
    print(("  ok  " if ok else "  MAL ") + nombre)
    fallos += not ok


# --- Telegram de mentira -------------------------------------------------------

class Doc:
    def __init__(self, size):
        self.size = size
        self.attributes = []


class Msg:
    def __init__(self, mid, size=100):
        self.id = mid
        self.media = types.SimpleNamespace(document=Doc(size))


VIVOS = {46342: Msg(46342), 46658: Msg(46658)}   # 46341 lo borro el canal


class Cliente:
    def is_connected(self):
        return True

    async def get_messages(self, _entidad, ids):
        return [VIVOS.get(i) for i in ids]


class Downloader:
    channels = {1: {"entity": object()}}

    def any_connected(self):
        return True

    def client_for_channel(self, _ch):
        return Cliente()


rp._downloader = lambda: Downloader()

# --- Catalogo de mentira ---------------------------------------------------------

CATALOGO = {(1, 46341): "VisionQuest 1x01.mkv", (1, 46342): "VisionQuest 1x01.mkv",
            (1, 46658): "VisionQuest 1x01.mkv"}
GUARDADO = {(1, 46341): {"v": rp.VERSION, "modo": "descargar", "path": None, "pistas": None,
                         "motivo": "una de las partes ya no esta en el canal"}}


async def nombre(ch, mid):
    return CATALOGO.get((ch, mid))


async def partes(ch, mid, fn):
    return [(0, mid, fn)]


async def pistas(path):
    return {"ok": True, "container": "matroska"}


async def leer_guardado(ch, mid):
    return GUARDADO.get((ch, mid))


async def guardar(ch, mid, r):
    GUARDADO[(ch, mid)] = r


async def otras_copias(ch, mid, limite=6):
    if (ch, mid) not in CATALOGO:
        return []
    return [k for k, v in CATALOGO.items() if k != (ch, mid) and v == CATALOGO[(ch, mid)]]


async def olvidar_mensajes(ch, mids):
    n = 0
    for m in mids:
        n += CATALOGO.pop((ch, m), None) is not None
        GUARDADO.pop((ch, m), None)
    return n


rp._nombre_en_catalogo = nombre
rp._partes = partes
rp._pistas = pistas
sys.modules["app.database.reproduccion"] = types.SimpleNamespace(leer=leer_guardado, guardar=guardar)
sys.modules["app.database.media"] = types.SimpleNamespace(otras_copias=otras_copias,
                                                          olvidar_mensajes=olvidar_mensajes)
sys.modules["app.services.cache"] = types.SimpleNamespace(cambio_en_catalogo=lambda: None)
import app.services  # noqa: E402
app.services.cache = sys.modules["app.services.cache"]
sys.modules["app.services.hls"] = types.SimpleNamespace(se_puede=lambda p: False)
app.services.hls = sys.modules["app.services.hls"]


async def main():
    print("Ver un episodio cuyo mensaje se borro (y estaba guardado como 'descargar'):")
    r = await rp.comprobar(1, 46341)
    comprobar("se sirve otra copia", r["modo"] == "ver" and r["path"] in ("tg:1:46342", "tg:1:46658"))
    comprobar("el borrado sale del catalogo", (1, 46341) not in CATALOGO)
    comprobar("y de lo guardado", (1, 46341) not in GUARDADO)
    comprobar("las copias buenas siguen", (1, 46342) in CATALOGO and (1, 46658) in CATALOGO)

    print("\nUn borrado sin ninguna otra copia:")
    CATALOGO[(1, 999)] = "Otra cosa 1x01.mkv"
    r = await rp.comprobar(1, 999)
    comprobar("se dice que hay que descargar", r["modo"] == "descargar" and "ya no esta en el canal" in r["motivo"])
    comprobar("y no se guarda", (1, 999) not in GUARDADO)

    print("\nUno guardado como 'ver' que el canal borra despues:")
    r = await rp.comprobar(1, 46342)
    comprobar("primero se ve", r["modo"] == "ver" and r["path"] == "tg:1:46342")
    del VIVOS[46342]
    rp._virtuales.clear()
    r = await rp.comprobar(1, 46342)
    comprobar("despues se sirve la otra copia", r["modo"] == "ver" and r["path"] == "tg:1:46658")
    comprobar("y el borrado sale del catalogo", (1, 46342) not in CATALOGO)

    print("\nEl reproductor ya tenia la ruta del borrado (stream directo):")
    CATALOGO[(1, 46343)] = "VisionQuest 1x01.mkv"   # otra subida que tambien se borra
    v = await rp.virtual_o_copia(1, 46343)
    comprobar("lee de la copia buena", v.clave == (1, 46658))
    VIVOS[46342] = Msg(46342)

    print("\nborrado_en_telegram:")
    comprobar("un borrado es borrado", await rp.borrado_en_telegram(1, 46341))
    comprobar("uno que existe no", not await rp.borrado_en_telegram(1, 46342))

    async def roto(*_a, **_k):
        raise ConnectionError("sin red")
    Cliente.get_messages = roto
    comprobar("sin red no se da nada por borrado", not await rp.borrado_en_telegram(1, 46342))


asyncio.run(main())
print("\nTodo bien" if not fallos else f"\n{fallos} fallos")
sys.exit(1 if fallos else 0)
