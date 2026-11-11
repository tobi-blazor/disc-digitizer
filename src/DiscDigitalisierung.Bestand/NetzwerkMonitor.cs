using System.Diagnostics;
using DiscDigitalisierung.Kern;

namespace DiscDigitalisierung.Bestand;

/// <summary>Prüft die Export-Ziele (z.B. Netzwerkfreigaben): erreichbar, freier Platz, Antwortzeit. Nur lesend.
/// Jede Prüfung läuft in einem eigenen Task mit Zeitlimit, damit ein hängender Server nichts blockiert.</summary>
public sealed class NetzwerkMonitor : INetzwerkMonitor
{
    readonly List<(string Name, string Pfad)> _ziele = [];
    readonly SemaphoreSlim _sperre = new(1, 1);
    System.Threading.Timer? _timer;
    volatile bool _verworfen;
    IReadOnlyList<ZielStatus> _status = [];

    /// <summary>Zeitlimit je Prüfung.</summary>
    public TimeSpan Zeitlimit { get; set; } = TimeSpan.FromSeconds(5);

    public NetzwerkMonitor(Einstellungen einstellungen)
    {
        if (!string.IsNullOrWhiteSpace(einstellungen.DvdZiel)) _ziele.Add(("Videos (DVD_ZIEL)", einstellungen.DvdZiel.Trim()));
        if (!string.IsNullOrWhiteSpace(einstellungen.MusikZiel)) _ziele.Add(("Musik (MUSIK_ZIEL)", einstellungen.MusikZiel.Trim()));
    }

    public IReadOnlyList<ZielStatus> Ziele => Volatile.Read(ref _status);
    public event EventHandler? Geaendert;

    public async Task PruefenAsync(CancellationToken ct = default)
    {
        await _sperre.WaitAsync(ct).ConfigureAwait(false);
        try
        {
            var aufgaben = _ziele.Select(z => ZielPruefen(z.Name, z.Pfad, ct)).ToArray();
            var ergebnis = await Task.WhenAll(aufgaben).ConfigureAwait(false);
            Volatile.Write(ref _status, ergebnis);
            Geaendert?.Invoke(this, EventArgs.Empty);
        }
        finally { _sperre.Release(); }
    }

    public void Starten(TimeSpan intervall)
    {
        if (_verworfen) return;
        _timer?.Dispose();
        _timer = new System.Threading.Timer(_ =>
        {
            if (_verworfen) return;
            try { PruefenAsync().GetAwaiter().GetResult(); } catch (Exception) { }
        }, null, TimeSpan.Zero, intervall);
    }

    async Task<ZielStatus> ZielPruefen(string name, string pfad, CancellationToken ct)
    {
        var anzeige = pfad;
        var messen = Stopwatch.StartNew();
        var arbeit = Task.Run(() =>
        {
            // WNetGetConnection kann bei toten Verbindungen ebenfalls hängen, daher im selben Task.
            var unc = Win32.UncZuLaufwerk(pfad);
            if (unc != null) anzeige = $"{pfad} = {unc}";
            if (!Directory.Exists(pfad)) return (Ok: false, Frei: (long?)null, Gesamt: (long?)null, Fehler: (string?)"Verzeichnis nicht erreichbar");
            if (Win32.PlatzLesen(pfad, out var frei, out var gesamt)) return (true, frei, gesamt, null);
            return (true, null, null, "Freier Platz nicht ermittelbar");
        }, CancellationToken.None);

        try
        {
            var r = await arbeit.WaitAsync(Zeitlimit, ct).ConfigureAwait(false);
            messen.Stop();
            return new ZielStatus(name, anzeige, r.Ok, r.Frei, r.Gesamt, messen.Elapsed, DateTimeOffset.Now, r.Fehler);
        }
        catch (TimeoutException)
        {
            return new ZielStatus(name, pfad, false, null, null, null, DateTimeOffset.Now,
                $"Keine Antwort innerhalb von {Zeitlimit.TotalSeconds:0} s");
        }
        catch (Exception e) when (e is not OperationCanceledException)
        {
            return new ZielStatus(name, pfad, false, null, null, null, DateTimeOffset.Now, e.Message);
        }
    }

    public void Dispose()
    {
        _verworfen = true;
        _timer?.Dispose();
    }
}
