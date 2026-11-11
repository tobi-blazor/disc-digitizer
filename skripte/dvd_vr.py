"""DVD-Video-Discs (auch von DVD-Rekordern) direkt lesen, ohne MakeMKV.

Zweck:
  - Titeltabelle aus VIDEO_TS lesen (Titel -> Programmkette -> Zellen mit Sektorbereichen)
  - doppelte Titel (gleiche Sektoren) und leere Titel aussortieren
  - Rekorderdaten aus VIDEO_RM/VIDEO_RM.IFO lesen (Panasonic, DVD-VR-Menü):
    Name, Sender, Sendedatum und -uhrzeit, Dauer je Aufnahme
  - einen Titel sektorgenau als MPEG-PS (.vob) kopieren, Lesefehler über kopieren.py
    (retten, nach einiger Zeit fragen: weiter versuchen, überspringen, abbrechen)

Rekorder-Discs sind nicht kopiergeschützt. Bei CSS-geschützten Kauf-DVDs liefert das
Kopieren nur verschlüsselte Daten, die gehören ohnehin nicht in diesen Ablauf.

Aufruf:
  python -I skripte/dvd_vr.py info E:/
  python -I skripte/dvd_vr.py info E:/ --json
  python -I skripte/dvd_vr.py kopieren E:/ 3 ausgabe/dvd/t03.vob

Umgebungsvariablen: keine. Nur Standardbibliothek. Liest nur von der Disc.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import asdict, dataclass, field

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # auch mit python -I
import kopieren  # noqa: E402

SEKTOR = 2048
MIN_DAUER_S = 30  # kürzere Titel gelten als leer (Rekorder legen Platzhalter an)


def _u16(b: bytes, o: int) -> int:
    return int.from_bytes(b[o:o + 2], "big")


def _u32(b: bytes, o: int) -> int:
    return int.from_bytes(b[o:o + 4], "big")


def _bcd(x: int) -> int:
    return (x >> 4) * 10 + (x & 0x0F)


@dataclass
class Aufnahme:
    """Ein Eintrag aus dem Rekorder-Menü (VIDEO_RM.IFO)."""
    name: str
    sender: str | None
    sendezeit: str | None  # ISO, z.B. 2012-01-15T11:30
    dauer_s: int | None


@dataclass
class Titel:
    nr: int                       # laufende Nummer nach dem Aussortieren, ab 1
    dvd_titel: int                # Titelnummer im DVD-Menü
    vts: int
    pgc: int
    dauer_s: int
    zellen: list[tuple[int, int]]  # (erster, letzter Sektor), relativ zu VTS_xx_1.VOB
    groesse_byte: int
    aufnahme: Aufnahme | None = None


@dataclass
class Disc:
    pfad: str
    rekorder: str | None
    titel: list[Titel]
    aussortiert: list[dict] = field(default_factory=list)
    hinweise: list[str] = field(default_factory=list)


def _pgc_dauer(b: bytes, pgc: int) -> int:
    h, m, s = b[pgc + 4], b[pgc + 5], b[pgc + 6]
    return _bcd(h) * 3600 + _bcd(m) * 60 + _bcd(s)


def _pgc_zellen(b: bytes, pgc: int) -> list[tuple[int, int]]:
    anzahl = b[pgc + 3]
    cpt = _u16(b, pgc + 0xE8)
    if not cpt:
        return []
    basis = pgc + cpt
    return [(_u32(b, basis + i * 24 + 8), _u32(b, basis + i * 24 + 20)) for i in range(anzahl)]


def rekorder_lesen(wurzel: str) -> tuple[str | None, list[Aufnahme]]:
    """Liest VIDEO_RM.IFO (Panasonic DVD-VR-Menü). Leere Liste, wenn nicht vorhanden/unbekannt.

    Aufbau (ermittelt an DMR-EHA10, 2026-11-04): Kopf mit Hersteller ab 0x100, Modell ab 0x120.
    Je Aufnahme ein Datensatz ab 0x1800, Länge 0x200, erstes Byte 0x01:
      +0x07..0x0A  Datum BCD: Jahrhundert, Jahr, Monat (untere 5 Bit), Tag
      +0x0C..0x0D  Uhrzeit BCD: Stunde, Minute
      +0x10..0x12  Dauer BCD: h, min, s
      +0x40        Name (Latin-1, 0-terminiert)
      +0x1C9       Sender (5 Zeichen)
    """
    p = os.path.join(wurzel, "VIDEO_RM", "VIDEO_RM.IFO")
    if not os.path.exists(p):
        return None, []
    rm = open(p, "rb").read()
    if not rm.startswith(b"DVDVRMANAGER"):
        return None, []
    m = re.match(rb"dvdvrx\d{3}([a-z]+)", rm[0x100:0x120])
    hersteller = m.group(1).decode() if m else None
    modell = rm[0x120:0x130].split(b"\0")[0].decode("latin-1").strip() or None
    rekorder = " ".join(x for x in (hersteller and hersteller.capitalize(), modell) if x) or None
    aufnahmen: list[Aufnahme] = []
    o = 0x1800
    while o + 0x200 <= len(rm) and rm[o] == 0x01:
        r = rm[o:o + 0x200]
        name = r[0x40:0xC0].split(b"\0")[0].decode("latin-1").strip()
        try:
            jahr = _bcd(r[7]) * 100 + _bcd(r[8])
            monat, tag = _bcd(r[9] & 0x1F), _bcd(r[10])
            std, minute = _bcd(r[12]), _bcd(r[13])
            sendezeit = f"{jahr:04d}-{monat:02d}-{tag:02d}T{std:02d}:{minute:02d}"
            if not (1990 < jahr < 2100 and 1 <= monat <= 12 and 1 <= tag <= 31):
                sendezeit = None
        except Exception:
            sendezeit = None
        dauer = _bcd(r[0x10]) * 3600 + _bcd(r[0x11]) * 60 + _bcd(r[0x12])
        sender = r[0x1C9:0x1CE].decode("latin-1", "replace").strip() or None
        if sender and not re.fullmatch(r"[\w .+&-]+", sender):
            sender = None
        aufnahmen.append(Aufnahme(name or "", sender, sendezeit, dauer or None))
        o += 0x200
    return rekorder, aufnahmen


def disc_lesen(wurzel: str) -> Disc:
    ts = os.path.join(wurzel, "VIDEO_TS")
    vmg = open(os.path.join(ts, "VIDEO_TS.IFO"), "rb").read()
    tt = _u32(vmg, 0xC4) * SEKTOR
    vts_cache: dict[int, bytes] = {}
    roh: list[Titel] = []
    for i in range(_u16(vmg, tt)):
        e = tt + 8 + i * 12
        vts, ttn = vmg[e + 6], vmg[e + 7]
        if vts not in vts_cache:
            vts_cache[vts] = open(os.path.join(ts, f"VTS_{vts:02d}_0.IFO"), "rb").read()
        b = vts_cache[vts]
        ptt, pgcit = _u32(b, 0xC8) * SEKTOR, _u32(b, 0xCC) * SEKTOR
        pgcn = _u16(b, ptt + _u32(b, ptt + 8 + (ttn - 1) * 4))
        pgc = pgcit + _u32(b, pgcit + 8 + (pgcn - 1) * 8 + 4)
        zellen = _pgc_zellen(b, pgc)
        groesse = sum(e2 - s + 1 for s, e2 in zellen) * SEKTOR
        roh.append(Titel(0, i + 1, vts, pgcn, _pgc_dauer(b, pgc), zellen, groesse))

    disc = Disc(wurzel, None, [])
    gesehen: dict[tuple, int] = {}
    for t in roh:
        schluessel = (t.vts, tuple(t.zellen))
        if t.dauer_s < MIN_DAUER_S or t.groesse_byte < 1024 * 1024:
            disc.aussortiert.append({"dvd_titel": t.dvd_titel, "grund": "leer"})
        elif schluessel in gesehen:
            disc.aussortiert.append({"dvd_titel": t.dvd_titel, "grund": f"doppelt zu Titel {gesehen[schluessel]}"})
        else:
            gesehen[schluessel] = t.dvd_titel
            t.nr = len(disc.titel) + 1
            disc.titel.append(t)

    disc.rekorder, aufnahmen = rekorder_lesen(wurzel)
    if aufnahmen:
        if len(aufnahmen) == len(disc.titel):
            for t, a in zip(disc.titel, aufnahmen):
                if a.dauer_s and abs(a.dauer_s - t.dauer_s) > 90:
                    disc.hinweise.append(f"Titel {t.nr}: Dauer {t.dauer_s}s passt nicht zu Rekorder {a.dauer_s}s")
                t.aufnahme = a
        else:
            disc.hinweise.append(f"Rekorder nennt {len(aufnahmen)} Aufnahmen, Disc hat {len(disc.titel)} Titel: nicht zugeordnet")
    return disc


def _vob_dateien(wurzel: str, vts: int) -> list[tuple[str, int]]:
    ts = os.path.join(wurzel, "VIDEO_TS")
    teile = []
    for n in range(1, 10):
        p = os.path.join(ts, f"VTS_{vts:02d}_{n}.VOB")
        if not os.path.exists(p):
            break
        teile.append((p, os.path.getsize(p)))
    return teile


def _ziel_zu_dateien(karte, offsets, von: int, bis: int):
    """Byte [von, bis) im kopierten Titel -> [(VOB-Datei, byte_von, byte_bis)] (über Zellen und Teildateien)."""
    for o, s, e in karte:
        o_ende = o + (e - s + 1) * SEKTOR
        if bis <= o or von >= o_ende:
            continue
        a, b = max(von, o), min(bis, o_ende)
        start, ende = s * SEKTOR + (a - o), s * SEKTOR + (b - o)  # Byte im VTS-Strom
        for x, y, p in offsets:
            if ende <= x or start >= y:
                continue
            yield p, max(start, x) - x, min(ende, y) - x


def titel_dateibereiche(wurzel: str, titel: Titel) -> list[tuple[str, int, int]]:
    """Wo der Titel in den VOB-Dateien liegt: [(datei, byte_von, byte_bis)] (für die Disc-Grafik)."""
    offsets, pos = [], 0
    for p, g in _vob_dateien(wurzel, titel.vts):
        offsets.append((pos, pos + g, p))
        pos += g
    karte, o = [], 0
    for s, e in titel.zellen:
        karte.append((o, s, e))
        o += (e - s + 1) * SEKTOR
    return list(_ziel_zu_dateien(karte, offsets, 0, o))


def titel_kopieren(wurzel: str, titel: Titel, ziel: str, versuche: int = 2, fortschritt=None,
                   fragen: bool | None = None, lesekarte=None) -> dict:
    """Kopiert die Zellen eines Titels sektorgenau in eine .vob-Datei.

    Lesefehler behandelt kopieren.py: sektorweise retten, nach einiger Zeit im Terminal fragen
    (weiter versuchen, überspringen, abbrechen). Unlesbare Sektoren werden mit Nullen gefüllt und
    gezählt. Rückgabe wie kopieren.kopieren(): {"byte", "fehler_sektoren", "luecken", "uebersprungen"}.
    `fortschritt(sektoren, gesamt_sektoren, fehler_sektoren)`. `lesekarte(datei, byte_von, byte_bis, zustand)`
    erfährt jeden gelesenen Bereich in der VOB-Datei (Disc-Grafik). Wirft kopieren.Abgebrochen.
    """
    teile = _vob_dateien(wurzel, titel.vts)
    offsets, pos = [], 0
    for p, g in teile:
        offsets.append((pos, pos + g, p))
        pos += g

    handles: dict[str, object] = {}

    def lesen(sektor: int, anzahl: int) -> bytes:
        start, ende = sektor * SEKTOR, (sektor + anzahl) * SEKTOR
        daten = bytearray()
        for a, b, p in offsets:
            if ende <= a or start >= b:
                continue
            f = handles.get(p)
            if f is None:
                f = handles[p] = open(p, "rb", buffering=0)
            f.seek(max(start, a) - a)
            daten += f.read(min(ende, b) - max(start, a))
        return bytes(daten)

    karte, o = [], 0  # Byte im Ziel -> Zelle auf der Disc
    for s, e in titel.zellen:
        karte.append((o, s, e))
        o += (e - s + 1) * SEKTOR

    def lesen_ziel(pos: int, n: int) -> bytes:
        daten = bytearray()
        for o, s, e in karte:
            o_ende = o + (e - s + 1) * SEKTOR
            if pos + n <= o or pos >= o_ende:
                continue
            a, b = max(pos, o), min(pos + n, o_ende)
            daten += lesen(s + (a - o) // SEKTOR, (b - a) // SEKTOR)
        return bytes(daten)

    melden = None
    if fortschritt:
        def melden(p: int, gesamt: int, fehler: int) -> None:
            fortschritt(p // SEKTOR, gesamt // SEKTOR, fehler)

    karte_dateien = None
    if lesekarte:
        def karte_dateien(von: int, bis: int, zustand: str) -> None:
            for pfad, a, b in _ziel_zu_dateien(karte, offsets, von, bis):
                lesekarte(pfad, a, b, zustand)

    os.makedirs(os.path.dirname(os.path.abspath(ziel)), exist_ok=True)
    try:
        with open(ziel, "wb") as out:
            return kopieren.kopieren(lesen_ziel, o, out, melden, versuche=versuche, fragen=fragen, karte=karte_dateien)
    finally:
        for f in handles.values():
            f.close()


def _als_dict(disc: Disc) -> dict:
    d = asdict(disc)
    for t in d["titel"]:
        t["zellen"] = [list(z) for z in t["zellen"]]
    return d


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("info", help="Titel und Rekorderdaten anzeigen")
    i.add_argument("wurzel")
    i.add_argument("--json", action="store_true")
    k = sub.add_parser("kopieren", help="einen Titel (Nummer nach dem Aussortieren) kopieren")
    k.add_argument("wurzel")
    k.add_argument("nr", type=int)
    k.add_argument("ziel")
    a = ap.parse_args()

    disc = disc_lesen(a.wurzel)
    if a.cmd == "info":
        if a.json:
            print(json.dumps(_als_dict(disc), ensure_ascii=False, indent=1))
            return 0
        print(f"Rekorder: {disc.rekorder or '-'}   Titel: {len(disc.titel)}   aussortiert: {len(disc.aussortiert)}")
        for t in disc.titel:
            au = t.aufnahme
            extra = f"  {au.sendezeit or '?':16} {au.sender or '?':6} {au.name}" if au else ""
            print(f"  {t.nr:2d} (DVD {t.dvd_titel:2d})  {t.dauer_s // 60:3d} min  {t.groesse_byte / 1e9:5.2f} GB{extra}")
        summe = sum(t.dauer_s for t in disc.titel)
        print(f"Summe: {summe / 3600:.2f} h, {sum(t.groesse_byte for t in disc.titel) / 1e9:.2f} GB")
        for h in disc.hinweise:
            print("Hinweis:", h)
        return 0
    titel = next((t for t in disc.titel if t.nr == a.nr), None)
    if not titel:
        print(f"Titel {a.nr} nicht gefunden", file=sys.stderr)
        return 2
    erg = titel_kopieren(a.wurzel, titel, a.ziel)
    print(f"{erg['byte'] / 1e9:.2f} GB geschrieben, Lesefehler: {erg['fehler_sektoren']} Sektoren")
    return 0 if erg["fehler_sektoren"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
