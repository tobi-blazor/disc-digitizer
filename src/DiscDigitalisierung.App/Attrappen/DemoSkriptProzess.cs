using System.Collections.Concurrent;
using DiscDigitalisierung.Kern;

namespace DiscDigitalisierung.App.Attrappen;

public sealed class DemoSkriptProzessFabrik : ISkriptProzessFabrik
{
    public ISkriptProzess Erzeugen(SkriptArt art) => new DemoSkriptProzess(art);
}

/// <summary>Simuliert einen Skript-Ablauf mit allen Ereignisarten. Ereignisse kommen bewusst aus einem
/// Hintergrund-Thread, damit das Marshalling der Oberfläche mitgetestet wird.</summary>
public sealed class DemoSkriptProzess : ISkriptProzess
{
    readonly ConcurrentDictionary<string, TaskCompletionSource<string>> _fragen = new();
    CancellationTokenSource? _cts;
    Task? _lauf;
    int _frageNr;
    volatile bool _absturz;

    public DemoSkriptProzess(SkriptArt art) => Art = art;

    public SkriptArt Art { get; }
    public ProzessZustand Zustand { get; private set; } = ProzessZustand.Gestoppt;
    public int? Pid { get; private set; }
    public event EventHandler<Ereignis>? EreignisEmpfangen;

    void Senden(Ereignis e) => EreignisEmpfangen?.Invoke(this, e with { Skript = Art, Zeit = DateTimeOffset.Now });
    void Log(string text, bool fehler = false) => Senden(new LogZeile(text, fehler));

    public Task StartenAsync(IReadOnlyList<string> argumente, CancellationToken ct = default)
    {
        if (Zustand is ProzessZustand.Laeuft or ProzessZustand.Startet)
            throw new InvalidOperationException("Läuft schon.");
        Pid = Random.Shared.Next(4000, 9999);
        Zustand = ProzessZustand.Laeuft;
        _absturz = false;
        _cts = new CancellationTokenSource();
        var token = _cts.Token;
        Senden(new ProzessEreignis(ProzessZustand.Laeuft, null, null));
        Senden(new StartEreignis(Pid.Value, argumente));
        _lauf = Task.Run(async () =>
        {
            try
            {
                if (Art == SkriptArt.Einlesen) await EinlesenAsync(token);
                else await UmwandelnAsync(token);
            }
            catch (OperationCanceledException) { }
        });
        return Task.CompletedTask;
    }

    public async Task StoppenAsync(TimeSpan warten, CancellationToken ct = default)
    {
        if (_cts == null || Zustand is not (ProzessZustand.Laeuft or ProzessZustand.Startet)) return;
        Log("stopp empfangen, beende sauber …");
        _cts.Cancel();
        if (_lauf != null) await Task.WhenAny(_lauf, Task.Delay(warten, ct));
        if (_absturz) return;
        Senden(new EndeEreignis(0));
        Zustand = ProzessZustand.Beendet;
        Senden(new ProzessEreignis(ProzessZustand.Beendet, 0, null));
    }

    /// <summary>Nur für die Demo: lässt das Skript abstürzen (Exit-Code 1, Traceback auf stderr).</summary>
    public void AbsturzSimulieren()
    {
        if (Zustand != ProzessZustand.Laeuft) return;
        _absturz = true;
        _cts?.Cancel();
        Log("Traceback (most recent call last):", true);
        Log("  File \"umwandeln.py\", line 812, in handbrake", true);
        Log("PermissionError: [WinError 5] Zugriff verweigert (Demo-Absturz)", true);
        Zustand = ProzessZustand.Abgestuerzt;
        Senden(new ProzessEreignis(ProzessZustand.Abgestuerzt, 1, "PermissionError: [WinError 5] Zugriff verweigert (Demo-Absturz)"));
    }

    public Task AntwortenAsync(string frageId, string wert, CancellationToken ct = default)
    {
        if (_fragen.TryGetValue(frageId, out var tcs)) tcs.TrySetResult(wert);
        else Senden(new MeldungEreignis(MeldungsStufe.Warnung, $"Antwort auf unbekannte oder erledigte Frage {frageId} ignoriert", null));
        return Task.CompletedTask;
    }

    public Task EinstellenAsync(string name, string wert, CancellationToken ct = default)
    {
        Senden(new EinstellungEreignis(name, wert)); // Demo: sofort bestätigen
        return Task.CompletedTask;
    }

    public ValueTask DisposeAsync()
    {
        _cts?.Cancel();
        return ValueTask.CompletedTask;
    }

    async Task<string> FrageAsync(string art, string text, Antwortoption[] optionen, bool freitext, string? vorgabe,
        string disc, CancellationToken ct)
    {
        var id = $"q{Interlocked.Increment(ref _frageNr)}";
        var tcs = new TaskCompletionSource<string>(TaskCreationOptions.RunContinuationsAsynchronously);
        _fragen[id] = tcs;
        Senden(new FrageEreignis(id, art, text, optionen, freitext, vorgabe, disc));
        using var reg = ct.Register(() => tcs.TrySetCanceled());
        var wert = await tcs.Task;
        _fragen.TryRemove(id, out _);
        Senden(new FrageErledigtEreignis(id, wert, "programm"));
        return wert;
    }

    static readonly string[] Namen =
    [
        "Kinderserie Folge 6", "Wissenssendung 2004", "Urlaub Italien 2009", "Krimi – Das Rätsel",
        "Hörspiel-Box", "Hochzeit Anna und Ben", "Dokumentation Nordsee", "Zeichentrick-Sammlung",
    ];

    async Task EinlesenAsync(CancellationToken ct)
    {
        Log("Beobachte E: (alle 3 s), Beenden mit Strg+C");
        var nr = 72;
        for (var i = 0; ; i++, nr++)
        {
            Senden(new LaufwerkEreignis("leer", null, null, 0));
            await Task.Delay(i == 0 ? 1500 : 4000, ct);
            var disc = $"D{nr:0000}";
            var name = Namen[i % Namen.Length];
            var label = name.ToUpperInvariant().Replace(' ', '_');
            Senden(new LaufwerkEreignis("disc", label, "DVD+R", 0));
            var beschriftung = name;
            if (i % 4 == 2)
            {
                var wert = await FrageAsync("beschriftung", $"Beschriftung für {disc}? (Label: {label})", [], true, label, disc, ct);
                if (wert != "s") beschriftung = wert;
            }
            Senden(new DiscEreignis(disc, beschriftung, label, "dvd-video", 1234567890 + i, "einlesen"));
            const int titel = 4;
            Senden(DemoMetadaten(disc, beschriftung, label, titel));
            // Lage wie auf einer echten Rekorder-DVD: VOBs ab Sektor 16384, Dateisystem 2 053 232 Sektoren, Rohling 4,7 GB
            const long start = 16384, sektoren = 898_437, belegt = 2_053_232, max = 2_295_104;
            Senden(new LesekarteEreignis(disc, belegt, max, [new(start, start + sektoren, "geplant")], []));
            var kratzer = i % 4 == 0;
            long kVon = start + sektoren * 48 / 100, kBis = kVon + 6000;   // Kratzer bei 48 %
            long fehler = 0, gelesen = start, demoByte = 0;
            string? frageId = null;
            var abbruch = false;
            for (var schritt = 1; schritt <= 40 && !abbruch; schritt++)
            {
                await Task.Delay(400, ct);
                var bis = start + sektoren * schritt / 40;
                var bereiche = new List<Lesebereich>();
                if (kratzer && gelesen < kBis && bis > kVon)
                {   // schneller Durchgang: Stelle sofort übersprungen, wird später nachgelesen
                    bereiche.Add(new(gelesen, kVon, "ok"));
                    bereiche.Add(new(kVon, kBis, "ausstehend"));
                    bereiche.Add(new(kBis, bis, "ok"));
                    fehler = kBis - kVon;
                    Senden(new MeldungEreignis(MeldungsStufe.Hinweis, "Fehlerstelle bei 48 % übersprungen, wird am Ende nachgelesen.", disc));
                }
                else bereiche.Add(new(gelesen, bis, "ok"));
                gelesen = bis;
                Senden(new LesekarteEreignis(disc, belegt, max, [], bereiche));
                var anteil = schritt / 40.0;
                // Demo: Byte-Angaben auf 1/12 verkleinert, damit die Geschwindigkeit realistisch wirkt (ca. 9–11 MB/s,
                // am Kratzer Einbruch); die Disc-Grafik zeigt trotzdem die volle Disc
                demoByte += (long)((kratzer && bis > kVon && gelesen - (bis - start) / 40 <= kBis ? 1.2 : 9.5 + Random.Shared.NextDouble() * 1.5) * 0.4e6);
                Senden(new FortschrittEreignis(disc, "einlesen", $"t{(schritt - 1) / 10 + 1:00}", (schritt - 1) / 10 + 1, titel,
                    demoByte, sektoren * 2048 / 12, anteil * 100, fehler, null, (1 - anteil) * 240, "kopieren"));
            }
            if (kratzer)
            {   // Nachlesen: Ränder kommen zurück, die Mitte bleibt teils unlesbar; Frage nach kurzer Zeit, ohne zu warten
                Senden(new MeldungEreignis(MeldungsStufe.Hinweis, "Lese 6000 Sektoren an 1 Stelle(n) nach.", disc));
                var gerettet = 0L;
                for (var k = 0; k < 12 && !abbruch; k++)
                {
                    await Task.Delay(700, ct);
                    var stueck = new List<Lesebereich>();
                    if (k < 4) { stueck.Add(new(kVon + k * 600, kVon + (k + 1) * 600, "ok")); stueck.Add(new(kBis - (k + 1) * 400, kBis - k * 400, "ok")); gerettet += 1000; }
                    else { var x = kVon + 2400 + (k - 4) * 50; stueck.Add(new(x, x + 30, "ok")); stueck.Add(new(x + 30, x + 50, "fehler")); gerettet += 30; }
                    Senden(new LesekarteEreignis(disc, belegt, max, [], stueck));
                    fehler = 6000 - gerettet;
                    Senden(new FortschrittEreignis(disc, "einlesen", "t02", 2, titel, demoByte, sektoren * 2048 / 12, 100, fehler, 0, null, "kopieren"));
                    if (k == 3)
                    {
                        frageId = $"q{Interlocked.Increment(ref _frageNr)}";
                        var tcs = new TaskCompletionSource<string>(TaskCreationOptions.RunContinuationsAsynchronously);
                        _fragen[frageId] = tcs;
                        var id = frageId;
                        _ = tcs.Task.ContinueWith(w =>
                        {
                            if (w.IsCompletedSuccessfully)
                            {
                                if (w.Result == "a") abbruch = true;
                                Senden(new FrageErledigtEreignis(id, w.Result, "programm"));
                            }
                        });
                        Senden(new FrageEreignis(frageId, "lesefehler",
                            $"Lesefehler: 1 Stelle(n), {fehler} Sektoren noch offen ({fehler * 2048 / 1e6:0.0} MB ≈ {fehler * 2048 / 600_000:0} s Video). Nachlesen läuft seit 2:00 min, bisher {gerettet} Sektoren gerettet.",
                            [new("w", "weiter nachlesen", "Liest die Stelle weiter Sektor für Sektor. Kein Datenverlust, kostet nur Zeit."),
                             new("ü", "Lücke lassen", $"Der Rest dieser Stelle bleibt leer ({fehler * 2048 / 1e6:0.0} MB): kurzer Bildfehler oder Aussetzer."),
                             new("i", "alle Lücken lassen", "Wie „Lücke lassen“, aber für alle weiteren Stellen dieser Disc. Am schnellsten."),
                             new("a", "abbrechen und reinigen", "Einlesen endet, Disc nach 3-problem. Reinigen und neu einlegen, gleiche Nummer.")],
                            false, "w", disc)
                        {
                            Details = "Alles andere ist schon gelesen. Restzeit fürs Nachlesen bis zu ca. 8:00 min. Ohne Antwort liest das "
                                      + "Programm weiter, solange es noch etwas rettet. Der Disc schadet das nicht.\nEmpfehlung: Das Nachlesen "
                                      + "rettet noch Sektoren. Weiterlaufen lassen lohnt sich.",
                            Empfehlung = "w",
                        });
                    }
                }
                Senden(new LesekarteEreignis(disc, belegt, max, [], [new(kVon + 2800, kBis - 1600, "fehler")]));  // Rest unlesbar
                if (frageId != null && _fragen.TryRemove(frageId, out var offen) && offen.TrySetCanceled())
                    Senden(new FrageErledigtEreignis(frageId, "w", "fertig"));
            }
            var problem = i % 4 == 3;
            if (kratzer)
                Senden(new EmpfehlungEreignis(disc, "reinigen", "empfohlen",
                    $"{fehler} Sektoren an 1 Stelle(n) unlesbar ({fehler * 2048 / 1e6:0.0} MB ≈ {fehler * 2048 / 600_000:0} s Video). Reinigen empfohlen "
                    + "(weiches, fusselfreies Tuch, von der Mitte gerade nach außen wischen), trocknen lassen und neu einlegen."));
            Senden(new MeldungEreignis(i % 4 == 1 ? MeldungsStufe.Fehler : MeldungsStufe.Info,
                i % 4 == 1 ? "Auswerfen hat nicht geklappt" : $"{disc}: {titel} Titel kopiert", disc));
            Senden(new DiscEndeEreignis(disc, "einlesen", abbruch || kratzer ? "problem" : problem ? "unlesbar" : "eingelesen",
                abbruch ? "abgebrochen" : kratzer ? $"Lesefehler: {fehler} Sektoren, Reinigen empfohlen" : problem ? "Disc nicht lesbar (Demo)" : null,
                $@"D:\Arbeit\1-roh\{disc}", 75));
            Senden(new LaufwerkEreignis("verarbeitet", label, "DVD+R", 0));
            await Task.Delay(kratzer ? 15000 : 3000, ct);
        }
    }

    static MetadatenEreignis DemoMetadaten(string disc, string beschriftung, string label, int titel)
    {
        var f = new List<MetadatenFeld>
        {
            new("Laufwerk", "laufwerk", @"E:\"), new("Laufwerk", "medium", "DVD+R"), new("Laufwerk", "dateisystem", "UDF"),
            new("Laufwerk", "belegt_byte", "1,84 GB"), new("Laufwerk", "kapazitaet_byte", "4,70 GB"),
            new("Laufwerk", "volume_seriennummer", "CDDB-2F03"),
            new("Disc", "disc", disc), new("Disc", "beschriftung", beschriftung), new("Disc", "label", label),
            new("Disc", "typ", "dvd-video"), new("Disc", "rekorder", "Panasonic DMR-EHA10"), new("Disc", "quelle", "dvd_vr"),
        };
        for (var t = 1; t <= titel; t++)
        {
            var g = $"Titel {t}";
            f.Add(new(g, "dvd_titel", (t + 2).ToString()));
            f.Add(new(g, "dauer_s", "24:48 min"));
            f.Add(new(g, "aufnahme.name", $"{beschriftung} – Folge {t}"));
            f.Add(new(g, "aufnahme.sender", "KiKA"));
            f.Add(new(g, "aufnahme.sendezeit", $"2009-05-{26 + t}T18:15"));
            f.Add(new(g, "groesse_byte", "0,46 GB"));
        }
        f.Add(new("Aussortiert", "DVD-Titel 1", "grund: leer, dauer_s: 0:02 min"));
        return new MetadatenEreignis(disc, f);
    }

    async Task UmwandelnAsync(CancellationToken ct)
    {
        Log("Umwandeln gestartet, Zeitfenster: keines");
        var warteschlange = new List<string> { "D0065", "D0066", "D0067", "D0068", "D0069", "D0070" };
        var nr = 71;
        var runde = 0;
        while (true)
        {
            if (warteschlange.Count == 0)
            {
                Senden(new WarteschlangeEreignis([], null, null));
                await Task.Delay(5000, ct);
                warteschlange.Add($"D{nr++:0000}");
            }
            var disc = warteschlange[0];
            warteschlange.RemoveAt(0);
            Senden(new WarteschlangeEreignis(warteschlange.ToArray(), disc, null));
            Senden(new DiscEreignis(disc, Namen[(runde + 3) % Namen.Length], null, "dvd-video", null, "umwandeln"));
            var schritte = new (string Name, int Teile, int Dauer)[] { ("handbrake", 14, 600), ("comskip", 4, 500), ("pruefen", 3, 400) };
            var gesamtTeile = schritte.Sum(x => x.Teile);
            var fertig = 0;
            foreach (var (name, teile, dauer) in schritte)
            {
                for (var s = 1; s <= teile; s++)
                {
                    await Task.Delay(dauer, ct);
                    fertig++;
                    var anteil = fertig / (double)gesamtTeile;
                    Senden(new FortschrittEreignis(disc, "umwandeln", name == "handbrake" ? "t01" : null, name == "handbrake" ? 1 : null,
                        name == "handbrake" ? 1 : null, null, null, anteil * 100, null, null, (1 - anteil) * 1500, name));
                }
                if (name == "comskip" && runde % 2 == 1)
                    Senden(new MeldungEreignis(MeldungsStufe.Warnung, $"Comskip: keine Werbeblöcke erkannt ({disc})", disc));
            }
            var pruefen = runde % 5 == 4;
            Senden(new DiscEndeEreignis(disc, "umwandeln", pruefen ? "pruefen" : "umgewandelt",
                pruefen ? "Laufzeit weicht ab (Demo)" : null, $@"D:\Arbeit\2-fertig\{disc}", 38));
            runde++;
            if (runde % 3 == 0) warteschlange.Add($"D{nr++:0000}");
        }
    }
}
