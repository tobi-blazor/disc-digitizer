using DiscDigitalisierung.Kern;
using DiscDigitalisierung.Prozesse;

namespace DiscDigitalisierung.Prozesse.Tests;

/// <summary>Echtes einlesen.py (skripte/) mit --quelle, ohne Laufwerk. Nur aktiv mit
/// DD_INTEGRATION_QUELLE=&lt;Testordner mit Videodatei&gt; und DD_INTEGRATION_ARBEIT=&lt;leerer Arbeitsordner&gt;.</summary>
public sealed class IntegrationTests
{
    [Fact]
    public async Task EchtesEinlesenMitQuelle()
    {
        var quelle = Environment.GetEnvironmentVariable("DD_INTEGRATION_QUELLE");
        var arbeit = Environment.GetEnvironmentVariable("DD_INTEGRATION_ARBEIT");
        if (string.IsNullOrEmpty(quelle) || string.IsNullOrEmpty(arbeit))
            return; // nicht konfiguriert: übersprungen

        var e = new Einstellungen { SkriptOrdner = Einstellungen.StandardSkriptOrdner(), ArbeitsOrdner = arbeit, Laufwerk = "Q:" };
        await using var p = new SkriptProzessFabrik(e).Erzeugen(SkriptArt.Einlesen);
        var liste = new List<Ereignis>();
        var fertig = new TaskCompletionSource();
        p.EreignisEmpfangen += async (_, ev) =>
        {
            lock (liste) liste.Add(ev);
            if (ev is FrageEreignis f)
                await p.AntwortenAsync(f.Id, f.Art == "beschriftung" ? "Integrationstest Ärger" : f.Vorgabe ?? "");
            if (ev is ProzessEreignis { Zustand: ProzessZustand.Beendet or ProzessZustand.Abgestuerzt })
                fertig.TrySetResult();
        };
        await p.StartenAsync(["--quelle", quelle, "--ohne-auswerfen"]);
        await fertig.Task.WaitAsync(TimeSpan.FromMinutes(2));

        List<Ereignis> l;
        lock (liste) l = [.. liste];
        File.WriteAllLines(Path.Combine(arbeit, "integration.txt"), l.Select(x => x.ToString()));
        Assert.Contains(l, x => x is StartEreignis);
        Assert.Contains(l, x => x is FrageEreignis { Art: "beschriftung" });
        Assert.Contains(l, x => x is FrageErledigtEreignis);
        Assert.Contains(l, x => x is FortschrittEreignis);
        var ende = Assert.Single(l.OfType<DiscEndeEreignis>());
        Assert.Equal("eingelesen", ende.Status);
        Assert.Contains(l, x => x is DiscEreignis { Beschriftung: "Integrationstest Ärger", Typ: "daten" });
        Assert.Contains(l, x => x is EndeEreignis { Code: 0 });
        Assert.Equal(ProzessZustand.Beendet, p.Zustand);
    }

    /// <summary>Leere Quelle, Start mit "fragen", sofort auf "immer" umschalten: keine Frage, Nummer vergeben.
    /// DD_INTEGRATION_LEER = leerer Ordner.</summary>
    [Fact]
    public async Task NummernZurLaufzeitUmschalten()
    {
        var leer = Environment.GetEnvironmentVariable("DD_INTEGRATION_LEER");
        var arbeit = Environment.GetEnvironmentVariable("DD_INTEGRATION_ARBEIT");
        if (string.IsNullOrEmpty(leer) || string.IsNullOrEmpty(arbeit))
            return;
        arbeit = Path.Combine(arbeit, "nummern");
        if (Directory.Exists(arbeit)) Directory.Delete(arbeit, true);

        var e = new Einstellungen { ArbeitsOrdner = arbeit, Laufwerk = "Q:", Nummern = NummernModus.Fragen };
        await using var p = new SkriptProzessFabrik(e).Erzeugen(SkriptArt.Einlesen);
        var liste = new List<Ereignis>();
        var fertig = new TaskCompletionSource();
        p.EreignisEmpfangen += async (_, ev) =>
        {
            lock (liste) liste.Add(ev);
            if (ev is StartEreignis) await p.EinstellenAsync("nummern", NummernModus.Immer);
            // Fragen nicht beantworten: kam die Frage vor dem Umschalten, muss das Umschalten sie erledigen
            if (ev is ProzessEreignis { Zustand: ProzessZustand.Beendet or ProzessZustand.Abgestuerzt })
                fertig.TrySetResult();
        };
        await p.StartenAsync(["--quelle", leer]);
        await fertig.Task.WaitAsync(TimeSpan.FromMinutes(1));

        List<Ereignis> l;
        lock (liste) l = [.. liste];
        var werte = l.OfType<EinstellungEreignis>().Select(x => x.Wert).ToList();
        Assert.Equal([NummernModus.Fragen, NummernModus.Immer], werte);
        // Mit --quelle geht es so schnell, dass die Frage meist schon offen ist: dann erledigt sie das Umschalten
        foreach (var f in l.OfType<FrageEreignis>())
            Assert.Contains(l, x => x is FrageErledigtEreignis { Durch: "einstellung", Wert: "j" } fe && fe.Id == f.Id);
        var ende = Assert.Single(l.OfType<DiscEndeEreignis>());
        Assert.Equal("leer", ende.Status);
        Assert.Equal("D0001", ende.Disc);
        Assert.Contains("D0001;leer", File.ReadAllText(Path.Combine(arbeit, "fortschritt.csv")));
    }
}
