"""DVD-Laufwerk beobachten und jede eingelegte Disc nach 1-roh/<disc>/ einlesen.

Zweck:
  Läuft dauerhaft in einem Terminal. Der Nutzer legt nur Discs nach. Je Disc:
  1. Disc-Nummer: immer die nächste freie, ohne Abfrage. Beschriftung: bei Rekorderdaten
     (Panasonic VIDEO_RM) aus Sendung und Datum, sonst abfragen (leer = Disc-Label).
     --fragen fragt beides wie früher (andere Nummer, Überschreiben).
  2. DVD-Video (VIDEO_TS): Titel mit dvd_vr.py sektorgenau als tNN.vob kopieren,
     dazu die IFO-Dateien und ein Menübild menue.png. Kann dvd_vr.py die Disc nicht lesen,
     springt MakeMKV ein (tNN.mkv, scannt Rekorder-Discs aber sehr lange).
     Daten-DVD: Videodateien samt Begleitdateien (.eit, .meta, ...) kopieren.
     Musik-CD (nur TrackNN.cda): musik_cd.py liest sie mit cd_leser.py als FLAC und legt das Album
     direkt in die Musikbibliothek (Status "einsortiert", Ordner 2-fertig/<disc>/ nur mit disc.json).
  3. disc.json schreiben, fortschritt.csv aktualisieren. Bei Lesefehlern oder Abbruch:
     Status "problem", Ordner nach 3-problem/<disc>/, Tipp zum Reinigen.
     Lesefehler behandelt kopieren.py: retten, nach 2 min fragen (weiter, überspringen, immer
     überspringen, abbrechen). Hat der Nutzer übersprungen, bleibt es bei "eingelesen" mit Hinweis.
  4. Disc auswerfen und einen Ton spielen (bei Problem ein anderer Ton)

  Leere oder unlesbare Disc (Medium erkannt, aber 30 s lang kein Dateisystem): Meldung mit Art des
  Mediums (z.B. DVD-R), Doppelton, Frage, ob die nächste Nummer trotzdem vergeben wird (die Discs sind
  vorab beschriftet). Bei "j" bekommt sie in fortschritt.csv den Status "leer". Dann auswerfen.
  Dasselbe, ohne 30 s Wartezeit und vor der Beschriftungsfrage, bei einer formatierten Disc
  ohne Dateien (z.B. DVD-RW mit leerem UDF-Dateisystem).
  --nummern legt fest, was mit der Nummer passiert: fragen (Standard), immer (Stapel durchnummeriert,
  Nummer ohne Frage als "leer" vergeben), nie (erst testen, keine Nummer; --unnummeriert ist dasselbe).
  Im Ereignis-Modus schaltet das Programm das zur Laufzeit um (stdin "einstellung" nummern).
  Antwortet das Laufwerk 60 s nicht (es beißt sich an einer schwer lesbaren Disc fest), zeigt das
  Skript das mit laufender Zeit an. Bleibt das Laufwerk danach 15 s leer, wurde die Disc
  herausgenommen, und es kommt dieselbe Frage (Status "unlesbar").

  Dieselbe Disc (Volume-Seriennummer) wird in einer Sitzung nur einmal verarbeitet. Ausnahme:
  Ist sie gescheitert und wird neu eingelegt, gibt es einen neuen Versuch. Eine Disc, die schon
  in 3-problem/ liegt, wird dabei unter derselben Nummer neu eingelesen.
  Vor dem Einlesen wartet das Skript, bis DVD_ARBEIT Platz für die ganze Disc plus 5 GB Reserve
  hat (umwandeln.py löscht die Rohdaten jeder fertigen Disc). Die Disc bleibt so lange im Laufwerk.
  Während des Kopierens liegt die Disc unter 1-roh/<disc>.einlesen/, erst am Ende wird
  der Ordner umbenannt. Die anderen Teile sehen also nur fertige Discs.

Aufruf:
  python -I skripte/einlesen.py                  dauerhaft beobachten
  python -I skripte/einlesen.py --einmal         nur die eingelegte Disc
  python -I skripte/einlesen.py --einmal --ohne-auswerfen \\
         --disc D0001 --beschriftung "Maus Jan-März 2012"               ohne Abfrage (Tests)
  python -I skripte/einlesen.py --quelle ausgabe/dvd/testquelle --disc D9001
                                                                         Ordner statt Laufwerk

Ereignis-Modus: Mit DVD_EREIGNISSE=1 meldet das Skript Laufwerk, Disc, Fortschritt, Fragen und Ende als
  "@@ "-JSON-Zeilen auf stdout und nimmt Antworten und "stopp" über stdin entgegen (siehe ereignisse.py und
  docs/ereignis-protokoll.md im Repo disc-digitalisierung). Ohne die Variable ändert sich nichts.

Umgebungsvariablen:
  DVD_EREIGNISSE  1 = Ereignis-Modus für das Programm Disc-Digitalisierung (optional)
  DVD_LAUFWERK  Laufwerk, Standard E:
  DVD_ARBEIT    Arbeitsordner, Standard <repo>/ausgabe/dvd (siehe gemeinsam.py)
  MAKEMKVCON    Pfad zu makemkvcon64.exe, Standard C:\\Program Files (x86)\\MakeMKV\\makemkvcon64.exe

Nur Standardbibliothek. Externe Programme: ffmpeg/ffprobe (im PATH), MakeMKV (nur als Ausweg).
Liest nur von der Disc, schreibt nur unter DVD_ARBEIT.
"""
from __future__ import annotations

import argparse
import ctypes
import os
import re
import shutil
import struct
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # auch mit python -I
import dvd_vr  # noqa: E402
import ereignisse  # noqa: E402
import gemeinsam as g  # noqa: E402
import kopieren  # noqa: E402
import musik_cd  # noqa: E402

POLL_S = 3
MAKEMKVCON = os.environ.get("MAKEMKVCON") or r"C:\Program Files (x86)\MakeMKV\makemkvcon64.exe"
VIDEO_ENDUNGEN = {".ts", ".mpg", ".mpeg", ".m2ts", ".mts", ".rec", ".avi", ".mkv", ".mp4", ".vob", ".trp"}
BEGLEIT_ENDUNGEN = {".eit", ".meta", ".txt", ".xml", ".ap", ".sc", ".cuts", ".nfo", ".info"}
PLATZ_RESERVE = 5 * 10**9  # frei bleibend auf DVD_ARBEIT, zusätzlich zur Disc
PLATZ_POLL_S = 60
OHNE_INHALT_S = 30  # so lange muss eine Disc ohne Dateisystem im Laufwerk liegen, bis sie als leer gilt
HAENGT_S = 60  # antwortet das Laufwerk so lange nicht, wird das angezeigt (Anlaufen dauert bis ca. 30 s)
RAUS_S = 15  # so lange muss das Laufwerk nach dem Hängen leer bleiben, bis die Disc als herausgenommen gilt
# Nummer bei leerer/unlesbarer Disc (--nummern, im Ereignis-Modus zur Laufzeit umschaltbar)
NUMMERN = {"fragen": "nachfragen, ob die Nummer vergeben wird",
           "immer": "Nummer immer vergeben (Stapel ist durchnummeriert)",
           "nie": "keine Nummer (erst testen, Nummer danach draufschreiben)"}

try:
    sys.stdout.reconfigure(errors="replace", line_buffering=True)
except Exception:
    pass
TTY = sys.stdout.isatty() and not ereignisse.AKTIV  # im Ereignis-Modus keine \r-Zeilen


class Uebersprungen(Exception):
    """Nutzer hat die Disc übersprungen oder die Nummer ist belegt (mit --disc)."""


class Doppelt(Uebersprungen):
    """Disc als Duplikat einer schon eingelesenen vermerkt (Status "doppelt"), nichts kopiert."""

    def __init__(self, text: str, disc: str | None) -> None:
        super().__init__(text)
        self.disc = disc


# ---------------------------------------------------------------- Ausgabe

def gb(byte: int) -> str:
    return f"{byte / 1e9:.2f}".replace(".", ",")


def zahl(x: float, stellen: int = 1) -> str:
    return f"{x:.{stellen}f}".replace(".", ",")


def mmss(s: float) -> str:
    s = int(round(s))
    return f"{s // 60}:{s % 60:02d}"


class Zeile:
    """Fortschritt in einer Konsolenzeile. Im Terminal überschrieben, sonst nur das Ergebnis."""

    def __init__(self, praefix: str):
        self.praefix = praefix
        self.zuletzt = 0.0

    def zwischen(self, text: str) -> None:
        if TTY and time.monotonic() - self.zuletzt > 1:
            self.zuletzt = time.monotonic()
            sys.stdout.write(f"\r{self.praefix}{text}".ljust(79)[:79])
            sys.stdout.flush()

    def ende(self, text: str) -> None:
        if TTY:
            sys.stdout.write("\r" + " " * 79 + "\r")
        print(f"{self.praefix}{text}")


class Fortschritt:
    """Gesamtfortschritt einer Disc für die Ereignisse (nur im Ereignis-Modus wirksam)."""

    def __init__(self, disc: str, byte_gesamt: int | None, titel_anzahl: int | None, schritt: str = "kopieren"):
        self.disc, self.byte_gesamt, self.titel_anzahl, self.schritt = disc, byte_gesamt, titel_anzahl, schritt
        self.t0 = time.monotonic()
        self.vorher = 0  # Byte der schon fertigen Titel

    def melden(self, titel_nr: int | None, titel: str | None, byte_titel: int, fehler: int,
               sofort: bool = False) -> None:
        if not ereignisse.AKTIV:
            return
        byte = self.vorher + byte_titel
        gesamt = self.byte_gesamt or 0
        prozent = min(100.0, round(byte * 100 / gesamt, 1)) if gesamt else None
        s = max(time.monotonic() - self.t0, 0.1)
        rate = byte / 1e6 / s
        rest = int((gesamt - byte) / (byte / s)) if gesamt and byte > 0 else None
        ereignisse.fortschritt("einlesen", self.disc, sofort=sofort, titel=titel, titel_nr=titel_nr,
                               titel_anzahl=self.titel_anzahl, byte=byte, byte_gesamt=gesamt or None,
                               prozent=prozent, fehler_sektoren=fehler, rate_mb_s=round(rate, 1),
                               rest_s=rest, schritt=self.schritt)


_medium_merker: dict = {}  # Medium der Disc, die gerade eingelesen wird (Kapazität der Disc-Grafik)
_lw_zustand: dict = {"z": None, "label": None}  # zuletzt gemeldeter Laufwerkszustand


def laufwerk_melden(zustand: str, label: str | None = None, art: str | None = None, seit_s: float = 0) -> None:
    """Ereignis "laufwerk" nur bei Zustandsänderung."""
    if not ereignisse.AKTIV or (_lw_zustand["z"], _lw_zustand["label"]) == (zustand, label):
        return
    _lw_zustand["z"], _lw_zustand["label"] = zustand, label
    ereignisse.senden("laufwerk", zustand=zustand, label=label, medium=art, seit_s=int(seit_s))


# ---------------------------------------------------------------- Windows: Laufwerk

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.CreateFileW.restype = wintypes.HANDLE
_k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                             wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
_k32.DeviceIoControl.restype = wintypes.BOOL
_k32.DeviceIoControl.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
                                 wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
_k32.CloseHandle.argtypes = [wintypes.HANDLE]
_k32.SetErrorMode(0x0001 | 0x8000)  # keine "Kein Datenträger"-Dialoge


def laufwerk_norm(lw: str) -> str:
    """'e', 'E:', 'E:\\' -> 'E:'"""
    return lw.strip().rstrip("\\/").upper()[:1] + ":"


def volume_info(wurzel: str) -> tuple[str, int] | None:
    """(Label, Seriennummer) der eingelegten Disc oder None, wenn keine lesbar ist."""
    name = ctypes.create_unicode_buffer(261)
    fs = ctypes.create_unicode_buffer(261)
    serie, maxlen, flags = wintypes.DWORD(), wintypes.DWORD(), wintypes.DWORD()
    ok = _k32.GetVolumeInformationW(ctypes.c_wchar_p(wurzel), name, 261, ctypes.byref(serie),
                                    ctypes.byref(maxlen), ctypes.byref(flags), fs, 261)
    if not ok:
        return None
    try:
        os.listdir(wurzel)
    except OSError:
        return None
    return name.value, serie.value


def laufwerk_meta(wurzel: str, seriennummer: int | None, quelle_ordner: bool) -> dict:
    """Angaben zum Datenträger für die Metadaten-Anzeige (nur lesend, Fehler werden verschluckt)."""
    m: dict = {"laufwerk": wurzel}
    if seriennummer is not None:
        m["volume_seriennummer"] = f"{seriennummer >> 16:04X}-{seriennummer & 0xFFFF:04X}"
    try:
        fs = ctypes.create_unicode_buffer(261)
        if _k32.GetVolumeInformationW(ctypes.c_wchar_p(wurzel), None, 0, None, None, None, fs, 261):
            m["dateisystem"] = fs.value
    except Exception:
        pass
    try:
        u = shutil.disk_usage(wurzel)
        m["belegt_byte"], m["kapazitaet_byte"] = u.used, u.total
    except OSError:
        pass
    if not quelle_ordner:
        try:
            m["medium"] = medium(laufwerk_norm(wurzel))
            m["medienwechsel_zaehler"] = wechselzaehler(laufwerk_norm(wurzel))
        except Exception:
            pass
    return m


PROFILE = {0x08: "CD-ROM", 0x09: "CD-R", 0x0A: "CD-RW", 0x10: "DVD-ROM", 0x11: "DVD-R", 0x12: "DVD-RAM",
           0x13: "DVD-RW", 0x14: "DVD-RW", 0x15: "DVD-R DL", 0x16: "DVD-R DL", 0x1A: "DVD+RW", 0x1B: "DVD+R",
           0x2B: "DVD+R DL", 0x40: "BD-ROM", 0x41: "BD-R", 0x42: "BD-R", 0x43: "BD-RE"}


def medium(laufwerk: str) -> str | None:
    """Art des eingelegten Mediums (z.B. "DVD-R"), "Medium" wenn unbekannt, None ohne Medium.

    Nur lesend: IOCTL_STORAGE_CHECK_VERIFY2 und IOCTL_CDROM_GET_CONFIGURATION (aktuelles Profil),
    beides ohne Adminrechte. Meldet auch leere Rohlinge, die kein Dateisystem haben.
    """
    IOCTL_STORAGE_CHECK_VERIFY2, IOCTL_CDROM_GET_CONFIGURATION = 0x2D0800, 0x24058
    h = _k32.CreateFileW("\\\\.\\" + laufwerk, 0x80000000, 0x3, None, 3, 0, None)
    if not h or h == ctypes.c_void_p(-1).value:
        return None
    try:
        n = wintypes.DWORD()
        da = bool(_k32.DeviceIoControl(h, IOCTL_STORAGE_CHECK_VERIFY2, None, 0, None, 0, ctypes.byref(n), None))
        ein = ctypes.create_string_buffer(struct.pack("<IIQQ", 0, 1, 0, 0))  # Feature 0, nur aktuelles
        aus = ctypes.create_string_buffer(64)
        profil = 0
        if _k32.DeviceIoControl(h, IOCTL_CDROM_GET_CONFIGURATION, ein, len(ein.raw), aus, 64, ctypes.byref(n), None) \
                and n.value >= 8:
            profil = struct.unpack(">H", aus.raw[6:8])[0]
    finally:
        _k32.CloseHandle(h)
    if not da and not profil:
        return None
    return PROFILE.get(profil, f"Profil 0x{profil:04x}" if profil else "Medium")


def _cluster_byte(wurzel: str) -> int | None:
    spc, bps, frei, gesamt = wintypes.DWORD(), wintypes.DWORD(), wintypes.DWORD(), wintypes.DWORD()
    if not _k32.GetDiskFreeSpaceW(ctypes.c_wchar_p(wurzel), ctypes.byref(spc), ctypes.byref(bps),
                                  ctypes.byref(frei), ctypes.byref(gesamt)):
        return None
    return spc.value * bps.value


def datei_lage(pfad: str) -> list[tuple[int, int, int]] | None:
    """Physische Lage einer Datei auf dem Datenträger: [(byte_von, byte_bis, sektor_start)] mit Sektoren zu
    2048 Byte ab Anfang des Dateisystems (FSCTL_GET_RETRIEVAL_POINTERS, ohne Adminrechte, nur lesend).
    Auf DVDs (UDF) ist ein Cluster ein Sektor, z.B. VTS_01_1.VOB ab Sektor 16384. None, wenn nicht ermittelbar."""
    wurzel = os.path.splitdrive(os.path.abspath(pfad))[0] + "\\"
    cluster = _cluster_byte(wurzel)
    if not cluster:
        return None
    h = _k32.CreateFileW(pfad, 0x80000000, 0x7, None, 3, 0, None)
    if not h or h == ctypes.c_void_p(-1).value:
        return None
    try:
        start = ctypes.c_longlong(0)
        groesse = 4096
        while True:
            puffer = ctypes.create_string_buffer(groesse)
            n = wintypes.DWORD()
            if _k32.DeviceIoControl(h, 0x00090073, ctypes.byref(start), 8, puffer, groesse, ctypes.byref(n), None):
                break
            if ctypes.get_last_error() == 234 and groesse < 1 << 22:  # ERROR_MORE_DATA: größerer Puffer
                groesse *= 8
                continue
            return None
        anzahl = int.from_bytes(puffer[0:4], "little")
        vcn = int.from_bytes(puffer[8:16], "little", signed=True)
        lage = []
        for i in range(anzahl):
            o = 16 + 16 * i
            naechste = int.from_bytes(puffer[o:o + 8], "little", signed=True)
            lcn = int.from_bytes(puffer[o + 8:o + 16], "little", signed=True)
            if lcn >= 0:  # -1 = nicht belegt (sparse)
                lage.append((vcn * cluster, naechste * cluster, lcn * cluster // 2048))
            vcn = naechste
        return lage or None
    finally:
        _k32.CloseHandle(h)


def disc_sektoren(wurzel: str) -> int | None:
    """Belegte Länge des Dateisystems in 2048-Byte-Sektoren (Volume-Größe)."""
    try:
        return shutil.disk_usage(wurzel).total // 2048
    except OSError:
        return None


def kapazitaet_sektoren(medium_name: str | None, belegt: int | None) -> int:
    """Physische Kapazität für die Disc-Grafik (Rohling-Größe), mindestens die belegte Länge."""
    m = (medium_name or "").upper()
    if m.startswith("CD"):
        k = 360_000          # 80 min
    elif "DL" in m:
        k = 4_173_824        # DVD±R DL (Grafik vereinfacht: eine Spirale)
    elif m.startswith("BD"):
        k = 12_219_392       # BD-R 25 GB
    else:
        k = 2_295_104        # DVD±R/RW einlagig, 4,7 GB
    return max(k, belegt or 0)


def wechselzaehler(laufwerk: str) -> int | None:
    """Zähler der Medienwechsel (IOCTL_STORAGE_CHECK_VERIFY mit ULONG-Ausgabe), None ohne Medium.

    Ändert er sich, wurde die Disc herausgenommen und wieder eingelegt, auch wenn das zwischen zwei
    Abfragen passiert ist. Beispiel LG BH16NS55: 197.
    """
    h = _k32.CreateFileW("\\\\.\\" + laufwerk, 0x80000000, 0x3, None, 3, 0, None)
    if not h or h == ctypes.c_void_p(-1).value:
        return None
    try:
        z, n = wintypes.DWORD(), wintypes.DWORD()
        if _k32.DeviceIoControl(h, 0x2D4800, None, 0, ctypes.byref(z), 4, ctypes.byref(n), None) and n.value == 4:
            return z.value
        return None
    finally:
        _k32.CloseHandle(h)


def auswerfen(laufwerk: str) -> bool:
    """Wirft die Disc aus: Volume sperren, aushängen, Sperre lösen, auswerfen. Ausweg: MCI."""
    GENERIC_READ, FILE_SHARE_RW, OPEN_EXISTING = 0x80000000, 0x3, 3
    FSCTL_LOCK_VOLUME, FSCTL_DISMOUNT_VOLUME = 0x90018, 0x90020
    IOCTL_STORAGE_MEDIA_REMOVAL, IOCTL_STORAGE_EJECT_MEDIA = 0x2D4804, 0x2D4808
    ungueltig = ctypes.c_void_p(-1).value
    ok = False
    h = _k32.CreateFileW("\\\\.\\" + laufwerk, GENERIC_READ, FILE_SHARE_RW, None, OPEN_EXISTING, 0, None)
    if h and h != ungueltig:
        try:
            def ioctl(code: int, puffer=None, groesse: int = 0) -> bool:
                n = wintypes.DWORD()
                return bool(_k32.DeviceIoControl(h, code, puffer, groesse, None, 0, ctypes.byref(n), None))

            for _ in range(5):
                if ioctl(FSCTL_LOCK_VOLUME):
                    break
                time.sleep(0.5)
            ioctl(FSCTL_DISMOUNT_VOLUME)
            erlauben = ctypes.c_ubyte(0)  # PREVENT_MEDIA_REMOVAL.PreventMediaRemoval = FALSE
            ioctl(IOCTL_STORAGE_MEDIA_REMOVAL, ctypes.byref(erlauben), 1)
            ok = ioctl(IOCTL_STORAGE_EJECT_MEDIA)
        finally:
            _k32.CloseHandle(h)
    if not ok:
        try:
            mci = ctypes.WinDLL("winmm").mciSendStringW
            if mci(f"open {laufwerk} type cdaudio alias dvdlw", None, 0, None) == 0:
                ok = mci("set dvdlw door open", None, 0, None) == 0
                mci("close dvdlw", None, 0, None)
        except Exception:
            pass
    return ok


def ton(ok: bool) -> None:
    if ereignisse.AKTIV:
        return  # im Ereignis-Modus spielt das Programm den Ton (einstellbar)
    try:
        import winsound
        if ok:
            for f in (660, 880, 1175):
                winsound.Beep(f, 140)
        else:
            for _ in range(3):
                winsound.Beep(330, 450)
                time.sleep(0.12)
    except Exception:
        pass


# ---------------------------------------------------------------- Disc-Nummern

def _nummer(name: str) -> int | None:
    m = re.fullmatch(r"D(\d{4})(\.einlesen)?", name)
    return int(m.group(1)) if m else None


def naechste_nummer() -> str:
    nummern = [_nummer(n) for n in g.fortschritt_lesen()]
    for basis in (g.ROH, g.FERTIG, g.PROBLEM):
        if basis.exists():
            # Reste abgebrochener Läufe (<disc>.einlesen) zählen nicht, die Nummer wird wieder vergeben
            nummern += [_nummer(p.name) for p in basis.iterdir() if p.is_dir() and not p.name.endswith(".einlesen")]
    # D9000 und höher sind Testnummern und zählen nicht für den Vorschlag
    return g.disc_name(max([n for n in nummern if n is not None and n < 9000], default=0) + 1)


def belegt(disc: str) -> tuple[str, bool] | None:
    """Wo die Disc-Nummer schon vorkommt: (Beschreibung, überschreibbar) oder None."""
    if (g.FERTIG / disc).exists():
        return f"{g.FERTIG.name}/{disc}", False
    for basis in (g.ROH, g.PROBLEM):
        if (basis / disc).exists():
            return f"{basis.name}/{disc}", True
    zeile = g.fortschritt_lesen().get(disc)
    if zeile:
        return f"fortschritt.csv, Status {zeile['status']}", zeile["status"] in ("eingelesen", "problem")
    return None


def discs_mit_serie(seriennummer: int) -> list[str]:
    """Alle eingelesenen Discs mit dieser Seriennummer (z.B. Original und zweite Kopie), sortiert."""
    erg = []
    for basis in (g.ROH, g.FERTIG, g.PROBLEM):
        if basis.exists():
            for p in basis.glob("D*/disc.json"):
                try:
                    d = g.disc_json_lesen(p.parent)
                    if d.get("seriennummer") is not None and int(d["seriennummer"]) == seriennummer:
                        erg.append(d["disc"])
                except Exception:
                    pass
    return sorted(set(erg))


def bekannte_seriennummern() -> dict[int, str]:
    erg = {}
    for basis in (g.ROH, g.FERTIG, g.PROBLEM):
        if basis.exists():
            for p in basis.glob("D*/disc.json"):
                try:
                    d = g.disc_json_lesen(p.parent)
                    if d.get("seriennummer") is not None:
                        erg[int(d["seriennummer"])] = d["disc"]
                except Exception:
                    pass
    return erg


def beschriftung_aus_rekorder(wurzel: str) -> str | None:
    """Beschriftung aus den Rekorderdaten (Panasonic VIDEO_RM), z.B. 'Wissen macht Ah! 2011-09-26 bis 2011-10-03'.

    None, wenn die Disc keine Aufnahmen mit Namen führt.
    """
    try:
        _, aufnahmen = dvd_vr.rekorder_lesen(wurzel)
    except Exception:
        return None
    namen = list(dict.fromkeys(a.name.strip() for a in aufnahmen if a.name and a.name.strip()))
    if not namen:
        return None
    tage = sorted(a.sendezeit[:10] for a in aufnahmen if a.sendezeit)
    text = ", ".join(namen[:3]) + (f" u.a. ({len(namen)} Sendungen)" if len(namen) > 3 else "")
    if tage:
        text += f" {tage[0]}" + (f" bis {tage[-1]}" if tage[-1] != tage[0] else "")
    return text


def disc_abfragen(vorgabe_disc: str | None, vorgabe_beschriftung: str | None,
                  auto_beschriftung: str | None = None, fragen: bool = False,
                  label: str = "") -> tuple[str, str, bool]:
    """Liefert (disc, beschriftung, überschreiben). Wirft Uebersprungen.

    Ohne --fragen: nächste freie Nummer ohne Abfrage. Die Beschriftung kommt aus den Rekorderdaten
    (auto_beschriftung), sonst wird nur sie abgefragt (leer = Disc-Label).
    """
    if not vorgabe_disc and not fragen:
        beschriftung = vorgabe_beschriftung or auto_beschriftung
        if not beschriftung:
            hilfe = f"leer = {label}, " if label else ""
            eingabe = ereignisse.frage(
                "beschriftung", f"Beschriftung (was auf der Disc steht; {hilfe}s = überspringen)",
                freitext=True, vorgabe=label or None,
                prompt=f"Beschriftung (was auf der Disc steht; {hilfe}s = überspringen): ").strip()
            if eingabe.lower() == "s":
                raise Uebersprungen("übersprungen")
            beschriftung = eingabe or label
        disc = naechste_nummer()
        print(f"\n  >>> {disc} <<<  bitte diese Nummer auf die Disc schreiben")
        return disc, beschriftung, False
    if vorgabe_disc:
        try:
            disc = g.disc_name(vorgabe_disc)
        except ValueError:
            raise Uebersprungen(f"ungültige Disc-Nummer {vorgabe_disc!r}") from None
        b = belegt(disc)
        if b:
            raise Uebersprungen(f"{disc} gibt es schon ({b[0]}), Abbruch statt Überschreiben")
        return disc, vorgabe_beschriftung or "", False
    while True:
        vorschlag = naechste_nummer()
        eingabe = ereignisse.frage("disc_nummer", f"Disc-Nummer [{vorschlag}] (s = überspringen)", freitext=True,
                                   vorgabe=vorschlag, prompt=f"Disc-Nummer [{vorschlag}] (s = überspringen): ").strip()
        if eingabe.lower() == "s":
            raise Uebersprungen("übersprungen")
        try:
            disc = g.disc_name(eingabe or vorschlag)
        except ValueError:
            print("  Ungültig, Beispiel: 7 oder D0007")
            continue
        ueberschreiben = False
        b = belegt(disc)
        if b:
            wo, erlaubt = b
            if not erlaubt:
                print(f"  {disc} gibt es schon ({wo}), bitte andere Nummer.")
                continue
            a = ereignisse.frage(
                "ueberschreiben", f"{disc} gibt es schon ({wo}).",
                optionen=[("a", "andere Nummer"), ("ü", "überschreiben"), ("s", "überspringen")],
                vorgabe="a", disc=disc,
                prompt=f"  {disc} gibt es schon ({wo}). [a]ndere Nummer, [ü]berschreiben, [s] überspringen: ")
            a = a.strip().lower()[:1]
            if a == "s":
                raise Uebersprungen("übersprungen")
            if a not in ("ü", "u"):
                continue
            ueberschreiben = True
        beschriftung = ereignisse.frage("beschriftung", "Beschriftung (was auf der Disc steht)", freitext=True,
                                        disc=disc, prompt="Beschriftung (was auf der Disc steht): ").strip()
        return disc, beschriftung, ueberschreiben


# ---------------------------------------------------------------- Hilfen

def ffprobe_dauer(datei: Path) -> float | None:
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                            "-of", "default=nw=1:nk=1", str(datei)],
                           capture_output=True, text=True, timeout=120)
        return float(r.stdout.strip())
    except Exception:
        return None


def menuebild(wurzel: str, ziel: Path) -> str | None:
    """Zieht ein Bild aus dem DVD-Menü. Rückgabe: Hinweis bei Misserfolg, sonst None."""
    for name in ("VIDEO_TS.VOB", "VTS_01_0.VOB"):
        q = Path(wurzel) / "VIDEO_TS" / name
        if not q.exists() or q.stat().st_size == 0:
            continue
        try:
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(q), "-map", "0:v:0", "-frames:v", "1",
                            "-update", "1", str(ziel)], capture_output=True, timeout=60)
        except Exception:
            continue
        if ziel.exists() and ziel.stat().st_size > 0:
            return None
    return "kein Menübild (kein Menü-VOB mit Bild)"


def datei_kopieren(quelle: Path, ziel: Path, fortschritt=None, karte=None) -> dict:
    """Kopiert eine Datei von der Disc. Lesefehler behandelt kopieren.py (retten, nach einiger Zeit
    fragen: weiter versuchen, überspringen, abbrechen). Rückgabe wie kopieren.kopieren()."""
    ziel.parent.mkdir(parents=True, exist_ok=True)
    groesse = quelle.stat().st_size
    with open(quelle, "rb", buffering=0) as f, open(ziel, "wb") as out:
        def lesen(pos: int, n: int) -> bytes:
            f.seek(pos)
            return f.read(n)

        erg = kopieren.kopieren(lesen, groesse, out, fortschritt, karte=karte)
    st = quelle.stat()
    os.utime(ziel, (st.st_atime, st.st_mtime))  # Aufnahmezeit erhalten
    return erg


def fehler_text(fehler: int) -> str:
    return f"  Lesefehler: {fehler} Sektoren" if fehler else ""


# ---------------------------------------------------------------- DVD-Video

def neue_lesekarte(wurzel: str, disc: str) -> "ereignisse.Lesekarte":
    """Lesekarte mit echter Lage der Dateien (Sektoren) und Kapazität des Mediums."""
    belegt = disc_sektoren(wurzel)
    return ereignisse.Lesekarte(disc, belegt, kapazitaet_sektoren(_medium_merker.get("medium"), belegt), datei_lage)


def _titel_meta(geplant: list, daten: dict) -> list[dict]:
    """Geplante DVD-Titel (dvd_vr) samt Angaben der schon kopierten Titel, für die Metadaten-Anzeige."""
    fertig = {t.get("nr"): t for t in daten["titel"]}
    aus = []
    for t in geplant:
        d = asdict(t)
        d["zellen"] = len(t.zellen)
        d.update(fertig.get(t.nr, {}))
        d.setdefault("lesefehler_sektoren", None)
        aus.append(d)
    return aus


def dvd_video_einlesen(wurzel: str, ordner: Path, daten: dict) -> None:
    try:
        disc = dvd_vr.disc_lesen(wurzel)
        if not disc.titel:
            raise ValueError("keine Titel gefunden")
    except Exception as e:
        print(f"  dvd_vr.py kann die Disc nicht lesen ({e}), Ausweg MakeMKV (kann lange dauern)")
        ereignisse.meldung("warnung", f"dvd_vr.py kann die Disc nicht lesen ({e}), Ausweg MakeMKV", daten["disc"])
        daten["hinweise"].append(f"dvd_vr.py: {e}")
        makemkv_einlesen(wurzel, ordner, daten, quelle_ordner=not re.fullmatch(r"[A-Za-z]:[\\/]?", wurzel))
        return

    daten["rekorder"] = disc.rekorder
    daten["aussortiert"] = disc.aussortiert
    daten["hinweise"] += disc.hinweise
    daten["quelle"] = "dvd_vr"
    summe = sum(t.groesse_byte for t in disc.titel)
    print(f"  DVD-Video, Rekorder {disc.rekorder or '-'}: {len(disc.titel)} Titel, "
          f"{zahl(sum(t.dauer_s for t in disc.titel) / 3600, 2)} h, {gb(summe)} GB, aussortiert {len(disc.aussortiert)}")

    for name in ("VIDEO_TS/VIDEO_TS.IFO", "VIDEO_RM/VIDEO_RM.IFO",
                 *(f"VIDEO_TS/VTS_{v:02d}_0.IFO" for v in sorted({t.vts for t in disc.titel}))):
        q = Path(wurzel) / name
        if q.exists():
            try:
                shutil.copyfile(q, ordner / q.name)
            except OSError as e:
                daten["hinweise"].append(f"{q.name} nicht kopiert: {e}")
    h = menuebild(wurzel, ordner / "menue.png")
    if h:
        print(f"  Hinweis: {h}")
        daten["hinweise"].append(h)

    fort = Fortschritt(daten["disc"], summe, len(disc.titel))
    karte = neue_lesekarte(wurzel, daten["disc"])
    karte.geplant([b for t in disc.titel for b in dvd_vr.titel_dateibereiche(wurzel, t)])
    ereignisse.metadaten(daten["disc"], daten, titel=_titel_meta(disc.titel, daten))
    for t in disc.titel:
        datei = f"t{t.nr:02d}.vob"
        zeile = Zeile(f"  {datei}  {mmss(t.dauer_s):>6}  {gb(t.groesse_byte)} GB  ")
        t0 = time.monotonic()
        fort.melden(t.nr, f"t{t.nr:02d}", 0, daten["lesefehler_sektoren"], sofort=True)
        kopieren.DISC.byte_pro_sekunde = t.groesse_byte / t.dauer_s if t.dauer_s else None

        def fortschritt(sek: int, gesamt: int, fehler: int = 0, *, zeile=zeile, t0=t0, t=t) -> None:
            s = time.monotonic() - t0
            zeile.zwischen(f"{sek * 100 // max(gesamt, 1):3d} %  {zahl(sek * dvd_vr.SEKTOR / 1e6 / max(s, 0.1)):>5} MB/s"
                           + fehler_text(fehler))
            fort.melden(t.nr, f"t{t.nr:02d}", sek * dvd_vr.SEKTOR, daten["lesefehler_sektoren"] + fehler)

        try:
            erg = dvd_vr.titel_kopieren(wurzel, t, str(ordner / datei), fortschritt=fortschritt,
                                         lesekarte=karte.bereich)
        except kopieren.Abgebrochen:
            zeile.ende("abgebrochen")
            raise
        s = time.monotonic() - t0
        daten["lesefehler_sektoren"] += erg["fehler_sektoren"]
        fort.vorher += erg["byte"]
        karte.senden()
        fort.melden(t.nr, f"t{t.nr:02d}", 0, daten["lesefehler_sektoren"], sofort=True)
        status = "ok" if not erg["fehler_sektoren"] else (
            f"LESEFEHLER {erg['fehler_sektoren']} Sektoren bei {kopieren.luecken_text(erg['luecken'], erg['byte'])}")
        zeile.ende(f"{mmss(s):>5} min  {zahl(erg['byte'] / 1e6 / max(s, 0.1)):>5} MB/s  {status}")
        eintrag = {
            "nr": t.nr, "datei": datei, "dauer_s": t.dauer_s, "dvd_titel": t.dvd_titel,
            "aufnahme": asdict(t.aufnahme) if t.aufnahme else None,
            "byte": erg["byte"], "lesefehler_sektoren": erg["fehler_sektoren"],
        }
        if erg["luecken"]:
            eintrag["luecken_byte"] = erg["luecken"]
        daten["titel"].append(eintrag)
        ereignisse.metadaten(daten["disc"], daten, titel=_titel_meta(disc.titel, daten))


def makemkv_einlesen(wurzel: str, ordner: Path, daten: dict, quelle_ordner: bool = False) -> None:
    """Ausweg: MakeMKV schreibt alle Titel als MKV, danach Umbenennen in tNN.mkv."""
    if not Path(MAKEMKVCON).exists():
        raise RuntimeError(f"MakeMKV nicht gefunden: {MAKEMKVCON}")
    daten["quelle"] = "makemkv"
    if quelle_ordner:
        spec = f"file:{wurzel}"
    else:
        spec = "disc:0"
        try:  # Index des Laufwerks aus der Laufwerksliste (schnell, ohne Scan)
            r = subprocess.run([MAKEMKVCON, "-r", "--cache=1", "info", "disc:9999"],
                               capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
            for z in r.stdout.splitlines():
                m = re.match(r'DRV:(\d+),.*,"([A-Z]:)\\?"$', z)
                if m and m.group(2) == laufwerk_norm(wurzel):
                    spec = f"disc:{m.group(1)}"
        except Exception:
            pass
    zeile = Zeile(f"  MakeMKV {spec}  ")
    t0 = time.monotonic()
    meldungen: list[str] = []
    lesefehler = 0
    p = subprocess.Popen([MAKEMKVCON, "-r", "--progress=-same", "--minlength=30", "mkv", spec, "all", str(ordner)],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                         encoding="utf-8", errors="replace")
    assert p.stdout
    for z in p.stdout:
        if z.startswith("PRGV:"):
            try:
                _, gesamt, maximum = (int(x) for x in z[5:].split(","))
                zeile.zwischen(f"{gesamt * 100 // max(maximum, 1):3d} %  {mmss(time.monotonic() - t0)} min")
                if ereignisse.AKTIV:
                    ereignisse.fortschritt("einlesen", daten["disc"], prozent=round(gesamt * 100 / max(maximum, 1), 1),
                                           schritt="makemkv", fehler_sektoren=lesefehler)
            except ValueError:
                pass
        elif z.startswith("MSG:"):
            teile = z[4:].split(",", 3)
            code = int(teile[0]) if teile[0].isdigit() else 0
            text = teile[3].split('","')[0].strip('"\n') if len(teile) > 3 else z
            if code == 2003:  # Lesefehler beim Lesen der Disc
                lesefehler += 1
            if 2000 <= code < 3000 or code in (5003, 5004, 5010):
                meldungen.append(text)
    rc = p.wait()
    mkvs = sorted(ordner.glob("*.mkv"), key=lambda f: int(m.group(1)) if (m := re.search(r"_t(\d+)\.mkv$", f.name)) else 999)
    zeile.ende(f"{mmss(time.monotonic() - t0)} min  {len(mkvs)} Dateien  Rückgabe {rc}")
    for m in meldungen[:5]:
        print(f"    MakeMKV: {m}")
    daten["hinweise"] += [f"MakeMKV: {m}" for m in meldungen[:20]]
    if lesefehler:
        daten["hinweise"].append(f"MakeMKV meldet {lesefehler} Lesefehler (Meldungen, nicht Sektoren)")
    daten["lesefehler_sektoren"] += lesefehler
    if rc != 0 or not mkvs:
        raise RuntimeError(f"MakeMKV fehlgeschlagen (Rückgabe {rc}, {len(mkvs)} Dateien)")

    rekorder, aufnahmen = dvd_vr.rekorder_lesen(wurzel)
    daten["rekorder"] = rekorder
    if aufnahmen and len(aufnahmen) != len(mkvs):
        daten["hinweise"].append(f"Rekorder nennt {len(aufnahmen)} Aufnahmen, MakeMKV {len(mkvs)} Titel: nicht zugeordnet")
        aufnahmen = []
    for i, f in enumerate(mkvs, 1):
        datei = f"t{i:02d}.mkv"
        f.rename(ordner / datei)
        dauer = ffprobe_dauer(ordner / datei)
        daten["titel"].append({
            "nr": i, "datei": datei, "dauer_s": int(round(dauer or 0)), "dvd_titel": None,
            "aufnahme": asdict(aufnahmen[i - 1]) if aufnahmen else None,
            "byte": (ordner / datei).stat().st_size,
        })


# ---------------------------------------------------------------- Daten-DVD

def daten_einlesen(wurzel: str, ordner: Path, daten: dict) -> None:
    daten["quelle"] = "kopie"
    wurzel_p = Path(wurzel)
    videos: list[Path] = []
    begleit: list[Path] = []
    sonstige: dict[str, int] = {}
    for verz, unter, dateien in os.walk(wurzel, onerror=lambda e: None):
        unter[:] = sorted(u for u in unter if u.lower() not in ("system volume information", "$recycle.bin"))
        for n in sorted(dateien, key=str.lower):
            p = Path(verz) / n
            e = p.suffix.lower()
            (videos if e in VIDEO_ENDUNGEN else begleit if e in BEGLEIT_ENDUNGEN else []).append(p)
            if e not in VIDEO_ENDUNGEN | BEGLEIT_ENDUNGEN:
                sonstige[e or "(ohne)"] = sonstige.get(e or "(ohne)", 0) + 1
    if not videos:
        raise RuntimeError("keine Videodateien auf der Disc")

    # Begleitdateien zuordnen: gleicher Name (aufnahme.eit, aufnahme.ts.ap), sonst gleicher Ordner
    zu: dict[Path, list[Path]] = {v: [] for v in videos}
    for b in begleit:
        im_ordner = [v for v in videos if v.parent == b.parent]
        passend = [v for v in im_ordner if b.name.lower().startswith(v.stem.lower())]
        if passend:
            zu[max(passend, key=lambda v: len(v.stem))].append(b)
        elif im_ordner:
            for v in im_ordner:
                zu[v].append(b)
        else:
            sonstige[b.suffix.lower()] = sonstige.get(b.suffix.lower(), 0) + 1
    daten["sonstige_dateien"] = dict(sorted(sonstige.items()))

    summe = sum(v.stat().st_size for v in videos)
    print(f"  Daten-DVD: {len(videos)} Videodateien, {gb(summe)} GB, {len(begleit)} Begleitdateien"
          + (f", nicht kopiert: {sum(sonstige.values())}" if sonstige else ""))
    kopiert: set[Path] = set()
    fort = Fortschritt(daten["disc"], summe, len(videos))
    karte = neue_lesekarte(wurzel, daten["disc"])
    karte.geplant([(str(v), 0, v.stat().st_size) for v in videos])
    geplant = [{"nr": i, "datei": v.relative_to(wurzel_p).as_posix(), "groesse_byte": v.stat().st_size,
                "begleitdateien": [b.name for b in zu[v]]} for i, v in enumerate(videos, 1)]
    ereignisse.metadaten(daten["disc"], daten, titel=geplant)
    for i, v in enumerate(videos, 1):
        rel = v.relative_to(wurzel_p)
        ziel = ordner / rel
        zeile = Zeile(f"  {str(rel)[:40]:40}  {gb(v.stat().st_size)} GB  ")
        t0 = time.monotonic()
        fort.melden(i, rel.name, 0, daten["lesefehler_sektoren"], sofort=True)

        def fortschritt(pos: int, gesamt: int, fehler: int = 0, *, zeile=zeile, i=i, rel=rel) -> None:
            zeile.zwischen(f"{pos * 100 // max(gesamt, 1):3d} %" + fehler_text(fehler))
            fort.melden(i, rel.name, pos, daten["lesefehler_sektoren"] + fehler)

        try:
            erg = datei_kopieren(v, ziel, fortschritt,
                                 karte=lambda a, b, z, v=v: karte.bereich(str(v), a, b, z))
        except kopieren.Abgebrochen:
            zeile.ende("abgebrochen")
            raise
        fehler = erg["fehler_sektoren"]
        karte.senden()
        for b in zu[v]:
            if b not in kopiert:
                fehler += datei_kopieren(b, ordner / b.relative_to(wurzel_p))["fehler_sektoren"]
                kopiert.add(b)
        daten["lesefehler_sektoren"] += fehler
        fort.vorher += erg["byte"]
        fort.melden(i, rel.name, 0, daten["lesefehler_sektoren"], sofort=True)
        dauer = ffprobe_dauer(ziel)
        if dauer is None:
            daten["hinweise"].append(f"{rel.as_posix()}: Dauer per ffprobe nicht lesbar")
        s = time.monotonic() - t0
        zeile.ende(f"{mmss(dauer or 0):>6}  {mmss(s)} min  " + ("ok" if not fehler else f"LESEFEHLER {fehler} Sektoren"
                   + (f" bei {kopieren.luecken_text(erg['luecken'], erg['byte'])}" if erg["luecken"] else "")))
        daten["titel"].append({
            "nr": i, "datei": rel.as_posix(), "dauer_s": int(round(dauer or 0)), "dvd_titel": None,
            "aufnahme": None,
            "begleitdateien": [b.relative_to(wurzel_p).as_posix() for b in zu[v]],
            "byte": ziel.stat().st_size, "lesefehler_sektoren": fehler,
        })
        geplant[i - 1].update(daten["titel"][-1])
        ereignisse.metadaten(daten["disc"], daten, titel=geplant)


# ---------------------------------------------------------------- Ablauf je Disc

def einlesen(wurzel: str, disc: str, beschriftung: str, label: str, seriennummer: int | None,
             ueberschreiben: bool, quelle_ordner: bool, vorher: dict | None = None) -> bool:
    """Liest eine Disc ein. Rückgabe: True = eingelesen, False = Problem.

    vorher: disc.json des vorigen Versuchs (Disc aus 3-problem neu eingelegt), für die Empfehlung, ob
    Reinigen geholfen hat. Bleiben Lesefehler, entscheidet das Skript ohne zu warten:
    Nutzer hat Lücken gelassen oder Reinigen lohnt nicht -> eingelesen mit Hinweis; Reinigen empfohlen
    -> 3-problem (reinigen, neu einlegen, gleiche Nummer)."""
    if ueberschreiben:
        for basis in (g.ROH, g.PROBLEM):
            if (basis / disc).exists():
                shutil.rmtree(basis / disc)
    arbeit = g.ROH / f"{disc}.einlesen"
    if arbeit.exists():
        shutil.rmtree(arbeit)  # Rest eines abgebrochenen Laufs
    arbeit.mkdir(parents=True)
    video = (Path(wurzel) / "VIDEO_TS").is_dir()
    audio = not video and not quelle_ordner and musik_cd.ist_audio_cd(wurzel)
    daten: dict = {
        "disc": disc, "beschriftung": beschriftung, "label": label,
        "typ": "dvd-video" if video else "daten", "rekorder": None, "status": "eingelesen",
        "eingelesen": None, "lesefehler_sektoren": 0, "aussortiert": [], "titel": [],
        "seriennummer": seriennummer, "quelle": None, "hinweise": [],
    }
    print(f"{disc}: \"{beschriftung}\"  (Label {label or '-'})")
    ereignisse.senden("disc", disc=disc, beschriftung=beschriftung, label=label,
                      medientyp="dvd-video" if video else "audio" if audio else "daten",
                      seriennummer=seriennummer, phase="einlesen")
    lw_meta = laufwerk_meta(wurzel, seriennummer, quelle_ordner)
    _medium_merker["medium"] = lw_meta.get("medium")
    ereignisse.metadaten(disc, daten, zusatz={"Laufwerk": lw_meta})
    t0 = time.monotonic()
    grund = ""
    abbruch = None
    lesen_abgebrochen = False
    kopieren.neue_disc(disc)
    try:
        if video:
            dvd_video_einlesen(wurzel, arbeit, daten)
        elif audio:
            grund = musik_cd.einlesen(wurzel, arbeit, daten)
        else:
            daten_einlesen(wurzel, arbeit, daten)
        if daten["lesefehler_sektoren"] or (vorher and vorher.get("lesefehler_sektoren")):
            stufe, text = kopieren.reinigen_empfehlung(daten["lesefehler_sektoren"], kopieren.DISC.stellen,
                                                       gleiche_stellen(vorher, daten))
            daten["reinigen"] = {"stufe": stufe, "empfehlung": text}
            print(f"  Empfehlung: {text}")
            ereignisse.senden("empfehlung", disc=disc, art="reinigen", stufe=stufe, text=text)
            if not daten["lesefehler_sektoren"]:
                pass  # voriger Versuch hatte Fehler, jetzt alles gelesen: Reinigen hat geholfen
            elif kopieren.DISC.uebersprungen or stufe != "empfohlen":
                # Nutzer hat Lücken gelassen, oder mehr ist hier nicht zu holen: übernehmen (umwandeln, einsortieren)
                daten["hinweise"].append(f"Lesefehler: {daten['lesefehler_sektoren']} Sektoren unlesbar, mit Nullen "
                                         + ("gefüllt (auf Wunsch gelassen)" if kopieren.DISC.uebersprungen
                                            else "gefüllt (nach dem Nachlesen)"))
            else:
                grund = f"Lesefehler: {daten['lesefehler_sektoren']} Sektoren, Reinigen empfohlen"
    except kopieren.Abgebrochen as e:
        grund, lesen_abgebrochen = f"Abbruch: {e}", True
    except KeyboardInterrupt as e:
        grund, abbruch = "Abbruch durch Nutzer", e
    except Exception as e:
        grund = f"Abbruch: {type(e).__name__}: {e}"

    daten["eingelesen"] = g.jetzt()
    daten["einlesen_s"] = int(time.monotonic() - t0)
    if not daten["hinweise"]:
        del daten["hinweise"]
    if grund:
        daten["status"] = "problem"
        daten["problem_grund"] = grund
        ziel = g.PROBLEM / disc
    elif daten["status"] == "einsortiert":  # Musik-CD: schon in der Bibliothek, kein Umwandeln
        ziel = g.FERTIG / disc
    else:
        ziel = g.ROH / disc
    g.disc_json_schreiben(arbeit, daten)
    ziel.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(arbeit), str(ziel))
    g.fortschritt_setzen(daten)

    byte = sum(t.get("byte", 0) for t in daten["titel"])
    stunden = sum(t["dauer_s"] for t in daten["titel"]) / 3600
    print(f"{disc}: {'PROBLEM: ' + grund if grund else 'eingelesen'}  {len(daten['titel'])} Titel, "
          f"{zahl(stunden, 2)} h, {gb(byte)} GB in {mmss(daten['einlesen_s'])} min, "
          f"Lesefehler {daten['lesefehler_sektoren']}  -> {ziel.parent.name}/{disc}")
    if grund and lesen_abgebrochen:
        print("  Tipp: Disc reinigen (weiches Tuch, von der Mitte gerade nach außen wischen), trocknen lassen "
              f"und wieder einlegen.\n  Der neue Versuch läuft unter derselben Nummer {disc}.")
        ereignisse.meldung("hinweis", "Disc reinigen (weiches Tuch, von der Mitte gerade nach außen wischen), "
                           f"trocknen lassen und wieder einlegen. Der neue Versuch läuft unter derselben Nummer {disc}.",
                           disc)
    if ereignisse.AKTIV:
        if grund and daten["lesefehler_sektoren"]:
            ereignisse.meldung("warnung", f"{grund}. {daten['reinigen']['empfehlung']}", disc)
        elif daten["lesefehler_sektoren"]:
            ereignisse.meldung("warnung", f"{daten['lesefehler_sektoren']} Sektoren unlesbar, mit Nullen gefüllt. "
                               f"{daten['reinigen']['empfehlung']}", disc)
        if not grund:
            ereignisse.fortschritt("einlesen", disc, prozent=100.0, byte=byte, byte_gesamt=byte,
                                   fehler_sektoren=daten["lesefehler_sektoren"], schritt="fertig")
        ereignisse.metadaten(disc, daten)  # Endstand: Status, Dauer, Hinweise, Problemgrund
        ereignisse.senden("disc_ende", disc=disc, phase="einlesen",
                          status="problem" if grund else ("einsortiert" if daten["status"] == "einsortiert"
                                                         else "eingelesen"),
                          grund=grund or None, ziel=str(ziel), dauer_s=daten["einlesen_s"])
    if abbruch:
        raise abbruch
    return not grund


def gleiche_stellen(vorher: dict | None, daten: dict) -> bool | None:
    """Liegen die Lücken an denselben Stellen wie beim vorigen Versuch? None ohne vorigen Versuch mit Lücken."""
    if not vorher:
        return None
    alt = {t.get("nr"): t.get("luecken_byte") or [] for t in vorher.get("titel") or []}
    if not any(alt.values()):
        return None
    for t in daten["titel"]:
        for a, b in t.get("luecken_byte") or []:
            if any(a < y and x < b for x, y in alt.get(t.get("nr"), [])):
                return True
    return False


def ohne_inhalt(args, lw: str, meldung: str, hinweis: str, status: str = "leer", ausw: bool = True) -> None:
    """Disc ohne lesbaren Inhalt: leerer Rohling (status "leer") oder herausgenommen, während das
    Laufwerk an ihr hing ("unlesbar").

    Ob die nächste Nummer trotzdem verbraucht wird (Zeile in fortschritt.csv mit diesem Status, damit
    vorab nummerierte Discs ihre Nummer behalten), hängt von args.nummern ab (siehe NUMMERN):
    fragen, immer (Stapel durchnummeriert) oder nie (erst testen, Nummer danach draufschreiben).
    """
    vorschlag = naechste_nummer()
    print(meldung)
    zaehlen = args.nummern == "immer"
    if args.nummern == "nie":
        print("  Keine Nummer vergeben (Modus: erst testen), Disc beiseitelegen.")
    elif args.nummern == "fragen" and (sys.stdin.isatty() or ereignisse.AKTIV):
        musik_cd._hinweiston()
        a = ereignisse.frage(
            "nummer_vergeben", f"{meldung} Nummer {vorschlag} trotzdem vergeben (weil sie schon auf der Disc steht)?",
            optionen=[("j", "ja"), ("n", "nein")], vorgabe="n",
            prompt=f"  Nummer {vorschlag} trotzdem vergeben (weil sie schon auf der Disc steht)? [j/N]: ")
        zaehlen = a.strip().lower() in ("j", "ja")
    if zaehlen:
        g.fortschritt_setzen({"disc": vorschlag, "beschriftung": status, "typ": status, "titel": [],
                              "status": status}, hinweis + ", Nummer trotzdem vergeben")
        print(f"  {vorschlag} als {status} vermerkt. Die nächste Disc bekommt {naechste_nummer()}.")
    elif args.nummern != "nie":
        print(f"  Keine Nummer vergeben. Die nächste Disc bekommt {vorschlag}.")
    if ausw and not args.ohne_auswerfen and not auswerfen(lw):
        print("Hinweis: Auswerfen hat nicht geklappt, bitte von Hand.")
        ereignisse.meldung("warnung", "Auswerfen hat nicht geklappt, bitte von Hand.")
    ereignisse.senden("disc_ende", disc=vorschlag if zaehlen else None, phase="einlesen", status=status,
                      grund=hinweis, ziel=None, dauer_s=None)
    ton(False)


def laufwerk_abfragen(lw: str, wurzel: str, erledigt: dict) -> tuple[tuple[str, int] | None, str | None, float]:
    """(volume_info, medium, Sekunden hängend). Fragt in einem eigenen Thread, damit ein Laufwerk,
    das sich an einer schwer lesbaren Disc festbeißt, gemeldet werden kann statt still zu warten."""
    erg: dict = {}

    def lauf() -> None:
        try:
            info = volume_info(wurzel)
            if info and info[1] not in erledigt:
                time.sleep(2)  # Laufwerk zur Ruhe kommen lassen
                info = volume_info(wurzel)
            erg["r"] = (info, medium(lw) if info is None else None)
        except Exception:
            erg["r"] = (None, None)

    t = threading.Thread(target=lauf, daemon=True)
    t.start()
    t.join(HAENGT_S)
    if not t.is_alive():
        return (*erg["r"], 0.0)
    t0 = time.monotonic() - HAENGT_S
    print(f"Laufwerk {lw} antwortet seit {HAENGT_S} s nicht. Die Disc ist vermutlich schwer lesbar "
          f"(Kratzer, schlechter Brand). Abwarten oder die Disc mit der Taste am Laufwerk auswerfen.")
    laufwerk_melden("haengt", None, None, HAENGT_S)
    ereignisse.meldung("warnung", f"Laufwerk {lw} antwortet seit {HAENGT_S} s nicht. Die Disc ist vermutlich "
                       "schwer lesbar (Kratzer, schlechter Brand).")
    while t.is_alive():
        t.join(1)
        if TTY:
            sys.stdout.write(f"\r  hängt seit {mmss(time.monotonic() - t0)} min ")
            sys.stdout.flush()
    dauer = time.monotonic() - t0
    if TTY:
        sys.stdout.write("\n")
    print(f"Laufwerk antwortet wieder (nach {mmss(dauer)} min).")
    ereignisse.meldung("info", f"Laufwerk antwortet wieder (nach {mmss(dauer)} min).")
    return (*erg["r"], dauer)


def platz_abwarten(wurzel: str, quelle_ordner: bool) -> None:
    """Wartet, bis DVD_ARBEIT Platz für die ganze Disc plus PLATZ_RESERVE hat."""
    if quelle_ordner:
        return  # Testquelle: disk_usage gäbe das ganze Laufwerk der Quelle an
    try:
        bedarf = shutil.disk_usage(wurzel).used + PLATZ_RESERVE  # bei Discs: belegter Platz der Disc
    except OSError:
        bedarf = 9 * 10**9 + PLATZ_RESERVE  # DVD-9 als Obergrenze
    g.ARBEIT.mkdir(parents=True, exist_ok=True)
    gemeldet = False
    while (frei := shutil.disk_usage(g.ARBEIT).free) < bedarf:
        if not gemeldet:
            print(f"Zu wenig Platz: {gb(frei)} GB frei in {g.ARBEIT}, nötig {gb(bedarf)} GB. "
                  f"Warte, bis umwandeln.py Rohdaten abgearbeitet hat. Disc im Laufwerk lassen ...")
            ereignisse.meldung("hinweis", f"Zu wenig Platz in DVD_ARBEIT ({gb(frei)} GB frei, nötig {gb(bedarf)} GB). "
                               "Warte, bis umwandeln.py Rohdaten abgearbeitet hat. Disc bleibt im Laufwerk.")
            gemeldet = True
        time.sleep(PLATZ_POLL_S)
    if gemeldet:
        print(f"Wieder genug Platz ({gb(frei)} GB frei), es geht weiter.")


def hat_dateien(wurzel: str) -> bool:
    """Mindestens eine Datei auf der Disc (Systemordner nicht gezählt). Eine formatierte, aber leere
    Disc (z.B. DVD-RW mit UDF) hat ein Dateisystem, aber keine Dateien. Audio-CDs zeigen Track*.cda.
    Kann ein Ordner nicht gelesen werden, gilt die Disc als nicht leer (das Einlesen meldet den Fehler)."""
    fehler: list[OSError] = []

    def merken(e: OSError) -> None:
        # Ein ganz leeres UDF-Verzeichnis meldet beim Auflisten "Datei nicht gefunden" (WinError 2)
        if not (getattr(e, "winerror", None) == 2 and e.filename and os.path.isdir(e.filename)):
            fehler.append(e)

    for _verz, unter, dateien in os.walk(wurzel, onerror=merken):
        if dateien:
            return True
        unter[:] = [u for u in unter if u.lower() not in ("system volume information", "$recycle.bin")]
    return bool(fehler)


def _disc_info(disc: str) -> dict:
    """Angaben zu einer schon eingelesenen Disc: Zeile aus fortschritt.csv plus disc.json, falls der Ordner da ist."""
    info = dict(g.fortschritt_lesen().get(disc, {}))
    for basis in (g.ROH, g.FERTIG, g.PROBLEM):
        if (basis / disc / "disc.json").exists():
            try:
                info.update({k: v for k, v in g.disc_json_lesen(basis / disc).items() if k != "titel"})
                info["ablage"] = basis.name
            except Exception:
                pass
            break
    return info


def duplikat_klaeren(args, bekannt: str, beschriftung_neu: str, seriennummer: int | None) -> None:
    """Disc mit schon bekannter Seriennummer, z.B. eine zweite gebrannte Kopie derselben Aufnahme (D0104/D0111,
    2026-11-09). Fragt mit Empfehlung und Folgen: als Duplikat vermerken (Nummer mit Status "doppelt", nichts
    kopieren), trotzdem einlesen (neue Nummer, z.B. wenn das Original Lesefehler hatte) oder überspringen.
    Kehrt nur bei "trotzdem einlesen" zurück, sonst Doppelt bzw. Uebersprungen. Ohne Terminal und ohne
    Ereignis-Modus wird wie früher übersprungen."""
    if not (sys.stdin.isatty() or ereignisse.AKTIV):
        raise Uebersprungen(f"schon als {bekannt} eingelesen (neu einlesen: --fragen)")
    alle = (discs_mit_serie(seriennummer) if seriennummer is not None else []) or [bekannt]
    infos = {d: _disc_info(d) for d in alle}
    bekannt = alle[0]  # Original = erste Nummer
    vorschlag = naechste_nummer()
    ohne_nummer = args.nummern == "nie"

    def fehlerhaft(i: dict) -> bool:
        return int(i.get("lesefehler_sektoren") or 0) > 0 or i.get("status") in ("problem", "unlesbar")

    schlecht = all(fehlerhaft(i) for i in infos.values())  # keine fehlerfreie Kopie vorhanden
    serie = f" {seriennummer >> 16:04X}-{seriennummer & 0xFFFF:04X}" if seriennummer is not None else ""
    text = (f"Diese Disc ist schon als {', '.join(alle)} bekannt (gleiche Volume-Seriennummer{serie}). "
            "Vermutlich eine weitere gebrannte Kopie derselben Aufnahmen.")
    details = ("; ".join(f"{d}: „{i.get('beschriftung') or '?'}“, Status {i.get('status') or '?'}"
                         + (f", {i.get('titel')} Titel, {i.get('stunden')} h" if i.get("titel") else "")
                         + (f", {i.get('lesefehler_sektoren')} Lesefehler-Sektoren" if int(i.get("lesefehler_sektoren") or 0)
                            else ", ohne Lesefehler") for d, i in infos.items())
               + f". Diese Disc: „{beschriftung_neu or '?'}“.\nEmpfehlung: "
               + ("Trotzdem einlesen: Das Original hatte Lesefehler, diese Kopie kann die Lücken schließen."
                  if schlecht else "Als Duplikat vermerken: Das Original ist vollständig gelesen, eine zweite Kopie "
                                   "bringt nichts und kostet ca. 5 min."))
    if ohne_nummer:
        dup = ("d", "nicht einlesen", "Keine Nummer (Modus „Erst testen“), nichts wird kopiert, die Disc wird "
                                      f"ausgeworfen. Auf die Disc z.B. „= {bekannt}“ schreiben.")
    else:
        dup = ("d", f"als Duplikat vermerken ({vorschlag})",
               f"Nummer {vorschlag} wird in der Liste als „doppelt, gleiche Disc wie {bekannt}“ eingetragen. Nichts "
               "wird kopiert, die Disc wird ausgeworfen. Die Nummer auf der Disc passt dann zur Liste.")
    optionen = [dup,
                ("e", f"trotzdem einlesen als {vorschlag}",
                 "Liest die Disc vollständig als eigene Disc (ca. 5 min, belegt Platz). Sinnvoll, wenn das Original "
                 "Lesefehler hatte oder der Inhalt doch anders ist. Doppelte Folgen sortiert das Einsortieren aus "
                 "(die längere Aufnahme bleibt)."),
                ("s", "überspringen", "Keine Nummer, kein Eintrag, die Disc wird ausgeworfen.")]
    empfehlung = "e" if schlecht else "d"
    if not ereignisse.AKTIV:
        print(f"  {text}\n  {details}")
        for w, tx, h in optionen:
            print(f"  [{w}] {tx}{' (empfohlen)' if w == empfehlung else ''}: {h}")
    musik_cd._hinweiston()
    try:
        wahl = ereignisse.frage("duplikat", text, optionen=optionen, vorgabe=empfehlung, disc=bekannt,
                                details=details, empfehlung=empfehlung,
                                prompt=f"Wahl [d/e/s, Enter = {empfehlung}]: ").strip().lower()[:1] or empfehlung
    except EOFError:
        wahl = "s"
    if wahl == "e":
        return
    if wahl == "d":
        if ohne_nummer:
            raise Doppelt(f"gleiche Disc wie {bekannt}, nicht eingelesen (keine Nummer)", None)
        g.fortschritt_setzen({"disc": vorschlag, "beschriftung": infos[bekannt].get("beschriftung") or beschriftung_neu,
                              "typ": "doppelt", "titel": [], "status": "doppelt"}, f"gleiche Disc wie {bekannt}")
        print(f"  {vorschlag} als Duplikat von {bekannt} vermerkt. Die nächste Disc bekommt {naechste_nummer()}.")
        raise Doppelt(f"{vorschlag}: gleiche Disc wie {bekannt}, nicht kopiert", vorschlag)
    raise Uebersprungen(f"schon als {bekannt} eingelesen, übersprungen")


def eine_disc(args, wurzel: str, label: str, seriennummer: int | None, quelle_ordner: bool) -> bool:
    """Fragt ab, liest ein, wirft aus, spielt den Ton. Rückgabe: True = eingelesen."""
    if not hat_dateien(wurzel):  # vor der Beschriftungsfrage (leere DVD-RW nach D0091, 2026-11-09)
        laufwerk_melden("ohne_inhalt", label or None)
        lw = laufwerk_norm(wurzel)
        ohne_inhalt(args, lw, f"Disc in {lw} ist leer: formatiert (Label {label or '-'}), aber keine Dateien.",
                    "formatiert, keine Dateien", ausw=not quelle_ordner)
        return False
    ok = False
    vorher: dict | None = None  # disc.json des vorigen Versuchs (Disc aus 3-problem neu eingelegt)
    disc_nr = None
    try:
        auto = None if args.fragen else beschriftung_aus_rekorder(wurzel)
        if not auto and not args.fragen and not quelle_ordner and musik_cd.ist_audio_cd(wurzel):
            auto = "Musik-CD"  # wird nach dem Einlesen durch Interpret – Album ersetzt
        bekannt = bekannte_seriennummern().get(seriennummer) if seriennummer is not None else None
        if bekannt and (g.PROBLEM / bekannt).is_dir() and not args.disc and not args.fragen:
            # gescheiterte Disc neu eingelegt: derselbe Ordner, neuer Versuch
            alt = vorher = g.disc_json_lesen(g.PROBLEM / bekannt)
            print(f"{bekannt} liegt in {g.PROBLEM.name}/ ({alt.get('problem_grund') or '?'}), "
                  f"neuer Versuch unter derselben Nummer.")
            disc, beschriftung, ueberschreiben = bekannt, alt.get("beschriftung") or auto or label, True
            disc_nr = disc
        else:
            if bekannt and not args.disc:
                print(f"Hinweis: Eine Disc mit dieser Seriennummer wurde schon als {bekannt} eingelesen.")
                disc_nr = bekannt
                duplikat_klaeren(args, bekannt, auto or label, seriennummer)  # wirft, außer bei "trotzdem einlesen"
            platz_abwarten(wurzel, quelle_ordner)  # vor der Nummernvergabe, die Disc kann lange warten
            disc, beschriftung, ueberschreiben = disc_abfragen(args.disc, args.beschriftung, auto,
                                                               fragen=args.fragen, label=label)
            args.disc = args.beschriftung = None  # Vorgaben gelten nur für die erste Disc
            disc_nr = disc
        platz_abwarten(wurzel, quelle_ordner)
        ok = einlesen(wurzel, disc, beschriftung, label, seriennummer, ueberschreiben, quelle_ordner, vorher)
    except Uebersprungen as e:
        print(f"Disc nicht eingelesen: {e}")
        doppelt = isinstance(e, Doppelt)
        ereignisse.senden("disc_ende", disc=e.disc if doppelt else disc_nr, phase="einlesen",
                          status="doppelt" if doppelt else "uebersprungen", grund=str(e), ziel=None, dauer_s=None)
    if not quelle_ordner and not args.ohne_auswerfen:
        if not auswerfen(laufwerk_norm(wurzel)):
            print("Hinweis: Auswerfen hat nicht geklappt, bitte von Hand.")
            ereignisse.meldung("warnung", "Auswerfen hat nicht geklappt, bitte von Hand.", disc_nr)
    ton(ok)
    return ok


_sperre_fd: int | None = None  # gehalten, solange der Prozess lebt (Betriebssystem gibt sie beim Ende frei)


def SPERRE() -> Path:
    return g.ARBEIT / "einlesen.lock"


def sperre_holen() -> bool:
    """Nur ein einlesen.py je Arbeitsordner (zwei Prozesse am selben Laufwerk). Wie umwandeln.lock:
    Betriebssystem-Sperre auf Byte 0, die Datei bleibt liegen."""
    global _sperre_fd
    g.ARBEIT.mkdir(parents=True, exist_ok=True)
    fd = os.open(SPERRE(), os.O_CREAT | os.O_RDWR)
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        print(f"einlesen.py läuft schon (Sperre belegt: {SPERRE()})")
        return False
    os.ftruncate(fd, 0)
    os.write(fd, f"{os.getpid()}\n".encode())  # nur zur Information
    _sperre_fd = fd
    return True


def main() -> int:
    ereignisse.senden("start", skript="einlesen", pid=os.getpid(), argumente=sys.argv[1:])
    code = 1
    try:
        code = _main()
        return code
    except KeyboardInterrupt:
        code = 130
        if ereignisse.AKTIV:  # stopp vom Programm: sauber beenden statt Traceback
            print()
            print("Beendet.")
            return code
        raise
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
        raise
    finally:
        ereignisse.senden("ende", code=code)


def _main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--laufwerk", default=os.environ.get("DVD_LAUFWERK") or "E:", help="Standard: DVD_LAUFWERK oder E:")
    ap.add_argument("--einmal", action="store_true", help="nur die eingelegte Disc verarbeiten, dann beenden")
    ap.add_argument("--quelle", help="Ordner statt Laufwerk als Disc behandeln (Tests), wirft nichts aus")
    ap.add_argument("--disc", help="Disc-Nummer ohne Abfrage, z.B. D0001 (bricht ab, wenn vorhanden)")
    ap.add_argument("--beschriftung", help="Beschriftung ohne Abfrage")
    ap.add_argument("--ohne-auswerfen", action="store_true", help="Disc nach dem Einlesen nicht auswerfen")
    ap.add_argument("--fragen", action="store_true",
                    help="auch bei Rekorderdaten nach Nummer und Beschriftung fragen")
    ap.add_argument("--nummern", choices=NUMMERN, default="fragen",
                    help="leere/unlesbare Disc: fragen, ob die Nummer vergeben wird (Standard); immer (Stapel ist "
                         "durchnummeriert); nie (erst testen, Nummer danach draufschreiben)")
    ap.add_argument("--unnummeriert", action="store_true", help="wie --nummern nie")
    args = ap.parse_args()
    if args.unnummeriert:
        args.nummern = "nie"

    def nummern_setzen(wert: str) -> str:
        if wert not in NUMMERN:
            raise ValueError(f"erlaubt: {', '.join(NUMMERN)}")
        args.nummern = wert
        print(f"Leere Discs ab jetzt: {NUMMERN[wert]}")
        if wert != "fragen":  # eine gerade offene Frage erübrigt sich
            ereignisse.offene_beantworten("nummer_vergeben", "j" if wert == "immer" else "n")
        return wert

    print(f"Arbeitsordner: {g.ARBEIT}")
    print(f"Leere Discs: {NUMMERN[args.nummern]}")
    ereignisse.einstellung_anmelden("nummern", nummern_setzen, args.nummern)
    if args.quelle:
        q = Path(args.quelle).resolve()
        if not q.is_dir():
            print(f"Quelle nicht gefunden: {q}", file=sys.stderr)
            return 2
        return 0 if eine_disc(args, str(q), q.name, None, quelle_ordner=True) else 1

    if not sperre_holen():
        ereignisse.meldung("fehler", f"einlesen.py läuft schon (Sperre belegt: {SPERRE()})")
        return 3
    lw = laufwerk_norm(args.laufwerk)
    wurzel = lw + "\\"
    erledigt: dict[int, str] = {}  # Seriennummer -> Meldung, in dieser Sitzung
    # Seriennummer -> Medienwechsel-Zähler beim Erkennen. Nach dem Herausnehmen wieder frei für einen
    # neuen Versuch: Laufwerk leer gesehen oder Zähler geändert (schnell wieder eingelegt, D0072 2026-11-09)
    gescheitert: dict[int, int | None] = {}
    gemeldet = None
    ohne_inhalt_seit: float | None = None  # Medium liegt drin, aber kein Dateisystem
    nach_haengen = 0.0  # Dauer des letzten Hängens, bis klar ist, ob die Disc noch drin ist
    leer_seit: float | None = None  # seit wann das Laufwerk nach dem Hängen leer ist
    beginn = time.monotonic()
    print(f"Beobachte {lw} (alle {POLL_S} s), Beenden mit Strg+C")
    try:
        while True:
            info, art, gehangen = laufwerk_abfragen(lw, wurzel, erledigt)
            if gehangen:
                # Erst entscheiden, wenn klar ist, ob die Disc noch drin ist: Direkt nach dem Hängen meldet
                # das Laufwerk beim Anlaufen auch "kein Medium" (Fehlalarm D0067, 2026-11-08).
                nach_haengen, leer_seit = gehangen, None
            if info or art:
                nach_haengen = 0.0
            if info and info[1] in gescheitert:
                vorher, jetzt = gescheitert[info[1]], wechselzaehler(lw)
                if vorher is not None and jetzt is not None and jetzt != vorher:
                    erledigt.pop(info[1], None)  # herausgenommen und wieder eingelegt: neuer Versuch
                    del gescheitert[info[1]]
            if nach_haengen and info is None and art is None:
                leer_seit = leer_seit or time.monotonic()
                if time.monotonic() - leer_seit >= RAUS_S:  # Laufwerk bleibt leer: Disc wurde herausgenommen
                    ohne_inhalt(args, lw, f"Die Disc wurde herausgenommen, ohne dass {lw} sie lesen konnte.",
                                f"nicht lesbar (Laufwerk hing {mmss(nach_haengen)} min)", "unlesbar", ausw=False)
                    nach_haengen, gemeldet = 0.0, None
                    if args.einmal:
                        return 1
            if art:
                if ohne_inhalt_seit is None:
                    ohne_inhalt_seit = time.monotonic()
                elif gemeldet != "ohne-inhalt" and time.monotonic() - ohne_inhalt_seit >= OHNE_INHALT_S:
                    laufwerk_melden("ohne_inhalt", None, art, time.monotonic() - ohne_inhalt_seit)
                    ohne_inhalt(args, lw, f"Disc in {lw} ist leer oder nicht lesbar: {art}, kein Dateisystem, "
                                f"keine Audiospuren.", f"{art} ohne Inhalt")
                    gemeldet = "ohne-inhalt"  # erst wieder melden, wenn das Laufwerk leer war
                    if args.einmal:
                        return 1
            elif info is None:
                ohne_inhalt_seit = None
                for serie in gescheitert:
                    erledigt.pop(serie, None)
                gescheitert.clear()
                if args.einmal and time.monotonic() - beginn > 30:
                    print(f"Keine lesbare Disc in {lw}.", file=sys.stderr)
                    return 1
                if not nach_haengen:
                    laufwerk_melden("leer")
                if gemeldet != "leer":
                    print("Warte auf Disc ...")
                    gemeldet = "leer"
            elif info[1] in erledigt:
                laufwerk_melden("verarbeitet", info[0] or None)
                if gemeldet != info[1]:
                    als = bekannte_seriennummern().get(info[1])
                    print(f"Disc {info[0] or '-'} ist schon verarbeitet{f' (als {als})' if als else ''}, "
                          f"bitte nächste Disc einlegen.")
                    gemeldet = info[1]
            else:
                label, serie = info
                laufwerk_melden("disc", label or None)
                zaehler = wechselzaehler(lw)  # vor dem Lesen: Laufwerk antwortet gerade sicher
                ok = eine_disc(args, wurzel, label, serie, quelle_ordner=False)
                erledigt[serie] = label
                if not ok:
                    gescheitert[serie] = zaehler
                gemeldet = None
                if args.einmal:
                    return 0 if ok else 1
            time.sleep(POLL_S)
    except KeyboardInterrupt:
        print("\nBeendet.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
