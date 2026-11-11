using System.Windows;
using System.Windows.Markup;
using DiscDigitalisierung.App.Helfer;
using DiscDigitalisierung.App.ViewModels;

namespace DiscDigitalisierung.App;

public partial class App : Application
{
    protected override void OnStartup(StartupEventArgs e)
    {
        base.OnStartup(e);
        FrameworkElement.LanguageProperty.OverrideMetadata(typeof(FrameworkElement),
            new FrameworkPropertyMetadata(XmlLanguage.GetLanguage(Ui.Kultur.IetfLanguageTag)));

        var dienste = Dienste.Erzeugen(e.Args);
        var fenster = new MainWindow { DataContext = new MainViewModel(dienste) };
        MainWindow = fenster;
        fenster.Show();
    }
}
