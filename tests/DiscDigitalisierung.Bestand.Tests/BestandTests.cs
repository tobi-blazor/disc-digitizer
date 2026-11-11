using System.Text;
using DiscDigitalisierung.Kern;

#pragma warning disable CA1416 // FileStream.Lock: nur Windows genutzt

namespace DiscDigitalisierung.Bestand.Tests;

public sealed class TempOrdner : IDisposable
{
    public string Pfad { get; } = Path.Combine(Path.GetTempPath(), "bestandtest-" + Guid.NewGuid().ToString("N"));
    public TempOrdner() => Directory.CreateDirectory(Pfad);

    public string Unter(params string[] teile) => Path.Combine([Pfad, .. teile]);

    public void Csv(string inhalt) =>
        File.WriteAllText(Unter("fortschritt.csv"), inhalt, new UTF8Encoding(false));

    public string DiscOrdner(string ablage, string name, string? json = null, params (string Datei, int Bytes)[] dateien)
    {
        var o = Unter(ablage, name);
        Directory.CreateDirectory(o);
        if (json != null) File.WriteAllText(Path.Combine(o, "disc.json"), json, new UTF8Encoding(false));
        foreach (var (datei, bytes) in dateien) File.WriteAllBytes(Path.Combine(o, datei), new byte[bytes]);
        return o;
    }

    public void Dispose()
    {
        try { Directory.Delete(Pfad, true); } catch (IOException) { } catch (UnauthorizedAccessException) { }
    }
}

public class BestandTests
{
    const string Kopf = "disc;beschriftung;typ;titel;stunden;status;geaendert;hinweis\n";

    static async Task<IReadOnlyList<DiscEintrag>> Lesen(TempOrdner t)
    {
        using var b = new Bestand(t.Pfad);
        await b.AktualisierenAsync();
        return b.Discs;
    }

    [Fact]
    public async Task Csv_Umlaute_Anfuehrungszeichen_Punktzahlen()
    {
        using var t = new TempOrdner();
        t.Csv(Kopf +
              "D0002;Wissenssendung Käse & Bären;dvd-video;5;2.06;einsortiert;2026-11-05T02:30:31;\n" +
              "D0001;\"Maus; \"\"Test\"\" lauf\";dvd-video;9;4.23;problem;2026-11-05T01:12:52;\"t02: Dauer;\nzweite Zeile\"\n");
        var d = await Lesen(t);
        Assert.Equal(["D0001", "D0002"], d.Select(x => x.Disc));
        Assert.Equal("Maus; \"Test\" lauf", d[0].Beschriftung);
        Assert.Equal("t02: Dauer;\nzweite Zeile", d[0].Hinweis);
        Assert.Equal(4.23, d[0].Stunden);
        Assert.Equal(9, d[0].Titel);
        Assert.Equal("Wissenssendung Käse & Bären", d[1].Beschriftung);
        Assert.Equal(new DateTime(2026, 11, 5, 2, 30, 31), d[1].Geaendert!.Value.LocalDateTime);
        Assert.Null(d[1].Ablage);
    }

    [Fact]
    public async Task Csv_KaputteZeile_FehlendeSpalte_WirdToleriert()
    {
        using var t = new TempOrdner();
        t.Csv(Kopf +
              "D0001;Eins;dvd-video;x;abc;umgewandelt;kein-datum;\n" +
              "D0002;Zwei\n" +
              ";;;;;\n" +
              "\n" +
              "D0003;Drei;audio-cd;12;0.5;einsortiert;2026-01-02T03:04:05;\n");
        var d = await Lesen(t);
        Assert.Equal(["D0001", "D0002", "D0003"], d.Select(x => x.Disc));
        Assert.Equal(0, d[0].Titel);
        Assert.Null(d[0].Geaendert);
        Assert.Equal("Zwei", d[1].Beschriftung);
        Assert.Equal("unbekannt", d[1].Status);
        Assert.Equal(12, d[2].Titel);
    }

    [Fact]
    public async Task Csv_NurTeilweiseSpalten()
    {
        using var t = new TempOrdner();
        t.Csv("disc;status\r\nD0005;eingelesen\r\n");
        var d = await Lesen(t);
        var e = Assert.Single(d);
        Assert.Equal("eingelesen", e.Status);
        Assert.Equal("", e.Beschriftung);
    }

    [Fact]
    public async Task ZusammenfuehrenMitDiscJson_AlleAblagen()
    {
        using var t = new TempOrdner();
        t.Csv(Kopf +
              "D0010;Roh;dvd-video;2;1.5;eingelesen;2026-11-09T01:00:00;\n" +
              "D0011;Fertig;dvd-video;1;0.5;umgewandelt;2026-11-09T02:00:00;\n" +
              "D0012;Problem;dvd-video;3;0.9;problem;2026-11-09T03:00:00;Hinweis\n");
        t.DiscOrdner("1-roh", "D0010", """{"disc":"D0010","status":"eingelesen","lesefehler_sektoren":7,"titel":[{"dauer_s":3600}]}""",
            ("a.vob", 100), ("b.vob", 50));
        t.DiscOrdner("2-fertig", "D0011", """{"disc":"D0011","status":"umgewandelt"}""", ("a.mkv", 10));
        t.DiscOrdner("3-problem", "D0012", """{"disc":"D0012","problem_grund":"Dauer weicht ab","lesefehler_sektoren":0}""");

        var d = await Lesen(t);
        Assert.Equal(3, d.Count);
        Assert.Equal("1-roh", d[0].Ablage);
        Assert.Equal(7, d[0].LesefehlerSektoren);
        Assert.True(d[0].GroesseByte >= 150);
        Assert.Equal(2, d[0].Titel); // CSV hat Vorrang
        Assert.Equal("2-fertig", d[1].Ablage);
        Assert.Null(d[1].ProblemGrund);
        Assert.Equal("3-problem", d[2].Ablage);
        Assert.Equal("Dauer weicht ab", d[2].ProblemGrund);
        Assert.Equal("Hinweis", d[2].Hinweis);
        Assert.Equal(t.Unter("3-problem", "D0012"), d[2].Ordner);
    }

    [Fact]
    public async Task OrdnerOhneCsv_WirdAufgenommen_AusDiscJson()
    {
        using var t = new TempOrdner();
        t.DiscOrdner("2-fertig", "D0020",
            """{"disc":"D0020","beschriftung":"Nur JSON","typ":"dvd-video","status":"umgewandelt","eingelesen":"2026-11-09T03:20:34","titel":[{"dauer_s":1800},{"dauer_s":1800}]}""",
            ("x.mkv", 5));
        t.DiscOrdner("3-problem", "D0021"); // nicht mal ein disc.json
        var d = await Lesen(t);
        Assert.Equal(2, d.Count);
        Assert.Equal("Nur JSON", d[0].Beschriftung);
        Assert.Equal(2, d[0].Titel);
        Assert.Equal(1.0, d[0].Stunden);
        Assert.Equal("umgewandelt", d[0].Status);
        Assert.NotNull(d[0].Geaendert);
        Assert.Equal("unbekannt", d[1].Status);
        Assert.Equal("3-problem", d[1].Ablage);
    }

    [Fact]
    public async Task EinlesenOrdner_GiltAlsEinlesen()
    {
        using var t = new TempOrdner();
        t.DiscOrdner("1-roh", "D0030.einlesen", null, ("a.vob", 1));
        var d = await Lesen(t);
        var e = Assert.Single(d);
        Assert.Equal("D0030", e.Disc);
        Assert.Equal("einlesen", e.Status);
        Assert.Equal("1-roh", e.Ablage);
    }

    [Fact]
    public async Task KaputteDiscJson_WirftNicht()
    {
        using var t = new TempOrdner();
        t.DiscOrdner("1-roh", "D0040", "{ kaputt");
        var d = await Lesen(t);
        Assert.Single(d);
    }

    [Fact]
    public async Task OhneArbeitsordner_LeereListe()
    {
        using var b = new Bestand(Path.Combine(Path.GetTempPath(), "gibt-es-nicht-" + Guid.NewGuid().ToString("N")));
        await b.AktualisierenAsync();
        Assert.Empty(b.Discs);
        Assert.False(b.UmwandelnLaeuft);
        Assert.Null(b.ArbeitFreiByte);
    }

    [Fact]
    public async Task ArbeitFreiByte_Lokal()
    {
        using var t = new TempOrdner();
        using var b = new Bestand(t.Pfad);
        Assert.True(b.ArbeitFreiByte > 0);
        await Task.CompletedTask;
    }

    [Fact]
    public void Sperre_GehaltenOderNicht()
    {
        using var t = new TempOrdner();
        using var b = new Bestand(t.Pfad);
        Assert.False(b.UmwandelnLaeuft); // Datei fehlt
        Assert.False(File.Exists(t.Unter("umwandeln.lock"))); // wurde nicht angelegt

        File.WriteAllBytes(t.Unter("umwandeln.lock"), []);
        Assert.False(b.UmwandelnLaeuft);

        using (var fs = new FileStream(t.Unter("umwandeln.lock"), FileMode.Open, FileAccess.ReadWrite, FileShare.ReadWrite | FileShare.Delete))
        {
            fs.Lock(0, 1);
            Assert.True(b.UmwandelnLaeuft);
            fs.Unlock(0, 1);
        }
        Assert.False(b.UmwandelnLaeuft);
        Assert.True(File.Exists(t.Unter("umwandeln.lock")));
    }

    [Fact]
    public async Task Watcher_MeldetAenderung()
    {
        using var t = new TempOrdner();
        t.Csv(Kopf + "D0001;Eins;dvd-video;1;1.0;eingelesen;2026-11-09T01:00:00;\n");
        using var b = new Bestand(t.Pfad);
        var tcs = new TaskCompletionSource();
        b.Beobachten();
        // erste Abfrage (sofort) abwarten
        for (var i = 0; i < 50 && b.Discs.Count == 0; i++) await Task.Delay(100);
        Assert.Single(b.Discs);

        b.Geaendert += (_, _) => { if (b.Discs.Count == 2) tcs.TrySetResult(); };
        // atomar ersetzen wie Python (os.replace)
        var tmp = t.Unter("fortschritt.csv.tmp");
        File.WriteAllText(tmp, Kopf + "D0001;Eins;dvd-video;1;1.0;eingelesen;2026-11-09T01:00:00;\nD0002;Zwei;dvd-video;1;1.0;eingelesen;2026-11-09T01:00:00;\n");
        File.Move(tmp, t.Unter("fortschritt.csv"), true);

        var fertig = await Task.WhenAny(tcs.Task, Task.Delay(5000));
        Assert.Same(tcs.Task, fertig); // schneller als die 10-s-Abfrage
        Assert.Equal(2, b.Discs.Count);
    }

    [Fact]
    public async Task Geaendert_NurBeiEchterAenderung()
    {
        using var t = new TempOrdner();
        t.Csv(Kopf + "D0001;Eins;dvd-video;1;1.0;eingelesen;2026-11-09T01:00:00;\n");
        using var b = new Bestand(t.Pfad);
        var n = 0;
        b.Geaendert += (_, _) => Interlocked.Increment(ref n);
        await b.AktualisierenAsync();
        await b.AktualisierenAsync();
        await b.AktualisierenAsync();
        Assert.Equal(1, n);
        Assert.NotNull(b.Stand);
    }
}

public class NetzwerkMonitorTests
{
    [Fact]
    public async Task LokalerOrdner_Erreichbar_NichtVorhanden_Nicht()
    {
        using var t = new TempOrdner();
        var e = new Einstellungen { DvdZiel = t.Pfad, MusikZiel = Path.Combine(t.Pfad, "gibt-es-nicht") };
        using var m = new NetzwerkMonitor(e);
        var gemeldet = 0;
        m.Geaendert += (_, _) => gemeldet++;
        await m.PruefenAsync();

        Assert.Equal(1, gemeldet);
        Assert.Equal(2, m.Ziele.Count);
        var ok = m.Ziele[0];
        Assert.True(ok.Erreichbar);
        Assert.True(ok.FreiByte > 0 && ok.GesamtByte >= ok.FreiByte);
        Assert.NotNull(ok.Antwortzeit);
        Assert.Null(ok.Fehler);
        var nein = m.Ziele[1];
        Assert.False(nein.Erreichbar);
        Assert.NotNull(nein.Fehler);
    }

    [Fact]
    public async Task LeereZiele_WerdenWeggelassen()
    {
        using var m = new NetzwerkMonitor(new Einstellungen { DvdZiel = "", MusikZiel = "  " });
        await m.PruefenAsync();
        Assert.Empty(m.Ziele);
    }

    [Fact]
    public async Task UnerreichbarerServer_HaengtNicht()
    {
        // 192.0.2.1 (TEST-NET-1) antwortet nie; die Prüfung muss am Zeitlimit enden.
        using var m = new NetzwerkMonitor(new Einstellungen { DvdZiel = @"\\192.0.2.1\freigabe", MusikZiel = "" })
        { Zeitlimit = TimeSpan.FromSeconds(1) };
        var uhr = System.Diagnostics.Stopwatch.StartNew();
        await m.PruefenAsync();
        Assert.True(uhr.Elapsed < TimeSpan.FromSeconds(4), $"dauerte {uhr.Elapsed}");
        Assert.False(m.Ziele[0].Erreichbar);
        Assert.False(string.IsNullOrEmpty(m.Ziele[0].Fehler));
    }

    [Fact]
    public async Task Starten_PruftPeriodisch()
    {
        using var t = new TempOrdner();
        using var m = new NetzwerkMonitor(new Einstellungen { DvdZiel = t.Pfad, MusikZiel = "" });
        var n = 0;
        m.Geaendert += (_, _) => Interlocked.Increment(ref n);
        m.Starten(TimeSpan.FromMilliseconds(200));
        for (var i = 0; i < 40 && Volatile.Read(ref n) < 3; i++) await Task.Delay(100);
        Assert.True(n >= 3);
        m.Dispose();
    }
}

/// <summary>Manueller Lauf gegen die echten Daten. Aktivieren: Umgebungsvariable BESTAND_ECHT=1.</summary>
public class EchterLauf
{
    [Fact]
    public async Task EchterBestandUndZiele()
    {
        if (Environment.GetEnvironmentVariable("BESTAND_ECHT") != "1") return;
        var e = Einstellungen.Laden();
        if (string.IsNullOrEmpty(e.ArbeitsOrdner)) e.ArbeitsOrdner = @"D:\dvd-digitalisierung";
        if (string.IsNullOrEmpty(e.DvdZiel)) e.DvdZiel = @"S:\";
        var aus = new StringBuilder();

        using var b = new Bestand(e);
        var uhr = System.Diagnostics.Stopwatch.StartNew();
        await b.AktualisierenAsync();
        aus.AppendLine($"Bestand {e.ArbeitsOrdner}: {b.Discs.Count} Discs in {uhr.ElapsedMilliseconds} ms, UmwandelnLaeuft={b.UmwandelnLaeuft}, frei={b.ArbeitFreiByte / 1_000_000_000} GB");
        foreach (var g in b.Discs.GroupBy(d => d.Status).OrderBy(g => g.Key))
            aus.AppendLine($"  {g.Key}: {g.Count()}");
        foreach (var d in b.Discs.Where(d => d.ProblemGrund != null || d.Status is "problem" or "unlesbar").Take(5))
            aus.AppendLine($"  [{d.Disc}] {d.Status} Ablage={d.Ablage} Grund={d.ProblemGrund} Lesefehler={d.LesefehlerSektoren}");

        using var m = new NetzwerkMonitor(e);
        await m.PruefenAsync();
        foreach (var z in m.Ziele)
            aus.AppendLine($"Ziel {z.Name} {z.Pfad}: erreichbar={z.Erreichbar} frei={z.FreiByte / 1_000_000_000} GB von {z.GesamtByte / 1_000_000_000} GB, {z.Antwortzeit?.TotalMilliseconds:0} ms, Fehler={z.Fehler}");

        var datei = Path.Combine(Path.GetTempPath(), "bestand-echt.txt");
        File.WriteAllText(datei, aus.ToString());
    }
}
