import re
import urllib.parse
import urllib.request
from html.parser import HTMLParser

LEETX_URL = "https://1337x.to"


class LeetxParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.results = []
        self._row = {}
        self._in_row = False
        self._td_idx = 0
        self._text = ""

    def handle_starttag(self, tag, attrs):
        ad = dict(attrs)
        if tag == "tr":
            self._in_row, self._row, self._td_idx = True, {}, 0
        if not self._in_row:
            return
        if tag == "td":
            self._td_idx += 1
            self._text = ""
        if tag == "a" and self._td_idx == 1:
            href = ad.get("href", "")
            match = re.search(r"/torrent/(\d+)/", href)
            if href.startswith("/torrent/") and match:
                self._row["link"] = urllib.parse.urljoin(LEETX_URL, href)
                self._row["torrent_id"] = int(match.group(1))

    def handle_data(self, data):
        if self._in_row:
            self._text += data.strip()

    def handle_endtag(self, tag):
        if self._in_row and tag == "td":
            text = self._text.strip()
            field = {1: "title", 2: "seeders", 3: "leechers", 4: "pub_date", 5: "size"}.get(self._td_idx)
            if field:
                if field in ("seeders", "leechers"):
                    try:
                        self._row[field] = int(text)
                    except ValueError:
                        self._row[field] = 0
                else:
                    self._row[field] = text
        elif self._in_row and tag == "tr":
            if self._row.get("title") and self._row.get("torrent_id"):
                for key, default in (("seeders", 0), ("leechers", 0), ("size", "0 B"), ("pub_date", "")):
                    self._row.setdefault(key, default)
                self.results.append(self._row)
            self._in_row = False


class MagnetParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.magnet = ""

    def handle_starttag(self, tag, attrs):
        href = dict(attrs).get("href", "")
        if tag == "a" and href.startswith("magnet:"):
            self.magnet = href


def search_leetx(query, page=1):
    url = f"{LEETX_URL}/search/{urllib.parse.quote(query)}/{page}/"
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            html = response.read().decode("utf-8", errors="replace")
    except Exception:
        return []
    parser = LeetxParser()
    parser.feed(html)
    return parser.results


def get_magnet(torrent_url):
    request = urllib.request.Request(torrent_url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            html = response.read().decode("utf-8", errors="replace")
    except Exception:
        return ""
    parser = MagnetParser()
    parser.feed(html)
    return parser.magnet
