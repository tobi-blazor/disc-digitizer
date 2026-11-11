using System.ComponentModel;
using System.Diagnostics;
using System.Text;
using DiscDigitalisierung.Kern;

namespace DiscDigitalisierung.Prozesse;

/// <summary>Startet einlesen.py bzw. umwandeln.py im Ereignis-Modus und übersetzt stdout/stderr in Ereignisse.
/// Ereignisse kommen aus Hintergrund-Threads (je Kanal in Reihenfolge).</summary>
public sealed class SkriptProzess(SkriptArt art, Einstellungen einstellungen) : ISkriptProzess
{
    const int StderrZeilenFuerAbsturz = 30;

    readonly object _sperre = new();
    readonly SemaphoreSlim _schreiben = new(1, 1);
    readonly Queue<string> _letzteFehler = new();
    Process? _prozess;
    Task? _ende;
    volatile bool _stoppAngefordert;
    ProzessZustand _zustand = ProzessZustand.Gestoppt;
    int? _pid;

    public SkriptArt Art => art;
    public ProzessZustand Zustand { get { lock (_sperre) return _zustand; } }
    public int? Pid { get { lock (_sperre) return _pid; } }

    public event EventHandler<Ereignis>? EreignisEmpfangen;

    public Task StartenAsync(IReadOnlyList<string> argumente, CancellationToken ct = default)
    {
        ct.ThrowIfCancellationRequested();
        lock (_sperre)
        {
            if (_zustand is ProzessZustand.Startet or ProzessZustand.Laeuft)
                throw new InvalidOperationException("Das Skript läuft bereits.");
            _zustand = ProzessZustand.Startet;
            _pid = null;
            _stoppAngefordert = false;
            _letzteFehler.Clear();
        }
        Melden(new ProzessEreignis(ProzessZustand.Startet, null, null));

        var skript = Path.Combine(einstellungen.SkriptOrdner, art == SkriptArt.Einlesen ? "einlesen.py" : "umwandeln.py");
        var psi = new ProcessStartInfo(einstellungen.PythonPfad)
        {
            WorkingDirectory = Arbeitsverzeichnis(),
            RedirectStandardInput = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            UseShellExecute = false,
            CreateNoWindow = true,
            StandardInputEncoding = new UTF8Encoding(false),
            StandardOutputEncoding = new UTF8Encoding(false),
            StandardErrorEncoding = new UTF8Encoding(false),
        };
        psi.ArgumentList.Add("-I");
        psi.ArgumentList.Add(skript);
        foreach (var a in art == SkriptArt.Einlesen ? einstellungen.EinlesenArgumente : einstellungen.UmwandelnArgumente)
            psi.ArgumentList.Add(a);
        if (art == SkriptArt.Einlesen)
        {
            psi.ArgumentList.Add("--nummern");
            psi.ArgumentList.Add(einstellungen.Nummern);
        }
        foreach (var a in argumente) psi.ArgumentList.Add(a);
        foreach (var (k, v) in einstellungen.PythonUmgebung()) psi.Environment[k] = v;

        var p = new Process { StartInfo = psi, EnableRaisingEvents = true };
        try
        {
            p.Start();
        }
        catch (Exception ex) when (ex is Win32Exception or InvalidOperationException or IOException)
        {
            p.Dispose();
            lock (_sperre) _zustand = ProzessZustand.Abgestuerzt;
            Melden(new ProzessEreignis(ProzessZustand.Abgestuerzt, null, $"Start fehlgeschlagen: {ex.Message}"));
            throw;
        }

        lock (_sperre)
        {
            _prozess = p;
            _pid = p.Id;
            _zustand = ProzessZustand.Laeuft;
        }
        Melden(new ProzessEreignis(ProzessZustand.Laeuft, null, null));

        var aus = Task.Run(() => KanalLesen(p.StandardOutput, false));
        var fehler = Task.Run(() => KanalLesen(p.StandardError, true));
        var lesen = Task.WhenAll(aus, fehler);
        _ende = Task.Run(() => AufEndeWarten(p, lesen));
        return Task.CompletedTask;
    }

    string Arbeitsverzeichnis()
    {
        var ordner = einstellungen.SkriptOrdner;
        try
        {
            var oben = Directory.GetParent(ordner.TrimEnd('\\', '/')); // Repo- bzw. Programmordner über skripte\
            if (oben is { Exists: true }) return oben.FullName;
        }
        catch (Exception) { /* Rückfall */ }
        return ordner;
    }

    void KanalLesen(StreamReader leser, bool fehlerkanal)
    {
        try
        {
            string? zeile;
            while ((zeile = leser.ReadLine()) is not null)
            {
                if (fehlerkanal)
                {
                    lock (_sperre)
                    {
                        _letzteFehler.Enqueue(zeile);
                        while (_letzteFehler.Count > StderrZeilenFuerAbsturz) _letzteFehler.Dequeue();
                    }
                    Melden(new LogZeile(zeile, true));
                }
                else
                {
                    Melden(EreignisParser.Parse(zeile));
                }
            }
        }
        catch (Exception) { /* Pipe geschlossen */ }
    }

    async Task AufEndeWarten(Process p, Task lesen)
    {
        await p.WaitForExitAsync().ConfigureAwait(false);
        await lesen.ConfigureAwait(false);
        var code = p.ExitCode;
        ProzessEreignis e;
        lock (_sperre)
        {
            if (code == 0 || _stoppAngefordert)
            {
                _zustand = ProzessZustand.Beendet;
                e = new ProzessEreignis(ProzessZustand.Beendet, code, null);
            }
            else
            {
                _zustand = ProzessZustand.Abgestuerzt;
                var text = $"Exit-Code {code}";
                if (_letzteFehler.Count > 0) text += Environment.NewLine + string.Join(Environment.NewLine, _letzteFehler);
                e = new ProzessEreignis(ProzessZustand.Abgestuerzt, code, text);
            }
        }
        Melden(e);
    }

    void Melden(Ereignis e)
    {
        var h = EreignisEmpfangen;
        if (h is null) return;
        try { h(this, e with { Skript = art }); }
        catch (Exception) { /* Fehler eines Abonnenten darf das Lesen nicht beenden */ }
    }

    public Task AntwortenAsync(string frageId, string wert, CancellationToken ct = default) =>
        ZeileSchreibenAsync(NachrichtSerialisierer.Antwort(frageId, wert), ct);

    public Task EinstellenAsync(string name, string wert, CancellationToken ct = default) =>
        ZeileSchreibenAsync(NachrichtSerialisierer.Einstellung(name, wert), ct);

    async Task ZeileSchreibenAsync(string zeile, CancellationToken ct)
    {
        Process? p;
        lock (_sperre) p = _prozess;
        if (p is null || Zustand != ProzessZustand.Laeuft)
            throw new InvalidOperationException("Das Skript läuft nicht.");
        await _schreiben.WaitAsync(ct).ConfigureAwait(false);
        try
        {
            await p.StandardInput.WriteAsync((zeile + "\n").AsMemory(), ct).ConfigureAwait(false);
            await p.StandardInput.FlushAsync(ct).ConfigureAwait(false);
        }
        finally { _schreiben.Release(); }
    }

    public async Task StoppenAsync(TimeSpan warten, CancellationToken ct = default)
    {
        Process? p;
        Task? ende;
        lock (_sperre)
        {
            p = _prozess;
            ende = _ende;
            if (p is null || _zustand is not (ProzessZustand.Startet or ProzessZustand.Laeuft)) return;
            _stoppAngefordert = true;
        }
        try { await ZeileSchreibenAsync(NachrichtSerialisierer.Stopp(), ct).ConfigureAwait(false); }
        catch (Exception ex) when (ex is IOException or InvalidOperationException or ObjectDisposedException) { }

        if (ende is not null)
        {
            try { await ende.WaitAsync(warten, ct).ConfigureAwait(false); return; }
            catch (TimeoutException) { }
        }
        try { p.Kill(entireProcessTree: true); }
        catch (Exception ex) when (ex is InvalidOperationException or Win32Exception) { /* schon beendet */ }
        if (ende is not null)
            try { await ende.WaitAsync(TimeSpan.FromSeconds(5), ct).ConfigureAwait(false); }
            catch (TimeoutException) { }
    }

    public async ValueTask DisposeAsync()
    {
        try { await StoppenAsync(TimeSpan.FromSeconds(2)).ConfigureAwait(false); }
        catch (Exception) { }
        Process? p;
        lock (_sperre) { p = _prozess; _prozess = null; }
        p?.Dispose();
    }
}
