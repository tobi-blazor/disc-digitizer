using System.Text;

namespace DiscDigitalisierung.Bestand;

/// <summary>Minimaler CSV-Leser: Semikolon, Anführungszeichen mit "" als Maskierung, Zeilenumbrüche in Feldern.</summary>
internal static class CsvLeser
{
    public static List<List<string>> Zeilen(string text, char trenner = ';')
    {
        var ergebnis = new List<List<string>>();
        var zeile = new List<string>();
        var feld = new StringBuilder();
        var inAnfuehrung = false;
        var feldBegonnen = false;

        void FeldEnde() { zeile.Add(feld.ToString()); feld.Clear(); feldBegonnen = false; }
        void ZeileEnde()
        {
            if (feldBegonnen || feld.Length > 0 || zeile.Count > 0) { FeldEnde(); ergebnis.Add(zeile); }
            zeile = new List<string>();
        }

        for (var i = 0; i < text.Length; i++)
        {
            var c = text[i];
            if (inAnfuehrung)
            {
                if (c == '"')
                {
                    if (i + 1 < text.Length && text[i + 1] == '"') { feld.Append('"'); i++; }
                    else inAnfuehrung = false;
                }
                else feld.Append(c);
            }
            else if (c == '"' && feld.Length == 0) { inAnfuehrung = true; feldBegonnen = true; }
            else if (c == trenner) FeldEnde();
            else if (c == '\r') { if (i + 1 < text.Length && text[i + 1] == '\n') i++; ZeileEnde(); }
            else if (c == '\n') ZeileEnde();
            else { feld.Append(c); feldBegonnen = true; }
        }
        ZeileEnde();
        return ergebnis;
    }
}
