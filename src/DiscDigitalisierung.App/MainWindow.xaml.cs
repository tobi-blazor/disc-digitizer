using System.ComponentModel;
using System.Windows;
using DiscDigitalisierung.App.Helfer;
using DiscDigitalisierung.App.ViewModels;

namespace DiscDigitalisierung.App;

/// <summary>Nur Fenster-Interop: Taskleistenblinken, Schließen-Rückfrage, Start. Keine Fachlogik.</summary>
public partial class MainWindow : Window
{
    bool _schliessenFreigegeben;

    public MainWindow()
    {
        InitializeComponent();
        Loaded += async (_, _) =>
        {
            var vm = (MainViewModel)DataContext;
            vm.NeueFrage += () => Benachrichtigung.FrageEingetroffen(this);
            await vm.StartenAsync();
        };
    }

    void Beenden_Click(object sender, RoutedEventArgs e) => Close();

    protected override async void OnClosing(CancelEventArgs e)
    {
        base.OnClosing(e);
        if (_schliessenFreigegeben) return;
        var vm = (MainViewModel)DataContext;
        e.Cancel = true;
        if (vm.IrgendeinSkriptLaeuft)
        {
            var antwort = MessageBox.Show(this, "Es läuft noch ein Skript. Beide Skripte beenden und das Programm schließen?",
                "Beenden", MessageBoxButton.YesNo, MessageBoxImage.Question, MessageBoxResult.No);
            if (antwort != MessageBoxResult.Yes) return;
        }
        IsEnabled = false;
        await vm.AllesStoppenAsync();
        _schliessenFreigegeben = true;
        Close();
    }
}
