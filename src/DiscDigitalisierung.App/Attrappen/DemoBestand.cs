using DiscDigitalisierung.Kern;

namespace DiscDigitalisierung.App.Attrappen;

/// <summary>Bestand mit rund 30 erfundenen Discs; ändert sich alle paar Sekunden ein wenig.</summary>
public sealed class DemoBestand : IBestand
{
    readonly List<DiscEintrag> _discs = [];
    readonly System.Threading.Timer _timer;
    readonly Random _zufall = new(7);

    public DemoBestand()
    {
        string[] namen = ["Kinderserie", "Wissen Sammlung", "Urlaub Italien", "Krimi", "Hörspiele", "Hochzeit", "Nordsee", "Zeichentrick", "Kinderlieder", "Familienfeier"];
        string[] status = ["einsortiert", "einsortiert", "einsortiert", "umgewandelt", "eingelesen", "pruefen", "problem", "unlesbar", "leer", "uebersprungen"];
        for (var i = 0; i < 30; i++)
        {
            var s = i < 14 ? "einsortiert" : status[i % status.Length];
            var typ = i % 7 == 6 ? "audio" : i % 9 == 8 ? "daten" : "dvd-video";
            _discs.Add(new DiscEintrag($"D{40 + i:0000}", $"{namen[i % namen.Length]} {i / 10 + 1}", typ,
                typ == "audio" ? 12 : 2 + i % 5, Math.Round(0.5 + (i % 6) * 0.4, 1), s,
                DateTimeOffset.Now.AddHours(-30 + i), s == "pruefen" ? "Laufzeit weicht ab" : "",
                s is "eingelesen" ? "1-roh" : s is "umgewandelt" or "pruefen" ? "2-fertig" : s is "problem" or "unlesbar" ? "3-problem" : null,
                null, s is "problem" ? "Lesefehler in Titel 2" : s == "unlesbar" ? "Disc nicht lesbar" : null,
                s is "problem" ? 128 : s == "unlesbar" ? 9000 : i % 11 == 0 ? 8 : 0, 1_500_000_000L + i * 100_000_000L));
        }
        Stand = DateTimeOffset.Now;
        _timer = new System.Threading.Timer(_ => Weiter(), null, Timeout.Infinite, Timeout.Infinite);
    }

    public IReadOnlyList<DiscEintrag> Discs { get { lock (_discs) return _discs.ToArray(); } }
    public DateTimeOffset? Stand { get; private set; }
    public bool UmwandelnLaeuft => false;
    public long? ArbeitFreiByte => 412_000_000_000L;
    public event EventHandler? Geaendert;

    public Task AktualisierenAsync(CancellationToken ct = default)
    {
        Stand = DateTimeOffset.Now;
        return Task.CompletedTask;
    }

    public void Beobachten() => _timer.Change(7000, 7000);

    void Weiter()
    {
        lock (_discs)
        {
            var i = _zufall.Next(14, _discs.Count);
            var d = _discs[i];
            var neu = d.Status switch { "eingelesen" => "umgewandelt", "umgewandelt" => "einsortiert", _ => d.Status };
            _discs[i] = d with { Status = neu, Geaendert = DateTimeOffset.Now };
        }
        Stand = DateTimeOffset.Now;
        Geaendert?.Invoke(this, EventArgs.Empty);
    }

    public void Dispose() => _timer.Dispose();
}
