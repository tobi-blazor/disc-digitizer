using System.Collections.Concurrent;
using DiscDigitalisierung.Kern;
using DiscDigitalisierung.Prozesse;

namespace DiscDigitalisierung.Prozesse.Tests;

public class SkriptProzessTests
{
    static Einstellungen Einst() => new()
    {
        PythonPfad = "python",
        SkriptOrdner = Path.Combine(AppContext.BaseDirectory, "Fake"),
        Laufwerk = "Q:",
        EinlesenArgumente = ["--a"],
    };

    sealed class Sammler
    {
        public ConcurrentQueue<Ereignis> Alle { get; } = new();
        public Sammler(ISkriptProzess p) => p.EreignisEmpfangen += (_, e) => Alle.Enqueue(e);

        public async Task<T> WartenAuf<T>(Func<T, bool>? bed = null, int sekunden = 20) where T : Ereignis
        {
            var ende = DateTime.UtcNow.AddSeconds(sekunden);
            while (DateTime.UtcNow < ende)
            {
                var treffer = Alle.OfType<T>().FirstOrDefault(x => bed?.Invoke(x) ?? true);
                if (treffer is not null) return treffer;
                await Task.Delay(25);
            }
            throw new TimeoutException($"{typeof(T).Name} nicht empfangen. Bisher: " +
                string.Join(" | ", Alle.Select(x => x.ToString())));
        }
    }

    [Fact]
    public async Task EndeZuEnde_Frage_Antwort_Stopp()
    {
        await using var p = new SkriptProzessFabrik(Einst()).Erzeugen(SkriptArt.Einlesen);
        var s = new Sammler(p);
        Assert.Equal(ProzessZustand.Gestoppt, p.Zustand);

        await p.StartenAsync(["--b"]);
        Assert.NotNull(p.Pid);

        var start = await s.WartenAuf<StartEreignis>();
        Assert.Equal(["--a", "--nummern", "fragen", "--b"], start.Argumente); // --nummern immer bei einlesen.py
        Assert.Equal(p.Pid, start.Pid);
        Assert.Equal(SkriptArt.Einlesen, start.Skript);

        var log = await s.WartenAuf<LogZeile>(l => l.Text.StartsWith("Hallo"));
        Assert.Equal("Hallo Ümlaut ✓", log.Text);
        Assert.False(log.Fehlerkanal);

        var meld = await s.WartenAuf<MeldungEreignis>();
        Assert.Equal("laufwerk=Q: modus=1", meld.Text);

        var frage = await s.WartenAuf<FrageEreignis>();
        Assert.Equal("Beschriftung für Ä?", frage.Text);

        await p.AntwortenAsync(frage.Id, "Größe ü");
        var erledigt = await s.WartenAuf<FrageErledigtEreignis>();
        Assert.Equal("Größe ü", erledigt.Wert);
        Assert.Equal("programm", erledigt.Durch);

        await p.StoppenAsync(TimeSpan.FromSeconds(10));
        var ende = await s.WartenAuf<EndeEreignis>();
        Assert.Equal(0, ende.Code);
        var pe = await s.WartenAuf<ProzessEreignis>(x => x.Zustand == ProzessZustand.Beendet);
        Assert.Equal(0, pe.ExitCode);
        Assert.Equal(ProzessZustand.Beendet, p.Zustand);

        var zustaende = s.Alle.OfType<ProzessEreignis>().Select(x => x.Zustand).ToList();
        Assert.Equal([ProzessZustand.Startet, ProzessZustand.Laeuft, ProzessZustand.Beendet], zustaende);
    }

    [Fact]
    public async Task Absturz_Exit3_MitStderr()
    {
        await using var p = new SkriptProzessFabrik(Einst()).Erzeugen(SkriptArt.Umwandeln);
        var s = new Sammler(p);
        await p.StartenAsync([]);

        var pe = await s.WartenAuf<ProzessEreignis>(x => x.Zustand == ProzessZustand.Abgestuerzt);
        Assert.Equal(3, pe.ExitCode);
        Assert.StartsWith("Exit-Code 3", pe.Text);
        Assert.Contains("Fehlerzeile 39", pe.Text);
        Assert.DoesNotContain("Fehlerzeile 9" + Environment.NewLine, pe.Text);
        Assert.Equal(31, pe.Text!.Split(Environment.NewLine).Length); // Kopfzeile + 30 stderr-Zeilen
        Assert.Equal(ProzessZustand.Abgestuerzt, p.Zustand);
        Assert.Equal(SkriptArt.Umwandeln, pe.Skript);

        var fehler = s.Alle.OfType<LogZeile>().Where(l => l.Fehlerkanal).ToList();
        Assert.Equal(40, fehler.Count);
        Assert.Equal("Fehlerzeile 0", fehler[0].Text); // Reihenfolge je Kanal
        Assert.Contains(s.Alle.OfType<LogZeile>(), l => !l.Fehlerkanal && l.Text == "vor dem Absturz");
    }

    [Fact]
    public async Task Stoppen_ErzwingtKill_WennSkriptNichtReagiert()
    {
        await using var p = new SkriptProzessFabrik(Einst()).Erzeugen(SkriptArt.Einlesen);
        var s = new Sammler(p);
        await p.StartenAsync(["ignoriere_stopp"]);
        await s.WartenAuf<StartEreignis>();

        await p.StoppenAsync(TimeSpan.FromMilliseconds(500));
        var pe = await s.WartenAuf<ProzessEreignis>(x => x.Zustand == ProzessZustand.Beendet);
        Assert.NotEqual(0, pe.ExitCode);
        Assert.Equal(ProzessZustand.Beendet, p.Zustand);
    }

    [Fact]
    public async Task ZweimalStarten_Wirft()
    {
        await using var p = new SkriptProzessFabrik(Einst()).Erzeugen(SkriptArt.Einlesen);
        await p.StartenAsync([]);
        await Assert.ThrowsAsync<InvalidOperationException>(() => p.StartenAsync([]));
    }

    [Fact]
    public async Task Antworten_OhneLaufendenProzess_Wirft()
    {
        await using var p = new SkriptProzessFabrik(Einst()).Erzeugen(SkriptArt.Einlesen);
        await Assert.ThrowsAsync<InvalidOperationException>(() => p.AntwortenAsync("q1", "j"));
    }

    [Fact]
    public async Task FalscherPython_MeldetAbsturzUndWirft()
    {
        var e = Einst();
        e.PythonPfad = Path.Combine(AppContext.BaseDirectory, "gibt-es-nicht.exe");
        await using var p = new SkriptProzessFabrik(e).Erzeugen(SkriptArt.Einlesen);
        var s = new Sammler(p);
        await Assert.ThrowsAnyAsync<Exception>(() => p.StartenAsync([]));
        Assert.Equal(ProzessZustand.Abgestuerzt, p.Zustand);
        Assert.Contains(s.Alle.OfType<ProzessEreignis>(), x => x.Zustand == ProzessZustand.Abgestuerzt);
    }

    static string[] LiesGeteilt(string pfad)
    {
        using var fs = new FileStream(pfad, FileMode.Open, FileAccess.Read, FileShare.ReadWrite);
        using var r = new StreamReader(fs);
        return r.ReadToEnd().Split('\n', StringSplitOptions.RemoveEmptyEntries);
    }

    [Fact]
    public async Task SitzungsProtokoll_SchreibtGueltigeJsonlZeilen()
    {
        var pfad = Path.Combine(Path.GetTempPath(), "disc-test-" + Guid.NewGuid().ToString("N"), "sitzung.jsonl");
        try
        {
            string[] zeilen;
            await using (var p = new SkriptProzessFabrik(Einst()).Erzeugen(SkriptArt.Einlesen))
            {
                using var prot = new SitzungsProtokoll(pfad);
                prot.Anhaengen(p);
                var s = new Sammler(p);
                await p.StartenAsync([]);
                var frage = await s.WartenAuf<FrageEreignis>();
                await p.AntwortenAsync(frage.Id, "ü");
                await s.WartenAuf<FrageErledigtEreignis>();
                await p.StoppenAsync(TimeSpan.FromSeconds(10));
                await s.WartenAuf<ProzessEreignis>(x => x.Zustand == ProzessZustand.Beendet);
                zeilen = LiesGeteilt(pfad);
            }

            Assert.NotEmpty(zeilen);
            var namen = new List<string>();
            foreach (var z in zeilen)
            {
                using var d = System.Text.Json.JsonDocument.Parse(z);
                namen.Add(d.RootElement.GetProperty("ereignis").GetString()!);
                Assert.Equal(System.Text.Json.JsonValueKind.Object, d.RootElement.GetProperty("daten").ValueKind);
                Assert.True(d.RootElement.GetProperty("daten").TryGetProperty("zeit", out _));
            }
            Assert.Contains("StartEreignis", namen);
            Assert.Contains("FrageEreignis", namen);
            Assert.Contains("LogZeile", namen);
            Assert.Contains("ProzessEreignis", namen);
            Assert.NotEqual(0xEF, File.ReadAllBytes(pfad)[0]);
            Assert.Contains("\"zustand\":\"Beendet\"", string.Join("\n", zeilen));
        }
        finally
        {
            try { Directory.Delete(Path.GetDirectoryName(pfad)!, true); } catch (IOException) { }
        }
    }
}
