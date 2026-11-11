using DiscDigitalisierung.Kern;

namespace DiscDigitalisierung.App.Attrappen;

/// <summary>Zwei Netzwerk-Ziele: Videos erreichbar, Musik nicht.</summary>
public sealed class DemoNetzwerk : INetzwerkMonitor
{
    readonly System.Threading.Timer _timer;
    IReadOnlyList<ZielStatus> _ziele = [];

    public DemoNetzwerk()
    {
        _timer = new System.Threading.Timer(_ => Erzeugen(), null, Timeout.Infinite, Timeout.Infinite);
        Erzeugen(false);
    }

    public IReadOnlyList<ZielStatus> Ziele => _ziele;
    public event EventHandler? Geaendert;

    void Erzeugen(bool melden = true)
    {
        var jetzt = DateTimeOffset.Now;
        _ziele =
        [
            new("Videos (DVD_ZIEL)", @"\\NAS\medien\dvd", true, 3_200_000_000_000L, 18_000_000_000_000L, TimeSpan.FromMilliseconds(14), jetzt, null),
            new("Musik (MUSIK_ZIEL)", @"\\NAS\medien\musik", false, null, null, null, jetzt, "Netzwerkpfad nicht gefunden (Demo)"),
        ];
        if (melden) Geaendert?.Invoke(this, EventArgs.Empty);
    }

    public Task PruefenAsync(CancellationToken ct = default)
    {
        Erzeugen();
        return Task.CompletedTask;
    }

    public void Starten(TimeSpan intervall) => _timer.Change(intervall, intervall);
    public void Dispose() => _timer.Dispose();
}
