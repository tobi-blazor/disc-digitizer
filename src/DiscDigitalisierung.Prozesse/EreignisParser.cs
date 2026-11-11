using System.Globalization;
using System.Text.Json;
using DiscDigitalisierung.Kern;

namespace DiscDigitalisierung.Prozesse;

/// <summary>Wandelt eine stdout-Zeile eines Python-Skripts in ein <see cref="Ereignis"/> um
/// (docs/ereignis-protokoll.md). Wirft nie: alles Unverständliche wird zur <see cref="LogZeile"/>.
/// Das Feld <see cref="Ereignis.Skript"/> setzt der Aufrufer.</summary>
public static class EreignisParser
{
    const string Praefix = "@@ ";

    public static Ereignis Parse(string? zeile)
    {
        zeile = (zeile ?? "").TrimEnd('\r', '\n');
        if (!zeile.StartsWith(Praefix, StringComparison.Ordinal))
            return new LogZeile(zeile, false);
        try
        {
            using var dok = JsonDocument.Parse(zeile.AsMemory(Praefix.Length));
            if (dok.RootElement.ValueKind != JsonValueKind.Object)
                return new LogZeile(zeile, false);
            return Abbilden(dok.RootElement) ?? new LogZeile(zeile, false);
        }
        catch (Exception)
        {
            return new LogZeile(zeile, false);
        }
    }

    static Ereignis? Abbilden(JsonElement wurzel)
    {
        var f = new Felder(wurzel);
        var typ = f.Text("typ");
        if (typ is null) return null;

        Ereignis? e = typ switch
        {
            "start" => new StartEreignis(f.Ganz("pid") ?? 0, f.Texte("argumente")),
            "laufwerk" => new LaufwerkEreignis(f.Text("zustand") ?? "", f.Text("label"), f.Text("medium"), f.Zahl("seit_s")),
            "disc" => new DiscEreignis(f.Text("disc") ?? "", f.Text("beschriftung"), f.Text("label"),
                f.Text("medientyp"), f.Lang("seriennummer"), f.Text("phase") ?? ""),
            "fortschritt" => new FortschrittEreignis(f.Text("disc"), f.Text("phase") ?? "", f.Text("titel"),
                f.Ganz("titel_nr"), f.Ganz("titel_anzahl"), f.Lang("byte"), f.Lang("byte_gesamt"), f.Zahl("prozent"),
                f.Lang("fehler_sektoren"), f.Zahl("rate_mb_s"), f.Zahl("rest_s"), f.Text("schritt")),
            "frage" => new FrageEreignis(f.Text("id") ?? "", f.Text("art") ?? "", f.Text("text") ?? "", f.Optionen(),
                f.Wahr("freitext"), f.Text("vorgabe"), f.Text("disc")) { Details = f.Text("details"), Empfehlung = f.Text("empfehlung") },
            "frage_erledigt" => new FrageErledigtEreignis(f.Text("id") ?? "", f.Text("wert") ?? "", f.Text("durch")),
            "meldung" => new MeldungEreignis(Stufe(f.Text("stufe")), f.Text("text") ?? "", f.Text("disc")),
            "disc_ende" => new DiscEndeEreignis(f.Text("disc") ?? "", f.Text("phase") ?? "", f.Text("status") ?? "",
                f.Text("grund"), f.Text("ziel"), f.Zahl("dauer_s")),
            "warteschlange" => new WarteschlangeEreignis(f.Texte("wartend"), f.Text("aktiv"), f.Zeit("pausiert_bis")),
            "ende" => new EndeEreignis(f.Ganz("code") ?? 0),
            "einstellung" => new EinstellungEreignis(f.Text("name") ?? "", f.Text("wert") ?? ""),
            "metadaten" => new MetadatenEreignis(f.Text("disc"), f.MetaFelder()),
            "lesekarte" => new LesekarteEreignis(f.Text("disc"), f.Lang("sektoren_belegt"), f.Lang("sektoren_max"),
                f.Bereiche("geplant"), f.Bereiche("bereiche")),
            "empfehlung" => new EmpfehlungEreignis(f.Text("disc"), f.Text("art") ?? "", f.Text("stufe") ?? "", f.Text("text") ?? ""),
            _ => null,
        };
        if (e is null) return null;
        var zeit = f.Zeit("zeit");
        return zeit is null ? e : e with { Zeit = zeit.Value };
    }

    static MeldungsStufe Stufe(string? s) => s switch
    {
        "hinweis" => MeldungsStufe.Hinweis,
        "warnung" => MeldungsStufe.Warnung,
        "fehler" => MeldungsStufe.Fehler,
        _ => MeldungsStufe.Info,
    };

    /// <summary>Tolerante Feldzugriffe: fehlend, null oder falscher Typ ergibt null bzw. Leerwert.</summary>
    readonly struct Felder(JsonElement w)
    {
        bool Holen(string name, out JsonElement v)
        {
            if (w.TryGetProperty(name, out v) && v.ValueKind != JsonValueKind.Null) return true;
            v = default;
            return false;
        }

        public string? Text(string n) => Holen(n, out var v) ? v.ValueKind switch
        {
            JsonValueKind.String => v.GetString(),
            JsonValueKind.Number or JsonValueKind.True or JsonValueKind.False => v.GetRawText(),
            _ => null,
        } : null;

        public double? Zahl(string n) =>
            Holen(n, out var v) && v.ValueKind == JsonValueKind.Number && v.TryGetDouble(out var d) ? d : null;

        public long? Lang(string n)
        {
            if (!Holen(n, out var v) || v.ValueKind != JsonValueKind.Number) return null;
            if (v.TryGetInt64(out var l)) return l;
            return v.TryGetDouble(out var d) && double.IsFinite(d) ? (long)d : null;
        }

        public int? Ganz(string n) => Lang(n) is { } l && l >= int.MinValue && l <= int.MaxValue ? (int)l : null;

        public bool Wahr(string n) => Holen(n, out var v) && v.ValueKind == JsonValueKind.True;

        public DateTimeOffset? Zeit(string n) =>
            Holen(n, out var v) && v.ValueKind == JsonValueKind.String
            && DateTimeOffset.TryParse(v.GetString(), CultureInfo.InvariantCulture, DateTimeStyles.None, out var z)
                ? z : null;

        public IReadOnlyList<string> Texte(string n)
        {
            var l = new List<string>();
            if (Holen(n, out var v) && v.ValueKind == JsonValueKind.Array)
                foreach (var x in v.EnumerateArray())
                    if (x.ValueKind == JsonValueKind.String) l.Add(x.GetString() ?? "");
                    else if (x.ValueKind != JsonValueKind.Null) l.Add(x.GetRawText());
            return l;
        }

        public IReadOnlyList<Antwortoption> Optionen()
        {
            var l = new List<Antwortoption>();
            if (Holen("optionen", out var v) && v.ValueKind == JsonValueKind.Array)
                foreach (var x in v.EnumerateArray())
                    if (x.ValueKind == JsonValueKind.Object)
                    {
                        var o = new Felder(x);
                        l.Add(new Antwortoption(o.Text("wert") ?? "", o.Text("text") ?? "", o.Text("hinweis")));
                    }
            return l;
        }

        public IReadOnlyList<MetadatenFeld> MetaFelder()
        {
            var l = new List<MetadatenFeld>();
            if (Holen("felder", out var v) && v.ValueKind == JsonValueKind.Array)
                foreach (var x in v.EnumerateArray())
                    if (x.ValueKind == JsonValueKind.Object)
                    {
                        var o = new Felder(x);
                        l.Add(new MetadatenFeld(o.Text("gruppe") ?? "", o.Text("name") ?? "", o.Text("wert") ?? ""));
                    }
            return l;
        }

        /// <summary>[[von, bis, zustand], ...] bzw. bei "geplant" [[von, bis], ...] (Zustand "geplant");
        /// ungültige Einträge entfallen.</summary>
        public IReadOnlyList<Lesebereich> Bereiche(string name)
        {
            var l = new List<Lesebereich>();
            if (Holen(name, out var v) && v.ValueKind == JsonValueKind.Array)
                foreach (var x in v.EnumerateArray())
                    if (x.ValueKind == JsonValueKind.Array && x.GetArrayLength() >= 2
                        && x[0].ValueKind == JsonValueKind.Number && x[1].ValueKind == JsonValueKind.Number
                        && x[0].TryGetInt64(out var von) && x[1].TryGetInt64(out var bis) && bis > von)
                        l.Add(new Lesebereich(von, bis, x.GetArrayLength() >= 3 && x[2].ValueKind == JsonValueKind.String
                            ? x[2].GetString() ?? "" : "geplant"));
            return l;
        }
    }
}
