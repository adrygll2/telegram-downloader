import os
import re
import shutil
import threading

import transmission_rpc

from app.services.storage import format_size
from app.services.layout import etiqueta_calidad, ruta_libre, sanitize

VIDEO_EXTENSIONS = (".mkv", ".mp4", ".avi", ".ts", ".mov", ".webm", ".m4v")
_TORRENT_NOISE = (
    re.compile(r"\[[^\]]*\]"),
    re.compile(r"\b\d{3,4}p\b", re.IGNORECASE),
    re.compile(r"\b(?:WEB-DL|WEBRip|BDRip|BluRay|AMZN|NF|CR|HDTV|BILI)\b", re.IGNORECASE),
    re.compile(r"\b(?:HEVC|AVC|H[. ]?26[45]|x26[45]|10bit|8bit)\b", re.IGNORECASE),
    re.compile(r"\b(?:AAC|AC3|EAC3?|FLAC|OPUS|MP3|DTS)\b", re.IGNORECASE),
)


def _clean_title(title):
    name = title
    for pattern in _TORRENT_NOISE:
        name = pattern.sub(" ", name)
    name = re.sub(r"\s+", " ", name).strip(" .-_")
    return name or "Descarga"


def _rename_file(original_name, clean_title):
    extension = os.path.splitext(original_name)[1] or ".mkv"
    episode = re.search(r"[Ss](\d{1,2})[Ee](\d{1,3})", clean_title)
    if episode:
        return f"S{int(episode.group(1)):02d}E{int(episode.group(2)):03d}{extension}"
    episode = re.search(r"\b(\d{2,3})\b", original_name)
    if episode:
        return f"E{episode.group(1)}{extension}"
    return f"{clean_title}{extension}"


class TransmissionManager:
    """Administra Transmission y publica sus videos en la biblioteca."""

    def __init__(self, config):
        self.host = config.get("transmission_host", "host.docker.internal")
        self.port = config.get("transmission_port", 9091)
        self.user = config.get("transmission_user", "")
        self.password = config.get("transmission_pass", "")
        self.download_dir = config.get("transmission_download_dir", config.get("download_path", "/app/downloads"))
        self.media_dir = config.get("extract_path", "/app/movies")
        self.client = None
        self._connect_lock = threading.Lock()

    def connect(self):
        with self._connect_lock:
            if self.client:
                try:
                    self.client.get_session()
                    return
                except Exception:
                    self.client = None
            kwargs = {"host": self.host, "port": self.port, "timeout": 10}
            if self.user:
                kwargs.update(username=self.user, password=self.password)
            self.client = transmission_rpc.Client(**kwargs)

    def _client(self):
        self.connect()
        return self.client

    def add_magnet(self, magnet):
        torrent = self._client().add_torrent(magnet, download_dir=self.download_dir)
        return torrent.id

    def get_torrents(self):
        torrents = self._client().get_torrents()
        return [{
            "id": item.id,
            "name": item.name,
            "progress": round(item.progress, 1),
            "status": item.status,
            "size": item.total_size,
            "size_str": format_size(item.total_size),
            "done": item.progress >= 100,
            "eta": str(item.eta) if getattr(item, "eta", None) else "",
            "ratio": round(getattr(item, "ratio", 0) or 0, 2),
            "peers_connected": getattr(item, "peers_connected", 0) or 0,
            "added": str(getattr(item, "date_added", getattr(item, "added_date", "")) or ""),
        } for item in torrents]

    def pause(self, torrent_id):
        self._client().stop_torrent(torrent_id)

    def resume(self, torrent_id):
        self._client().start_torrent(torrent_id)

    def remove(self, torrent_id, delete_data=True):
        self._client().remove_torrent(torrent_id, delete_data=delete_data)

    def organize_completed(self):
        client = self._client()
        os.makedirs(self.media_dir, exist_ok=True)
        for torrent in client.get_torrents():
            if torrent.progress < 100:
                continue
            try:
                files = client.get_torrent_files(torrent.id)
            except Exception:
                continue
            candidates = [f for f in files if f.name.lower().endswith(VIDEO_EXTENSIONS)]
            if not candidates:
                continue
            file_info = candidates[0]
            source_root = os.path.realpath(getattr(torrent, "download_dir", None) or self.download_dir)
            source = os.path.realpath(os.path.join(source_root, file_info.name))
            if not source.startswith(source_root + os.sep) or not os.path.isfile(source):
                continue

            clean = _clean_title(torrent.name)
            episode = re.search(r"(?:\d{1,2}x\d{2,3}|[Ss]\d{1,2}[Ee]\d{1,3})", clean)
            folder_title = clean[:episode.start()].strip(" .-_") if episode else clean
            folder_title = folder_title or clean
            target_dir = os.path.join(self.media_dir, sanitize(folder_title))
            os.makedirs(target_dir, exist_ok=True)
            if episode:
                stem = _rename_file(os.path.basename(file_info.name), clean)
                stem = os.path.splitext(stem)[0]
            else:
                quality = etiqueta_calidad(os.path.basename(file_info.name)) or etiqueta_calidad(torrent.name)
                stem = f"{clean} - {quality}" if quality else clean
            extension = os.path.splitext(file_info.name)[1] or ".mkv"
            target = ruta_libre(target_dir, sanitize(stem), extension)
            try:
                os.link(source, target)
            except OSError:
                # Preserve Transmission's source so completed torrents can seed.
                shutil.copy2(source, target)
