"""Warteschlange: eingelesene Discs umwandeln (x265), Werbung markieren, prüfen, nach 2-fertig legen.

Zweck:
  Läuft nebenbei mit niedriger Prozesspriorität. Nimmt Discs mit Status
  "eingelesen" aus 1-roh/, älteste zuerst, eine nach der anderen. Je Titel:
  1. Interlace-Messung: ffmpeg idet an 3 Stellen je 30 s. Anteil TFF+BFF (Multi-Frame) ab
     Schwelle -> 50p (bwdif bob), sonst 25p (bwdif)
  2. HandBrakeCLI (Standard x265 medium RF 21), alle Tonspuren (AC3 durchgereicht, sonst AAC),
     alle Untertitel. Schreibt erst nach tNN.mkv.part, umbenannt erst nach bestandener Prüfung
  3. Comskip über die Quelle (läuft parallel zu HandBrake), außer bei Video-DVDs ohne Rekorderdaten
     und bei öffentlich-rechtlichen
     Sendern. Werbeblöcke -> Kapitel "Sendung"/"Werbung" per mkvpropedit, titel[].werbung.
     Ohne Werbung keine künstlichen Kapitel (Kapitel nur an echten Inhaltsgrenzen; in Jellyfin
     reicht zum Spulen die Zeitleiste)
  4. Prüfung: Dauer Ziel = Quelle (±2 s), ffmpeg dekodiert ohne Fehler, Tonspuren vollständig,
     sechs Vorschaubilder vorschau/tNN-1.jpg ... -6.jpg
  Ausnahme Daten-Disc (typ "daten") mit fertig komprimiertem, progressivem Video (H.264, HEVC,
  AV1, VP9, z.B. eine MP4 aus dem Netz): nur mit ffmpeg nach MKV umpacken (-c copy, mov_text ->
  SRT), kein Neukodieren, keine Interlace-Messung, kein Comskip. Prüfung wie oben.
  Alle Titel bestanden: Rohdateien löschen, Status "umgewandelt", Ordner nach 2-fertig/<disc>.
  Danach "einsortieren.py auto <disc>": sicher zugeordnete Discs wandern nach DVD_ZIEL, der Rest
  wartet zum Prüfen (abschalten mit --ohne-einsortieren).
  Fehler: Status "problem" mit Grund, Rohdaten bleiben, Ordner nach 3-problem/<disc>.
  Log je Disc: umwandeln.log im Disc-Ordner.

  Abbruch-sicher: Strg+C oder Neustart lässt die Disc in 1-roh (Status "eingelesen"). Fertige
  Titel stehen in disc.json und werden beim nächsten Start übersprungen, halbe *.part-Dateien
  verworfen. Nochmal versuchen nach "problem": Ordner zurück nach 1-roh, Status "eingelesen".

Aufruf:
  python -I skripte/umwandeln.py                       Dauerschleife
  python -I skripte/umwandeln.py --einmal              eine Disc, dann Ende
  python -I skripte/umwandeln.py --disc D0001          nur diese Disc
  python -I skripte/umwandeln.py --zeitfenster 22-07 --prioritaet niedrig
  python -I skripte/umwandeln.py --encoder vce_h265    GPU statt CPU
  Weitere Schalter: --rf, --preset, --schwelle 0.3, --werbung-immer (Test: Comskip auch bei ÖR)
  Exit-Code: 0 ok, 1 nichts zu tun bzw. läuft schon, 2 Programm fehlt, 3 Disc als Problem abgelegt

Ereignis-Modus: Mit DVD_EREIGNISSE=1 meldet das Skript Warteschlange, Disc, Fortschritt (HandBrake, Comskip,
  Prüfung ...) und Ergebnis (auch von einsortieren.py auto) als "@@ "-JSON-Zeilen auf stdout und beendet sich
  auf "stopp" über stdin wie bei Strg+C (siehe ereignisse.py und docs/ereignis-protokoll.md im Repo
  disc-digitalisierung). Ohne die Variable ändert sich nichts.

Umgebungsvariablen:
  DVD_EREIGNISSE  1 = Ereignis-Modus für das Programm Disc-Digitalisierung (optional)
  DVD_ARBEIT  Arbeitsordner, Standard <repo>/ausgabe/dvd (siehe gemeinsam.py)
  DVD_ZIEL    Ziel fürs automatische Einsortieren (siehe einsortieren.py)

Nur Standardbibliothek. Externe Programme: HandBrakeCLI, ffmpeg, ffprobe (im PATH),
Comskip (%LOCALAPPDATA%\\Programs\\Comskip), mkvpropedit (C:\\Program Files\\MKVToolNix).
Schreibt unter DVD_ARBEIT, über einsortieren.py auch nach DVD_ZIEL.
"""
from __future__ import annotations

import argparse
import ctypes
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # auch mit python -I
import ereignisse  # noqa: E402
import gemeinsam as g  # noqa: E402

try:
    sys.stdout.reconfigure(errors="replace", line_buffering=True)
except Exception:
    pass
TTY = sys.stdout.isatty() and not ereignisse.AKTIV  # im Ereignis-Modus keine \r-Zeilen

WERKZEUG = Path(__file__).resolve().parent
COMSKIP_INI = WERKZEUG / "comskip.ini"
SPERRE = g.ARBEIT / "umwandeln.lock"

POLL_S = 60
DAUER_TOLERANZ_S = 2.0
IDET_STELLEN = 3
IDET_DAUER_S = 30
VORSCHAU_ANZAHL = 6
VORSCHAU_BREITE = 480
MIN_KAPITEL_S = 2.0  # kürzere Reste vor/nach Werbung werden kein eigenes Kapitel
UMPACK_CODECS = {"h264", "hevc", "av1", "vp9"}  # Daten-Discs: schon effizient, nur umpacken

ENCODER = {  # Standardwerte je Encoder (Testlauf 2026-11-04)
    "x265": {"preset": "medium", "rf": 21},
    "vce_h265": {"preset": "quality", "rf": 24},
}
PRIORITAET = {"unter-normal": 0x00004000, "niedrig": 0x00000040}  # Windows-Prioritätsklassen
NICE = {"unter-normal": 5, "niedrig": 19}

# Öffentlich-rechtliche Sender ohne Werbeblöcke (Vergleich ohne Groß/Klein, Leer- und Satzzeichen).
# Der Rekorder speichert nur 5 Zeichen ("PHOEN", "ZDFne"), deshalb zählt auch ein Anfang ab 4 Zeichen.
OEFFENTLICH_RECHTLICH = (
    "ARD", "Das Erste", "ZDF", "ZDFneo", "ZDFinfo", "ZDFkultur", "KIKA", "KI.KA", "3sat", "arte",
    "Phoenix", "tagesschau24", "ONE", "EinsFestival", "EinsPlus", "ARD-alpha", "BR-alpha",
    "WDR", "NDR", "BR", "BR Fernsehen", "HR", "MDR", "RBB", "SWR", "SR", "RB", "Radio Bremen",
    "SWR BW", "SWR RP", "SR Fernsehen", "hr-fernsehen", "ORF1", "ORF2", "ORF III", "SRF1", "SRF zwei",
    "DW", "Deutsche Welle",
)


def _norm(s: str) -> str:
    s = s.lower().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    return re.sub(r"[^a-z0-9]", "", s)


_OER = {_norm(x) for x in OEFFENTLICH_RECHTLICH}


def oeffentlich_rechtlich(sender: str | None) -> bool:
    if not sender or not _norm(sender):
        return False
    n = _norm(sender)
    erstes_wort = _norm(sender.split()[0])
    if n in _OER or erstes_wort in _OER:
        return True
    return len(n) >= 4 and any(x.startswith(n) for x in _OER)  # abgeschnittener Name


# ---------------------------------------------------------------- Programme

def programm(name: str, *kandidaten: str) -> str:
    """Pfad eines Programms: erst PATH, dann die festen Orte. Leer, wenn nicht gefunden."""
    p = shutil.which(name)
    if p:
        return p
    for k in kandidaten:
        k = os.path.expandvars(k)
        if os.path.exists(k):
            return k
    return ""


HANDBRAKE = programm("HandBrakeCLI")
FFMPEG = programm("ffmpeg")
FFPROBE = programm("ffprobe")
COMSKIP = programm("comskip", r"%LOCALAPPDATA%\Programs\Comskip\comskip.exe")
MKVPROPEDIT = programm("mkvpropedit", r"C:\Program Files\MKVToolNix\mkvpropedit.exe")


class TitelFehler(Exception):
    """Ein Titel hat Umwandlung oder Prüfung nicht bestanden."""


# ---------------------------------------------------------------- Ausgabe, Log

def mmss(s: float) -> str:
    s = int(round(s))
    return f"{s // 60}:{s % 60:02d}"


def gb(byte: int) -> str:
    return f"{byte / 1e9:.2f}".replace(".", ",")


class Zeile:
    """Fortschritt in einer Konsolenzeile. Im Terminal überschrieben, sonst nur das Ergebnis."""

    def __init__(self, praefix: str):
        self.praefix = praefix
        self.zuletzt = 0.0

    def zwischen(self, text: str) -> None:
        if TTY and time.monotonic() - self.zuletzt > 1:
            self.zuletzt = time.monotonic()
            sys.stdout.write(f"\r{self.praefix}{text}".ljust(100)[:100])
            sys.stdout.flush()

    def ende(self, text: str) -> None:
        if TTY:
            sys.stdout.write("\r" + " " * 100 + "\r")
        print(f"{self.praefix}{text}")


class Log:
    """umwandeln.log je Disc (binär angehängt, damit Kindprozesse direkt hineinschreiben können)."""

    def __init__(self, pfad: Path):
        self.f = open(pfad, "ab")

    def __call__(self, text: str) -> None:
        self.f.write(f"{g.jetzt()}  {text}\n".encode("utf-8", "replace"))
        self.f.flush()

    def roh(self, daten: bytes) -> None:
        self.f.write(daten)
        self.f.flush()

    def close(self) -> None:
        if not self.f.closed:
            self.f.close()


# ---------------------------------------------------------------- Prozesse

_PRIO_FLAG = 0
_laufend: list[subprocess.Popen] = []


def prioritaet_setzen(stufe: str) -> None:
    """Senkt die Priorität dieses Prozesses. Kindprozesse erben sie (und bekommen sie zusätzlich
    per creationflags)."""
    global _PRIO_FLAG
    if os.name == "nt":
        _PRIO_FLAG = PRIORITAET[stufe]
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetCurrentProcess.restype = ctypes.c_void_p
        k32.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        if not k32.SetPriorityClass(k32.GetCurrentProcess(), _PRIO_FLAG):
            print(f"Hinweis: Priorität nicht gesetzt (Fehler {ctypes.get_last_error()})")
    else:
        os.nice(NICE[stufe])


def popen(cmd: list[str], **kw) -> subprocess.Popen:
    if os.name == "nt":
        kw.setdefault("creationflags", _PRIO_FLAG)
    p = subprocess.Popen([str(c) for c in cmd], **kw)
    _laufend.append(p)
    return p


def lauf(cmd: list[str], timeout: float | None = None) -> subprocess.CompletedProcess:
    """Kurzer Aufruf mit erfasster Ausgabe (Text)."""
    p = popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL)
    try:
        out, err = p.communicate(timeout=timeout)
    finally:
        _beenden(p)
    return subprocess.CompletedProcess(cmd, p.returncode, out.decode("utf-8", "replace"),
                                       err.decode("utf-8", "replace"))


def _beenden(p: subprocess.Popen) -> None:
    if p.poll() is None:
        p.kill()
        try:
            p.wait(10)
        except subprocess.TimeoutExpired:
            pass
    if p in _laufend:
        _laufend.remove(p)


def alle_beenden() -> None:
    for p in list(_laufend):
        _beenden(p)


# ---------------------------------------------------------------- Sperre (nur eine Instanz)

# Betriebssystem-Sperre auf der Datei, solange der Prozess lebt. Windows/Linux geben sie beim
# Prozessende selbst frei, auch bei Absturz oder geschlossenem Fenster. Früher stand nur die PID in der
# Datei: Nach einem Abbruch hat Windows die PID neu vergeben, und umwandeln.py startete nicht mehr.
_sperre_fd: int | None = None


def sperre_holen() -> bool:
    global _sperre_fd
    g.ARBEIT.mkdir(parents=True, exist_ok=True)
    fd = os.open(SPERRE, os.O_CREAT | os.O_RDWR)
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)  # Byte 0, auch hinter dem Dateiende erlaubt
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        print(f"umwandeln.py läuft schon (Sperre belegt: {SPERRE})")
        return False
    os.ftruncate(fd, 0)
    os.write(fd, f"{os.getpid()}\n".encode())  # nur zur Information
    _sperre_fd = fd
    return True


def sperre_freigeben() -> None:
    global _sperre_fd
    if _sperre_fd is not None:
        os.close(_sperre_fd)  # gibt die Sperre frei
        _sperre_fd = None


# ---------------------------------------------------------------- Zeitfenster

def zeitfenster_parsen(text: str | None) -> tuple[int, int] | None:
    """'22-07' oder '22:30-06:45' -> (Startminute, Endminute) des Tages."""
    if not text:
        return None
    m = re.fullmatch(r"(\d{1,2})(?::(\d\d))?-(\d{1,2})(?::(\d\d))?", text.strip())
    if not m:
        raise argparse.ArgumentTypeError(f"Zeitfenster {text!r}: erwartet z.B. 22-07 oder 22:30-06:45")
    a = int(m.group(1)) * 60 + int(m.group(2) or 0)
    b = int(m.group(3)) * 60 + int(m.group(4) or 0)
    if not (0 <= a < 24 * 60 and 0 <= b <= 24 * 60):
        raise argparse.ArgumentTypeError(f"Zeitfenster {text!r}: Stunde 0..24")
    return a, b


def im_zeitfenster(fenster: tuple[int, int] | None) -> bool:
    if not fenster:
        return True
    jetzt = dt.datetime.now()
    m = jetzt.hour * 60 + jetzt.minute
    a, b = fenster
    return a <= m < b if a <= b else (m >= a or m < b)


def naechster_start(fenster: tuple[int, int]) -> str:
    """ISO-Zeit (mit Offset), zu der das Zeitfenster das nächste Mal aufgeht."""
    jetzt = dt.datetime.now().astimezone()
    a = fenster[0]
    start = jetzt.replace(hour=a // 60 % 24, minute=a % 60, second=0, microsecond=0)
    if start <= jetzt:
        start += dt.timedelta(days=1)
    return start.isoformat(timespec="seconds")


def zeitfenster_abwarten(args) -> None:
    if im_zeitfenster(args.zeitfenster):
        return
    print(f"Warte auf Zeitfenster {args.zeitfenster_text} ...")
    if ereignisse.AKTIV:
        ws_senden(pausiert_bis=naechster_start(args.zeitfenster))
    while not im_zeitfenster(args.zeitfenster):
        time.sleep(POLL_S)
    ws_senden(pausiert_bis=None)


# ---------------------------------------------------------------- Ereignisse (nur im Ereignis-Modus)

_WS: dict = {"wartend": [], "aktiv": None, "pausiert_bis": None}
_WS_ZULETZT: list = [None]


def ws_senden(wartend: list[str] | None = None, aktiv: str | None | bool = False, pausiert_bis="__keep__") -> None:
    """Ereignis "warteschlange", nur bei Änderung."""
    if not ereignisse.AKTIV:
        return
    if wartend is not None:
        _WS["wartend"] = wartend
    if aktiv is not False:
        _WS["aktiv"] = aktiv
    if pausiert_bis != "__keep__":
        _WS["pausiert_bis"] = pausiert_bis
    jetzt = (tuple(_WS["wartend"]), _WS["aktiv"], _WS["pausiert_bis"])
    if jetzt == _WS_ZULETZT[0]:
        return
    _WS_ZULETZT[0] = jetzt
    ereignisse.senden("warteschlange", wartend=list(_WS["wartend"]), aktiv=_WS["aktiv"],
                      pausiert_bis=_WS["pausiert_bis"])


class Fortschritt:
    """Fortschritt der Disc in der Phase "umwandeln" für die Ereignisse (gewichtet nach Titeldauer)."""

    def __init__(self, disc: str, titel: list[dict]):
        self.disc = disc
        self.anzahl = len(titel)
        self.gesamt_s = sum(t.get("dauer_s", 0) for t in titel) or 1
        self.fertig_s = 0.0
        self.titel_nr: int | None = None
        self.titel_s = 0.0
        self.t0 = time.monotonic()

    def titel(self, nr: int, dauer_s: float) -> None:
        self.titel_nr, self.titel_s = nr, dauer_s

    def fertig(self, dauer_s: float) -> None:
        self.fertig_s += dauer_s
        self.titel_nr = None
        self.melden("fertig", 0.0, sofort=True)

    def melden(self, schritt: str, anteil: float, sofort: bool = False, **extra) -> None:
        """anteil: 0..1 des laufenden Titels."""
        if not ereignisse.AKTIV:
            return
        prozent = min(100.0, round((self.fertig_s + anteil * self.titel_s) * 100 / self.gesamt_s, 1))
        ereignisse.fortschritt("umwandeln", self.disc, sofort=sofort,
                               titel=f"t{self.titel_nr:02d}" if self.titel_nr else None, titel_nr=self.titel_nr,
                               titel_anzahl=self.anzahl, prozent=prozent, schritt=schritt, **extra)


_FORT: Fortschritt | None = None


def schritt(name: str, anteil: float = 0.0, **extra) -> None:
    """Schrittwechsel des laufenden Titels (sofort gesendet)."""
    if _FORT:
        _FORT.melden(name, anteil, sofort=True, **extra)


# ---------------------------------------------------------------- Messen und Prüfen

def ffprobe(datei: Path) -> dict:
    r = lauf([FFPROBE, "-v", "error", "-show_entries",
              "format=duration:stream=index,codec_type,codec_name,width,height,"
              "sample_aspect_ratio,display_aspect_ratio,channels,avg_frame_rate,field_order"
              ":stream_disposition=attached_pic",
              "-of", "json", datei], timeout=300)
    if r.returncode:
        raise TitelFehler(f"ffprobe {datei.name}: {r.stderr.strip()[-200:]}")
    return json.loads(r.stdout or "{}")


def dauer(info: dict) -> float | None:
    try:
        return float(info["format"]["duration"])
    except (KeyError, TypeError, ValueError):
        return None


def interlace_messen(quelle: Path, dauer_s: float, log: Log) -> float:
    """Anteil interlaced Bilder (TFF+BFF der Multi-Frame-Erkennung) an 3 Stellen je 30 s."""
    if dauer_s <= IDET_STELLEN * IDET_DAUER_S:
        stellen = [0.0]
        laenge = dauer_s
    else:
        stellen = [max(0.0, dauer_s * (i + 1) / (IDET_STELLEN + 1) - IDET_DAUER_S / 2) for i in range(IDET_STELLEN)]
        laenge = IDET_DAUER_S
    summe = {"TFF": 0, "BFF": 0, "Progressive": 0, "Undetermined": 0}
    for s in stellen:
        r = lauf([FFMPEG, "-hide_banner", "-nostats", "-ss", f"{s:.1f}", "-t", f"{laenge:.1f}", "-i", quelle,
                  "-map", "0:v:0", "-vf", "idet", "-an", "-sn", "-dn", "-f", "null", "-"], timeout=600)
        # ffmpeg legt den Filter teils zweimal an, die erste Instanz meldet nur Nullen: letzte zählt
        m = re.findall(r"Multi frame detection:\s*TFF:\s*(\d+)\s*BFF:\s*(\d+)\s*Progressive:\s*(\d+)\s*"
                       r"Undetermined:\s*(\d+)", r.stderr)
        if not m:
            raise TitelFehler(f"idet ohne Ergebnis bei {s:.0f} s: {r.stderr.strip()[-200:]}")
        for k, v in zip(summe, m[-1]):
            summe[k] += int(v)
    gesamt = sum(summe.values())
    anteil = (summe["TFF"] + summe["BFF"]) / gesamt if gesamt else 0.0
    log(f"idet {quelle.name} bei {', '.join(f'{s:.0f}' for s in stellen)} s: {summe} -> {anteil:.1%}")
    return anteil


def handbrake_scan(quelle: Path) -> dict:
    r = lauf([HANDBRAKE, "-i", quelle, "--scan", "--json", "-t", "1"], timeout=600)
    i = r.stdout.find("JSON Title Set:")
    if i < 0:
        raise TitelFehler("HandBrake-Scan ohne Ergebnis")
    daten, _ = json.JSONDecoder().raw_decode(r.stdout[i + len("JSON Title Set:"):].lstrip())
    if not daten.get("TitleList"):
        raise TitelFehler("HandBrake findet keinen Titel in der Quelle")
    return daten["TitleList"][0]


def comskip_starten(quelle: Path, arbeit: Path) -> subprocess.Popen:
    if arbeit.exists():
        shutil.rmtree(arbeit)
    arbeit.mkdir()
    ausgabe = open(arbeit / "comskip.ausgabe.txt", "wb")
    try:
        return popen([COMSKIP, f"--ini={COMSKIP_INI}", f"--output={arbeit}", "--quiet", quelle],
                     stdout=ausgabe, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, cwd=arbeit)
    finally:
        ausgabe.close()


def comskip_ergebnis(p: subprocess.Popen, quelle: Path, arbeit: Path, log: Log) -> list[list[float]] | None:
    """Wartet auf Comskip und liest die EDL. None = Comskip ist gescheitert."""
    try:
        p.wait()
    finally:
        _beenden(p)
    ausgabe = arbeit / "comskip.ausgabe.txt"
    if ausgabe.exists() and ausgabe.stat().st_size:
        log(f"Comskip-Ausgabe (Exit {p.returncode}):")
        log.roh(ausgabe.read_bytes()[-4000:] + b"\n")
    edl = arbeit / (quelle.stem + ".edl")
    if not edl.exists():
        # Comskip schreibt bei "keine Werbung gefunden" teils keine EDL, Exit 0
        bloecke = [] if p.returncode in (0, 1) else None
    else:
        bloecke = []
        for z in edl.read_text(encoding="utf-8", errors="replace").splitlines():
            teile = z.split()
            if len(teile) >= 2:
                try:
                    a, b = float(teile[0]), float(teile[1])
                except ValueError:
                    continue
                if b > a:
                    bloecke.append([round(a, 1), round(b, 1)])
    shutil.rmtree(arbeit, ignore_errors=True)
    return bloecke


def kapitel_xml(bloecke: list[list[float]], dauer_s: float) -> str:
    """Matroska-Kapitel: "Sendung" und "Werbung" abwechselnd."""
    kapitel: list[tuple[str, float, float]] = []
    pos = 0.0
    for a, b in sorted(bloecke):
        a, b = max(a, pos), min(b, dauer_s)
        if b <= a:
            continue
        if a - pos >= MIN_KAPITEL_S:
            kapitel.append(("Sendung", pos, a))
        elif kapitel and kapitel[-1][0] == "Werbung":  # direkt anschließender Block
            kapitel[-1] = ("Werbung", kapitel[-1][1], b)
            pos = b
            continue
        else:
            a = pos
        kapitel.append(("Werbung", a, b))
        pos = b
    if dauer_s - pos >= MIN_KAPITEL_S:
        kapitel.append(("Sendung", pos, dauer_s))
    elif kapitel:
        kapitel[-1] = (kapitel[-1][0], kapitel[-1][1], dauer_s)

    def zeit(s: float) -> str:
        ns = int(round(s * 1e9))
        return f"{ns // 3_600_000_000_000:02d}:{ns // 60_000_000_000 % 60:02d}:{ns // 1_000_000_000 % 60:02d}.{ns % 1_000_000_000:09d}"

    atome = "".join(
        f"<ChapterAtom><ChapterUID>{i + 1}</ChapterUID><ChapterTimeStart>{zeit(a)}</ChapterTimeStart>"
        f"<ChapterTimeEnd>{zeit(b)}</ChapterTimeEnd><ChapterDisplay><ChapterString>{name}</ChapterString>"
        f"<ChapterLanguage>ger</ChapterLanguage><ChapLanguageIETF>de</ChapLanguageIETF></ChapterDisplay></ChapterAtom>\n"
        for i, (name, a, b) in enumerate(kapitel))
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE Chapters SYSTEM "matroskachapters.dtd">\n'
            f"<Chapters><EditionEntry>\n{atome}</EditionEntry></Chapters>\n")


def kapitel_schreiben(ziel: Path, bloecke: list[list[float]], dauer_s: float, log: Log) -> None:
    xml = ziel.with_name(ziel.name + ".kapitel.xml")
    xml.write_text(kapitel_xml(bloecke, dauer_s), encoding="utf-8")
    try:
        r = lauf([MKVPROPEDIT, ziel, "--chapters", xml], timeout=300)
        log(f"mkvpropedit Kapitel ({len(bloecke)} Werbeblöcke): Exit {r.returncode} {r.stdout.strip()[-300:]}")
        if r.returncode not in (0, 1):  # 1 = Warnungen
            raise TitelFehler(f"mkvpropedit: {(r.stdout + r.stderr).strip()[-200:]}")
    finally:
        xml.unlink(missing_ok=True)


def dekodieren_pruefen(ziel: Path) -> None:
    r = lauf([FFMPEG, "-hide_banner", "-nostats", "-v", "error", "-i", ziel, "-map", "0", "-f", "null", "-"])
    if r.returncode or r.stderr.strip():
        zeilen = r.stderr.strip().splitlines()
        raise TitelFehler(f"Dekodierfehler ({len(zeilen)} Meldungen): {' | '.join(zeilen[:2])[:200]}")


def vorschau(ziel: Path, ordner: Path, nr: int, dauer_s: float) -> None:
    ordner.mkdir(exist_ok=True)
    for k in range(1, VORSCHAU_ANZAHL + 1):
        bild = ordner / f"t{nr:02d}-{k}.jpg"
        pos = dauer_s * k / (VORSCHAU_ANZAHL + 1)
        r = lauf([FFMPEG, "-hide_banner", "-v", "error", "-y", "-ss", f"{pos:.2f}", "-i", ziel, "-map", "0:v:0",
                  "-frames:v", "1", "-vf", f"scale={VORSCHAU_BREITE}:'trunc({VORSCHAU_BREITE}/dar/2)*2',setsar=1",
                  "-q:v", "3", "-update", "1", bild], timeout=120)
        if r.returncode or not bild.exists() or not bild.stat().st_size:
            raise TitelFehler(f"Vorschaubild {bild.name}: {r.stderr.strip()[-200:]}")


# ---------------------------------------------------------------- Ein Titel

def titel_fertig(ordner: Path, t: dict) -> bool:
    v = t.get("video")
    if not v:
        return False
    p = ordner / t["datei"]
    return p.exists() and p.stat().st_size == v.get("groesse_byte")


def handbrake(quelle: Path, ziel: Path, scan: dict, args, modus: int, log: Log, zeile: Zeile) -> None:
    n_audio = len(scan.get("AudioList", []))
    cmd = [HANDBRAKE, "-i", quelle, "-t", "1", "-o", ziel, "--format", "av_mkv",
           "-e", args.encoder, "--encoder-preset", args.preset, "-q", str(args.rf),
           "--crop", "0:0:0:0", "--auto-anamorphic", "--comb-detect",
           "--bwdif=bob" if modus == 50 else "--bwdif", "--rate", str(modus), "--cfr"]
    if n_audio:
        # "copy" + Maske: AC3 durchreichen, alles andere (LPCM, MP2) nach AAC. Mit "copy:ac3"
        # verwirft HandBrake 1.11 eine Nicht-AC3-Spur, statt den Fallback zu nutzen (D0005, LPCM).
        cmd += ["-a", ",".join(str(i + 1) for i in range(n_audio)),
                "-E", ",".join(["copy"] * n_audio), "--audio-copy-mask", "ac3", "--audio-fallback", "av_aac"]
    else:
        cmd += ["-a", "none"]
    cmd += ["--all-subtitles"] if scan.get("SubtitleList") else []
    cmd += ["--markers"] if len(scan.get("ChapterList", [])) > 1 else ["--no-markers"]
    log("HandBrake: " + " ".join(f'"{c}"' if " " in str(c) else str(c) for c in cmd))
    p = popen(cmd, stdout=subprocess.PIPE, stderr=log.f, stdin=subprocess.DEVNULL)
    assert p.stdout
    rest = b""
    try:
        while True:
            stueck = p.stdout.read1(4096) if hasattr(p.stdout, "read1") else p.stdout.read(4096)
            if not stueck:
                break
            rest += stueck
            teile = re.split(rb"[\r\n]", rest)
            rest = teile.pop()
            for z in reversed(teile):
                m = re.search(rb"Encoding: task \d+ of \d+, ([\d.]+) %(?: \(([\d.]+) fps)?", z)
                if m:
                    fps = f"  {float(m.group(2)):5.0f} fps" if m.group(2) else ""
                    zeile.zwischen(f"{float(m.group(1)):5.1f} %{fps}")
                    if _FORT:
                        eta = re.search(rb"ETA (\d+)h(\d+)m(\d+)s", z)
                        _FORT.melden("handbrake", float(m.group(1)) / 100,
                                     fps=float(m.group(2)) if m.group(2) else None,
                                     rest_s=int(eta.group(1)) * 3600 + int(eta.group(2)) * 60 + int(eta.group(3))
                                     if eta else None)
                    break
        p.wait()
    finally:
        _beenden(p)
    if p.returncode:
        raise TitelFehler(f"HandBrake Exit {p.returncode} (Details in umwandeln.log)")
    if not ziel.exists() or not ziel.stat().st_size:
        raise TitelFehler("HandBrake hat keine Ausgabe geschrieben")


def _streams(info: dict, art: str) -> list[dict]:
    return [s for s in info.get("streams", []) if s.get("codec_type") == art
            and not (s.get("disposition") or {}).get("attached_pic")]  # Coverbild ist kein Video


def umpack_codec(daten: dict, info: dict) -> str | None:
    """Videocodec, wenn der Titel nur umgepackt wird (Daten-Disc, effizienter Codec, progressiv)."""
    if daten.get("typ") != "daten":
        return None
    v = _streams(info, "video")
    if len(v) != 1 or v[0].get("codec_name") not in UMPACK_CODECS:
        return None
    if v[0].get("field_order") in ("tt", "bb", "tb", "bt"):  # interlaced: normal mit bwdif umwandeln
        return None
    return v[0]["codec_name"]


def bildrate(info: dict) -> int | None:
    try:
        z, n = (_streams(info, "video")[0].get("avg_frame_rate") or "0/0").split("/")
        return round(int(z) / int(n)) if int(n) else None
    except (IndexError, ValueError):
        return None


def umpacken(quelle: Path, ziel: Path, info: dict, log: Log) -> None:
    """Video, Ton und Untertitel ohne Neukodieren in MKV kopieren (MP4-Textuntertitel -> SRT)."""
    cmd = [FFMPEG, "-hide_banner", "-nostats", "-v", "error", "-y", "-i", quelle,
           "-map", "0:V", "-map", "0:a?", "-map", "0:s?", "-c", "copy"]
    if any(s.get("codec_name") == "mov_text" for s in _streams(info, "subtitle")):
        cmd += ["-c:s", "srt"]
    cmd += ["-f", "matroska", ziel]
    log("ffmpeg: " + " ".join(f'"{c}"' if " " in str(c) else str(c) for c in cmd))
    r = lauf(cmd, timeout=3600)
    if r.returncode or not ziel.exists() or not ziel.stat().st_size:
        raise TitelFehler(f"Umpacken: ffmpeg Exit {r.returncode}: {r.stderr.strip()[-200:]}")


def titel_umwandeln(ordner: Path, daten: dict, t: dict, args, log: Log) -> str:
    """Wandelt einen Titel um, prüft ihn und trägt das Ergebnis in t ein. Rückgabe: Konsolenzeile."""
    nr = t["nr"]
    name = f"t{nr:02d}.mkv"
    ziel = ordner / name
    quelle = ordner / t["datei"]
    if quelle.resolve() == ziel.resolve():  # MakeMKV-Quelle heißt schon tNN.mkv
        neu = ordner / f"t{nr:02d}.quelle.mkv"
        if quelle.exists():
            os.replace(quelle, neu)
        t["datei"] = neu.name
        g.disc_json_schreiben(ordner, daten)
        quelle = neu
    if not quelle.exists():
        raise TitelFehler(f"Quelle {t['datei']} fehlt")
    ziel.unlink(missing_ok=True)  # Rest eines Laufs, der vor dem disc.json-Eintrag abbrach
    part = ordner / (name + ".part")

    info = ffprobe(quelle)
    d_quelle = dauer(info) or float(t.get("dauer_s") or 0)
    if not d_quelle:
        raise TitelFehler("Dauer der Quelle unbekannt")
    log(f"Titel {nr}: Quelle {quelle.name}, {quelle.stat().st_size} Byte, ffprobe {d_quelle:.1f} s, "
        f"disc.json {t.get('dauer_s')} s")
    zeile = Zeile(f"  t{nr:02d}  {mmss(d_quelle):>6}  ")
    if _FORT:
        _FORT.titel(nr, d_quelle)

    codec = umpack_codec(daten, info)
    sender = (t.get("aufnahme") or {}).get("sender")
    cs_proc = cs_arbeit = None
    if codec:  # Daten-Disc mit fertiger Videodatei: keine Messung, kein Comskip, nur umpacken
        anteil, modus = 0.0, bildrate(info)
        werbung_erkennung = "übersprungen: Daten-Disc, nur umgepackt"
        n_audio_q, n_sub = len(_streams(info, "audio")), len(_streams(info, "subtitle"))
        log(f"Titel {nr}: Daten-Disc mit {codec}, {modus}p, wird nur umgepackt")
    else:
        zeile.zwischen("Interlace-Messung")
        schritt("messen")
        anteil = interlace_messen(quelle, d_quelle, log)
        modus = 50 if anteil >= args.schwelle else 25
        scan = handbrake_scan(quelle)
        n_audio_q, n_sub = len(scan.get("AudioList", [])), len(scan.get("SubtitleList", []))
        if oeffentlich_rechtlich(sender) and not args.werbung_immer:
            werbung_erkennung = f"übersprungen: öffentlich-rechtlich ({sender})"
        elif daten.get("typ") == "dvd-video" and not daten.get("rekorder") and not args.werbung_immer:
            # gebrannte oder gekaufte Video-DVD (z.B. Privatvideo D0005): Comskip markiert Szenenwechsel
            werbung_erkennung = "übersprungen: Video-DVD ohne Rekorderdaten"
        elif not COMSKIP:
            werbung_erkennung = "übersprungen: Comskip nicht gefunden"
        else:
            werbung_erkennung = "comskip"
            cs_arbeit = ordner / f"t{nr:02d}.comskip"
            cs_proc = comskip_starten(quelle, cs_arbeit)
        log(f"Titel {nr}: interlaced {anteil:.1%} -> {modus}p, Sender {sender or '-'}, "
            f"Werbung: {werbung_erkennung}")

    t0 = time.monotonic()
    try:
        if codec:
            zeile.zwischen("umpacken")
            schritt("umpacken")
            umpacken(quelle, part, info, log)
        else:
            handbrake(quelle, part, scan, args, modus, log, zeile)
        rechenzeit = time.monotonic() - t0
        bloecke: list[list[float]] | None = []
        if cs_proc:
            zeile.zwischen("warte auf Comskip")
            schritt("comskip", 1.0)
            bloecke = comskip_ergebnis(cs_proc, quelle, cs_arbeit, log)
            if bloecke is None:
                werbung_erkennung = f"comskip gescheitert (Exit {cs_proc.returncode})"
            log(f"Titel {nr}: Werbeblöcke {bloecke}")

        zeile.zwischen("Prüfung")
        schritt("pruefen", 1.0)
        info_z = ffprobe(part)
        d_ziel = dauer(info_z)
        if d_ziel is None:
            raise TitelFehler("Dauer des Ziels nicht lesbar")
        if abs(d_ziel - d_quelle) > DAUER_TOLERANZ_S:
            raise TitelFehler(f"Dauer Ziel {d_ziel:.1f} s, Quelle {d_quelle:.1f} s")
        n_audio_z = sum(1 for s in info_z.get("streams", []) if s.get("codec_type") == "audio")
        if n_audio_z != n_audio_q:
            raise TitelFehler(f"Tonspuren Ziel {n_audio_z}, Quelle {n_audio_q}")
        if bloecke:
            schritt("kapitel", 1.0)
            kapitel_schreiben(part, bloecke, d_ziel, log)
        schritt("pruefen", 1.0)
        dekodieren_pruefen(part)
        schritt("vorschau", 1.0)
        vorschau(part, ordner / "vorschau", nr, d_ziel)
        groesse = part.stat().st_size
        if not groesse:
            raise TitelFehler("Ziel ist leer")
        os.replace(part, ziel)
    except BaseException:
        if cs_proc:
            _beenden(cs_proc)
            shutil.rmtree(cs_arbeit, ignore_errors=True)
        part.unlink(missing_ok=True)
        raise

    t["roh_datei"] = t["datei"]
    t["datei"] = name
    t["werbung"] = bloecke
    t["werbung_erkennung"] = werbung_erkennung
    t["video"] = {
        "encoder": f"kopie ({codec})" if codec else args.encoder,
        "preset": None if codec else args.preset, "rf": None if codec else args.rf, "bildrate": modus,
        "interlaced_anteil": None if codec else round(anteil, 3), "groesse_byte": groesse,
        "dauer_s": round(d_ziel, 1), "sekunden_rechenzeit": int(round(rechenzeit)),
        "tonspuren": n_audio_q, "untertitel": n_sub,
    }
    t.pop("umwandeln_fehler", None)
    g.disc_json_schreiben(ordner, daten)
    if bloecke is None:
        w = "Werbung: Comskip-Fehler"
    elif werbung_erkennung == "comskip":
        w = f"Werbung: {len(bloecke)} Blöcke ({mmss(sum(b - a for a, b in bloecke))} min)"
    else:
        w = "Werbung: -"
    art = f"umgepackt ({codec})" if codec else f"(interl. {anteil:.0%})  {args.encoder} RF{args.rf}"
    text = (f"{modus}p {art}  {mmss(rechenzeit):>5} min "
            f"({d_quelle / max(rechenzeit, 1):.1f}x)  {gb(groesse)} GB  {w}  ok")
    zeile.ende(text)
    log(f"Titel {nr}: fertig  {text}")
    if _FORT:
        _FORT.fertig(d_quelle)
    return text


# ---------------------------------------------------------------- Eine Disc

def aufraeumen(ordner: Path) -> None:
    """Reste eines unterbrochenen Laufs entfernen: *.part, Kapitel-XML, Comskip-Ordner, Temp-Dateien von disc.json."""
    for p in ordner.glob("*.part"):
        p.unlink(missing_ok=True)
    for p in ordner.glob("tmp*.tmp"):
        p.unlink(missing_ok=True)
    for p in ordner.glob("*.kapitel.xml"):
        p.unlink(missing_ok=True)
    for p in ordner.glob("t*.comskip"):
        shutil.rmtree(p, ignore_errors=True)


def verschieben(ordner: Path, ziel_basis: Path) -> Path | None:
    ziel = ziel_basis / ordner.name
    if ziel.exists():
        return None
    ziel_basis.mkdir(parents=True, exist_ok=True)
    for versuch in range(5):  # Windows: kurz gesperrte Dateien (Virenscanner, Explorer-Vorschau)
        try:
            shutil.move(str(ordner), str(ziel))
            return ziel
        except PermissionError:
            if versuch == 4:
                raise
            time.sleep(5)
    return None


def abschliessen(ordner: Path, daten: dict, log: Log) -> Path:
    """Alle Titel bestanden: Rohdateien löschen, Status setzen, nach 2-fertig verschieben."""
    if (g.FERTIG / ordner.name).exists():
        raise RuntimeError(f"{g.FERTIG.name}/{ordner.name} gibt es schon")
    for t in daten["titel"]:
        roh = t.get("roh_datei")
        if roh and roh != t["datei"]:
            p = ordner / roh
            if p.exists():
                p.unlink()
                log(f"Rohdatei gelöscht: {roh}")
            eltern = p.parent
            while eltern != ordner and eltern.is_dir() and not any(eltern.iterdir()):
                eltern.rmdir()
                eltern = eltern.parent
    daten["status"] = "umgewandelt"
    daten["umgewandelt"] = g.jetzt()
    daten.pop("problem_grund", None)
    g.disc_json_schreiben(ordner, daten)
    return fertig_verschieben(ordner, daten, log)


def fertig_verschieben(ordner: Path, daten: dict, log: Log) -> Path:
    v = [t["video"] for t in daten["titel"] if t.get("video")]
    hinweis = (f"{sum(x['groesse_byte'] for x in v) / 1e9:.2f} GB, "
               f"{sum(len(t.get('werbung') or []) for t in daten['titel'])} Werbeblöcke")
    log(f"Disc umgewandelt: {hinweis}")
    log.close()
    ziel = verschieben(ordner, g.FERTIG)
    if not ziel:
        raise RuntimeError(f"{g.FERTIG.name}/{ordner.name} gibt es schon")
    g.fortschritt_setzen(daten, hinweis)
    return ziel


def problem(ordner: Path, daten: dict, grund: str, log: Log) -> Path:
    daten["status"] = "problem"
    daten["problem_grund"] = grund
    g.disc_json_schreiben(ordner, daten)
    log(f"PROBLEM: {grund}")
    log.close()
    ziel = verschieben(ordner, g.PROBLEM)
    g.fortschritt_setzen(daten, grund)
    if not ziel:
        print(f"  Hinweis: {g.PROBLEM.name}/{ordner.name} gibt es schon, Disc bleibt in {g.ROH.name}/")
        return ordner
    return ziel


def disc_umwandeln(ordner: Path, args) -> bool:
    """Wandelt alle Titel einer Disc um. Rückgabe: True = umgewandelt, False = Problem."""
    daten = g.disc_json_lesen(ordner)
    aufraeumen(ordner)
    log = Log(ordner / "umwandeln.log")
    disc = daten["disc"]
    titel = daten.get("titel", [])
    stunden = sum(t.get("dauer_s", 0) for t in titel) / 3600
    print(f"{disc}: \"{daten.get('beschriftung', '')}\"  {len(titel)} Titel, {stunden:.2f} h")
    global _FORT
    if ereignisse.AKTIV:
        _FORT = Fortschritt(disc, titel)
        ws_senden(aktiv=disc, pausiert_bis=None)
        typ = daten.get("typ") or ""
        ereignisse.senden("disc", disc=disc, beschriftung=daten.get("beschriftung", ""), label=daten.get("label", ""),
                          medientyp="audio" if typ.startswith("audio") else typ,
                          seriennummer=daten.get("seriennummer"), phase="umwandeln")
        for t in titel:  # schon fertige Titel (nach Neustart) zählen zum Fortschritt
            if titel_fertig(ordner, t):
                _FORT.fertig_s += t.get("dauer_s", 0)
    log(f"Start: Encoder {args.encoder} {args.preset} RF {args.rf}, Schwelle {args.schwelle:.0%}, "
        f"Priorität {args.prioritaet}, PID {os.getpid()}")
    fehler: list[str] = []
    t0 = time.monotonic()
    try:
        if not titel:
            fehler.append("keine Titel in disc.json")
        for t in titel:
            if titel_fertig(ordner, t):
                v = t["video"]
                print(f"  t{t['nr']:02d}  schon fertig ({v['bildrate']}p, {gb(v['groesse_byte'])} GB)")
                continue
            zeitfenster_abwarten(args)
            try:
                titel_umwandeln(ordner, daten, t, args, log)
            except TitelFehler as e:
                t["umwandeln_fehler"] = str(e)
                g.disc_json_schreiben(ordner, daten)
                fehler.append(f"t{t['nr']:02d}: {e}")
                log(f"Titel {t['nr']}: FEHLER {e}")
                if TTY:
                    sys.stdout.write("\r" + " " * 100 + "\r")
                print(f"  t{t['nr']:02d}  FEHLER: {e}")
                ereignisse.meldung("fehler", f"t{t['nr']:02d}: {e}", disc)
    except KeyboardInterrupt:
        alle_beenden()
        aufraeumen(ordner)
        log("Abbruch durch Nutzer, Disc bleibt eingelesen")
        log.close()
        raise
    except Exception as e:  # unerwarteter Fehler: Disc als Problem ablegen
        alle_beenden()
        aufraeumen(ordner)
        fehler.append(f"Abbruch: {type(e).__name__}: {e}")
        log(f"Abbruch: {type(e).__name__}: {e}")

    dauer_s = time.monotonic() - t0
    if fehler:
        grund = "; ".join(fehler)[:500]
        ziel = problem(ordner, daten, grund, log)
        print(f"{disc}: PROBLEM nach {mmss(dauer_s)} min: {grund}  -> {ziel.parent.name}/{ziel.name}")
        ereignisse.senden("disc_ende", disc=disc, phase="umwandeln", status="problem", grund=grund,
                          ziel=str(ziel), dauer_s=int(dauer_s))
        return False
    try:
        ziel = abschliessen(ordner, daten, log)
    except Exception as e:
        log.close()
        log = Log(ordner / "umwandeln.log")
        ziel = problem(ordner, daten, f"Abschluss: {e}", log)
        print(f"{disc}: PROBLEM beim Abschluss: {e}")
        ereignisse.senden("disc_ende", disc=disc, phase="umwandeln", status="problem", grund=f"Abschluss: {e}",
                          ziel=str(ziel), dauer_s=int(dauer_s))
        return False
    v = [t["video"] for t in daten["titel"]]
    print(f"{disc}: umgewandelt in {mmss(dauer_s)} min, {gb(sum(x['groesse_byte'] for x in v))} GB  "
          f"-> {ziel.parent.name}/{ziel.name}")
    if _FORT:
        _FORT.fertig_s = _FORT.gesamt_s
        _FORT.titel_nr = None
        _FORT.melden("fertig", 0.0, sofort=True)
    ereignisse.senden("disc_ende", disc=disc, phase="umwandeln", status="umgewandelt", grund=None,
                      ziel=str(ziel), dauer_s=int(dauer_s))
    return True


def einsortieren_auto(disc: str) -> None:
    """Ruft 'einsortieren.py auto <disc>' auf: sichere Discs wandern nach DVD_ZIEL, der Rest wartet."""
    skript = Path(__file__).resolve().parent / "einsortieren.py"
    t0 = time.monotonic()
    rc = None
    fehler = None
    if ereignisse.AKTIV:
        ereignisse.senden("disc", disc=disc, phase="einsortieren")
    try:
        # im Ereignis-Modus ohne stdin: einsortieren.py darf dem Protokoll keine Eingaben wegnehmen
        r = subprocess.run([sys.executable, "-I", str(skript), "auto", disc], timeout=1800,
                           **({"stdin": subprocess.DEVNULL} if ereignisse.AKTIV else {}))
        rc = r.returncode
        if r.returncode == 2:
            print(f"{disc}: automatisches Einsortieren nicht möglich (siehe oben)")
    except Exception as e:
        fehler = str(e)
        print(f"{disc}: einsortieren.py auto fehlgeschlagen: {e}")
    if ereignisse.AKTIV:
        hinweis = ""
        try:
            hinweis = g.fortschritt_lesen().get(disc, {}).get("hinweis", "")
        except Exception:
            pass
        if rc == 0:
            ereignisse.senden("disc_ende", disc=disc, phase="einsortieren", status="einsortiert", grund=None,
                              ziel=os.environ.get("DVD_ZIEL"), dauer_s=int(time.monotonic() - t0))
        else:
            grund = fehler or hinweis or ("automatisches Einsortieren nicht möglich" if rc == 2 else "zum Prüfen")
            if rc == 2 or fehler:
                ereignisse.meldung("warnung", f"{disc}: einsortieren.py auto: {grund}", disc)
            ereignisse.senden("disc_ende", disc=disc, phase="einsortieren", status="pruefen", grund=grund,
                              ziel=str(g.FERTIG / disc), dauer_s=int(time.monotonic() - t0))


# ---------------------------------------------------------------- Warteschlange

def warteschlange(nur: str | None = None) -> list[Path]:
    """Discs mit Status "eingelesen" in 1-roh, älteste zuerst. Ordner mit Punkt (z.B. D0001.einlesen)
    sind noch in Arbeit und werden übergangen. Hängengebliebene "umgewandelt" werden nachgeschoben."""
    if not g.ROH.exists():
        return []
    liste = []
    for o in g.ROH.iterdir():
        if not o.is_dir() or "." in o.name or (nur and o.name != nur):
            continue
        try:
            d = g.disc_json_lesen(o)
        except (OSError, ValueError):
            continue
        if d.get("status") == "umgewandelt":  # Abbruch zwischen Abschluss und Verschieben
            log = Log(o / "umwandeln.log")
            try:
                z = fertig_verschieben(o, d, log)
                print(f"{o.name}: war schon umgewandelt -> {z.parent.name}/{z.name}")
            except Exception as e:
                print(f"{o.name}: umgewandelt, aber nicht verschiebbar: {e}")
            continue
        if d.get("status") == "eingelesen":
            liste.append((d.get("eingelesen") or "", o.name, o))
    return [o for *_, o in sorted(liste)]


def main() -> int:
    ereignisse.senden("start", skript="umwandeln", pid=os.getpid(), argumente=sys.argv[1:])
    code = 1
    try:
        code = _main()
        return code
    except KeyboardInterrupt:
        code = 130
        raise
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
        raise
    finally:
        ereignisse.senden("ende", code=code)


def _main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--einmal", action="store_true", help="eine Disc umwandeln, dann Ende")
    ap.add_argument("--disc", help="nur diese Disc (z.B. D0001), danach Ende")
    ap.add_argument("--encoder", choices=sorted(ENCODER), default="x265")
    ap.add_argument("--rf", type=float, help="Qualität (x265 Standard 21, vce_h265 Standard 24)")
    ap.add_argument("--preset", help="Encoder-Preset (x265 Standard medium, vce_h265 Standard quality)")
    ap.add_argument("--schwelle", type=float, default=0.30,
                    help="Anteil interlaced Bilder, ab dem 50p (bob) gewählt wird (Standard 0.30)")
    ap.add_argument("--prioritaet", choices=sorted(PRIORITAET), default="unter-normal")
    ap.add_argument("--zeitfenster", help="nur darin neue Titel anfangen, z.B. 22-07 oder 22:30-06:00")
    ap.add_argument("--werbung-immer", action="store_true", help="Comskip auch bei öffentlich-rechtlichen Sendern")
    ap.add_argument("--ohne-einsortieren", action="store_true",
                    help="nach dem Umwandeln nicht 'einsortieren.py auto' aufrufen")
    args = ap.parse_args()
    args.zeitfenster_text = args.zeitfenster or ""
    try:
        args.zeitfenster = zeitfenster_parsen(args.zeitfenster)
    except argparse.ArgumentTypeError as e:
        ap.error(str(e))
    std = ENCODER[args.encoder]
    args.rf = std["rf"] if args.rf is None else (int(args.rf) if float(args.rf).is_integer() else args.rf)
    args.preset = args.preset or std["preset"]
    nur = g.disc_name(args.disc) if args.disc else None

    fehlt = [n for n, p in (("HandBrakeCLI", HANDBRAKE), ("ffmpeg", FFMPEG), ("ffprobe", FFPROBE),
                            ("mkvpropedit", MKVPROPEDIT)) if not p]
    if fehlt:
        print("Fehlt: " + ", ".join(fehlt))
        ereignisse.meldung("fehler", "Fehlt: " + ", ".join(fehlt))
        return 2
    if not COMSKIP:
        print("Hinweis: Comskip nicht gefunden, Werbung wird nicht markiert")
        ereignisse.meldung("hinweis", "Comskip nicht gefunden, Werbung wird nicht markiert")
    prioritaet_setzen(args.prioritaet)
    if not sperre_holen():
        ereignisse.meldung("warnung", f"umwandeln.py läuft schon (Sperre belegt: {SPERRE})")
        return 1
    try:
        wartet = False
        while True:
            liste = warteschlange(nur)
            if ereignisse.AKTIV:
                ws_senden([o.name for o in liste], None, None)
            if liste:
                wartet = False
                zeitfenster_abwarten(args)
                ws_senden([o.name for o in liste[1:]], liste[0].name, None)
                ok = disc_umwandeln(liste[0], args)
                if ok and not args.ohne_einsortieren:
                    einsortieren_auto(liste[0].name)
                ws_senden([o.name for o in liste[1:]], None, None)
                if args.einmal or nur:
                    return 0 if ok else 3
                continue
            if args.einmal:
                print(f"Keine Disc {nur + ' ' if nur else ''}mit Status \"eingelesen\" in {g.ROH}")
                return 1 if nur else 0
            if not wartet:
                print(f"Warte auf eingelesene Discs in {g.ROH} ... (Strg+C beendet)")
                wartet = True
            time.sleep(POLL_S)
    except KeyboardInterrupt:
        alle_beenden()
        print("\nAbgebrochen. Beim nächsten Start geht es mit dem unterbrochenen Titel weiter.")
        return 130
    finally:
        sperre_freigeben()


if __name__ == "__main__":
    sys.exit(main())
