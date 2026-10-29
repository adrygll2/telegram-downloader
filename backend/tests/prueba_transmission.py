"""Prueba el enlace de torrents completados a la biblioteca, sin Transmission real."""
import os
import tempfile
from types import SimpleNamespace

from app.services.transmission_client import TransmissionManager


class TransmissionFalsa:
    def __init__(self, torrents, files):
        self.torrents = torrents
        self.files = files

    def get_session(self):
        return True

    def get_torrents(self):
        return self.torrents

    def get_torrent_files(self, torrent_id):
        return self.files[torrent_id]


with tempfile.TemporaryDirectory() as temporal:
    descargas = os.path.join(temporal, "descargas")
    biblioteca = os.path.join(temporal, "peliculas")
    os.makedirs(descargas)
    os.makedirs(biblioteca)

    torrents = []
    files = {}
    for torrent_id, quality in ((1, "1080p"), (2, "2160p")):
        filename = f"Dune.2021.{quality}.BluRay.mkv"
        source = os.path.join(descargas, filename)
        with open(source, "wb") as stream:
            stream.write(quality.encode("ascii"))
        torrents.append(SimpleNamespace(
            id=torrent_id,
            name=f"[Release] Dune (2021) {quality} BluRay",
            progress=100,
            download_dir=descargas,
        ))
        files[torrent_id] = [SimpleNamespace(name=filename)]

    manager = TransmissionManager({
        "transmission_download_dir": descargas,
        "extract_path": biblioteca,
    })
    manager.client = TransmissionFalsa(torrents, files)
    manager.organize_completed()

    carpeta = os.path.join(biblioteca, "Dune (2021)")
    resultados = sorted(os.listdir(carpeta))
    esperados = ["Dune (2021) - 1080p BluRay.mkv", "Dune (2021) - 2160p BluRay.mkv"]
    if resultados != esperados:
        raise AssertionError(f"Versiones organizadas incorrectamente: {resultados!r}")
    if not all(os.path.isfile(os.path.join(descargas, files[t.id][0].name)) for t in torrents):
        raise AssertionError("Organizar la biblioteca no debe quitar los datos que Transmission esta sembrando")

print("Transmission: conserva 1080p/2160p en biblioteca y deja los torrents sembrando")
