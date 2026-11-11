using System.Runtime.InteropServices;
using System.Text;

namespace DiscDigitalisierung.Bestand;

/// <summary>Kleine Windows-Helfer: freier Platz und UNC-Pfad eines Netzlaufwerks. Nur lesend.</summary>
internal static class Win32
{
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    static extern bool GetDiskFreeSpaceExW(string verzeichnis, out ulong frei, out ulong gesamt, out ulong freiGesamt);

    [DllImport("mpr.dll", CharSet = CharSet.Unicode)]
    static extern int WNetGetConnectionW(string lokalerName, StringBuilder remoteName, ref int laenge);

    /// <summary>Freier und gesamter Platz des Datenträgers, auf dem <paramref name="pfad"/> liegt (auch UNC).</summary>
    public static bool PlatzLesen(string pfad, out long frei, out long gesamt)
    {
        frei = gesamt = 0;
        if (!OperatingSystem.IsWindows()) return false;
        var p = pfad.EndsWith('\\') ? pfad : pfad + "\\";
        if (!GetDiskFreeSpaceExW(p, out var f, out var g, out _)) return false;
        frei = (long)f;
        gesamt = (long)g;
        return true;
    }

    /// <summary>UNC-Pfad hinter einem Laufwerksbuchstaben ("S:" → "\\NAS\freigabe"), sonst null.</summary>
    public static string? UncZuLaufwerk(string pfad)
    {
        if (!OperatingSystem.IsWindows() || pfad.Length < 2 || pfad[1] != ':' || !char.IsLetter(pfad[0])) return null;
        var sb = new StringBuilder(512);
        var laenge = sb.Capacity;
        return WNetGetConnectionW(pfad[..2], sb, ref laenge) == 0 ? sb.ToString() : null;
    }
}
