using DiscDigitalisierung.Kern;
using DiscDigitalisierung.Prozesse;

namespace DiscDigitalisierung.Prozesse.Tests;

public class EreignisParserTests
{
    static string Z(string json) => "@@ " + json;

    [Fact]
    public void NormaleZeile_WirdLogZeile()
    {
        var e = Assert.IsType<LogZeile>(EreignisParser.Parse("Beobachte E: (alle 3 s)"));
        Assert.Equal("Beobachte E: (alle 3 s)", e.Text);
        Assert.False(e.Fehlerkanal);
    }

    [Fact]
    public void Start_MitZeit()
    {
        var e = Assert.IsType<StartEreignis>(EreignisParser.Parse(Z(
            """{"v":1,"typ":"start","zeit":"2026-11-09T14:00:00+01:00","skript":"einlesen","pid":4711,"argumente":["--a","b"]}""")));
        Assert.Equal(4711, e.Pid);
        Assert.Equal(["--a", "b"], e.Argumente);
        Assert.Equal(new DateTimeOffset(2026, 11, 9, 14, 0, 0, TimeSpan.FromHours(1)), e.Zeit);
    }

    [Fact]
    public void Laufwerk()
    {
        var e = Assert.IsType<LaufwerkEreignis>(EreignisParser.Parse(Z(
            """{"v":1,"typ":"laufwerk","zustand":"disc","label":"KINDERSERIE","medium":"DVD+R","seit_s":0}""")));
        Assert.Equal("disc", e.Zustand);
        Assert.Equal("KINDERSERIE", e.Label);
        Assert.Equal("DVD+R", e.Medium);
        Assert.Equal(0, e.SeitSekunden);
    }

    [Fact]
    public void Disc_DoppeltesTypFeld_ErsteIstEreignisTypZweiteDiscTyp()
    {
        var e = Assert.IsType<DiscEreignis>(EreignisParser.Parse(Z(
            """{"v":1,"typ":"disc","zeit":"2026-11-09T14:00:00+01:00","disc":"D0072","beschriftung":"Kinderserie Folge 6","label":"KINDERSERIE_FOLGE_6","medientyp":"dvd-video","seriennummer":1234567890,"phase":"einlesen"}""")));
        Assert.Equal("D0072", e.Disc);
        Assert.Equal("dvd-video", e.Typ);
        Assert.Equal(1234567890L, e.Seriennummer);
        Assert.Equal("einlesen", e.Phase);
        Assert.Equal("Kinderserie Folge 6", e.Beschriftung);
    }

    [Fact]
    public void Fortschritt_MitNull()
    {
        var e = Assert.IsType<FortschrittEreignis>(EreignisParser.Parse(Z(
            """{"v":1,"typ":"fortschritt","disc":"D0072","phase":"einlesen","titel":"t02","titel_nr":2,"titel_anzahl":4,"byte":1330000000,"byte_gesamt":1840000000,"prozent":72.3,"fehler_sektoren":46,"rate_mb_s":0.0,"rest_s":null,"schritt":"kopieren"}""")));
        Assert.Equal("t02", e.Titel);
        Assert.Equal(2, e.TitelNr);
        Assert.Equal(4, e.TitelAnzahl);
        Assert.Equal(1330000000L, e.Byte);
        Assert.Equal(1840000000L, e.ByteGesamt);
        Assert.Equal(72.3, e.Prozent);
        Assert.Equal(46, e.FehlerSektoren);
        Assert.Equal(0.0, e.RateMbS);
        Assert.Null(e.RestSekunden);
        Assert.Equal("kopieren", e.Schritt);
    }

    [Fact]
    public void Frage_BeispielAusVertrag()
    {
        var e = Assert.IsType<FrageEreignis>(EreignisParser.Parse(Z(
            """{"v":1,"typ":"frage","zeit":"2026-11-09T14:00:00+01:00","id":"q1","art":"lesefehler","text":"Fehlerstelle bei 84 %","optionen":[{"wert":"w","text":"weiter versuchen"},{"wert":"ü","text":"überspringen"},{"wert":"i","text":"immer überspringen"},{"wert":"a","text":"abbrechen"}],"freitext":false,"vorgabe":"w","disc":"D0072"}""")));
        Assert.Equal("q1", e.Id);
        Assert.Equal("lesefehler", e.Art);
        Assert.Equal(4, e.Optionen.Count);
        Assert.Equal(new Antwortoption("ü", "überspringen"), e.Optionen[1]);
        Assert.False(e.Freitext);
        Assert.Equal("w", e.Vorgabe);
        Assert.Equal("D0072", e.Disc);
    }

    [Fact]
    public void Frage_Freitext_FehlendeOptionaleFelder()
    {
        var e = Assert.IsType<FrageEreignis>(EreignisParser.Parse(Z(
            """{"v":1,"typ":"frage","id":"q2","art":"beschriftung","text":"Name?","freitext":true}""")));
        Assert.Empty(e.Optionen);
        Assert.True(e.Freitext);
        Assert.Null(e.Vorgabe);
        Assert.Null(e.Disc);
    }

    [Fact]
    public void FrageErledigt()
    {
        var e = Assert.IsType<FrageErledigtEreignis>(EreignisParser.Parse(Z(
            """{"v":1,"typ":"frage_erledigt","id":"q1","wert":"ü","durch":"terminal"}""")));
        Assert.Equal(("q1", "ü", "terminal"), (e.Id, e.Wert, e.Durch));
    }

    [Theory]
    [InlineData("info", MeldungsStufe.Info)]
    [InlineData("hinweis", MeldungsStufe.Hinweis)]
    [InlineData("warnung", MeldungsStufe.Warnung)]
    [InlineData("fehler", MeldungsStufe.Fehler)]
    [InlineData("seltsam", MeldungsStufe.Info)]
    public void Meldung_Stufen(string stufe, MeldungsStufe erwartet)
    {
        var e = Assert.IsType<MeldungEreignis>(EreignisParser.Parse(Z(
            $$"""{"v":1,"typ":"meldung","stufe":"{{stufe}}","text":"Auswerfen hat nicht geklappt","disc":null}""")));
        Assert.Equal(erwartet, e.Stufe);
        Assert.Null(e.Disc);
    }

    [Fact]
    public void DiscEnde()
    {
        var e = Assert.IsType<DiscEndeEreignis>(EreignisParser.Parse(Z(
            """{"v":1,"typ":"disc_ende","disc":"D0001","phase":"einlesen","status":"problem","grund":"Lesefehler","ziel":"E:\\3-problem\\D0001","dauer_s":12.5}""")));
        Assert.Equal("problem", e.Status);
        Assert.Equal("Lesefehler", e.Grund);
        Assert.Equal(@"E:\3-problem\D0001", e.Ziel);
        Assert.Equal(12.5, e.DauerSekunden);
    }

    [Fact]
    public void DiscEnde_OhneOptionaleFelder()
    {
        var e = Assert.IsType<DiscEndeEreignis>(EreignisParser.Parse(Z(
            """{"v":1,"typ":"disc_ende","disc":"D0001","phase":"umwandeln","status":"umgewandelt"}""")));
        Assert.Null(e.Grund);
        Assert.Null(e.Ziel);
        Assert.Null(e.DauerSekunden);
    }

    [Fact]
    public void Warteschlange()
    {
        var e = Assert.IsType<WarteschlangeEreignis>(EreignisParser.Parse(Z(
            """{"v":1,"typ":"warteschlange","wartend":["D0003",4],"aktiv":"D0002","pausiert_bis":"2026-11-09T22:00:00+01:00"}""")));
        Assert.Equal(["D0003", "4"], e.Wartend);
        Assert.Equal("D0002", e.Aktiv);
        Assert.Equal(new DateTimeOffset(2026, 11, 9, 22, 0, 0, TimeSpan.FromHours(1)), e.PausiertBis);
    }

    [Fact]
    public void Warteschlange_Null()
    {
        var e = Assert.IsType<WarteschlangeEreignis>(EreignisParser.Parse(Z(
            """{"v":1,"typ":"warteschlange","wartend":[],"aktiv":null,"pausiert_bis":null}""")));
        Assert.Empty(e.Wartend);
        Assert.Null(e.Aktiv);
        Assert.Null(e.PausiertBis);
    }

    [Fact]
    public void Ende()
    {
        var e = Assert.IsType<EndeEreignis>(EreignisParser.Parse(Z("""{"v":1,"typ":"ende","code":2}""")));
        Assert.Equal(2, e.Code);
    }

    [Fact]
    public void FehlendeZeit_NimmtEmpfangszeit()
    {
        var vorher = DateTimeOffset.Now.AddSeconds(-1);
        var e = EreignisParser.Parse(Z("""{"v":1,"typ":"ende","code":0}"""));
        Assert.True(e.Zeit >= vorher);
    }

    [Fact]
    public void UnbekannteFelder_WerdenIgnoriert()
    {
        var e = Assert.IsType<EndeEreignis>(EreignisParser.Parse(Z("""{"v":1,"typ":"ende","code":0,"neu":{"x":[1]}}""")));
        Assert.Equal(0, e.Code);
    }

    [Theory]
    [InlineData("@@ {kaputt")]
    [InlineData("@@ ")]
    [InlineData("@@ [1,2]")]
    [InlineData("@@ \"text\"")]
    [InlineData("@@ {\"v\":1}")]
    [InlineData("@@ {\"v\":1,\"typ\":5}")]
    [InlineData("@@ {\"v\":1,\"typ\":\"unbekannt\",\"x\":1}")]
    public void UngueltigOderUnbekannt_WirdLogMitOriginaltext(string zeile)
    {
        var e = Assert.IsType<LogZeile>(EreignisParser.Parse(zeile));
        Assert.Equal(zeile, e.Text);
    }

    [Fact]
    public void Null_UndLeer_WerfenNicht()
    {
        Assert.Equal("", Assert.IsType<LogZeile>(EreignisParser.Parse(null)).Text);
        Assert.Equal("", Assert.IsType<LogZeile>(EreignisParser.Parse("")).Text);
    }

    [Fact]
    public void Umlaute_BleibenErhalten()
    {
        var e = Assert.IsType<MeldungEreignis>(EreignisParser.Parse(Z(
            """{"v":1,"typ":"meldung","stufe":"info","text":"Größe überprüft: Ärger ✓ \u00e4"}""")));
        Assert.Equal("Größe überprüft: Ärger ✓ ä", e.Text);
    }

    [Fact]
    public void ZeilenendeCr_WirdEntfernt()
    {
        var e = Assert.IsType<LogZeile>(EreignisParser.Parse("Hallo\r"));
        Assert.Equal("Hallo", e.Text);
    }

    [Fact]
    public void Serialisierer_Antwort_EineZeileOhneBom()
    {
        var s = NachrichtSerialisierer.Antwort("q1", "ü");
        Assert.DoesNotContain('\n', s);
        using var d = System.Text.Json.JsonDocument.Parse(s);
        Assert.Equal("antwort", d.RootElement.GetProperty("typ").GetString());
        Assert.Equal("q1", d.RootElement.GetProperty("id").GetString());
        Assert.Equal("ü", d.RootElement.GetProperty("wert").GetString());
        Assert.NotEqual('\uFEFF', s[0]);
    }

    [Fact]
    public void Serialisierer_Stopp()
    {
        using var d = System.Text.Json.JsonDocument.Parse(NachrichtSerialisierer.Stopp());
        Assert.Equal("stopp", d.RootElement.GetProperty("typ").GetString());
    }

    [Fact]
    public void Serialisierer_Einstellung()
    {
        using var d = System.Text.Json.JsonDocument.Parse(NachrichtSerialisierer.Einstellung("nummern", "immer"));
        Assert.Equal("einstellung", d.RootElement.GetProperty("typ").GetString());
        Assert.Equal("nummern", d.RootElement.GetProperty("name").GetString());
        Assert.Equal("immer", d.RootElement.GetProperty("wert").GetString());
    }

    [Fact]
    public void Lesekarte_MitGeplantUndBereichen()
    {
        var e = Assert.IsType<LesekarteEreignis>(EreignisParser.Parse(
            """@@ {"v":1,"typ":"lesekarte","disc":"D1","sektoren_belegt":2053232,"sektoren_max":2295104,"geplant":[[16384,914821]],"bereiche":[[16384,20000,"ok"],[20000,20100,"ausstehend"],[5,1,"ok"],"kaputt"]}"""));
        Assert.Equal(2053232, e.SektorenBelegt);
        Assert.Equal(2295104, e.SektorenMax);
        Assert.Equal([new Lesebereich(16384, 914821, "geplant")], e.Geplant);
        Assert.Equal([new Lesebereich(16384, 20000, "ok"), new Lesebereich(20000, 20100, "ausstehend")], e.Bereiche);
    }

    [Fact]
    public void Frage_MitDetailsEmpfehlungUndHinweisen()
    {
        var e = Assert.IsType<FrageEreignis>(EreignisParser.Parse(
            """@@ {"v":1,"typ":"frage","id":"q3","art":"lesefehler","text":"t","details":"d","empfehlung":"ü","optionen":[{"wert":"w","text":"weiter","hinweis":"kostet Zeit"},{"wert":"ü","text":"lassen"}],"freitext":false}"""));
        Assert.Equal("d", e.Details);
        Assert.Equal("ü", e.Empfehlung);
        Assert.Equal(new Antwortoption("w", "weiter", "kostet Zeit"), e.Optionen[0]);
        Assert.Null(e.Optionen[1].Hinweis);
    }

    [Fact]
    public void Empfehlung()
    {
        var e = Assert.IsType<EmpfehlungEreignis>(EreignisParser.Parse(
            """@@ {"v":1,"typ":"empfehlung","disc":"D1","art":"reinigen","stufe":"empfohlen","text":"Reinigen empfohlen"}"""));
        Assert.Equal(("reinigen", "empfohlen", "Reinigen empfohlen"), (e.Art, e.Stufe, e.Text));
    }

    [Fact]
    public void Einstellung()
    {
        var e = Assert.IsType<EinstellungEreignis>(EreignisParser.Parse(
            """@@ {"v":1,"typ":"einstellung","zeit":"2026-11-09T18:00:00+01:00","name":"nummern","wert":"nie"}"""));
        Assert.Equal("nummern", e.Name);
        Assert.Equal("nie", e.Wert);
    }
}
