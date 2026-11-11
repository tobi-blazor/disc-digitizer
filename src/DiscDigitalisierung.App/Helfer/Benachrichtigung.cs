using System.Runtime.InteropServices;
using System.Windows;
using System.Windows.Interop;

namespace DiscDigitalisierung.App.Helfer;

/// <summary>Fenster-Interop: Ton und blinkende Taskleiste, ohne das Fenster in den Vordergrund zu holen.</summary>
public static class Benachrichtigung
{
    [StructLayout(LayoutKind.Sequential)]
    struct FLASHWINFO
    {
        public uint cbSize;
        public IntPtr hwnd;
        public uint dwFlags;
        public uint uCount;
        public uint dwTimeout;
    }

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    static extern bool FlashWindowEx(ref FLASHWINFO pwfi);

    const uint FLASHW_ALL = 3, FLASHW_TIMERNOFG = 12;

    /// <summary>Taskleiste blinken lassen (der Klang kommt über <see cref="Klaenge"/>).</summary>
    public static void FrageEingetroffen(Window fenster)
    {
        if (fenster.IsActive) return;
        var h = new WindowInteropHelper(fenster).Handle;
        if (h == IntPtr.Zero) return;
        var info = new FLASHWINFO
        {
            cbSize = (uint)Marshal.SizeOf<FLASHWINFO>(),
            hwnd = h,
            dwFlags = FLASHW_ALL | FLASHW_TIMERNOFG, // blinkt, bis das Fenster aktiv wird
            uCount = uint.MaxValue,
            dwTimeout = 0,
        };
        FlashWindowEx(ref info);
    }
}
