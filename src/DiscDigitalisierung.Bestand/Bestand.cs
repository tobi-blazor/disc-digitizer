using System.Globalization;
using System.Text.Json;
using DiscDigitalisierung.Kern;

#pragma warning disable CA1416 // FileStream.Lock: nur Windows genutzt

namespace DiscDigitalisierung.Bestand;

/// <summary>Liest den Stand aller Discs aus fortschritt.csv und den Ordnern 1-roh, 2-fertig, 3-problem.
/// Nur lesend; Python schreibt, dieses Objekt beobachtet.</summary>
public sealed class Bestand : IBestand
{
    static readonly string[] Ablagen = ["1-roh", "2-fertig", "3-problem"];
    const string EinlesenEndung = ".einlesen";

    readonly string _arbeit;
    readonly SemaphoreSlim _sperre = new(1, 1);
    readonly object _beobachtenSperre = new();
    FileSystemWatcher? _watcher;
    System.Threading.Timer? _entprellTimer;
    System.Threading.Timer? _abfrageTimer;
    volatile bool _verworfen;
    IReadOnlyList<DiscEintrag> _discs = [];
    DateTimeOffset? _stand;

    public Bestand(Einstellungen einstellungen) : this(einstellungen.ArbeitsOrdner) { }

    public Bestand(string arbeitsOrdner)
    {
        _arbeit = arbeitsOrdner ?? "";
    }

    public IReadOnlyList<DiscEintrag> Discs => Volatile.Read(ref _discs);
    public DateTimeOffset? Stand => _stand;
    public event EventHandler? Geaendert;

    public bool UmwandelnLaeuft
    {
        get
        {
            if (string.IsNullOrWhiteSpace(_arbeit)) return false;
            var datei = Path.Combine(_arbeit, "umwandeln.lock");
            if (!File.Exists(datei)) return false; // nie anlegen
            FileStream? fs = null;
            try
            {
                try { fs = new FileStream(datei, FileMode.Open, FileAccess.ReadWrite, FileShare.ReadWrite | FileShare.Delete); }
                catch (UnauthorizedAccessException) { fs = new FileStream(datei, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete); }
                try
                {
                    fs.Lock(0, 1);
                }
                catch (IOException) { return true; } // Region gesperrt: ein Prozess hält die Sperre
                try { fs.Unlock(0, 1); } catch (IOException) { }
                return false;
            }
            catch (FileNotFoundException) { return false; }
            catch (DirectoryNotFoundException) { return false; }
            catch (IOException) { return false; }
            catch (UnauthorizedAccessException) { return false; }
            finally { fs?.Dispose(); }
        }
    }

    public long? ArbeitFreiByte
    {
        get
        {
            if (string.IsNullOrWhiteSpace(_arbeit) || !Directory.Exists(_arbeit)) return null;
            return Win32.PlatzLesen(_arbeit, out var frei, out _) ? frei : null;
        }
    }

    public async Task AktualisierenAsync(CancellationToken ct = default)
    {
        await _sperre.WaitAsync(ct).ConfigureAwait(false);
        try
        {
            var neu = await Task.Run(Einlesen, ct).ConfigureAwait(false);
            _stand = DateTimeOffset.Now;
            var alt = Discs;
            if (!alt.SequenceEqual(neu))
            {
                Volatile.Write(ref _discs, neu);
                Geaendert?.Invoke(this, EventArgs.Empty);
            }
        }
        finally { _sperre.Release(); }
    }

    public void Beobachten()
    {
        lock (_beobachtenSperre)
        {
            if (_verworfen || _abfrageTimer != null) return;
            _entprellTimer = new System.Threading.Timer(_ => AktualisierenStill(), null, Timeout.Infinite, Timeout.Infinite);
            if (Directory.Exists(_arbeit))
            {
                try
                {
                    var w = new FileSystemWatcher(_arbeit)
                    {
                        IncludeSubdirectories = true,
                        NotifyFilter = NotifyFilters.FileName | NotifyFilters.DirectoryName | NotifyFilters.LastWrite | NotifyFilters.Size,
                        InternalBufferSize = 64 * 1024,
                    };
                    FileSystemEventHandler h = (_, e) => Anstossen(e.Name);
                    w.Changed += h; w.Created += h; w.Deleted += h;
                    w.Renamed += (_, e) => Anstossen(e.Name);
                    w.Error += (_, _) => Anstossen(null);
                    w.EnableRaisingEvents = true;
                    _watcher = w;
                }
                catch (Exception e) when (e is ArgumentException or IOException or PlatformNotSupportedException) { }
            }
            // Rückfall: regelmäßig abfragen, falls der Watcher Ereignisse verliert.
            _abfrageTimer = new System.Threading.Timer(_ => AktualisierenStill(), null, TimeSpan.Zero, TimeSpan.FromSeconds(10));
        }
    }

    void Anstossen(string? name)
    {
        // Relevant: fortschritt.csv sowie alles in den Ablagen (disc.json, Dateien für die Größe).
        if (name != null && !name.StartsWith("fortschritt", StringComparison.OrdinalIgnoreCase)
            && !Ablagen.Any(a => name.StartsWith(a, StringComparison.OrdinalIgnoreCase))) return;
        try { _entprellTimer?.Change(500, Timeout.Infinite); } catch (ObjectDisposedException) { }
    }

    void AktualisierenStill()
    {
        if (_verworfen) return;
        try { AktualisierenAsync().GetAwaiter().GetResult(); }
        catch (Exception) { /* nächste Runde versucht es erneut */ }
    }

    public void Dispose()
    {
        lock (_beobachtenSperre)
        {
            if (_verworfen) return;
            _verworfen = true;
            _watcher?.Dispose();
            _entprellTimer?.Dispose();
            _abfrageTimer?.Dispose();
        }
    }

    // ---------- Einlesen ----------

    IReadOnlyList<DiscEintrag> Einlesen()
    {
        var zeilen = CsvZeilen();
        var ordner = OrdnerSuchen();
        var ergebnis = new List<DiscEintrag>();

        foreach (var disc in zeilen.Keys.Union(ordner.Keys, StringComparer.OrdinalIgnoreCase))
        {
            zeilen.TryGetValue(disc, out var z);
            ordner.TryGetValue(disc, out var o);
            ergebnis.Add(Zusammenfuehren(disc, z, o));
        }
        ergebnis.Sort((a, b) => DiscNummer.Vergleich(a.Disc, b.Disc));
        return ergebnis;
    }

    sealed record CsvZeile(string Disc, string Beschriftung, string Typ, int? Titel, double? Stunden, string Status, DateTimeOffset? Geaendert, string Hinweis);
    sealed record OrdnerInfo(string Ablage, string Pfad, bool Einlesen);

    Dictionary<string, CsvZeile> CsvZeilen()
    {
        var ergebnis = new Dictionary<string, CsvZeile>(StringComparer.OrdinalIgnoreCase);
        var text = DateiHelfer.TextLesen(Path.Combine(_arbeit, "fortschritt.csv"));
        if (string.IsNullOrEmpty(text)) return ergebnis;
        var zeilen = CsvLeser.Zeilen(text.TrimStart('﻿'));
        if (zeilen.Count == 0) return ergebnis;

        var spalten = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
        for (var i = 0; i < zeilen[0].Count; i++) spalten[zeilen[0][i].Trim()] = i;
        if (!spalten.ContainsKey("disc")) return ergebnis;

        string Feld(List<string> z, string name) =>
            spalten.TryGetValue(name, out var i) && i < z.Count ? z[i].Trim() : "";

        foreach (var z in zeilen.Skip(1))
        {
            try
            {
                var disc = Feld(z, "disc");
                if (disc == "") continue;
                int? titel = int.TryParse(Feld(z, "titel"), NumberStyles.Integer, CultureInfo.InvariantCulture, out var t) ? t : null;
                double? stunden = double.TryParse(Feld(z, "stunden"), NumberStyles.Float, CultureInfo.InvariantCulture, out var s) ? s : null;
                DateTimeOffset? geaendert = DateTime.TryParse(Feld(z, "geaendert"), CultureInfo.InvariantCulture,
                    DateTimeStyles.AssumeLocal, out var d) ? new DateTimeOffset(d) : null;
                ergebnis[disc] = new CsvZeile(disc, Feld(z, "beschriftung"), Feld(z, "typ"), titel, stunden,
                    Feld(z, "status"), geaendert, Feld(z, "hinweis"));
            }
            catch (Exception) { /* kaputte Zeile überspringen */ }
        }
        return ergebnis;
    }

    Dictionary<string, OrdnerInfo> OrdnerSuchen()
    {
        var ergebnis = new Dictionary<string, OrdnerInfo>(StringComparer.OrdinalIgnoreCase);
        // Priorität bei Doppelten: problem > fertig > roh (später überschreibt)
        foreach (var ablage in Ablagen)
        {
            var basis = Path.Combine(_arbeit, ablage);
            if (!Directory.Exists(basis)) continue;
            string[] unter;
            try { unter = Directory.GetDirectories(basis); }
            catch (IOException) { continue; }
            catch (UnauthorizedAccessException) { continue; }
            foreach (var pfad in unter)
            {
                var name = Path.GetFileName(pfad);
                var einlesen = ablage == "1-roh" && name.EndsWith(EinlesenEndung, StringComparison.OrdinalIgnoreCase);
                var disc = einlesen ? name[..^EinlesenEndung.Length] : name;
                if (einlesen && ergebnis.ContainsKey(disc)) continue;
                ergebnis[disc] = new OrdnerInfo(ablage, pfad, einlesen);
            }
        }
        return ergebnis;
    }

    static DiscEintrag Zusammenfuehren(string disc, CsvZeile? z, OrdnerInfo? o)
    {
        JsonElement? json = null;
        JsonDocument? doc = null;
        long? groesse = null;
        if (o != null)
        {
            var text = DateiHelfer.TextLesen(Path.Combine(o.Pfad, "disc.json"));
            if (!string.IsNullOrWhiteSpace(text))
            {
                try { doc = JsonDocument.Parse(text); if (doc.RootElement.ValueKind == JsonValueKind.Object) json = doc.RootElement; }
                catch (JsonException) { }
            }
            groesse = OrdnerGroesse(o.Pfad);
        }

        try
        {
            string? Str(string name) => json is { } j && j.TryGetProperty(name, out var v) && v.ValueKind == JsonValueKind.String ? v.GetString() : null;
            long? Zahl(string name) => json is { } j && j.TryGetProperty(name, out var v) && v.ValueKind == JsonValueKind.Number && v.TryGetInt64(out var n) ? n : null;

            int? titelAnzahl = null;
            double? sekunden = null;
            if (json is { } jj && jj.TryGetProperty("titel", out var tl) && tl.ValueKind == JsonValueKind.Array)
            {
                titelAnzahl = tl.GetArrayLength();
                sekunden = 0;
                foreach (var t in tl.EnumerateArray())
                    if (t.ValueKind == JsonValueKind.Object && t.TryGetProperty("dauer_s", out var d) && d.ValueKind == JsonValueKind.Number)
                        sekunden += d.GetDouble();
            }

            var status = z?.Status;
            if (string.IsNullOrEmpty(status)) status = Str("status");
            if (o is { Einlesen: true }) status = "einlesen";
            if (string.IsNullOrEmpty(status)) status = "unbekannt";

            DateTimeOffset? geaendert = z?.Geaendert;
            if (geaendert == null && Str("eingelesen") is { } e
                && DateTime.TryParse(e, CultureInfo.InvariantCulture, DateTimeStyles.AssumeLocal, out var dt))
                geaendert = new DateTimeOffset(dt);

            var beschriftung = z?.Beschriftung;
            if (string.IsNullOrEmpty(beschriftung)) beschriftung = Str("beschriftung") ?? "";
            var typ = z?.Typ;
            if (string.IsNullOrEmpty(typ)) typ = Str("typ") ?? "";

            return new DiscEintrag(
                disc, beschriftung, typ,
                z?.Titel ?? titelAnzahl ?? 0,
                z?.Stunden ?? (sekunden.HasValue ? Math.Round(sekunden.Value / 3600, 2) : 0),
                status, geaendert, z?.Hinweis ?? "",
                o?.Ablage, o?.Pfad,
                Str("problem_grund"), Zahl("lesefehler_sektoren"), groesse);
        }
        finally { doc?.Dispose(); }
    }

    static long? OrdnerGroesse(string pfad)
    {
        try
        {
            long summe = 0;
            foreach (var f in new DirectoryInfo(pfad).EnumerateFiles("*", SearchOption.AllDirectories))
            {
                try { summe += f.Length; } catch (IOException) { }
            }
            return summe;
        }
        catch (IOException) { return null; }
        catch (UnauthorizedAccessException) { return null; }
    }
}

/// <summary>Sortiert Disc-Namen wie "D0012" nach der Nummer, andere Namen danach alphabetisch.</summary>
internal static class DiscNummer
{
    public static int Vergleich(string a, string b)
    {
        var na = Nummer(a); var nb = Nummer(b);
        if (na.HasValue && nb.HasValue && na != nb) return na.Value.CompareTo(nb.Value);
        if (na.HasValue != nb.HasValue) return na.HasValue ? -1 : 1;
        return string.Compare(a, b, StringComparison.OrdinalIgnoreCase);
    }

    static int? Nummer(string s)
    {
        var ziffern = new string(s.Where(char.IsDigit).ToArray());
        return s.Length > 0 && char.IsLetter(s[0]) && int.TryParse(ziffern, out var n) ? n : null;
    }
}
