using System.Collections.Specialized;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Threading;

namespace DiscDigitalisierung.App.Helfer;

/// <summary>Angehängte Eigenschaft: scrollt eine ListBox zum letzten Eintrag, solange Aktiv gesetzt ist.</summary>
public static class AutoScroll
{
    public static readonly DependencyProperty AktivProperty = DependencyProperty.RegisterAttached(
        "Aktiv", typeof(bool), typeof(AutoScroll), new PropertyMetadata(false, BeiAenderung));

    public static bool GetAktiv(DependencyObject o) => (bool)o.GetValue(AktivProperty);
    public static void SetAktiv(DependencyObject o, bool v) => o.SetValue(AktivProperty, v);

    static readonly DependencyProperty VerdrahtetProperty = DependencyProperty.RegisterAttached(
        "Verdrahtet", typeof(bool), typeof(AutoScroll), new PropertyMetadata(false));

    static void BeiAenderung(DependencyObject o, DependencyPropertyChangedEventArgs e)
    {
        if (o is not ListBox lb) return;
        if (!(bool)lb.GetValue(VerdrahtetProperty))
        {
            lb.SetValue(VerdrahtetProperty, true);
            var geplant = false;
            ((INotifyCollectionChanged)lb.Items).CollectionChanged += (_, _) =>
            {
                if (!GetAktiv(lb) || geplant) return;
                geplant = true;
                lb.Dispatcher.BeginInvoke(DispatcherPriority.Background, () =>
                {
                    geplant = false;
                    if (lb.Items.Count > 0) lb.ScrollIntoView(lb.Items[^1]!);
                });
            };
        }
        if ((bool)e.NewValue && lb.Items.Count > 0) lb.ScrollIntoView(lb.Items[^1]!);
    }
}
