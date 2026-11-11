using System.Text.Json;

namespace DiscDigitalisierung.Kern;

/// <summary>Werte für einlesen.py --nummern bzw. die Einstellung "nummern".</summary>
public static class NummernModus
{
    /// <summary>Bei leerer Disc nachfragen, ob die Nummer vergeben wird.</summary>
    public const string Fragen = "fragen";
    /// <summary>Stapel ist durchnummeriert: Nummer ohne Frage als "leer" vergeben.</summary>
    public const string Immer = "immer";
    /// <summary>Erst testen, Nummer danach draufschreiben: leere Discs bekommen keine Nummer.</summary>
    public const string Nie = "nie";
    public static readonly string[] Alle = [Fragen, Immer, Nie];
}

/// <summary>Einstellungen des Programms, gespeichert in %APPDATA%\DiscDigitalisierung\einstellungen.json.
/// Ein leerer Skriptordner wird beim Laden zu <see cref="StandardSkriptOrdner"/>. Leere Pfade werden aus den
/// Benutzer-Umgebungsvariablen ergänzt (DVD_ARBEIT, DVD_ZIEL, MUSIK_ZIEL, DVD_LAUFWERK), genau wie es die
/// Python-Skripte tun. Keine Secrets hier ablegen.</summary>
public sealed class Einstellungen
{
    public string PythonPfad { get; set; } = "python";
    /// <summary>Ordner mit einlesen.py, umwandeln.py usw. Leer = <see cref="StandardSkriptOrdner"/>.</summary>
    public string SkriptOrdner { get; set; } = "";
    public string ArbeitsOrdner { get; set; } = "";
    public string DvdZiel { get; set; } = "";
    public string MusikZiel { get; set; } = "";
    public string Laufwerk { get; set; } = "E:";

    /// <summary>Nummer bei leerer/unlesbarer Disc: <see cref="NummernModus"/> (fragen, immer, nie).
    /// Wird beim Start als --nummern übergeben und lässt sich zur Laufzeit umschalten.</summary>
    public string Nummern { get; set; } = NummernModus.Fragen;

    /// <summary>Zusätzliche Argumente für einlesen.py.</summary>
    public List<string> EinlesenArgumente { get; set; } = [];

    /// <summary>Zusätzliche Argumente für umwandeln.py, z.B. "--zeitfenster", "22-07".</summary>
    public List<string> UmwandelnArgumente { get; set; } = [];

    public bool UmwandelnAutomatischStarten { get; set; } = true;

    /// <summary>Klang je Anlass (siehe Klaenge in der App): "aus", eingebauter Name (z.B. "glocke"),
    /// "windows:Asterisk" oder "datei:C:\…\x.wav". Fehlende Anlässe nehmen den Standard der App.</summary>
    public Dictionary<string, string> Klaenge { get; set; } = [];

    /// <summary>Lautstärke der Klänge, 0–100.</summary>
    public int KlangLautstaerke { get; set; } = 70;
    public int NetzwerkIntervallSekunden { get; set; } = 60;

    public static string Datei => Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "DiscDigitalisierung", "einstellungen.json");

    /// <summary>Datei, aus der geladen wurde; <see cref="Speichern"/> schreibt ohne Angabe dorthin.</summary>
    [System.Text.Json.Serialization.JsonIgnore]
    public string? GeladenAus { get; private set; }

    static readonly JsonSerializerOptions Json = new() { WriteIndented = true };

    public static Einstellungen Laden(string? datei = null)
    {
        datei ??= Datei;
        Einstellungen e;
        try
        {
            e = File.Exists(datei) ? JsonSerializer.Deserialize<Einstellungen>(File.ReadAllText(datei), Json) ?? new() : new();
        }
        catch (JsonException)
        {
            e = new();
        }
        e.GeladenAus = datei;
        if (e.EinlesenArgumente.Remove("--unnummeriert")) // alte Schreibweise übernehmen
            e.Nummern = NummernModus.Nie;
        if (!NummernModus.Alle.Contains(e.Nummern))
            e.Nummern = NummernModus.Fragen;
        if (string.IsNullOrWhiteSpace(e.SkriptOrdner))
            e.SkriptOrdner = StandardSkriptOrdner();
        e.ArbeitsOrdner = Ergaenzen(e.ArbeitsOrdner, "DVD_ARBEIT");
        e.DvdZiel = Ergaenzen(e.DvdZiel, "DVD_ZIEL");
        e.MusikZiel = Ergaenzen(e.MusikZiel, "MUSIK_ZIEL"); // leer: musik_cd.py nimmt <DVD_ARBEIT>\musik
        if (string.IsNullOrWhiteSpace(e.Laufwerk))
            e.Laufwerk = Umgebung("DVD_LAUFWERK") ?? "E:";
        return e;
    }

    public void Speichern(string? datei = null)
    {
        datei ??= GeladenAus ?? Datei;
        Directory.CreateDirectory(Path.GetDirectoryName(datei)!);
        File.WriteAllText(datei, JsonSerializer.Serialize(this, Json));
    }

    /// <summary>Umgebungsvariablen für die Python-Prozesse (Ereignis-Modus plus Pfade aus den Einstellungen).</summary>
    public IReadOnlyDictionary<string, string> PythonUmgebung()
    {
        var u = new Dictionary<string, string>
        {
            ["DVD_EREIGNISSE"] = "1",
            ["PYTHONIOENCODING"] = "utf-8",
            ["PYTHONUNBUFFERED"] = "1",
        };
        if (ArbeitsOrdner != "") u["DVD_ARBEIT"] = ArbeitsOrdner;
        if (DvdZiel != "") u["DVD_ZIEL"] = DvdZiel;
        if (MusikZiel != "") u["MUSIK_ZIEL"] = MusikZiel;
        if (Laufwerk != "") u["DVD_LAUFWERK"] = Laufwerk;
        return u;
    }

    /// <summary>Ordner "skripte" neben dem Programm (Release-Zip), sonst der nächste übergeordnete Ordner
    /// mit skripte\einlesen.py (Start aus dem Quellcode, z.B. aus bin\Debug).</summary>
    public static string StandardSkriptOrdner(string? basis = null)
    {
        basis ??= AppContext.BaseDirectory;
        for (var d = new DirectoryInfo(basis); d is not null; d = d.Parent)
        {
            var kandidat = Path.Combine(d.FullName, "skripte");
            if (File.Exists(Path.Combine(kandidat, "einlesen.py")))
                return kandidat;
        }
        return Path.Combine(basis, "skripte");
    }

    static string Ergaenzen(string wert, string variable) =>
        string.IsNullOrWhiteSpace(wert) ? Umgebung(variable) ?? "" : wert;

    static string? Umgebung(string name) =>
        Environment.GetEnvironmentVariable(name)
        ?? Environment.GetEnvironmentVariable(name, EnvironmentVariableTarget.User);
}
