"""Audio-CDs unter Windows direkt lesen, mit AccurateRip prüfen und Titeldaten von MusicBrainz holen.

Zweck:
  Ersatz für cyanrip: cyanrip 0.9.3.1 (libcdio-paranoia) konnte das Laufwerk BH16NS55 nicht öffnen,
  auch nicht mit Adminrechten (D0063, 2026-11-07). Windows liest Audio-Sektoren aber ohne Adminrechte.
  - TOC und Audio-Sektoren über DeviceIoControl (IOCTL_CDROM_READ_TOC, IOCTL_CDROM_RAW_READ)
  - Laufwerks-Offset: mit der Prüfsumme von Frame 450 aus der AccurateRip-Datenbank suchen
  - Jeder Titel wird mit AccurateRip (CRC v1 und v2) geprüft. Ohne Treffer wird er erneut gelesen
    (vorher Laufwerks-Cache verdrängen), bis zwei Lesungen gleich sind, höchstens LESUNGEN_MAX Mal.
    Keine Fehlerkorrektur wie cd-paranoia, aber jede Abweichung fällt auf.
  - Titeldaten: MusicBrainz-Disc-ID, Cover vom Cover Art Archive, FLAC mit Tags über ffmpeg

Aufruf (Test, ohne die Arbeitsordner von einlesen.py):
  python -I skripte/cd_leser.py E:                     TOC, IDs, AccurateRip, MusicBrainz
  python -I skripte/cd_leser.py E: --offset            Laufwerks-Offset suchen
  python -I skripte/cd_leser.py E: --lesen <ordner> --offset-wert 667 [--titel 1,3]
Im Betrieb ruft musik_cd.py die Funktionen auf.

Abhängigkeiten: numpy, ffmpeg (im PATH). Netz: accuraterip.com, musicbrainz.org, coverartarchive.org.
Keine Umgebungsvariablen.
"""
from __future__ import annotations

import argparse
import base64
import ctypes
import hashlib
import json
import re
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request
import wave
from ctypes import wintypes as w
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

SEKTOR = 2352            # Byte je Audio-Sektor
SAMPLES = 588            # Stereo-Samples je Sektor
BLOCK = 26               # Sektoren je Lesebefehl (61 KB, unter der 64-KB-Grenze)
LESUNGEN_MAX = 5
CACHE_SEKTOREN = 2500    # ca. 5,9 MB fremde Sektoren lesen, damit der Laufwerks-Cache verdrängt ist
OFFSET_SUCHE = 2940      # Offsets -2940 .. +2940 Samples (5 Sektoren) prüfen
UA = "disc-digitizer-cd-leser/0.1 ( https://github.com/tobi-blazor/disc-digitizer )"

_k = ctypes.WinDLL("kernel32", use_last_error=True)
_k.CreateFileW.restype = w.HANDLE
_k.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, ctypes.c_void_p, w.DWORD, w.DWORD, w.HANDLE]
_k.DeviceIoControl.argtypes = [w.HANDLE, w.DWORD, ctypes.c_void_p, w.DWORD, ctypes.c_void_p, w.DWORD,
                               ctypes.POINTER(w.DWORD), ctypes.c_void_p]
_k.CloseHandle.argtypes = [w.HANDLE]
IOCTL_CDROM_READ_TOC = 0x24000
IOCTL_CDROM_RAW_READ = 0x2403E
UNGUELTIG = w.HANDLE(-1).value


class RAW_READ_INFO(ctypes.Structure):
    _fields_ = [("DiskOffset", ctypes.c_longlong), ("SectorCount", w.ULONG), ("TrackMode", ctypes.c_int)]


class CdFehler(Exception):
    pass


# ---------------------------------------------------------------- Laufwerk

@dataclass
class Spur:
    nr: int
    start: int           # LBA
    ende: int            # LBA, exklusiv
    audio: bool
    emphasis: bool

    @property
    def sektoren(self) -> int:
        return self.ende - self.start


@dataclass
class Toc:
    spuren: list[Spur]
    leadout: int

    @property
    def audio(self) -> list[Spur]:
        return [s for s in self.spuren if s.audio]


class Laufwerk:
    def __init__(self, laufwerk: str):
        name = laufwerk.strip().rstrip("\\/")[:2].upper()
        self.h = _k.CreateFileW(rf"\\.\{name}", 0x80000000, 3, None, 3, 0, None)  # GENERIC_READ, teilen
        if self.h == UNGUELTIG:
            raise CdFehler(f"Laufwerk {name} lässt sich nicht öffnen (Fehler {ctypes.get_last_error()})")
        self.fehler_sektoren: list[int] = []

    def __enter__(self) -> "Laufwerk":
        return self

    def __exit__(self, *_) -> None:
        _k.CloseHandle(self.h)

    def toc(self, versuche: int = 20) -> Toc:
        buf = ctypes.create_string_buffer(804)
        n = w.DWORD()
        for _ in range(versuche):  # direkt nach dem Einlegen meldet das Laufwerk kurz "nicht bereit"
            if _k.DeviceIoControl(self.h, IOCTL_CDROM_READ_TOC, None, 0, buf, 804, ctypes.byref(n), None):
                break
            time.sleep(3)
        else:
            raise CdFehler(f"Inhaltsverzeichnis nicht lesbar (Fehler {ctypes.get_last_error()})")
        b = buf.raw
        erste, letzte = b[2], b[3]
        eintraege = []
        for i in range(letzte - erste + 2):  # inkl. Lead-out (Spur 0xAA)
            e = b[4 + 8 * i: 12 + 8 * i]
            control = e[1] & 0x0F  # TRACK_DATA: Control im unteren, ADR im oberen Halbbyte
            lba = (e[5] * 60 + e[6]) * 75 + e[7] - 150
            eintraege.append((e[2], control, lba))
        spuren = []
        for (nr, control, lba), (_, _, naechste) in zip(eintraege, eintraege[1:]):
            spuren.append(Spur(nr, lba, naechste, not control & 0x04, bool(control & 0x01)))
        # Enhanced CD: vor einer Datenspur in der zweiten Session liegen 11400 Sektoren Lücke
        for a, b_ in zip(spuren, spuren[1:]):
            if a.audio and not b_.audio:
                a.ende = b_.start - 11400
        return Toc(spuren, eintraege[-1][2])

    def _ioctl(self, lba: int, anzahl: int, buf) -> bool:
        ri = RAW_READ_INFO(lba * 2048, anzahl, 2)  # CDDA; DiskOffset in 2048er-Einheiten (Windows-Doku)
        n = w.DWORD()
        ok = _k.DeviceIoControl(self.h, IOCTL_CDROM_RAW_READ, ctypes.byref(ri), ctypes.sizeof(ri), buf,
                                anzahl * SEKTOR, ctypes.byref(n), None)
        return bool(ok) and n.value == anzahl * SEKTOR

    def lesen(self, lba: int, anzahl: int, fortschritt=None) -> bytes:
        """Liest anzahl Audio-Sektoren ab lba. Unlesbare Sektoren werden Stille und in fehler_sektoren vermerkt."""
        aus = bytearray()
        buf = ctypes.create_string_buffer(BLOCK * SEKTOR)
        pos = lba
        while pos < lba + anzahl:
            m = min(BLOCK, lba + anzahl - pos)
            if any(self._ioctl(pos, m, buf) for _ in range(3)):
                aus += buf.raw[:m * SEKTOR]
            else:  # Sektor für Sektor, damit nur die kaputten fehlen
                for s in range(pos, pos + m):
                    if any(self._ioctl(s, 1, buf) for _ in range(5)):
                        aus += buf.raw[:SEKTOR]
                    else:
                        self.fehler_sektoren.append(s)
                        aus += bytes(SEKTOR)
            pos += m
            if fortschritt:
                fortschritt((pos - lba) / anzahl)
        return bytes(aus)

    def spur_pcm(self, toc: Toc, spur: Spur, offset: int, fortschritt=None) -> bytes:
        """PCM der Spur mit Offset-Korrektur. Außerhalb der Disc (Lead-in/Lead-out) wird mit Stille gefüllt."""
        s0, s1 = spur.start * SAMPLES + offset, spur.ende * SAMPLES + offset
        sek0, sek1 = s0 // SAMPLES, -(-s1 // SAMPLES)
        von, bis = max(sek0, 0), min(sek1, toc.leadout)
        daten = bytes((von - sek0) * SEKTOR) + self.lesen(von, bis - von, fortschritt) \
            + bytes((sek1 - bis) * SEKTOR)
        schnitt = (s0 - sek0 * SAMPLES) * 4
        return daten[schnitt: schnitt + (s1 - s0) * 4]

    def cache_verdraengen(self, toc: Toc, spur: Spur) -> None:
        """Fremde Sektoren lesen, damit eine erneute Lesung wirklich von der Disc kommt."""
        weit = 0 if spur.start > toc.leadout // 2 else max(toc.leadout - CACHE_SEKTOREN, 0)
        self.lesen(weit, min(CACHE_SEKTOREN, toc.leadout))


# ---------------------------------------------------------------- Disc-IDs

def ar_ids(toc: Toc) -> tuple[int, int, int]:
    """AccurateRip-IDs (id1, id2, freedb-ID). Bei Enhanced CDs zählt die Datenspur mit."""
    starts = [s.start for s in toc.spuren]
    lo = toc.leadout
    id1 = sum(starts) + lo
    id2 = sum(max(o, 1) * (i + 1) for i, o in enumerate(starts)) + max(lo, 1) * (len(starts) + 1)
    quersumme = sum(sum(int(z) for z in str((o + 150) // 75)) for o in starts)
    dauer = (lo + 150) // 75 - (starts[0] + 150) // 75
    cddb = ((quersumme % 255) << 24) | (dauer << 8) | len(starts)
    return id1 & 0xFFFFFFFF, id2 & 0xFFFFFFFF, cddb


def mb_discid(toc: Toc) -> str:
    audio = toc.audio
    lo = toc.leadout if audio[-1] is toc.spuren[-1] else audio[-1].ende  # Enhanced CD: Ende der Audio-Session
    versatz = {s.nr: s.start + 150 for s in audio}
    text = f"{audio[0].nr:02X}{audio[-1].nr:02X}{lo + 150:08X}" + "".join(
        f"{versatz.get(i, 0):08X}" for i in range(1, 100))
    roh = base64.b64encode(hashlib.sha1(text.encode("ascii")).digest()).decode()
    return roh.replace("+", ".").replace("/", "_").replace("=", "-")


def mb_toc_text(toc: Toc) -> str:
    audio = toc.audio
    lo = toc.leadout if audio[-1] is toc.spuren[-1] else audio[-1].ende
    return " ".join(str(x) for x in [audio[0].nr, audio[-1].nr, lo + 150] + [s.start + 150 for s in audio])


# ---------------------------------------------------------------- AccurateRip

def _holen(url: str, timeout: int = 30) -> bytes | None:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise CdFehler(f"{url}: HTTP {e.code}") from e
    except (urllib.error.URLError, TimeoutError) as e:
        raise CdFehler(f"{url}: {e}") from e


@dataclass
class ArEintrag:
    konfidenz: int
    crc: int
    crc450: int


def ar_abfragen(toc: Toc) -> list[list[ArEintrag]]:
    """Alle Datensätze (Pressungen, v1/v2) zur Disc, je Datensatz eine Liste je Spur. [] = Disc unbekannt."""
    id1, id2, cddb = ar_ids(toc)
    n = len(toc.spuren)
    url = (f"http://www.accuraterip.com/accuraterip/{id1 & 15:x}/{id1 >> 4 & 15:x}/{id1 >> 8 & 15:x}/"
           f"dBAR-{n:03d}-{id1:08x}-{id2:08x}-{cddb:08x}.bin")
    daten = _holen(url)
    saetze: list[list[ArEintrag]] = []
    pos = 0
    while daten and pos + 13 <= len(daten):
        anzahl = daten[pos]
        pos += 13
        satz = []
        for _ in range(anzahl):
            konf, crc, crc450 = struct.unpack_from("<BII", daten, pos)
            satz.append(ArEintrag(konf, crc, crc450))
            pos += 9
        saetze.append(satz)
    return saetze


def ar_crc(pcm: bytes, erste: bool, letzte: bool) -> tuple[int, int]:
    """AccurateRip-Prüfsummen v1 und v2. Erste Spur ohne die ersten, letzte ohne die letzten 5 Sektoren."""
    s = np.frombuffer(pcm, dtype="<u4").astype(np.uint64)
    n = len(s)
    lo = 5 * SAMPLES if erste else 1          # Multiplikator ab hier zählen
    hi = n - 5 * SAMPLES if letzte else n     # bis einschließlich hier
    mult = np.arange(lo, hi + 1, dtype=np.uint64)
    p = s[lo - 1: hi] * mult                  # < 2^32 * 2^25, passt in uint64
    unten = int((p & np.uint64(0xFFFFFFFF)).sum(dtype=np.uint64))
    oben = int((p >> np.uint64(32)).sum(dtype=np.uint64))
    return unten & 0xFFFFFFFF, (unten + oben) & 0xFFFFFFFF


def ar_vergleich(saetze: list[list[ArEintrag]], idx: int, v1: int, v2: int) -> int:
    """Summe der Konfidenz aller Datensätze, deren CRC passt. 0 = kein Treffer."""
    return sum(s[idx].konfidenz for s in saetze if idx < len(s) and s[idx].crc in (v1, v2) and s[idx].crc)


def offset_suchen(lw: Laufwerk, toc: Toc, saetze: list[list[ArEintrag]]) -> int | None:
    """Laufwerks-Offset über die Frame-450-Prüfsumme der AccurateRip-Datenbank (wie cyanrip -f, EAC)."""
    gefunden: dict[int, int] = {}
    gewicht = np.arange(1, SAMPLES + 1, dtype=np.uint64)
    for idx, spur in enumerate(toc.spuren):
        if not spur.audio or spur.sektoren < 460:
            continue
        ziele = {s[idx].crc450 for s in saetze if idx < len(s) and s[idx].crc450}
        if not ziele:
            continue
        rand = OFFSET_SUCHE // SAMPLES + 1
        roh = lw.lesen(spur.start + 450 - rand, 2 * rand + 1)
        x = np.frombuffer(roh, dtype="<u4").astype(np.uint64)
        basis = rand * SAMPLES
        for o in range(-OFFSET_SUCHE, OFFSET_SUCHE + 1):
            crc = int((x[basis + o: basis + o + SAMPLES] * gewicht).sum(dtype=np.uint64)) & 0xFFFFFFFF
            if crc in ziele:
                gefunden[o] = gefunden.get(o, 0) + 1
        if gefunden and max(gefunden.values()) >= 2:
            break
    if not gefunden:
        return None
    return max(gefunden, key=gefunden.get)


# ---------------------------------------------------------------- MusicBrainz

@dataclass
class Album:
    interpret: str
    titel: str
    jahr: str
    release_id: str
    releasegroup_id: str
    interpret_ids: list[str]
    disc: int
    discs: int
    spuren: list[dict]          # je Audiospur: titel, interpret, recording_id, track_id, interpret_ids
    cover: bool
    hinweise: list[str] = field(default_factory=list)


def _credit(ac: list[dict]) -> str:
    return "".join(a.get("name", "") + a.get("joinphrase", "") for a in ac or []).strip()


def musicbrainz(toc: Toc) -> Album | None:
    disc_id = mb_discid(toc)
    url = (f"https://musicbrainz.org/ws/2/discid/{disc_id}?inc=recordings+artist-credits+release-groups"
           f"&fmt=json&toc={urllib.request.quote(mb_toc_text(toc))}")
    roh = _holen(url)
    if not roh:
        return None
    releases = json.loads(roh).get("releases") or []
    if not releases:
        return None
    hinweise = []
    if len(releases) > 1:
        releases.sort(key=lambda r: (not (r.get("cover-art-archive") or {}).get("front"),
                                     r.get("country") not in ("DE", "XE", "XW"), r.get("date") or "9999"))
        hinweise.append(f"MusicBrainz kennt {len(releases)} Ausgaben, genommen: {releases[0]['id']} "
                        f"({releases[0].get('country') or '?'}, {releases[0].get('date') or '?'})")
    r = releases[0]
    medien = r.get("media") or []
    medium = next((m for m in medien if any(d.get("id") == disc_id for d in m.get("discs") or [])), None)
    if medium is None:  # Treffer nur über die TOC-Suche: Medium mit passender Spuranzahl
        medium = next((m for m in medien if m.get("track-count") == len(toc.audio)), None)
        hinweise.append("MusicBrainz-Treffer nur über das Inhaltsverzeichnis, nicht über die Disc-ID")
    if medium is None:
        return None
    spuren = []
    for t in medium.get("tracks") or []:
        rec = t.get("recording") or {}
        ac = t.get("artist-credit") or rec.get("artist-credit") or []
        spuren.append({"titel": t.get("title") or rec.get("title") or "", "interpret": _credit(ac),
                       "recording_id": rec.get("id", ""), "track_id": t.get("id", ""),
                       "interpret_ids": [a["artist"]["id"] for a in ac if a.get("artist")]})
    if len(spuren) != len(toc.audio):
        hinweise.append(f"MusicBrainz nennt {len(spuren)} Titel, die CD hat {len(toc.audio)}")
    rg = r.get("release-group") or {}
    jahr = (r.get("date") or rg.get("first-release-date") or "")[:4]
    return Album(
        interpret=_credit(r.get("artist-credit")), titel=r.get("title") or "", jahr=jahr,
        release_id=r["id"], releasegroup_id=rg.get("id", ""),
        interpret_ids=[a["artist"]["id"] for a in r.get("artist-credit") or [] if a.get("artist")],
        disc=medium.get("position") or 1, discs=len(medien) or 1, spuren=spuren,
        cover=bool((r.get("cover-art-archive") or {}).get("front")), hinweise=hinweise)


def cover_holen(album: Album, ziel: Path) -> bool:
    if not album.cover:
        return False
    try:
        daten = _holen(f"https://coverartarchive.org/release/{album.release_id}/front-1200", timeout=60)
    except CdFehler:
        daten = None
    if not daten:
        return False
    ziel.write_bytes(daten)
    return True


# ---------------------------------------------------------------- FLAC

def sauber(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip().rstrip(". ")
    return name[:120] or "_"


def flac_schreiben(pcm: bytes, ziel: Path, tags: dict[str, str], cover: Path | None, emphasis: bool) -> None:
    wav = ziel.with_suffix(".wav")
    with wave.open(str(wav), "wb") as f:
        f.setnchannels(2)
        f.setsampwidth(2)
        f.setframerate(44100)
        f.writeframes(pcm)
    cmd = ["ffmpeg", "-hide_banner", "-v", "error", "-y", "-i", str(wav)]
    if cover:
        cmd += ["-i", str(cover)]
    cmd += ["-map", "0:a"]
    if cover:
        cmd += ["-map", "1:v", "-c:v", "copy", "-disposition:v", "attached_pic",
                "-metadata:s:v", "title=Album cover", "-metadata:s:v", "comment=Cover (front)"]
    if emphasis:  # Pre-Emphasis rückgängig machen, sonst klingt es zu hell
        cmd += ["-af", "aemphasis=type=cd:mode=reproduction", "-sample_fmt", "s32", "-bits_per_raw_sample", "24"]
    cmd += ["-c:a", "flac", "-compression_level", "8"]
    for k, v in tags.items():
        if v:
            cmd += ["-metadata", f"{k}={v}"]
    cmd += [str(ziel)]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    wav.unlink(missing_ok=True)
    if r.returncode or not ziel.exists():
        raise CdFehler(f"ffmpeg {ziel.name}: {r.stderr.strip()[-200:]}")


def titel_tags(album: Album | None, i: int, nr: int, gesamt: int) -> tuple[str, dict[str, str]]:
    """Dateiname und Tags für die i-te Audiospur (Spurnummer nr von gesamt)."""
    meta = album.spuren[i] if album and i < len(album.spuren) else {}
    titel = meta.get("titel") or f"Titel {nr:02d}"
    praefix = f"{album.disc}." if album and album.discs > 1 else ""
    tags = {"title": titel, "track": f"{nr}/{gesamt}", "TOTALTRACKS": str(gesamt)}
    if album:
        tags.update({
            "artist": meta.get("interpret") or album.interpret, "album": album.titel,
            "album_artist": album.interpret, "date": album.jahr,
            "disc": f"{album.disc}/{album.discs}", "TOTALDISCS": str(album.discs),
            "MUSICBRAINZ_ALBUMID": album.release_id, "MUSICBRAINZ_RELEASEGROUPID": album.releasegroup_id,
            "MUSICBRAINZ_ALBUMARTISTID": "/".join(album.interpret_ids),
            "MUSICBRAINZ_TRACKID": meta.get("recording_id", ""),
            "MUSICBRAINZ_RELEASETRACKID": meta.get("track_id", ""),
            "MUSICBRAINZ_ARTISTID": "/".join(meta.get("interpret_ids") or []),
        })
    return f"{praefix}{nr:02d} - {sauber(titel)}.flac", tags


def neu_taggen(ordner: Path, titel: list[dict], album: Album, gesamt: int) -> None:
    """Schreibt die Tags schon gelesener FLAC-Dateien neu (ohne Neukodieren) und benennt sie um.

    titel: Ergebnis von disc_lesen()["titel"], "datei" wird auf den neuen Namen gesetzt.
    """
    for i, t in enumerate(titel):
        alt = ordner / t["datei"]
        name, tags = titel_tags(album, i, t["nr"], gesamt)
        tmp = ordner / f".neu-{t['nr']:02d}.flac"
        cmd = ["ffmpeg", "-hide_banner", "-v", "error", "-y", "-i", str(alt), "-map", "0", "-c", "copy",
               "-map_metadata", "-1"]
        for k, v in tags.items():
            if v:
                cmd += ["-metadata", f"{k}={v}"]
        r = subprocess.run(cmd + [str(tmp)], capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode or not tmp.exists():
            tmp.unlink(missing_ok=True)
            raise CdFehler(f"ffmpeg {alt.name}: {r.stderr.strip()[-200:]}")
        alt.unlink()
        tmp.rename(ordner / name)
        t["datei"] = name
        t["byte"] = (ordner / name).stat().st_size


def _mmss(sek: float) -> str:
    return f"{int(sek // 60)}:{int(sek % 60):02d}"


def _anzeige(text: str) -> None:
    if sys.stdout.isatty():
        sys.stdout.write("\r" + text.ljust(78)[:78])
        sys.stdout.flush()


# ---------------------------------------------------------------- Ganze Disc

def disc_lesen(laufwerk: str, ordner: Path, offset: int, saetze: list[list[ArEintrag]] | None,
               album: Album | None, nur: set[int] | None = None, log=print) -> dict:
    """Liest alle Audiospuren als FLAC nach ordner. Rückgabe: Ergebnis je Spur und Zusammenfassung."""
    ordner.mkdir(parents=True, exist_ok=True)
    cover = ordner / "cover.jpg"
    hat_cover = bool(album) and cover_holen(album, cover)
    ergebnis: dict = {"titel": [], "fehler": []}
    with Laufwerk(laufwerk) as lw:
        toc = lw.toc()
        audio = toc.audio
        alle = toc.spuren
        for i, spur in enumerate(audio):
            if nur and spur.nr not in nur:
                continue
            idx = alle.index(spur)
            erste, letzte = spur is alle[0], spur is alle[-1] or not alle[idx + 1].audio
            lw.fehler_sektoren = []
            lesungen: list[str] = []
            pcm = b""
            status = ""
            konf = 0
            t0 = time.monotonic()
            for versuch in range(1, LESUNGEN_MAX + 1):
                # Auch vor der ersten Lesung: Direkt im Anschluss an den vorigen Titel lieferte das
                # Laufwerk die Daten um 210 Samples verschoben, nach einem Sprung immer gleich (D0063).
                _anzeige(f"  {spur.nr:02d}  Cache verdrängen ...")
                lw.cache_verdraengen(toc, spur)
                pcm = lw.spur_pcm(toc, spur, offset, lambda a, v=versuch: _anzeige(
                    f"  {spur.nr:02d}  {_mmss(spur.sektoren / 75):>6}  Lesung {v}  {a:4.0%}"))
                v1, v2 = ar_crc(pcm, erste, letzte)
                konf = ar_vergleich(saetze or [], idx, v1, v2)
                if konf and not lw.fehler_sektoren:
                    status = f"AccurateRip ok (Konfidenz {konf})"
                    break
                h = hashlib.sha1(pcm).hexdigest()
                if h in lesungen and not lw.fehler_sektoren:
                    status = ("nicht in AccurateRip" if not saetze else "AccurateRip abweichend") + \
                        f", {versuch} Lesungen, zwei gleich"
                    break
                lesungen.append(h)
            else:
                status = f"FEHLER: {LESUNGEN_MAX} Lesungen, keine zwei gleich"
                if lw.fehler_sektoren:
                    status += f", {len(set(lw.fehler_sektoren))} unlesbare Sektoren"
                ergebnis["fehler"].append(f"Titel {spur.nr}: {status}")
            dauer = time.monotonic() - t0

            name, tags = titel_tags(album, i, spur.nr, len(audio))
            datei = ordner / name
            flac_schreiben(pcm, datei, tags, cover if hat_cover else None, spur.emphasis)
            zeile = f"  {spur.nr:02d}  {_mmss(spur.sektoren / 75):>6}  {_mmss(dauer):>5} min  {status}"
            if sys.stdout.isatty():
                sys.stdout.write("\r" + " " * 78 + "\r")
            log(zeile)
            ergebnis["titel"].append({
                "nr": spur.nr, "datei": datei.name, "dauer_s": round(spur.sektoren / 75),
                "byte": datei.stat().st_size, "accuraterip": status, "konfidenz": konf,
                "crc_v1": f"{v1:08x}", "crc_v2": f"{v2:08x}", "sha1": hashlib.sha1(pcm).hexdigest(),
                "unlesbare_sektoren": len(set(lw.fehler_sektoren)), "emphasis": spur.emphasis})
    return ergebnis


def info(laufwerk: str) -> tuple[Toc, list[list[ArEintrag]], Album | None]:
    with Laufwerk(laufwerk) as lw:
        toc = lw.toc()
    try:
        saetze = ar_abfragen(toc)
    except CdFehler as e:
        print(f"  AccurateRip nicht erreichbar: {e}")
        saetze = []
    album = musicbrainz(toc)
    return toc, saetze, album


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("laufwerk", nargs="?", default="E:")
    ap.add_argument("--offset", action="store_true", help="Laufwerks-Offset per AccurateRip suchen")
    ap.add_argument("--lesen", type=Path, help="Titel als FLAC in diesen Ordner lesen (Test)")
    ap.add_argument("--offset-wert", type=int, default=0)
    ap.add_argument("--titel", help="nur diese Titel, z.B. 1,3")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    toc, saetze, album = info(a.laufwerk)
    id1, id2, cddb = ar_ids(toc)
    print(f"Spuren: {len(toc.spuren)} ({len(toc.audio)} Audio), Länge {_mmss(toc.leadout / 75)}")
    print(f"MusicBrainz-Disc-ID: {mb_discid(toc)}")
    print(f"AccurateRip: {id1:08x}-{id2:08x}-{cddb:08x}, {len(saetze)} Datensätze, "
          f"Konfidenz Titel 1: {[s[0].konfidenz for s in saetze][:8]}")
    if album:
        print(f"Album: {album.interpret} – {album.titel} ({album.jahr}), Disc {album.disc}/{album.discs}, "
              f"Cover {'ja' if album.cover else 'nein'}")
        for h in album.hinweise:
            print(f"  Hinweis: {h}")
        for s, t in zip(toc.audio, album.spuren):
            print(f"  {s.nr:02d}  {_mmss(s.sektoren / 75):>6}  {t['titel']}")
    else:
        print("Album: MusicBrainz kennt die CD nicht")
    if a.offset:
        with Laufwerk(a.laufwerk) as lw:
            o = offset_suchen(lw, toc, saetze)
        print(f"Offset: {o:+d}" if o is not None else "Offset: nicht gefunden")
    if a.lesen:
        nur = {int(x) for x in a.titel.split(",")} if a.titel else None
        e = disc_lesen(a.laufwerk, a.lesen, a.offset_wert, saetze, album, nur)
        print(json.dumps(e["fehler"], ensure_ascii=False) if e["fehler"] else "alle Titel ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
