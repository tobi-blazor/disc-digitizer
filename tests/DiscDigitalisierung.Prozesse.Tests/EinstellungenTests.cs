using DiscDigitalisierung.Kern;

namespace DiscDigitalisierung.Prozesse.Tests;

public sealed class EinstellungenTests : IDisposable
{
    readonly string _wurzel = Path.Combine(Path.GetTempPath(), "dd-einstellungen-" + Guid.NewGuid().ToString("N"));

    public EinstellungenTests() => Directory.CreateDirectory(_wurzel);

    public void Dispose()
    {
        try { Directory.Delete(_wurzel, true); } catch (IOException) { }
    }

    [Fact]
    public void SkriptOrdnerNebenDemProgramm()
    {
        var skripte = Directory.CreateDirectory(Path.Combine(_wurzel, "skripte")).FullName;
        File.WriteAllText(Path.Combine(skripte, "einlesen.py"), "");
        Assert.Equal(skripte, Einstellungen.StandardSkriptOrdner(_wurzel));
    }

    [Fact]
    public void SkriptOrdnerImRepoBeimStartAusBin()
    {
        var skripte = Directory.CreateDirectory(Path.Combine(_wurzel, "skripte")).FullName;
        File.WriteAllText(Path.Combine(skripte, "einlesen.py"), "");
        var bin = Directory.CreateDirectory(Path.Combine(_wurzel, "src", "App", "bin", "Debug")).FullName;
        Assert.Equal(skripte, Einstellungen.StandardSkriptOrdner(bin));
    }

    [Fact]
    public void OhneSkripteNebenDemProgrammAlsVorgabe()
    {
        var bin = Directory.CreateDirectory(Path.Combine(_wurzel, "leer")).FullName;
        var ergebnis = Einstellungen.StandardSkriptOrdner(bin);
        Assert.Equal(Path.Combine(bin, "skripte"), ergebnis);
    }

    [Fact]
    public void LeererSkriptOrdnerWirdBeimLadenErgaenzt()
    {
        var datei = Path.Combine(_wurzel, "einstellungen.json");
        File.WriteAllText(datei, """{"SkriptOrdner":"","Laufwerk":"F:"}""");
        var e = Einstellungen.Laden(datei);
        Assert.NotEqual("", e.SkriptOrdner);
        Assert.Equal("F:", e.Laufwerk);
    }
}
