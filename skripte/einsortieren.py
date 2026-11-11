"""Umgewandelte Discs halbautomatisch in getrennte Jellyfin-Bibliotheken einsortieren.

Zweck:
  1. vorschlagen: je Titel einer Disc in 2-fertig/ (Status "umgewandelt") eine Zuordnung
     vorschlagen und in disc.json unter titel[].zuordnung ablegen (bestaetigt: false):
       folge     Serie + Staffel/Folge, über Sendedatum/-zeit/Sender aus den Rekorderdaten
                 und dem Episodenführer von fernsehserien.de (Erstausstrahlung + Sendetermine)
       film      Titel + Jahr (Name ist keine bekannte Serie, Dauer über 75 min)
       aufnahme  nur Sendung (+ Datum) bekannt, Rückfall
     Dazu je Disc pruefen.html (Prüfseite, ohne Server zu öffnen) und zuordnung.csv
     (Semikolon, UTF-8 mit BOM). In der CSV korrigieren und Spalte "ok" auf "x" setzen.
     art "weg" = Titel nicht einsortieren (MKV bleibt liegen).
  2. anwenden: zuordnung.csv lesen (nur Zeilen mit ok), Trockenlauf ist Standard.
     Mit --ausfuehren MKV nach DVD_ZIEL verschieben (kopieren, Größe prüfen, Quelle löschen):
       Serien/<Serie>/Season NN/<Serie> SNNENN.mkv
       Filme/<Titel> (<Jahr>)/<Titel> (<Jahr>).mkv
       Fernsehaufnahmen/<Sendung>/Season <Jahr>/<Sendung> - <JJJJ-MM-TT>.mkv + NFO
     Vorhandene Dateien werden nie überschrieben, sondern als Konflikt gemeldet.
     Sind alle Titel erledigt, Status "einsortiert". Der Disc-Ordner bleibt (disc.json, Vorschau).
  3. auto: vorschlagen und, wenn alle Titel Konfidenz "hoch" haben (Folge oder Film), ohne
     Rückfrage anwenden. Bei gleicher Folge bleibt die längste Aufnahme, die anderen werden "weg".
     Sonst bleibt die Disc zum Prüfen liegen (Hinweis in fortschritt.csv). umwandeln.py ruft das
     nach jeder Disc auf. Nur, wenn noch keine zuordnung.csv existiert.
  4. liste: Discs mit Status "umgewandelt" und Stand der Zuordnung.

Abrufe bei fernsehserien.de: robots.txt sperrt nur GPTBot (geprüft 2026-11-04). Laut Impressum
sind die Inhalte nur zum privaten Gebrauch bestimmt, also nur für die eigene Sammlung nutzen.
Höflich: generischer User-Agent, mindestens 3 s Pause zwischen Abrufen, Cache 30 Tage unter
<DVD_ARBEIT>/cache/. Es werden nur Suche, Episodenführer und einzelne Folgenseiten abgerufen.

Aufruf:
  python -I skripte/einsortieren.py liste
  python -I skripte/einsortieren.py vorschlagen            (alle umgewandelten)
  python -I skripte/einsortieren.py vorschlagen D0001 D0002 [--neu]
  python -I skripte/einsortieren.py anwenden D0001         (Trockenlauf)
  python -I skripte/einsortieren.py anwenden D0001 --ausfuehren
  python -I skripte/einsortieren.py auto D0003

Umgebungsvariablen:
  DVD_ARBEIT     Arbeitsordner (Standard: <repo>/ausgabe/dvd), siehe gemeinsam.py
  DVD_ZIEL       Wurzel der Jellyfin-Bibliotheken, z.B. \\\\nas\\medien\\dvd (für "anwenden" nötig)
  TMDB_API_KEY   optional: TMDB-Schlüssel (v3-Key oder v4-Token). Ohne ihn kein TMDB-Abgleich.

Nur Standardbibliothek.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import html
import json
import os
import re
import shutil
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # nötig bei python -I
import gemeinsam as g  # noqa: E402

FS = "https://www.fernsehserien.de"
TMDB = "https://api.themoviedb.org/3"
UA = "Mozilla/5.0 (compatible; dvd-einsortieren/1.0)"
PAUSE_S = 3.0
CACHE_TAGE = 30
CACHE = g.ARBEIT / "cache"
FILM_MIN_S = 75 * 60
FENSTER_TAGE = 14        # Folgen mit Erstausstrahlung bis so viele Tage vor der Aufnahme prüfen
MAX_FOLGENSEITEN = 10    # höchstens so viele Folgenseiten je Titel abrufen
ZEIT_TOLERANZ_MIN = 20
# Wochenserien ohne Folgennummer bei fernsehserien.de (z.B. Maus vor 2012): neue Folge jeden Sonntag,
# Wiederholung Montag (KiKA) und Samstag (Dritte). TMDB und fernsehserien.de zählen die Folgen als
# n-ten Sonntag im Jahr, Staffel = Jahr - Startjahr + 1 (geprüft 2011-2013, siehe README).
SONNTAGSSERIEN = {"diesendungmitdermaus": 1971}
CSV_SPALTEN = ("nr","art", "sendung", "staffel", "folge", "titel", "jahr", "sendedatum", "ok")
ARTEN = ("folge", "film", "aufnahme", "weg")
NFO_MARKE = "<!-- einsortieren.py -->"

_letzter_abruf = 0.0


# ---------------------------------------------------------------- Hilfen

def norm(s: str | None) -> str:
    """Vergleichsschlüssel: klein, ohne Akzente/Satzzeichen, Umlaute ausgeschrieben."""
    if not s:
        return ""
    s = s.lower().replace("ß", "ss").replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
    s = unicodedata.normalize("NFKD", s)
    return re.sub(r"[^a-z0-9]", "", "".join(c for c in s if not unicodedata.combining(c)))


SENDER_ALIAS = {
    "ard": "daserste", "erste": "daserste", "daserste": "daserste",
    "kika": "kika", "kikavonardundzdf": "kika",
    "pro7": "prosieben", "prosieben": "prosieben", "sat1": "sat1",
    "kabel1": "kabeleins", "kabel": "kabeleins", "kabeleins": "kabeleins",
    "rtl2": "rtlzwei", "rtlii": "rtlzwei", "rtlzwei": "rtlzwei",
    "brfernsehen": "br", "bayerischesfernsehen": "br", "bralpha": "ardalpha", "ardalpha": "ardalpha",
    "einsfestival": "one", "one": "one", "nick": "nickelodeon", "superrtl": "superrtl", "super": "superrtl",
}


def sender_norm(s: str | None) -> str:
    k = norm(s)
    for endung in ("hd", "fernsehen", "tv"):
        if k.endswith(endung) and len(k) > len(endung) + 1 and k not in SENDER_ALIAS:
            k = k[: -len(endung)]
    return SENDER_ALIAS.get(k, k)


def sender_gleich(a: str | None, b: str | None) -> bool | None:
    """True/False, None wenn ein Sender unbekannt ist. Panasonic kürzt auf 5 Zeichen."""
    na, nb = sender_norm(a), sender_norm(b)
    if not na or not nb:
        return None
    return na == nb or (min(len(na), len(nb)) >= 3 and (na.startswith(nb) or nb.startswith(na)))


def dateiname(s: str) -> str:
    """Name für Windows/SMB säubern."""
    s = s.replace(":", " -").replace("/", "-").replace("\\", "-").replace('"', "'")
    s = re.sub(r'[<>|?*\x00-\x1f]', "", s)
    s = re.sub(r"\s+", " ", s).strip().rstrip(". ")
    if re.fullmatch(r"(?i)(con|prn|aux|nul|com\d|lpt\d)", s):
        s += "_"
    return s[:120] or "Unbenannt"


def datum_lesen(s: str | None) -> dt.date | None:
    """JJJJ-MM-TT, TT.MM.JJJJ (Excel) oder ISO-Zeitstempel."""
    s = (s or "").strip()
    for muster, fmt in ((r"\d{4}-\d{2}-\d{2}", "%Y-%m-%d"), (r"\d{1,2}\.\d{1,2}\.\d{4}", "%d.%m.%Y")):
        m = re.search(muster, s)
        if m:
            try:
                return dt.datetime.strptime(m.group(), fmt).date()
            except ValueError:
                return None
    return None


def zahl(s) -> int | None:
    try:
        return int(str(s).strip())
    except (TypeError, ValueError):
        return None


def dauer_text(sek: int | None) -> str:
    sek = int(sek or 0)
    return f"{sek // 60}:{sek % 60:02d}"


# ---------------------------------------------------------------- Abruf mit Cache

def holen(url: str, cache_name: str | None = None, headers: dict | None = None,
          cache_url: str | None = None) -> tuple[int, str, str]:
    """Liefert (status, end_url, text). Cache unter CACHE/<bereich>/<hash>.json, 30 Tage.

    cache_url ist die URL ohne Schlüssel, damit kein Token im Cache landet.
    """
    global _letzter_abruf
    schluessel = cache_url or url
    bereich = cache_name or urllib.parse.urlparse(schluessel).hostname or "web"
    datei = CACHE / bereich / (hashlib.sha1(schluessel.encode()).hexdigest()[:20] + ".json")
    if datei.exists() and time.time() - datei.stat().st_mtime < CACHE_TAGE * 86400:
        d = json.loads(datei.read_text(encoding="utf-8"))
        return d["status"], d["end_url"], d["text"]
    warte = PAUSE_S - (time.time() - _letzter_abruf)
    if warte > 0:
        time.sleep(warte)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "de-DE,de;q=0.9",
                                               **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            status, end_url, text = r.status, r.geturl(), r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        status, end_url, text = e.code, url, ""
    finally:
        _letzter_abruf = time.time()
    if status in (200, 404):
        datei.parent.mkdir(parents=True, exist_ok=True)
        if cache_url:
            end_url = cache_url
        datei.write_text(json.dumps({"url": schluessel, "status": status, "end_url": end_url,
                                     "text": text, "zeit": g.jetzt()}, ensure_ascii=False), encoding="utf-8")
    return status, end_url, text


def _text(s: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", s)).strip()


# ---------------------------------------------------------------- fernsehserien.de

def fs_suchen(name: str) -> list[dict]:
    """Suche nach Serie/Film. Liefert [{art: serie|film, pfad, titel, jahr}]."""
    q = urllib.parse.quote(re.sub(r"[/?]", " ", name)[:50])
    status, end, text = holen(f"{FS}/suche/{q}")
    if status != 200:
        return []
    pfad = urllib.parse.urlparse(end).path
    if not pfad.startswith("/suche/"):  # eindeutiger Treffer, Weiterleitung auf die Seite
        m = re.search(r'<h1[^>]*class="serien-titel"[^>]*>(.*?)</h1>', text, re.S)
        titel = _text(re.sub(r'<span class="hidden">.*?</span>', "", m.group(1))) if m else name
        if pfad.startswith("/filme/"):
            j = re.search(r"\b(19|20)\d{2}\b", _text(text[text.find("<h1"):text.find("<h1") + 3000]))
            return [{"art": "film", "pfad": pfad, "titel": titel, "jahr": int(j.group()) if j else None}]
        return [{"art": "serie", "pfad": "/" + pfad.strip("/").split("/")[0], "titel": titel, "jahr": None}]
    erg = []
    for m in re.finditer(r'<li class="ep-hover suchergebnisse-(sendung|film)"><a [^>]*title="([^"]*)" '
                         r'href="([^"]+)".*?<dd>(.*?)</dd>', text, re.S):
        art, titel, pfad, dd = m.groups()
        j = re.search(r"(19|20)\d{2}", _text(dd))
        erg.append({"art": "serie" if art == "sendung" else "film", "pfad": pfad,
                    "titel": html.unescape(titel), "jahr": int(j.group()) if j else None})
    return erg


def _zeilen_parsen(abschnitt: str) -> list[dict]:
    folgen = []
    for r in re.findall(r'<a role="row".*?</a>', abschnitt, re.S):
        url = re.search(r'href="([^"]+)"', r).group(1)
        kopf = html.unescape(re.search(r'title="([^"]*)"', r).group(1))
        st = re.match(r"(\d+)\.(\d*)\s", kopf)
        nr = re.search(r'itemprop="episodeNumber" content="(\d+)"', r)
        name = re.search(r'<span itemprop="name">(.*?)</span>', r, re.S)
        zellen = re.findall(r'<div role="cell"[^>]*>(.*?)</div>', r, re.S)
        gesamt = zahl(_text(zellen[1])) if len(zellen) > 1 else None
        d = re.search(r'<div role="cell" title="([^"]*)">(\d\d\.\d\d\.\d{4})</div>', r)
        folgen.append({
            "staffel": int(st.group(1)) if st else None,
            "folge": int(nr.group(1)) if nr and int(nr.group(1)) > 0 else None,
            "gesamt": gesamt, "titel": _text(name.group(1)) if name else "", "url": url,
            "premiere": dt.datetime.strptime(d.group(2), "%d.%m.%Y").date() if d else None,
            "sender": html.unescape(d.group(1)) if d else None,
        })
    return folgen


def _staffelseiten(pfad_staffel: str) -> list[dict]:
    """Gekürzte Staffel: Staffelseite(n) lesen. Folgennummer = Reihenfolge (unsicherer)."""
    m = re.search(r"episodenguide/(\d+)/", pfad_staffel)
    staffel = int(m.group(1)) if m else None
    folgen: list[dict] = []
    for seite in range(1, 16):
        url = urllib.parse.urljoin(FS + "/", pfad_staffel) + (f"/{seite}" if seite > 1 else "")
        status, _, text = holen(url)
        neu = 0
        for s in re.findall(r'<section itemprop="episode".*?</section>', text, re.S):
            u = re.search(r'href="([^"]+/folgen/[^"]+)"', s)
            if not u or any(f["url"] == u.group(1) for f in folgen):
                continue
            d = re.search(r'data-sort="\d+-(\d{4}-\d{2}-\d{2})"', s)
            sd = re.search(r"<ea-angabe-sender>([^<]*)", s)
            name = re.search(r'<span itemprop="name">(.*?)</span>', s, re.S)
            folgen.append({"staffel": staffel, "folge": len(folgen) + 1, "gesamt": None, "url": u.group(1),
                           "titel": _text(name.group(1)) if name else "", "reihenfolge": True,
                           "premiere": dt.date.fromisoformat(d.group(1)) if d else None,
                           "sender": html.unescape(sd.group(1)) if sd else None})
            neu += 1
        if status != 200 or not neu:
            break
    return folgen


def fs_folgen(serie_pfad: str, datum: dt.date) -> list[dict]:
    """Episodenliste; gekürzte Abschnitte nur nachladen, wenn sie das Datum enthalten können."""
    status, _, text = holen(f"{FS}{serie_pfad}/episodenguide")
    if status != 200:
        return []
    abschnitte = []
    for s in text.split('itemprop="containsSeason"')[1:]:
        s = s[: s.find("</section>") + 10]
        kopf = re.search(r'<h2[^>]*>(?:<a[^>]*href="([^"]+)"[^>]*>)?(.*?)</h2>', s, re.S)
        folgen = _zeilen_parsen(s)
        daten = [f["premiere"] for f in folgen if f["premiere"]]
        abschnitte.append({"pfad": kopf.group(1) if kopf else None, "name": _text(kopf.group(2)) if kopf else "",
                           "folgen": folgen, "gekuerzt": "button-mehr-episoden" in s,
                           "ab": min(daten) if daten else None})
    alle = [f for a in abschnitte for f in a["folgen"]]
    starts = sorted(a["ab"] for a in abschnitte if a["ab"])
    for a in abschnitte:
        if not a["gekuerzt"] or not a["pfad"]:
            continue
        jahre = [int(x) for x in re.findall(r"(?:19|20)\d{2}", a["name"])]
        passt = bool(jahre) and min(jahre) <= datum.year <= max(jahre)
        if not jahre and a["ab"]:
            naechster = next((s for s in starts if s > a["ab"]), None)
            passt = a["ab"] <= datum and (naechster is None or datum < naechster)
        if passt:
            pfad = a["pfad"] if a["pfad"].startswith("/") else f"{serie_pfad}/{a['pfad']}"
            urls = {f["url"] for f in alle}
            alle += [f for f in _staffelseiten(pfad) if f["url"] not in urls]
    return alle


def fs_sendetermine(folge_url: str) -> list[tuple[dt.datetime, dt.datetime | None, str]]:
    status, _, text = holen(FS + folge_url)
    if status != 200:
        return []
    termine = []
    for m in re.finditer(r'itemprop="startDate" datetime="([^"]+)"(.*?)itemprop="name" content="([^"]*)"',
                         text, re.S):
        start = dt.datetime.fromisoformat(m.group(1)).replace(tzinfo=None)
        e = re.search(r'itemprop="endDate" datetime="([^"]+)"', m.group(2))
        ende = dt.datetime.fromisoformat(e.group(1)).replace(tzinfo=None) if e else None
        termine.append((start, ende, html.unescape(m.group(3))))
    return termine


def fs_termine_am(serie_pfad: str, datum: dt.date) -> list[dict]:
    """Alle Sendetermine der Serie am Datum (alle Sender), auch Wiederholungen.

    Quelle: /<serie>/sendetermine/datum-JJJJMMTT, leitet auf die Seite mit dem Datum weiter.
    """
    status, _, text = holen(f"{FS}{serie_pfad}/sendetermine/datum-{datum:%Y%m%d}")
    if status != 200:
        return []
    termine = []
    # Zeilen mit Folgen-Link: <a role="rowgroup" href=...><div role="row" itemprop=...>,
    # Zeilen ohne bekannte Folge (z.B. Maus 2011): nur <div id="datum-..." role="row" class=...>
    zeilen = re.split(r'(?=<a [^>]*role="rowgroup"|<div id="datum-\d+" role="row" class=)', text)[1:]
    for r in zeilen:
        href = re.match(r'<a [^>]*href="([^"]+)"', r)
        url = href.group(1) if href else ""
        s = re.search(r'itemprop="startDate" datetime="([^"]+)"', r)
        if not s:
            continue
        start = dt.datetime.fromisoformat(s.group(1)).replace(tzinfo=None)
        if start.date() != datum:
            continue
        e = re.search(r'itemprop="endDate" datetime="([^"]+)"', r)
        sender = re.search(r'itemprop="publishedOn".*?itemprop="name"[^>]*content="([^"]*)"', r, re.S)
        se = re.search(r'class="no-smartphone2 sendetermine-2019-staffel-und-episode">\s*(\d+)\.(\d+)\s*<', r)
        titel = re.search(r'class="sendetermine-2019-episodentitel">(.*?)</span>', r, re.S)
        termine.append({
            "start": start,
            "ende": dt.datetime.fromisoformat(e.group(1)).replace(tzinfo=None) if e else None,
            "sender": html.unescape(sender.group(1)) if sender else None,
            "url": url if "/folgen/" in url else None,
            "staffel": int(se.group(1)) if se else None, "folge": int(se.group(2)) if se else None,
            "titel": re.sub(r"\s+NEU$", "", _text(titel.group(1))) if titel else "",
            "premiere": None,
        })
    return termine


# ---------------------------------------------------------------- TMDB (optional)

def tmdb(pfad: str, **params) -> dict | None:
    key = os.environ.get("TMDB_API_KEY", "").strip()
    if not key:
        return None
    params.setdefault("language", "de-DE")
    ohne = f"{TMDB}{pfad}?{urllib.parse.urlencode(sorted(params.items()))}"
    headers = {"Accept": "application/json"}
    if len(key) > 40:  # v4-Lesetoken
        headers["Authorization"] = f"Bearer {key}"
        url = ohne
    else:
        url = f"{ohne}&api_key={urllib.parse.quote(key)}"
    try:
        status, _, text = holen(url, "tmdb", headers, cache_url=ohne)
    except OSError:
        return None
    return json.loads(text) if status == 200 and text else None


def tmdb_film(titel: str, jahr: int | None) -> dict | None:
    d = tmdb("/search/movie", query=titel, **({"year": jahr} if jahr else {}))
    for r in (d or {}).get("results", [])[:5]:
        if norm(r.get("title")) == norm(titel) or norm(r.get("original_title")) == norm(titel):
            j = (r.get("release_date") or "")[:4]
            return {"tmdb_id": r["id"], "titel": r.get("title") or titel, "jahr": int(j) if j else jahr}
    return None


def tmdb_folge(serie: str, datum: dt.date) -> dict | None:
    """TMDB-Nummerierung (die nutzt Jellyfin) für eine Folge mit Erstausstrahlung am Datum."""
    d = tmdb("/search/tv", query=serie)
    tv = next((r for r in (d or {}).get("results", [])[:5]
               if norm(r.get("name")) == norm(serie) or norm(r.get("original_name")) == norm(serie)), None)
    if not tv:
        return None
    info = tmdb(f"/tv/{tv['id']}") or {}
    staffeln = sorted((s for s in info.get("seasons", []) if s.get("air_date") and s["air_date"] <= datum.isoformat()),
                      key=lambda s: s["air_date"], reverse=True)[:3]
    for s in staffeln:
        sd = tmdb(f"/tv/{tv['id']}/season/{s['season_number']}") or {}
        for e in sd.get("episodes", []):
            if e.get("air_date") == datum.isoformat():
                return {"tmdb_id": tv["id"], "staffel": e["season_number"], "folge": e["episode_number"],
                        "titel": e.get("name")}
    return {"tmdb_id": tv["id"]}


# ---------------------------------------------------------------- Vorschlag

def _leer(art: str, sendung: str | None, quelle: str, **kw) -> dict:
    z = {"art": art, "sendung": sendung, "staffel": None, "folge": None, "titel": None, "jahr": None,
         "tmdb_id": None, "quelle": quelle, "bestaetigt": False, "sendedatum": None,
         "konfidenz": "niedrig", "kandidaten": [], "hinweise": [], "url": None}
    z.update(kw)
    return z


def _fk(f: dict) -> str:
    st = f"S{f['staffel']:02d}E{f['folge']:02d}" if f.get("staffel") is not None and f.get("folge") else "ohne Nr."
    return f"{st} {f['titel']}"


def sonntagsfolge(sendung: str | None, datum: dt.date) -> tuple[int, int, dt.date] | None:
    """(Staffel, Folge, Sonntag) für Sonntagsserien, nur für Sendungen am Samstag, Sonntag oder Montag."""
    start = SONNTAGSSERIEN.get(norm(sendung))
    if not start or datum.weekday() not in (5, 6, 0):
        return None
    sonntag = datum - dt.timedelta(days=(datum.weekday() + 1) % 7)  # letzter Sonntag, auch derselbe Tag
    return sonntag.year - start + 1, (sonntag.timetuple().tm_yday - 1) // 7 + 1, sonntag


def titel_vorschlagen(t: dict, beschriftung: str) -> dict:
    au = t.get("aufnahme") or {}
    name = (au.get("name") or "").strip()
    zeit = dt.datetime.fromisoformat(au["sendezeit"]) if au.get("sendezeit") else None
    datum = zeit.date() if zeit else datum_lesen(beschriftung)
    dauer = t.get("dauer_s") or 0
    if not name:  # kein Rekordername: Beschriftung ohne Datum als Sendung
        sendung = re.sub(r"\s*\d{1,2}\.\d{1,2}\.\d{2,4}|\s*\d{4}-\d{2}-\d{2}", "", beschriftung or "").strip() or None
        return _leer("aufnahme", sendung, "beschriftung", sendedatum=datum and datum.isoformat(),
                     hinweise=["keine Rekorderdaten, Sendung aus der Beschriftung"])

    try:
        treffer: list[dict] = []
        for q in dict.fromkeys([name, re.split(r"\s*[:–]\s*|\s+-\s+", name)[0]]):
            treffer = fs_suchen(q)
            if treffer:
                break
    except OSError as e:
        return _leer("aufnahme", name, "rekorder", sendedatum=datum and datum.isoformat(), konfidenz="mittel",
                     hinweise=[f"fernsehserien.de nicht erreichbar: {e}"])
    serien = [x for x in treffer if x["art"] == "serie"]
    filme = [x for x in treffer if x["art"] == "film"]
    serie = next((x for x in serien if norm(x["titel"]) == norm(name)), None)
    if not serie and serien and norm(name).startswith(norm(serien[0]["titel"])):
        serie = serien[0]
    film = next((x for x in filme if norm(x["titel"]) == norm(name)), None)

    if not serie and (film or dauer >= FILM_MIN_S):
        kandidaten = [f"{x['titel']} ({x['jahr'] or '?'})" for x in filme if norm(x["titel"]) == norm(name)]
        z = _leer("film", None, "fernsehserien.de" if film else "rekorder", titel=film["titel"] if film else name,
                  jahr=film and film["jahr"], url=film and FS + film["pfad"],
                  sendedatum=datum and datum.isoformat(),
                  konfidenz="hoch" if film and len(kandidaten) == 1 and film["jahr"] else "niedrig",
                  kandidaten=kandidaten if len(kandidaten) > 1 else [])
        if not film:
            z["hinweise"].append("Film vermutet (Dauer über 75 min, keine Serie gefunden), Jahr eintragen")
        tm = tmdb_film(z["titel"], z["jahr"])
        if tm:
            z.update(tmdb_id=tm["tmdb_id"], jahr=z["jahr"] or tm["jahr"], quelle=z["quelle"] + "+tmdb")
        return z

    if not serie:
        return _leer("aufnahme", name, "rekorder", sendedatum=datum and datum.isoformat(), konfidenz="mittel",
                     hinweise=["keine passende Serie bei fernsehserien.de"])
    if not datum:
        return _leer("aufnahme", serie["titel"], "fernsehserien.de", url=FS + serie["pfad"],
                     hinweise=["kein Sendedatum, Folge nicht bestimmbar"])

    folgen = fs_folgen(serie["pfad"], datum)
    nahe = [f for f in folgen if f["premiere"] and dt.timedelta(0) <= datum - f["premiere"]
            <= dt.timedelta(days=FENSTER_TAGE)]
    nahe.sort(key=lambda f: (datum - f["premiere"]).days)
    am_tag = [f for f in nahe if f["premiere"] == datum]
    zeit_treffer: list[tuple[int, dict, tuple]] = []
    weitere: list[dict] = []
    if zeit:
        ende = zeit + dt.timedelta(seconds=dauer)
        for f in nahe[:MAX_FOLGENSEITEN]:
            for termin in fs_sendetermine(f["url"]):
                if termin[0].date() != datum:
                    continue
                delta = abs((termin[0] - zeit).total_seconds()) / 60
                if delta <= ZEIT_TOLERANZ_MIN and sender_gleich(au.get("sender"), termin[2]) is not False:
                    zeit_treffer.append((int(delta), f, termin))
                elif zeit + dt.timedelta(minutes=ZEIT_TOLERANZ_MIN) < termin[0] < ende - dt.timedelta(minutes=5) \
                        and sender_gleich(au.get("sender"), termin[2]) is not False and f not in weitere:
                    weitere.append(f)
    zeit_treffer.sort(key=lambda x: x[0])
    eindeutig = list({id(f): f for _, f, _ in zeit_treffer}.values())

    hinweise: list[str] = []
    termin_ohne_folge = None  # Sendetermin passt, aber fernsehserien.de kennt die Folge nicht
    if not eindeutig and zeit:
        # Rückfall für Wiederholungen: Sendetermine der Serie am Aufnahmetag
        try:
            am_datum = fs_termine_am(serie["pfad"], datum)
        except OSError:
            am_datum = []
        ende = zeit + dt.timedelta(seconds=dauer)
        for f in am_datum:
            if sender_gleich(au.get("sender"), f["sender"]) is False:
                continue
            delta = abs((f["start"] - zeit).total_seconds()) / 60
            if f["url"] is None:
                if delta <= ZEIT_TOLERANZ_MIN and termin_ohne_folge is None:
                    termin_ohne_folge = f
                continue
            if delta <= ZEIT_TOLERANZ_MIN:
                zeit_treffer.append((int(delta), f, (f["start"], f["ende"], f["sender"])))
            elif zeit + dt.timedelta(minutes=ZEIT_TOLERANZ_MIN) < f["start"] < ende - dt.timedelta(minutes=5) \
                    and f not in weitere:
                weitere.append(f)
        zeit_treffer.sort(key=lambda x: x[0])
        eindeutig = list({f["url"]: f for _, f, _ in zeit_treffer}.values())
        if eindeutig:
            hinweise.append("gefunden über die Sendetermine am Aufnahmetag (Wiederholung)")

    if eindeutig:
        wahl = eindeutig[0]
        konf = "hoch" if len(eindeutig) == 1 else "niedrig"
        termin = zeit_treffer[0][2]
        hinweise.append(f"Sendetermin {termin[0]:%d.%m.%Y %H:%M} {termin[2]} (Abweichung {zeit_treffer[0][0]} min)")
    elif len(am_tag) == 1:
        wahl, konf = am_tag[0], "mittel"
        hinweise.append(f"nur Erstausstrahlung am {datum:%d.%m.%Y} ({wahl['sender']}), Uhrzeit nicht bestätigt")
    elif am_tag:
        wahl, konf = am_tag[0], "niedrig"
    else:
        wahl, konf = None, "niedrig"
    kandidaten = [_fk(f) for f in (eindeutig or am_tag)][1:] if wahl else [_fk(f) for f in nahe[:5]]
    if weitere:
        konf = "mittel" if konf == "hoch" else konf
        hinweise.append("Aufnahme enthält evtl. weitere Folgen: " + ", ".join(_fk(f) for f in weitere))

    if not wahl and (regel := sonntagsfolge(serie["titel"], datum)):
        staffel, folge, sonntag = regel
        if termin_ohne_folge:
            hinweise.append(f"Sendetermin {termin_ohne_folge['start']:%d.%m.%Y %H:%M} {termin_ohne_folge['sender']}"
                            " ohne Folgennummer bei fernsehserien.de")
        hinweise.append(f"Nummer nach der Sonntagsregel: {folge}. Sonntag {sonntag.year} ({sonntag:%d.%m.%Y})")
        konf = "hoch" if termin_ohne_folge and not weitere else "mittel"
        if not termin_ohne_folge:
            hinweise.append("kein passender Sendetermin bei fernsehserien.de, Sender und Uhrzeit prüfen")
        return _leer("folge", serie["titel"], "regel", staffel=staffel, folge=folge,
                     titel=f"Sendung vom {sonntag:%d.%m.%Y}", sendedatum=datum.isoformat(), konfidenz=konf,
                     kandidaten=kandidaten, hinweise=hinweise, url=FS + serie["pfad"])
    if not wahl or not wahl.get("folge") or wahl.get("staffel") is None:
        if wahl:
            hinweise.append(f"Folge ohne Nummer: {wahl['titel']}")
        else:
            hinweise.append(f"keine Folge mit Sendetermin am {datum:%d.%m.%Y} gefunden")
        return _leer("aufnahme", serie["titel"], "fernsehserien.de", sendedatum=datum.isoformat(),
                     konfidenz="mittel", kandidaten=kandidaten, hinweise=hinweise,
                     url=FS + (wahl["url"] if wahl else serie["pfad"]))
    if wahl.get("reihenfolge"):
        konf = "mittel" if konf == "hoch" else konf
        hinweise.append("Folgennummer aus der Reihenfolge der Staffelseite")
    z = _leer("folge", serie["titel"], "fernsehserien.de", staffel=wahl["staffel"], folge=wahl["folge"],
              titel=wahl["titel"], sendedatum=datum.isoformat(), konfidenz=konf, kandidaten=kandidaten,
              hinweise=hinweise, url=FS + wahl["url"])
    tm = tmdb_folge(serie["titel"], wahl["premiere"] or datum)
    if tm:
        z["tmdb_id"] = tm["tmdb_id"]
        if tm.get("folge"):
            if (tm["staffel"], tm["folge"]) != (z["staffel"], z["folge"]):
                hinweise.append(f"fernsehserien.de zählt S{z['staffel']:02d}E{z['folge']:02d}, TMDB-Nummer übernommen")
            z.update(staffel=tm["staffel"], folge=tm["folge"], quelle="fernsehserien.de+tmdb")
    elif not os.environ.get("TMDB_API_KEY"):
        hinweise.append("Nummer laut fernsehserien.de, ohne TMDB-Abgleich")
    return z


def doppelte_markieren(titel: list[dict]) -> None:
    gesehen: dict[tuple, dict] = {}
    for t in titel:
        z = t.get("zuordnung") or {}
        if z.get("art") != "folge":
            continue
        k = (norm(z["sendung"]), z["staffel"], z["folge"])
        if k in gesehen:
            a = gesehen[k]
            laenger = a if (a.get("dauer_s") or 0) >= (t.get("dauer_s") or 0) else t
            for x, y in ((t, a), (a, t)):
                h = f"gleiche Folge wie Titel {y['nr']}, längere Aufnahme: Titel {laenger['nr']}"
                if h not in x["zuordnung"].setdefault("hinweise", []):
                    x["zuordnung"]["hinweise"].append(h)
        else:
            gesehen[k] = t


# ---------------------------------------------------------------- Prüfseite und CSV

def csv_schreiben(ordner: Path, daten: dict) -> None:
    with (ordner / "zuordnung.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(CSV_SPALTEN)
        for t in daten["titel"]:
            z = t.get("zuordnung") or {}
            w.writerow([t["nr"], z.get("art") or "", z.get("sendung") or "", z.get("staffel") if z.get("staffel") is not None else "",
                        z.get("folge") or "", z.get("titel") or "", z.get("jahr") or "", z.get("sendedatum") or "",
                        "x" if z.get("bestaetigt") else ""])


def csv_lesen(ordner: Path) -> list[dict]:
    roh = (ordner / "zuordnung.csv").read_bytes()
    try:
        text = roh.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = roh.decode("cp1252")  # Excel "CSV (Trennzeichen-getrennt)"
    trenner = ";" if text.splitlines()[0].count(";") >= text.splitlines()[0].count(",") else ","
    return [{k.strip().lower(): (v or "").strip() for k, v in z.items() if k}
            for z in csv.DictReader(text.splitlines(), delimiter=trenner)]


def ziel_rel(z: dict) -> tuple[Path | None, str | None]:
    """Zielpfad relativ zu DVD_ZIEL, oder (None, Fehler)."""
    art = z.get("art")
    if art == "folge":
        if not z.get("sendung") or z.get("staffel") is None or not z.get("folge"):
            return None, "Serie, Staffel oder Folge fehlt"
        s = dateiname(z["sendung"])
        return Path("Serien", s, f"Season {z['staffel']:02d}", f"{s} S{z['staffel']:02d}E{z['folge']:02d}.mkv"), None
    if art == "film":
        if not z.get("titel") or not z.get("jahr"):
            return None, "Titel oder Jahr fehlt"
        n = dateiname(f"{z['titel']} ({z['jahr']})")
        return Path("Filme", n, f"{n}.mkv"), None
    if art == "aufnahme":
        d = datum_lesen(z.get("sendedatum"))
        if not z.get("sendung") or not d:
            return None, "Sendung oder Sendedatum fehlt"
        s = dateiname(z["sendung"])
        return Path("Fernsehaufnahmen", s, f"Season {d.year}", f"{s} - {d.isoformat()}.mkv"), None
    if art == "weg":
        return None, None
    return None, f"unbekannte Art {art!r}"


KONF_FARBE = {"hoch": "#2e7d32", "mittel": "#b26a00", "niedrig": "#c62828"}


def pruefseite_schreiben(ordner: Path, daten: dict) -> None:
    e = html.escape
    teile = [f"""<!doctype html><html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(daten['disc'])} prüfen</title><style>
:root{{--bg:#fafafa;--fg:#1b1b1b;--karte:#fff;--rand:#ddd;--leise:#666}}
@media (prefers-color-scheme:dark){{:root{{--bg:#16181b;--fg:#e8e8e8;--karte:#202327;--rand:#3a3d42;--leise:#9aa}}}}
body{{background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,sans-serif;margin:0;padding:16px;max-width:1200px;margin:auto}}
h1{{margin:.2em 0}} .leise{{color:var(--leise)}} img{{max-width:100%;border-radius:4px}}
.karte{{background:var(--karte);border:1px solid var(--rand);border-radius:8px;padding:12px 14px;margin:14px 0}}
.bilder{{display:flex;gap:6px;overflow-x:auto}} .bilder img{{height:120px;width:auto}}
.konf{{color:#fff;border-radius:10px;padding:1px 8px;font-size:13px}}
table{{border-collapse:collapse}} td{{padding:2px 10px 2px 0;vertical-align:top}} code{{font-size:13px}}
ul{{margin:.3em 0 .3em 1.2em;padding:0}}</style></head><body>
<h1>{e(daten['disc'])} – {e(daten.get('beschriftung') or 'ohne Beschriftung')}</h1>
<p class="leise">{e(daten.get('rekorder') or daten.get('typ') or '')} · {len(daten['titel'])} Titel ·
{sum(t.get('dauer_s', 0) for t in daten['titel']) / 3600:.2f} h · Stand {e(g.jetzt())}</p>
<p>Korrigieren in <code>zuordnung.csv</code> (gleicher Ordner), Spalte <b>ok</b> auf <b>x</b> setzen.
Art: <code>folge</code>, <code>film</code>, <code>aufnahme</code> oder <code>weg</code> (nicht einsortieren).
Danach: <code>einsortieren.py anwenden {e(daten['disc'])}</code></p>"""]
    if (ordner / "menue.png").exists():
        teile.append('<div class="karte"><b>Menü</b><br><img src="menue.png" alt="Menübild"></div>')
    for t in daten["titel"]:
        z = t.get("zuordnung") or {}
        au = t.get("aufnahme") or {}
        bilder = sorted((ordner / "vorschau").glob(f"t{t['nr']:02d}-*.jpg")) if (ordner / "vorschau").exists() else []
        rel, fehler = ziel_rel(z)
        if z.get("art") == "folge":
            vorschlag = f"{e(z['sendung'] or '')} <b>S{z['staffel']:02d}E{z['folge']:02d}</b> {e(z.get('titel') or '')}"
        elif z.get("art") == "film":
            vorschlag = f"Film <b>{e(z.get('titel') or '')} ({z.get('jahr') or '?'})</b>"
        else:
            vorschlag = f"Aufnahme <b>{e(z.get('sendung') or '?')}</b> vom {e(z.get('sendedatum') or '?')}"
        konf = z.get("konfidenz") or "niedrig"
        link = f' · <a href="{e(z["url"])}" target="_blank" rel="noopener">{e(z.get("quelle") or "Quelle")}</a>' if z.get("url") else f" · {e(z.get('quelle') or '')}"
        teile.append(f"""<div class="karte"><h2 style="margin:0 0 6px">Titel {t['nr']}
<span class="konf" style="background:{KONF_FARBE.get(konf, '#555')}">{e(konf)}</span></h2>
<div class="bilder">{''.join(f'<img src="vorschau/{e(b.name)}" alt="" loading="lazy">' for b in bilder) or '<span class="leise">keine Vorschaubilder</span>'}</div>
<table><tr><td>Datei</td><td><code>{e(t.get('datei') or '')}</code>, Dauer {dauer_text(t.get('dauer_s'))}</td></tr>
<tr><td>Rekorder</td><td>{e(au.get('name') or '–')} · {e(au.get('sender') or '?')} · {e((au.get('sendezeit') or '?').replace('T', ' '))}</td></tr>
<tr><td>Vorschlag</td><td>{vorschlag}{link}</td></tr>
<tr><td>Ziel</td><td><code>{e(rel.as_posix()) if rel else e(fehler or 'wird nicht einsortiert')}</code></td></tr>""")
        if z.get("kandidaten"):
            teile.append("<tr><td>Kandidaten</td><td><ul>" + "".join(f"<li>{e(k)}</li>" for k in z["kandidaten"]) + "</ul></td></tr>")
        if z.get("hinweise"):
            teile.append("<tr><td>Hinweise</td><td><ul>" + "".join(f"<li>{e(h)}</li>" for h in z["hinweise"]) + "</ul></td></tr>")
        if t.get("einsortiert"):
            teile.append(f"<tr><td>Erledigt</td><td>{e(t['einsortiert'].get('pfad') or 'weg')}</td></tr>")
        teile.append("</table></div>")
    teile.append("</body></html>")
    (ordner / "pruefen.html").write_text("\n".join(teile), encoding="utf-8")


# ---------------------------------------------------------------- Befehle

def disc_ordner(name: str) -> Path:
    return g.FERTIG / g.disc_name(name)


def umgewandelte() -> list[Path]:
    if not g.FERTIG.exists():
        return []
    erg = []
    for o in sorted(g.FERTIG.iterdir()):
        if (o / "disc.json").exists() and g.disc_json_lesen(o).get("status") == "umgewandelt":
            erg.append(o)
    return erg


def cmd_vorschlagen(a) -> int:
    ordner = [disc_ordner(d) for d in a.disc] if a.disc else umgewandelte()
    if not ordner:
        print("Keine Discs mit Status 'umgewandelt'.")
        return 0
    for o in ordner:
        daten = g.disc_json_lesen(o)
        if daten.get("status") != "umgewandelt":
            print(f"{daten['disc']}: Status {daten.get('status')}, übersprungen")
            continue
        print(f"{daten['disc']}  {daten.get('beschriftung') or ''}")
        for t in daten["titel"]:
            alt = t.get("zuordnung") or {}
            if alt.get("bestaetigt") or t.get("einsortiert"):
                continue
            try:
                t["zuordnung"] = titel_vorschlagen(t, daten.get("beschriftung") or "")
            except (OSError, ValueError) as e:
                au = t.get("aufnahme") or {}
                t["zuordnung"] = _leer("aufnahme", au.get("name") or None, "rekorder",
                                       sendedatum=(au.get("sendezeit") or "")[:10] or None,
                                       hinweise=[f"Abruf fehlgeschlagen: {e}"])
        doppelte_markieren(daten["titel"])
        g.disc_json_schreiben(o, daten)
        for t in daten["titel"]:
            z = t["zuordnung"]
            rel, fehler = ziel_rel(z)
            ziel = rel.name if rel else fehler
            extra = " !" + z["hinweise"][-1] if any("gleiche Folge" in h for h in z.get("hinweise", [])) else ""
            print(f"  t{t['nr']:02d} {z['art']:8} {z.get('konfidenz', ''):7} {ziel}{extra}")
        pruefseite_schreiben(o, daten)
        if (o / "zuordnung.csv").exists() and not a.neu:
            print(f"  zuordnung.csv existiert schon, nicht überschrieben (--neu erzwingt)")
        else:
            csv_schreiben(o, daten)
        print(f"  Prüfseite: {o / 'pruefen.html'}")
    return 0


def _nfo_aired(p: Path) -> str:
    m = re.search(r"<aired>([^<]*)</aired>", p.read_text(encoding="utf-8", errors="replace"))
    return m.group(1) if m else ""


def _episode_nfo(z: dict, nr: int, d: dt.date, t: dict, daten: dict) -> str:
    au = t.get("aufnahme") or {}
    zeit = dt.datetime.fromisoformat(au["sendezeit"]) if au.get("sendezeit") else None
    plot = f"Fernsehaufnahme vom {d:%d.%m.%Y}"
    if zeit and zeit.date() == d:
        plot += f", {zeit:%H:%M} Uhr"
    if au.get("sender"):
        plot += f", {au['sender']}"
    plot += f". Disc {daten['disc']}, Titel {t['nr']}"
    if daten.get("beschriftung"):
        plot += f", Beschriftung „{daten['beschriftung']}“"
    e = html.escape
    return (f'<?xml version="1.0" encoding="utf-8" standalone="yes"?>\n{NFO_MARKE}\n<episodedetails>\n'
            f"  <title>{e(z['sendung'])} vom {d:%d.%m.%Y}</title>\n  <showtitle>{e(z['sendung'])}</showtitle>\n"
            f"  <season>{d.year}</season>\n  <episode>{nr}</episode>\n  <aired>{d.isoformat()}</aired>\n"
            f"  <plot>{e(plot + '.')}</plot>\n  <lockdata>true</lockdata>\n</episodedetails>\n")


def _regel_nfo(z: dict, t: dict, daten: dict) -> str:
    """NFO für Folgen nach der Sonntagsregel: Titel und Datum, weil TMDB die Folge meist nicht kennt."""
    au = t.get("aufnahme") or {}
    jahr = SONNTAGSSERIEN[norm(z["sendung"])] + z["staffel"] - 1
    erster = dt.date(jahr, 1, 1) + dt.timedelta(days=(6 - dt.date(jahr, 1, 1).weekday()) % 7)
    sonntag = erster + dt.timedelta(weeks=z["folge"] - 1)
    plot = f"Erstausstrahlung {sonntag:%d.%m.%Y}. Aufnahme vom {z['sendedatum']}"
    if au.get("sender"):
        plot += f", {au['sender']}"
    plot += f". Disc {daten['disc']}, Titel {t['nr']}. Folgennummer nach der Sonntagsregel"
    e = html.escape
    return (f'<?xml version="1.0" encoding="utf-8" standalone="yes"?>\n{NFO_MARKE}\n<episodedetails>\n'
            f"  <title>{e(z.get('titel') or f'Sendung vom {sonntag:%d.%m.%Y}')}</title>\n"
            f"  <showtitle>{e(z['sendung'])}</showtitle>\n"
            f"  <season>{z['staffel']}</season>\n  <episode>{z['folge']}</episode>\n  <aired>{sonntag.isoformat()}</aired>\n"
            f"  <plot>{e(plot + '.')}</plot>\n</episodedetails>\n")


def _tvshow_nfo(sendung: str) -> str:
    e = html.escape
    return (f'<?xml version="1.0" encoding="utf-8" standalone="yes"?>\n{NFO_MARKE}\n<tvshow>\n'
            f"  <title>{e(sendung)}</title>\n  <plot>Eigene Fernsehaufnahmen von DVD, je Jahr eine Staffel.</plot>\n"
            f"  <lockdata>true</lockdata>\n</tvshow>\n")


def zuordnung_aus_zeile(z: dict, vorschlag: dict) -> dict:
    neu = dict(vorschlag or {})
    art = (z.get("art") or "").lower()
    neu.update(art=art, sendung=z.get("sendung") or None, staffel=zahl(z.get("staffel")),
               folge=zahl(z.get("folge")), titel=z.get("titel") or None, jahr=zahl(z.get("jahr")),
               bestaetigt=True)
    d = datum_lesen(z.get("sendedatum"))
    neu["sendedatum"] = d.isoformat() if d else None
    felder = ("art", "sendung", "staffel", "folge", "titel", "jahr", "sendedatum")
    if any((vorschlag or {}).get(k) != neu.get(k) for k in felder):
        neu["quelle"] = "nutzer"
    return neu


def cmd_anwenden(a) -> int:
    o = disc_ordner(a.disc)
    if not (o / "zuordnung.csv").exists():
        print(f"{o / 'zuordnung.csv'} fehlt, zuerst: einsortieren.py vorschlagen {a.disc}")
        return 2
    ziel_wurzel = os.environ.get("DVD_ZIEL")
    if not ziel_wurzel:
        print("DVD_ZIEL ist nicht gesetzt.")
        return 2
    zw = Path(ziel_wurzel)
    daten = g.disc_json_lesen(o)
    if daten.get("status") not in ("umgewandelt", "einsortiert"):
        print(f"{daten['disc']}: Status {daten.get('status')}, erwartet 'umgewandelt'")
        return 2
    titel = {t["nr"]: t for t in daten["titel"]}
    plan, konflikte, offen = [], [], 0
    belegt: dict[Path, int] = {}
    for z in csv_lesen(o):
        nr = zahl(z.get("nr"))
        if nr not in titel:
            konflikte.append(f"Zeile mit nr {z.get('nr')!r}: Titel unbekannt")
            continue
        t = titel[nr]
        if t.get("einsortiert"):
            continue
        if (z.get("ok") or "").lower() not in ("x", "ja", "j", "1"):
            offen += 1
            continue
        zu = zuordnung_aus_zeile(z, t.get("zuordnung"))
        if zu["art"] not in ARTEN:
            konflikte.append(f"t{nr:02d}: unbekannte Art {zu['art']!r}")
            continue
        rel, fehler = ziel_rel(zu)
        if fehler:
            konflikte.append(f"t{nr:02d}: {fehler}")
            continue
        quelle = o / (t.get("datei") if str(t.get("datei", "")).endswith(".mkv") else f"t{nr:02d}.mkv")
        if rel is not None:
            if not quelle.exists():
                konflikte.append(f"t{nr:02d}: Quelle {quelle.name} fehlt")
                continue
            if (zw / rel).exists():
                konflikte.append(f"t{nr:02d}: Ziel existiert schon: {rel.as_posix()}")
                continue
            if rel in belegt:
                konflikte.append(f"t{nr:02d}: gleiches Ziel wie t{belegt[rel]:02d}: {rel.as_posix()}")
                continue
            belegt[rel] = nr
        plan.append((t, zu, quelle, rel))

    # Fernsehaufnahmen: laufende Nummer je Jahr nach Datum, vorhandene eigene NFO mitzählen
    nummern: dict[int, int] = {}
    umnummern: list[tuple[Path, int]] = []
    staffeln: dict[Path, list] = {}
    for t, zu, _, rel in plan:
        if zu["art"] == "aufnahme":
            staffeln.setdefault(rel.parent, []).append((zu["sendedatum"], rel.name, t["nr"]))
    for ordner_rel, neue in staffeln.items():
        eintraege = list(neue)
        if (zw / ordner_rel).exists():
            for p in (zw / ordner_rel).glob("*.nfo"):
                if p.name not in ("tvshow.nfo", "season.nfo"):
                    eintraege.append((_nfo_aired(p), p.with_suffix(".mkv").name, p))
        for i, (_, _, wer) in enumerate(sorted(eintraege, key=lambda x: (x[0], x[1])), 1):
            if isinstance(wer, int):
                nummern[wer] = i
            else:
                alt = re.search(r"<episode>(\d+)</episode>", wer.read_text(encoding="utf-8", errors="replace"))
                if NFO_MARKE in wer.read_text(encoding="utf-8", errors="replace") and (not alt or int(alt.group(1)) != i):
                    umnummern.append((wer, i))

    modus = "AUSFÜHREN" if a.ausfuehren else "Trockenlauf"
    print(f"{daten['disc']} → {zw}   ({modus}; {len(plan)} bestätigt, {offen} offen, {len(konflikte)} Konflikte)")
    for t, zu, quelle, rel in plan:
        extra = f"  (E{nummern[t['nr']]})" if t["nr"] in nummern else ""
        print(f"  t{t['nr']:02d} → {rel.as_posix() if rel else 'weg (MKV bleibt liegen)'}{extra}")
    for p, i in umnummern:
        print(f"  NFO neu nummeriert: {p.name} → E{i}")
    for k in konflikte:
        print(f"  KONFLIKT {k}")
    if not a.ausfuehren:
        if plan:
            print("Nichts geändert. Mit --ausfuehren übernehmen.")
        return 1 if konflikte else 0

    fehler = 0
    for t, zu, quelle, rel in plan:
        if rel is None:
            t["zuordnung"] = zu
            t["einsortiert"] = {"pfad": None, "verworfen": True, "zeit": g.jetzt()}
            continue
        ziel = zw / rel
        try:
            ziel.parent.mkdir(parents=True, exist_ok=True)
            if ziel.exists():
                raise FileExistsError(f"Ziel existiert schon: {rel.as_posix()}")
            teil = ziel.with_name(ziel.name + ".teil")
            shutil.copyfile(quelle, teil)
            if teil.stat().st_size != quelle.stat().st_size:
                teil.unlink()
                raise OSError("Größe nach dem Kopieren verschieden")
            os.replace(teil, ziel)
            if zu["art"] == "aufnahme":
                d = dt.date.fromisoformat(zu["sendedatum"])
                show = zw / rel.parents[1] / "tvshow.nfo"
                if not show.exists():
                    show.write_text(_tvshow_nfo(zu["sendung"]), encoding="utf-8")
                nfo = ziel.with_suffix(".nfo")
                if nfo.exists():
                    raise FileExistsError(f"NFO existiert schon: {nfo.name}")
                nfo.write_text(_episode_nfo(zu, nummern[t["nr"]], d, t, daten), encoding="utf-8")
            elif zu["art"] == "folge" and zu.get("quelle") == "regel" and norm(zu["sendung"]) in SONNTAGSSERIEN:
                nfo = ziel.with_suffix(".nfo")
                if not nfo.exists():
                    nfo.write_text(_regel_nfo(zu, t, daten), encoding="utf-8")
            quelle.unlink()
        except OSError as e:
            fehler += 1
            print(f"  FEHLER t{t['nr']:02d}: {e}")
            continue
        t["zuordnung"] = zu
        t["einsortiert"] = {"pfad": rel.as_posix(), "zeit": g.jetzt()}
    for p, i in umnummern:
        txt = p.read_text(encoding="utf-8")
        p.write_text(re.sub(r"<episode>\d+</episode>", f"<episode>{i}</episode>", txt, count=1), encoding="utf-8")
    g.disc_json_schreiben(o, daten)
    fertig = sum(1 for t in daten["titel"] if t.get("einsortiert"))
    if fertig == len(daten["titel"]):
        g.status_setzen(o, "einsortiert")
        print(f"Alle {fertig} Titel erledigt, Status 'einsortiert'. Ordner {o} bleibt (disc.json, Vorschau).")
    else:
        g.fortschritt_setzen(daten, f"{fertig}/{len(daten['titel'])} Titel einsortiert")
        print(f"{fertig}/{len(daten['titel'])} Titel erledigt, Status bleibt '{daten['status']}'.")
    pruefseite_schreiben(o, g.disc_json_lesen(o))
    return 1 if (fehler or konflikte) else 0


def _doppelte_weg(titel: list[dict]) -> list[int]:
    """Bei gleicher Folge bleibt die längste Aufnahme, die anderen werden 'weg'. Liefert deren Nummern."""
    gruppen: dict[tuple, list[dict]] = {}
    for t in titel:
        z = t.get("zuordnung") or {}
        if z.get("art") == "folge":
            gruppen.setdefault((norm(z["sendung"]), z["staffel"], z["folge"]), []).append(t)
    weg = []
    for ts in gruppen.values():
        ts.sort(key=lambda t: -(t.get("dauer_s") or 0))
        for t in ts[1:]:
            t["zuordnung"]["art"] = "weg"
            weg.append(t["nr"])
    return weg


def cmd_auto(a) -> int:
    """Vorschlagen und, wenn alle Titel sicher zugeordnet sind, ohne Rückfrage einsortieren.

    Rückgabe 0 = einsortiert, 1 = liegt zum Prüfen bereit, 2 = Fehler/Voraussetzung fehlt.
    """
    o = disc_ordner(a.disc)
    daten = g.disc_json_lesen(o)
    if daten.get("status") != "umgewandelt":
        print(f"{daten['disc']}: Status {daten.get('status')}, übersprungen")
        return 2
    if (o / "zuordnung.csv").exists():
        print(f"{daten['disc']}: zuordnung.csv gibt es schon, bitte von Hand: einsortieren.py anwenden {daten['disc']}")
        return 1
    cmd_vorschlagen(argparse.Namespace(disc=[daten["disc"]], neu=False))
    daten = g.disc_json_lesen(o)
    unsicher = [t["nr"] for t in daten["titel"]
                if (t.get("zuordnung") or {}).get("konfidenz") != "hoch"
                or (t.get("zuordnung") or {}).get("art") not in ("folge", "film")]
    ziel = os.environ.get("DVD_ZIEL")
    if unsicher or not ziel or not Path(ziel).exists():
        grund = (f"{len(unsicher)} Titel unsicher ({', '.join(f't{n:02d}' for n in unsicher)})" if unsicher
                 else f"DVD_ZIEL {'nicht gesetzt' if not ziel else 'nicht erreichbar: ' + ziel}")
        g.fortschritt_setzen(daten, f"prüfen: {grund}")
        print(f"{daten['disc']}: zum Prüfen, {grund}. Prüfseite: {o / 'pruefen.html'}")
        return 1
    weg = _doppelte_weg(daten["titel"])
    for t in daten["titel"]:
        t["zuordnung"]["bestaetigt"] = True
        if t["nr"] in weg:
            t["zuordnung"].setdefault("hinweise", []).append("automatisch 'weg': kürzere Aufnahme derselben Folge")
    g.disc_json_schreiben(o, daten)
    csv_schreiben(o, daten)
    print(f"{daten['disc']}: alle {len(daten['titel'])} Titel sicher, sortiere automatisch ein"
          + (f" (weg: {', '.join(f't{n:02d}' for n in weg)})" if weg else ""))
    rc = cmd_anwenden(argparse.Namespace(disc=daten["disc"], ausfuehren=True))
    if rc:
        g.fortschritt_setzen(g.disc_json_lesen(o), "prüfen: Konflikt beim automatischen Einsortieren")
        return 1
    return 0


def cmd_liste(a) -> int:
    if not g.FERTIG.exists():
        print(f"{g.FERTIG} fehlt.")
        return 0
    zeilen = []
    for o in sorted(g.FERTIG.iterdir()):
        if not (o / "disc.json").exists():
            continue
        d = g.disc_json_lesen(o)
        if d.get("status") != "umgewandelt" and not a.alle:
            continue
        ts = d.get("titel", [])
        vorg = sum(1 for t in ts if t.get("zuordnung"))
        hoch = sum(1 for t in ts if (t.get("zuordnung") or {}).get("konfidenz") == "hoch")
        ok = 0
        if (o / "zuordnung.csv").exists():
            ok = sum(1 for z in csv_lesen(o) if (z.get("ok") or "").lower() in ("x", "ja", "j", "1"))
        erl = sum(1 for t in ts if t.get("einsortiert"))
        zeilen.append(f"{d['disc']}  {d.get('status', ''):11} {len(ts):3} Titel  vorgeschlagen {vorg:2} "
                      f"(hoch {hoch:2})  ok {ok:2}  erledigt {erl:2}  {d.get('beschriftung') or ''}")
    print("\n".join(zeilen) if zeilen else "Keine Discs mit Status 'umgewandelt'.")
    return 0


def main() -> int:
    for strom in (sys.stdout, sys.stderr):
        try:
            strom.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("vorschlagen", help="Zuordnung vorschlagen, Prüfseite und zuordnung.csv schreiben")
    v.add_argument("disc", nargs="*", help="z.B. D0001 (Standard: alle mit Status umgewandelt)")
    v.add_argument("--neu", action="store_true", help="vorhandene zuordnung.csv überschreiben")
    an = sub.add_parser("anwenden", help="bestätigte Zeilen aus zuordnung.csv einsortieren")
    an.add_argument("disc")
    an.add_argument("--ausfuehren", action="store_true", help="wirklich verschieben (sonst Trockenlauf)")
    au = sub.add_parser("auto", help="vorschlagen und sichere Discs ohne Rückfrage einsortieren")
    au.add_argument("disc")
    li = sub.add_parser("liste", help="Discs mit Status umgewandelt")
    li.add_argument("--alle", action="store_true", help="auch andere Status zeigen")
    a = ap.parse_args()
    return {"vorschlagen": cmd_vorschlagen, "anwenden": cmd_anwenden, "auto": cmd_auto,
            "liste": cmd_liste}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
