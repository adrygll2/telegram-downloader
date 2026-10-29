import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

AT_RSS = "https://feed.animetosho.org/rss2"
NS = {"torrent": "http://xmlns.ezrss.it/0.9/"}
_SPANISH_SUB_PATTERNS = (
    r"\bsub(?:titles|s)?\s*:.*?(?:spanish|español|espanol|spa\b|castellano)",
    r"\b(?:spanish|español|espanol|spa|castellano)\b.*?\bsub(?:titles?)?\b",
    r"\[multi[\s-]?sub\]",
    r"\[multi[\s-]?audio[\s-]?sub\]",
)


def _has_spanish_subs(title):
    return any(re.search(pattern, title, re.IGNORECASE) for pattern in _SPANISH_SUB_PATTERNS)


def _find_text(item, tag, default):
    element = item.find(tag, NS)
    return element.text if element is not None and element.text else default


def search_animetosho(query, page=1, spanish_subs_only=False):
    params = urllib.parse.urlencode({"q": query, "only_tor": "1", "p": page})
    try:
        with urllib.request.urlopen(f"{AT_RSS}?{params}", timeout=15) as response:
            tree = ET.parse(response)
    except Exception:
        return []

    results = []
    for item in tree.findall(".//item"):
        title = item.findtext("title", "") or ""
        if spanish_subs_only and not _has_spanish_subs(title):
            continue
        magnet_el = item.find("{http://xmlns.ezrss.it/0.9/magnetURI}")
        magnet = magnet_el.text if magnet_el is not None and magnet_el.text else ""
        if not magnet:
            enclosure = item.find("enclosure")
            magnet = enclosure.get("url", "") if enclosure is not None else ""
        if not magnet:
            continue
        try:
            seeders = int(_find_text(item, "torrent:seeds", "0") or "0")
            leechers = int(_find_text(item, "torrent:peers", "0") or "0")
        except ValueError:
            seeders, leechers = 0, 0
        results.append({
            "title": title,
            "guid": item.findtext("guid", "") or "",
            "link": item.findtext("link", "") or "",
            "magnet": magnet,
            "seeders": seeders,
            "leechers": leechers,
            "size": _find_text(item, "torrent:contentLength", "0 B") or "0 B",
            "pub_date": item.findtext("pubDate", "") or "",
        })
    return results
