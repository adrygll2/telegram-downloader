import re
import urllib.parse
import urllib.request
from html.parser import HTMLParser

NYAA_URL = "https://nyaa.si/"
_SPANISH_SUB_PATTERNS = (
    r"\bsub(?:titles|s)?\s*:.*?(?:spanish|español|espanol|spa\b|castellano)",
    r"\b(?:spanish|español|espanol|spa|castellano)\b.*?\bsub(?:titles?)?\b",
    r"\[multi[\s-]?sub\]",
)


class NyaaParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.results = []
        self._row = {}
        self._in_row = False
        self._td_idx = 0
        self._text = ""
        self._skip_colspan = 0

    def handle_starttag(self, tag, attrs):
        ad = dict(attrs)
        if tag == "tr" and any(x in ad.get("class", "") for x in ("default", "success", "danger", "warning")):
            self._in_row, self._row, self._td_idx, self._skip_colspan = True, {}, 0, 0
        if not self._in_row:
            return
        if tag == "td":
            if self._skip_colspan:
                self._skip_colspan -= 1
                return
            colspan = int(ad.get("colspan", 1))
            self._skip_colspan = max(0, colspan - 1)
            self._td_idx += 1
            self._text = ""
        if tag == "a":
            href = ad.get("href", "")
            if self._td_idx == 2 and "magnet" in href:
                self._row["magnet"] = href
            elif self._td_idx == 2 and href.startswith("/view/"):
                self._row["guid"] = urllib.parse.urljoin(NYAA_URL, href)
            elif self._td_idx == 3:
                if href.startswith("/download/"):
                    self._row["link"] = urllib.parse.urljoin(NYAA_URL, href)
                if "magnet" in href:
                    self._row["magnet"] = href

    def handle_data(self, data):
        if self._in_row:
            self._text += data.strip()

    def handle_endtag(self, tag):
        if tag == "td" and self._in_row and self._skip_colspan == 0:
            text = self._text.strip()
            fields = {2: "title", 4: "size", 5: "pub_date", 6: "seeders", 7: "leechers"}
            field = fields.get(self._td_idx)
            if field:
                if field in ("seeders", "leechers"):
                    try:
                        self._row[field] = int(text)
                    except ValueError:
                        self._row[field] = 0
                else:
                    self._row[field] = text
        elif tag == "tr" and self._in_row:
            if self._row.get("title"):
                for key, default in (("size", "0 B"), ("pub_date", ""), ("seeders", 0),
                                     ("leechers", 0), ("magnet", ""), ("guid", ""), ("link", "")):
                    self._row.setdefault(key, default)
                self.results.append(self._row)
            self._in_row = False


def _has_spanish_subs(title):
    return any(re.search(pattern, title, re.IGNORECASE) for pattern in _SPANISH_SUB_PATTERNS)


def search_nyaa(query, page=1, category="1_2", spanish_subs_only=False):
    params = urllib.parse.urlencode({"q": query, "c": category, "f": 0, "p": page})
    try:
        with urllib.request.urlopen(f"{NYAA_URL}?{params}", timeout=15) as response:
            html = response.read().decode("utf-8", errors="replace")
    except Exception:
        return []
    parser = NyaaParser()
    parser.feed(html)
    if spanish_subs_only:
        return [item for item in parser.results if _has_spanish_subs(item.get("title", ""))]
    return parser.results
