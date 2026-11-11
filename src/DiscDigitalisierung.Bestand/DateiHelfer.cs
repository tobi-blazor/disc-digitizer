using System.Text;

namespace DiscDigitalisierung.Bestand;

internal static class DateiHelfer
{
    /// <summary>Liest eine Datei als UTF-8, ohne andere Prozesse zu behindern (Python ersetzt Dateien atomar).
    /// Bei IOException kurz wiederholen; danach null.</summary>
    public static string? TextLesen(string pfad, int versuche = 5)
    {
        for (var i = 0; i < versuche; i++)
        {
            try
            {
                using var fs = new FileStream(pfad, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete);
                using var sr = new StreamReader(fs, new UTF8Encoding(false), true);
                return sr.ReadToEnd();
            }
            catch (FileNotFoundException) { return null; }
            catch (DirectoryNotFoundException) { return null; }
            catch (IOException) { Thread.Sleep(50 * (i + 1)); }
            catch (UnauthorizedAccessException) { Thread.Sleep(50 * (i + 1)); }
        }
        return null;
    }
}
