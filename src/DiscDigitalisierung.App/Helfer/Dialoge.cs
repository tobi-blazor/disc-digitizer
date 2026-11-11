using System.Windows;
using DiscDigitalisierung.App.ViewModels;
using DiscDigitalisierung.Kern;

namespace DiscDigitalisierung.App.Helfer;

public static class Dialoge
{
    /// <summary>Zeigt den Einstellungen-Dialog; true, wenn gespeichert wurde.</summary>
    public static bool EinstellungenZeigen(Einstellungen einstellungen)
    {
        var vm = new EinstellungenViewModel(einstellungen);
        var fenster = new EinstellungenFenster { DataContext = vm, Owner = Application.Current.MainWindow };
        vm.Schliessen += ok => fenster.DialogResult = ok;
        return fenster.ShowDialog() == true;
    }
}
