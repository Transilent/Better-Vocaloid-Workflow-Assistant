using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Imaging;

namespace VwaDataMigrator
{
    internal sealed class ThemePalette
    {
        public bool Dark;
        public Brush Base, Surface, Raised, InputBg, Border, Text, Muted, Accent, AccentInk, AccentSoft, Success, Error;

        public static ThemePalette Create(bool dark)
        {
            return dark
                ? new ThemePalette
                {
                    Dark = true,
                    Base = BrushFrom("#0E1016"),
                    Surface = BrushFrom("#161923"),
                    Raised = BrushFrom("#1E2332"),
                    InputBg = BrushFrom("#10121A"),
                    Border = BrushFrom("#2A3044"),
                    Text = BrushFrom("#EEEEF5"),
                    Muted = BrushFrom("#98A2B8"),
                    Accent = BrushFrom("#6E9BFF"),
                    AccentInk = BrushFrom("#091020"),
                    AccentSoft = BrushFrom("#1B2846"),
                    Success = BrushFrom("#43C58E"),
                    Error = BrushFrom("#EF6A6A")
                }
                : new ThemePalette
                {
                    Dark = false,
                    Base = BrushFrom("#F3F2F7"),
                    Surface = BrushFrom("#FFFFFF"),
                    Raised = BrushFrom("#EAE8F2"),
                    InputBg = BrushFrom("#F9F8FC"),
                    Border = BrushFrom("#D5D2E0"),
                    Text = BrushFrom("#242233"),
                    Muted = BrushFrom("#64607A"),
                    Accent = BrushFrom("#3B6CE8"),
                    AccentInk = BrushFrom("#FFFFFF"),
                    AccentSoft = BrushFrom("#E9F0FE"),
                    Success = BrushFrom("#1E9B66"),
                    Error = BrushFrom("#D14343")
                };
        }

        private static SolidColorBrush BrushFrom(string hex)
        {
            var b = (SolidColorBrush)(new BrushConverter().ConvertFromString(hex));
            b.Freeze();
            return b;
        }
    }

    internal sealed class MigratorWindow : Window
    {
        private bool _dark = true;
        private ThemePalette _pal = ThemePalette.Create(true);
        private bool _running;
        private bool _done;
        private bool _hadError;
        private double _lastPct;
        private CancellationTokenSource _cts;
        private ScanSummary _scan = new ScanSummary();

        private Border _rootBorder, _headerBar, _footerBar, _pathsCard, _itemsCard, _progressCard;
        private Border _badgeBox, _srcPill, _dstPill, _freePill, _progressTrack, _progressFill;
        private TextBlock _lblAppName, _lblBadge, _lblSubtitle, _lblStepPaths, _lblOldDir, _lblNewDir;
        private TextBlock _lblSrcInfo, _lblDstInfo, _lblFreeSpace, _lblStepItems, _lblStepMode;
        private TextBlock _lblPct, _lblSpeed, _lblStatus, _lblFooter;
        private TextBox _txtOldDir, _txtNewDir;
        private Button _btnRescan, _btnTheme, _btnLang, _btnBrowseOld, _btnBrowseNew, _btnSwap;
        private Button _btnOpenDir, _btnCancel, _btnPrimary;
        private RadioButton _radCopy, _radMove;
        private readonly Dictionary<string, CheckBox> _itemChecks = new Dictionary<string, CheckBox>();
        private readonly Dictionary<string, TextBlock> _itemMetas = new Dictionary<string, TextBlock>();

        private static readonly string[] CategoryKeys = { "config", "jobs", "presets", "accounts", "publish", "optional", "qwen" };

        public MigratorWindow(string initialSource, string initialTarget)
        {
            Width = 820;
            Height = 785;
            MinWidth = 760;
            MinHeight = 700;
            WindowStartupLocation = WindowStartupLocation.CenterScreen;
            FontFamily = new FontFamily("Microsoft YaHei UI, Segoe UI, Yu Gothic UI");
            FontSize = 13;

            BuildUi();

            if (!string.IsNullOrEmpty(initialSource)) _txtOldDir.Text = initialSource;
            if (!string.IsNullOrEmpty(initialTarget))
            {
                _txtNewDir.Text = initialTarget;
            }
            else
            {
                // Auto-detect if user placed VWA-Data-Migrator.exe inside their newly extracted VWA folder
                string exeDir = AppDomain.CurrentDomain.BaseDirectory.TrimEnd('\\');
                var selfProbe = MigrationEngine.InspectInstall(exeDir);
                if (selfProbe.HasLauncher && !string.IsNullOrEmpty(selfProbe.Version))
                    _txtNewDir.Text = exeDir;
            }

            ApplyTheme();
            ApplyLanguage();
            TriggerScan();

            SourceInitialized += delegate { WindowsHelpers.ApplyTitlebarTheme(this, _dark); };
            Closing += delegate { if (_running && _cts != null) _cts.Cancel(); };
        }

        public void SetThemeDark(bool dark)
        {
            _dark = dark;
            ApplyTheme();
            WindowsHelpers.ApplyTitlebarTheme(this, _dark);
        }

        public void SaveScreenshot(string pngPath)
        {
            UpdateLayout();
            double w = ActualWidth > 0 ? ActualWidth : Width;
            double h = ActualHeight > 0 ? ActualHeight : Height;
            var rtb = new RenderTargetBitmap((int)Math.Ceiling(w), (int)Math.Ceiling(h), 96, 96, PixelFormats.Pbgra32);
            rtb.Render(this);
            var encoder = new PngBitmapEncoder();
            encoder.Frames.Add(BitmapFrame.Create(rtb));
            string dir = System.IO.Path.GetDirectoryName(pngPath);
            if (!string.IsNullOrEmpty(dir)) Directory.CreateDirectory(dir);
            using (var fs = File.Create(pngPath)) encoder.Save(fs);
        }

        private void BuildUi()
        {
            _rootBorder = new Border();
            var main = new Grid();
            main.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
            main.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
            main.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
            _rootBorder.Child = main;

            // Header
            _headerBar = new Border { Padding = new Thickness(22, 14, 22, 14), BorderThickness = new Thickness(0, 0, 0, 1) };
            var hdr = new Grid();
            hdr.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
            hdr.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            hdr.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });

            var logo = new Border
            {
                Width = 40, Height = 40, CornerRadius = new CornerRadius(10), Margin = new Thickness(0, 0, 13, 0),
                Background = new LinearGradientBrush(
                    (Color)ColorConverter.ConvertFromString("#43C58E"),
                    (Color)ColorConverter.ConvertFromString("#5D8BFF"),
                    new Point(0, 0), new Point(1, 1))
            };
            logo.Child = new TextBlock
            {
                Text = "⇄", FontSize = 20, FontWeight = FontWeights.Bold, Foreground = Brushes.White,
                HorizontalAlignment = HorizontalAlignment.Center, VerticalAlignment = VerticalAlignment.Center
            };
            hdr.Children.Add(logo);

            var titleStack = new StackPanel { VerticalAlignment = VerticalAlignment.Center };
            var titleTop = new StackPanel { Orientation = Orientation.Horizontal, VerticalAlignment = VerticalAlignment.Center };
            _lblAppName = new TextBlock { FontSize = 16.5, FontWeight = FontWeights.SemiBold, VerticalAlignment = VerticalAlignment.Center };
            _badgeBox = new Border { CornerRadius = new CornerRadius(9), Padding = new Thickness(8, 2, 8, 2), Margin = new Thickness(10, 0, 0, 0), VerticalAlignment = VerticalAlignment.Center };
            _lblBadge = new TextBlock { FontSize = 11, FontWeight = FontWeights.SemiBold };
            _badgeBox.Child = _lblBadge;
            titleTop.Children.Add(_lblAppName);
            titleTop.Children.Add(_badgeBox);
            _lblSubtitle = new TextBlock { FontSize = 12, Margin = new Thickness(0, 3, 0, 0), TextTrimming = TextTrimming.CharacterEllipsis };
            titleStack.Children.Add(titleTop);
            titleStack.Children.Add(_lblSubtitle);
            Grid.SetColumn(titleStack, 1);
            hdr.Children.Add(titleStack);

            var hdrBtns = new StackPanel { Orientation = Orientation.Horizontal, VerticalAlignment = VerticalAlignment.Center };
            _btnRescan = MakePillButton(TriggerScan);
            _btnTheme = MakePillButton(delegate { _dark = !_dark; ApplyTheme(); WindowsHelpers.ApplyTitlebarTheme(this, _dark); });
            _btnLang = MakePillButton(CycleLanguage);
            _btnRescan.Margin = new Thickness(0, 0, 6, 0);
            _btnTheme.Margin = new Thickness(0, 0, 6, 0);
            hdrBtns.Children.Add(_btnRescan);
            hdrBtns.Children.Add(_btnTheme);
            hdrBtns.Children.Add(_btnLang);
            Grid.SetColumn(hdrBtns, 2);
            hdr.Children.Add(hdrBtns);
            _headerBar.Child = hdr;
            main.Children.Add(_headerBar);

            // Body
            var scroll = new ScrollViewer { VerticalScrollBarVisibility = ScrollBarVisibility.Auto, Padding = new Thickness(22, 14, 22, 12) };
            var body = new StackPanel();
            scroll.Content = body;
            Grid.SetRow(scroll, 1);
            main.Children.Add(scroll);

            // Card 1: Source & Target Paths
            _pathsCard = new Border { CornerRadius = new CornerRadius(12), BorderThickness = new Thickness(1), Padding = new Thickness(16, 13, 16, 13), Margin = new Thickness(0, 0, 0, 12) };
            var pStack = new StackPanel();
            _pathsCard.Child = pStack;

            var pHead = new DockPanel { Margin = new Thickness(0, 0, 0, 10) };
            _lblStepPaths = new TextBlock { FontSize = 13.5, FontWeight = FontWeights.SemiBold, VerticalAlignment = VerticalAlignment.Center };
            _btnSwap = MakePillButton(OnSwapDirs);
            _btnSwap.Padding = new Thickness(10, 4, 10, 4);
            DockPanel.SetDock(_btnSwap, Dock.Right);
            pHead.Children.Add(_btnSwap);
            pHead.Children.Add(_lblStepPaths);
            pStack.Children.Add(pHead);

            // Old dir
            var oldRowHead = new DockPanel { Margin = new Thickness(0, 0, 0, 4) };
            _lblOldDir = new TextBlock { FontSize = 12, VerticalAlignment = VerticalAlignment.Center };
            _srcPill = new Border { CornerRadius = new CornerRadius(7), Padding = new Thickness(8, 2, 8, 2), HorizontalAlignment = HorizontalAlignment.Right };
            _lblSrcInfo = new TextBlock { FontSize = 11.5 };
            _srcPill.Child = _lblSrcInfo;
            DockPanel.SetDock(_srcPill, Dock.Right);
            oldRowHead.Children.Add(_srcPill);
            oldRowHead.Children.Add(_lblOldDir);
            pStack.Children.Add(oldRowHead);

            _txtOldDir = new TextBox { Padding = new Thickness(10, 6, 10, 6), VerticalContentAlignment = VerticalAlignment.Center };
            _txtOldDir.TextChanged += delegate { TriggerScan(); };
            _btnBrowseOld = MakePillButton(OnBrowseOld);
            pStack.Children.Add(MakePathPickerRow(_txtOldDir, _btnBrowseOld));

            // New dir
            var newRowHead = new DockPanel { Margin = new Thickness(0, 8, 0, 4) };
            _lblNewDir = new TextBlock { FontSize = 12, VerticalAlignment = VerticalAlignment.Center };
            var dstPills = new StackPanel { Orientation = Orientation.Horizontal, HorizontalAlignment = HorizontalAlignment.Right };
            _freePill = new Border { CornerRadius = new CornerRadius(7), Padding = new Thickness(8, 2, 8, 2), Margin = new Thickness(0, 0, 6, 0) };
            _lblFreeSpace = new TextBlock { FontSize = 11.5 };
            _freePill.Child = _lblFreeSpace;
            _dstPill = new Border { CornerRadius = new CornerRadius(7), Padding = new Thickness(8, 2, 8, 2) };
            _lblDstInfo = new TextBlock { FontSize = 11.5 };
            _dstPill.Child = _lblDstInfo;
            dstPills.Children.Add(_freePill);
            dstPills.Children.Add(_dstPill);
            DockPanel.SetDock(dstPills, Dock.Right);
            newRowHead.Children.Add(dstPills);
            newRowHead.Children.Add(_lblNewDir);
            pStack.Children.Add(newRowHead);

            _txtNewDir = new TextBox { Padding = new Thickness(10, 6, 10, 6), VerticalContentAlignment = VerticalAlignment.Center };
            _txtNewDir.TextChanged += delegate { TriggerScan(); };
            _btnBrowseNew = MakePillButton(OnBrowseNew);
            pStack.Children.Add(MakePathPickerRow(_txtNewDir, _btnBrowseNew));
            body.Children.Add(_pathsCard);

            // Card 2: Migration Items + Mode
            _itemsCard = new Border { CornerRadius = new CornerRadius(12), BorderThickness = new Thickness(1), Padding = new Thickness(16, 13, 16, 13), Margin = new Thickness(0, 0, 0, 12) };
            var iStack = new StackPanel();
            _itemsCard.Child = iStack;

            _lblStepItems = new TextBlock { FontSize = 13.5, FontWeight = FontWeights.SemiBold, Margin = new Thickness(0, 0, 0, 8) };
            iStack.Children.Add(_lblStepItems);

            foreach (string key in CategoryKeys)
            {
                var row = new DockPanel { Margin = new Thickness(0, 3, 0, 3) };
                var meta = new TextBlock { FontSize = 11.5, VerticalAlignment = VerticalAlignment.Center, Margin = new Thickness(10, 0, 0, 0) };
                DockPanel.SetDock(meta, Dock.Right);
                var chk = new CheckBox { IsChecked = true, VerticalContentAlignment = VerticalAlignment.Center };
                row.Children.Add(meta);
                row.Children.Add(chk);
                iStack.Children.Add(row);
                _itemChecks[key] = chk;
                _itemMetas[key] = meta;
            }

            _lblStepMode = new TextBlock { FontSize = 13, FontWeight = FontWeights.SemiBold, Margin = new Thickness(0, 11, 0, 6) };
            iStack.Children.Add(_lblStepMode);
            _radCopy = new RadioButton { GroupName = "TransferMode", IsChecked = true, Margin = new Thickness(0, 2, 0, 4) };
            _radMove = new RadioButton { GroupName = "TransferMode", IsChecked = false, Margin = new Thickness(0, 2, 0, 0) };
            iStack.Children.Add(_radCopy);
            iStack.Children.Add(_radMove);
            body.Children.Add(_itemsCard);

            // Card 3: Progress
            _progressCard = new Border { CornerRadius = new CornerRadius(12), BorderThickness = new Thickness(1), Padding = new Thickness(16, 13, 16, 13) };
            var prStack = new StackPanel();
            _progressCard.Child = prStack;

            _progressTrack = new Border { Height = 10, CornerRadius = new CornerRadius(5), ClipToBounds = true, Margin = new Thickness(0, 0, 0, 8) };
            _progressFill = new Border { Height = 10, CornerRadius = new CornerRadius(5), HorizontalAlignment = HorizontalAlignment.Left, Width = 0 };
            _progressTrack.Child = _progressFill;
            _progressTrack.SizeChanged += delegate { UpdateProgressBar(_lastPct); };
            prStack.Children.Add(_progressTrack);

            var metaRow = new DockPanel { Margin = new Thickness(0, 0, 0, 4) };
            _lblSpeed = new TextBlock { FontSize = 12 };
            DockPanel.SetDock(_lblSpeed, Dock.Right);
            _lblPct = new TextBlock { FontSize = 12, Text = "0%" };
            metaRow.Children.Add(_lblSpeed);
            metaRow.Children.Add(_lblPct);
            prStack.Children.Add(metaRow);

            _lblStatus = new TextBlock { FontSize = 12.5, TextWrapping = TextWrapping.Wrap };
            prStack.Children.Add(_lblStatus);
            body.Children.Add(_progressCard);

            // Footer
            _footerBar = new Border { Padding = new Thickness(22, 12, 22, 14), BorderThickness = new Thickness(0, 1, 0, 0) };
            var ftr = new DockPanel();
            var rightBtns = new StackPanel { Orientation = Orientation.Horizontal, VerticalAlignment = VerticalAlignment.Center };
            _btnOpenDir = MakePillButton(delegate { WindowsHelpers.OpenFolder(_txtNewDir.Text.Trim()); });
            _btnOpenDir.Visibility = Visibility.Collapsed;
            _btnOpenDir.Margin = new Thickness(0, 0, 8, 0);
            _btnCancel = MakePillButton(OnCancelClicked);
            _btnCancel.Visibility = Visibility.Collapsed;
            _btnCancel.Margin = new Thickness(0, 0, 8, 0);
            _btnPrimary = MakePrimaryButton(OnPrimaryClicked);
            rightBtns.Children.Add(_btnOpenDir);
            rightBtns.Children.Add(_btnCancel);
            rightBtns.Children.Add(_btnPrimary);
            DockPanel.SetDock(rightBtns, Dock.Right);
            _lblFooter = new TextBlock { FontSize = 11.5, VerticalAlignment = VerticalAlignment.Center };
            ftr.Children.Add(rightBtns);
            ftr.Children.Add(_lblFooter);
            _footerBar.Child = ftr;
            Grid.SetRow(_footerBar, 2);
            main.Children.Add(_footerBar);

            Content = _rootBorder;
        }

        private Grid MakePathPickerRow(TextBox box, Button btn)
        {
            var g = new Grid();
            g.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            g.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(8) });
            g.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
            btn.Padding = new Thickness(14, 6, 14, 6);
            Grid.SetColumn(box, 0);
            Grid.SetColumn(btn, 2);
            g.Children.Add(box);
            g.Children.Add(btn);
            return g;
        }

        private static ControlTemplate MakeRoundedButtonTemplate(double radius)
        {
            var tmpl = new ControlTemplate(typeof(Button));
            var bd = new FrameworkElementFactory(typeof(Border), "bd");
            bd.SetValue(Border.CornerRadiusProperty, new CornerRadius(radius));
            bd.SetBinding(Border.BackgroundProperty, new System.Windows.Data.Binding("Background") { RelativeSource = new System.Windows.Data.RelativeSource(System.Windows.Data.RelativeSourceMode.TemplatedParent) });
            bd.SetBinding(Border.BorderBrushProperty, new System.Windows.Data.Binding("BorderBrush") { RelativeSource = new System.Windows.Data.RelativeSource(System.Windows.Data.RelativeSourceMode.TemplatedParent) });
            bd.SetBinding(Border.BorderThicknessProperty, new System.Windows.Data.Binding("BorderThickness") { RelativeSource = new System.Windows.Data.RelativeSource(System.Windows.Data.RelativeSourceMode.TemplatedParent) });
            bd.SetBinding(Border.PaddingProperty, new System.Windows.Data.Binding("Padding") { RelativeSource = new System.Windows.Data.RelativeSource(System.Windows.Data.RelativeSourceMode.TemplatedParent) });
            var cp = new FrameworkElementFactory(typeof(ContentPresenter));
            cp.SetValue(ContentPresenter.HorizontalAlignmentProperty, HorizontalAlignment.Center);
            cp.SetValue(ContentPresenter.VerticalAlignmentProperty, VerticalAlignment.Center);
            bd.AppendChild(cp);
            tmpl.VisualTree = bd;

            var hover = new Trigger { Property = UIElement.IsMouseOverProperty, Value = true };
            hover.Setters.Add(new Setter(UIElement.OpacityProperty, 0.88, "bd"));
            tmpl.Triggers.Add(hover);
            var dis = new Trigger { Property = UIElement.IsEnabledProperty, Value = false };
            dis.Setters.Add(new Setter(UIElement.OpacityProperty, 0.45, "bd"));
            tmpl.Triggers.Add(dis);
            return tmpl;
        }

        private Button MakePillButton(Action onClick)
        {
            var b = new Button { Padding = new Thickness(12, 6, 12, 6), Cursor = Cursors.Hand, BorderThickness = new Thickness(1), FontSize = 12, Template = MakeRoundedButtonTemplate(8) };
            b.Click += delegate { onClick(); };
            return b;
        }

        private Button MakePrimaryButton(Action onClick)
        {
            var b = new Button { Padding = new Thickness(22, 9, 22, 9), Cursor = Cursors.Hand, BorderThickness = new Thickness(0), FontSize = 13, FontWeight = FontWeights.SemiBold, Template = MakeRoundedButtonTemplate(9) };
            b.Click += delegate { onClick(); };
            return b;
        }

        private void CycleLanguage()
        {
            if (I18n.CurrentLanguage == "zh-CN") I18n.CurrentLanguage = "en";
            else if (I18n.CurrentLanguage == "en") I18n.CurrentLanguage = "ja";
            else I18n.CurrentLanguage = "zh-CN";
            ApplyLanguage();
            RenderScanSummary();
        }

        private void ApplyTheme()
        {
            _pal = ThemePalette.Create(_dark);
            Background = _pal.Base;
            Foreground = _pal.Text;
            _rootBorder.Background = _pal.Base;
            _headerBar.Background = _pal.Surface; _headerBar.BorderBrush = _pal.Border;
            _footerBar.Background = _pal.Surface; _footerBar.BorderBrush = _pal.Border;
            _pathsCard.Background = _pal.Surface; _pathsCard.BorderBrush = _pal.Border;
            _itemsCard.Background = _pal.Surface; _itemsCard.BorderBrush = _pal.Border;
            _progressCard.Background = _pal.Surface; _progressCard.BorderBrush = _pal.Border;

            _lblAppName.Foreground = _pal.Text;
            _lblSubtitle.Foreground = _pal.Muted;
            _badgeBox.Background = _pal.AccentSoft; _lblBadge.Foreground = _pal.Accent;
            _lblStepPaths.Foreground = _pal.Text;
            _lblOldDir.Foreground = _pal.Muted;
            _lblNewDir.Foreground = _pal.Muted;
            _srcPill.Background = _pal.Raised; _lblSrcInfo.Foreground = _pal.Muted;
            _dstPill.Background = _pal.Raised; _lblDstInfo.Foreground = _pal.Muted;
            _freePill.Background = _pal.Raised; _lblFreeSpace.Foreground = _pal.Muted;
            _lblStepItems.Foreground = _pal.Text;
            _lblStepMode.Foreground = _pal.Text;

            StyleTextBox(_txtOldDir);
            StyleTextBox(_txtNewDir);
            StylePillButton(_btnRescan); StylePillButton(_btnTheme); StylePillButton(_btnLang);
            StylePillButton(_btnBrowseOld); StylePillButton(_btnBrowseNew); StylePillButton(_btnSwap);
            StylePillButton(_btnOpenDir); StylePillButton(_btnCancel);

            foreach (var kv in _itemChecks) kv.Value.Foreground = _pal.Text;
            foreach (var kv in _itemMetas) kv.Value.Foreground = _pal.Muted;
            _radCopy.Foreground = _pal.Text;
            _radMove.Foreground = _pal.Text;

            _progressTrack.Background = _pal.InputBg;
            _progressFill.Background = _hadError ? _pal.Error : (_done ? _pal.Success : _pal.Accent);
            _lblPct.Foreground = _pal.Muted;
            _lblSpeed.Foreground = _pal.Muted;
            _lblStatus.Foreground = _hadError ? _pal.Error : (_done ? _pal.Success : _pal.Text);
            _lblFooter.Foreground = _pal.Muted;
            _btnPrimary.Background = _done ? _pal.Success : _pal.Accent;
            _btnPrimary.Foreground = _pal.AccentInk;
            _btnTheme.Content = _dark ? "☀" : "☾";
        }

        private void StyleTextBox(TextBox tb)
        {
            tb.Background = _pal.InputBg;
            tb.Foreground = _pal.Text;
            tb.BorderBrush = _pal.Border;
            tb.CaretBrush = _pal.Accent;
            var tmpl = new ControlTemplate(typeof(TextBox));
            var bd = new FrameworkElementFactory(typeof(Border));
            bd.SetValue(Border.CornerRadiusProperty, new CornerRadius(8));
            bd.SetValue(Border.BackgroundProperty, _pal.InputBg);
            bd.SetValue(Border.BorderBrushProperty, _pal.Border);
            bd.SetValue(Border.BorderThicknessProperty, new Thickness(1));
            bd.SetValue(Border.PaddingProperty, new Thickness(4, 2, 4, 2));
            var host = new FrameworkElementFactory(typeof(ScrollViewer), "PART_ContentHost");
            host.SetValue(FrameworkElement.VerticalAlignmentProperty, VerticalAlignment.Center);
            bd.AppendChild(host);
            tmpl.VisualTree = bd;
            tb.Template = tmpl;
        }

        private void StylePillButton(Button b)
        {
            b.Background = _pal.Raised;
            b.Foreground = _pal.Text;
            b.BorderBrush = _pal.Border;
        }

        private void ApplyLanguage()
        {
            Title = I18n.T("window_title");
            _lblAppName.Text = I18n.T("app_name");
            _lblBadge.Text = I18n.T("badge_tool");
            _lblSubtitle.Text = I18n.T("subtitle");
            _lblStepPaths.Text = I18n.T("step_paths");
            _lblOldDir.Text = I18n.T("old_dir");
            _lblNewDir.Text = I18n.T("new_dir");
            _txtOldDir.ToolTip = I18n.T("old_placeholder");
            _txtNewDir.ToolTip = I18n.T("new_placeholder");
            _btnBrowseOld.Content = I18n.T("browse");
            _btnBrowseNew.Content = I18n.T("browse");
            _btnSwap.Content = I18n.T("swap_dirs");
            _btnRescan.Content = I18n.T("btn_rescan");
            _btnLang.Content = I18n.CurrentLanguage == "zh-CN" ? "中 / EN" : (I18n.CurrentLanguage == "en" ? "EN / 日" : "日 / 中");
            _lblStepItems.Text = I18n.T("step_items");
            foreach (string k in CategoryKeys) _itemChecks[k].Content = I18n.T("item_" + k);
            _lblStepMode.Text = I18n.T("step_mode");
            _radCopy.Content = I18n.T("mode_copy");
            _radMove.Content = I18n.T("mode_move");
            _btnCancel.Content = I18n.T("btn_cancel");
            _btnOpenDir.Content = I18n.T("btn_open_dir");
            _lblFooter.Text = I18n.T("footer_info");
            if (!_running && !_done && !_hadError) _lblStatus.Text = I18n.T("status_ready");
            UpdateButtons();
        }

        private void UpdateButtons()
        {
            bool locked = _running;
            _txtOldDir.IsEnabled = !locked;
            _txtNewDir.IsEnabled = !locked;
            _btnBrowseOld.IsEnabled = !locked;
            _btnBrowseNew.IsEnabled = !locked;
            _btnSwap.IsEnabled = !locked;
            _btnRescan.IsEnabled = !locked;
            _radCopy.IsEnabled = !locked;
            _radMove.IsEnabled = !locked;
            foreach (var kv in _itemChecks) kv.Value.IsEnabled = !locked;

            if (_done)
            {
                _btnPrimary.Content = I18n.T("btn_launch");
                _btnPrimary.Background = _pal.Success;
                _btnPrimary.IsEnabled = true;
                _btnOpenDir.Visibility = Visibility.Visible;
                _btnCancel.Visibility = Visibility.Collapsed;
            }
            else if (_running)
            {
                _btnPrimary.Content = I18n.T("btn_start");
                _btnPrimary.IsEnabled = false;
                _btnCancel.Visibility = Visibility.Visible;
                _btnCancel.IsEnabled = true;
                _btnOpenDir.Visibility = Visibility.Collapsed;
            }
            else
            {
                _btnPrimary.Content = _hadError ? I18n.T("btn_retry") : I18n.T("btn_start");
                _btnPrimary.Background = _pal.Accent;
                _btnPrimary.IsEnabled = true;
                _btnCancel.Visibility = Visibility.Collapsed;
                _btnOpenDir.Visibility = Visibility.Collapsed;
            }
        }

        private int _scanSeq;

        private void TriggerScan()
        {
            int seq = Interlocked.Increment(ref _scanSeq);
            string s = (_txtOldDir.Text ?? "").Trim();
            string t = (_txtNewDir.Text ?? "").Trim();
            Task.Run(delegate
            {
                var summary = MigrationEngine.Scan(s, t);
                Dispatcher.BeginInvoke(new Action(delegate
                {
                    if (seq != _scanSeq) return;
                    _scan = summary;
                    RenderScanSummary();
                }));
            });
        }

        private void RenderScanSummary()
        {
            RenderProbePill(_txtOldDir.Text.Trim(), _scan.SourceInfo, _srcPill, _lblSrcInfo);
            RenderProbePill(_txtNewDir.Text.Trim(), _scan.TargetInfo, _dstPill, _lblDstInfo);

            long free = WindowsHelpers.GetAvailableFreeSpace(_txtNewDir.Text.Trim());
            _lblFreeSpace.Text = free >= 0 ? I18n.F("free_space", WindowsHelpers.FormatBytes(free)) : "—";

            foreach (string k in CategoryKeys)
            {
                CategoryStats st;
                if (!_scan.Categories.TryGetValue(k, out st)) continue;
                if (st.AlreadyInTarget)
                {
                    _itemMetas[k].Text = I18n.T("meta_already_in_target");
                    _itemMetas[k].Foreground = _pal.Success;
                    _itemChecks[k].IsChecked = false;
                }
                else if (st.FileCount == 0)
                {
                    _itemMetas[k].Text = I18n.T("meta_none");
                    _itemMetas[k].Foreground = _pal.Muted;
                }
                else if (k == "config")
                {
                    _itemMetas[k].Text = I18n.T("meta_config_ready");
                    _itemMetas[k].Foreground = _pal.Accent;
                }
                else
                {
                    _itemMetas[k].Text = I18n.F("meta_count_size", st.FileCount, WindowsHelpers.FormatBytes(st.TotalBytes));
                    _itemMetas[k].Foreground = _pal.Accent;
                }
            }
        }

        private void RenderProbePill(string rawPath, InstallProbe p, Border pill, TextBlock lbl)
        {
            if (string.IsNullOrEmpty(rawPath))
            {
                lbl.Text = I18n.T("dir_empty");
                lbl.Foreground = _pal.Muted;
                pill.Background = _pal.Raised;
            }
            else if (!p.Exists)
            {
                lbl.Text = I18n.T("dir_not_found");
                lbl.Foreground = _pal.Error;
                pill.Background = _pal.Raised;
            }
            else if (!string.IsNullOrEmpty(p.Version))
            {
                lbl.Text = I18n.F("dir_detected", p.Version, string.IsNullOrEmpty(p.Variant) ? "VWA" : p.Variant);
                lbl.Foreground = _pal.Success;
                pill.Background = _pal.AccentSoft;
            }
            else if (p.HasConfigOrJobs)
            {
                lbl.Text = I18n.T("dir_legacy_data");
                lbl.Foreground = _pal.Accent;
                pill.Background = _pal.AccentSoft;
            }
            else
            {
                lbl.Text = I18n.T("dir_no_vwa");
                lbl.Foreground = _pal.Error;
                pill.Background = _pal.Raised;
            }
        }

        private void OnBrowseOld()
        {
            string picked = WindowsHelpers.PickFolder(this, I18n.T("old_dir"), _txtOldDir.Text.Trim());
            if (!string.IsNullOrEmpty(picked)) _txtOldDir.Text = picked;
        }

        private void OnBrowseNew()
        {
            string picked = WindowsHelpers.PickFolder(this, I18n.T("new_dir"), _txtNewDir.Text.Trim());
            if (!string.IsNullOrEmpty(picked)) _txtNewDir.Text = picked;
        }

        private void OnSwapDirs()
        {
            string a = _txtOldDir.Text;
            _txtOldDir.Text = _txtNewDir.Text;
            _txtNewDir.Text = a;
        }

        private void OnCancelClicked()
        {
            if (_cts != null && !_cts.IsCancellationRequested)
            {
                _btnCancel.IsEnabled = false;
                _btnCancel.Content = I18n.T("btn_cancelling");
                _cts.Cancel();
            }
        }

        private void UpdateProgressBar(double pct)
        {
            _lastPct = Math.Max(0, Math.Min(100, pct));
            double trackW = _progressTrack.ActualWidth;
            if (trackW > 0) _progressFill.Width = trackW * (_lastPct / 100.0);
        }

        private async void OnPrimaryClicked()
        {
            if (_done)
            {
                WindowsHelpers.LaunchVwa(_txtNewDir.Text.Trim());
                Close();
                return;
            }
            if (_running) return;

            var opts = new MigrationOptions
            {
                SourceDir = _txtOldDir.Text.Trim(),
                TargetDir = _txtNewDir.Text.Trim(),
                MoveMode = _radMove.IsChecked == true,
                IncludeConfig = _itemChecks["config"].IsChecked == true,
                IncludeJobs = _itemChecks["jobs"].IsChecked == true,
                IncludePresets = _itemChecks["presets"].IsChecked == true,
                IncludeAccounts = _itemChecks["accounts"].IsChecked == true,
                IncludePublish = _itemChecks["publish"].IsChecked == true,
                IncludeOptional = _itemChecks["optional"].IsChecked == true,
                IncludeQwen = _itemChecks["qwen"].IsChecked == true
            };

            _running = true;
            _done = false;
            _hadError = false;
            _cts = new CancellationTokenSource();
            _progressFill.Background = _pal.Accent;
            _lblStatus.Foreground = _pal.Text;
            UpdateProgressBar(0);
            UpdateButtons();

            try
            {
                await MigrationEngine.ExecuteAsync(opts, delegate (MigrationProgress p)
                {
                    Dispatcher.BeginInvoke(new Action(delegate
                    {
                        UpdateProgressBar(p.Percent);
                        _lblPct.Text = string.Format(CultureInfo.InvariantCulture, "{0:0.0}% ({1}/{2})", p.Percent, p.DoneFiles, p.TotalFiles);
                        _lblSpeed.Text = p.SpeedBps > 1024 ? (WindowsHelpers.FormatBytes((long)p.SpeedBps) + "/s") : "";
                        _lblStatus.Text = p.Message;
                    }));
                }, _cts.Token);

                _done = true;
                _running = false;
                _progressFill.Background = _pal.Success;
                _lblStatus.Foreground = _pal.Success;
                UpdateProgressBar(100);
                UpdateButtons();
                TriggerScan();
            }
            catch (OperationCanceledException)
            {
                _running = false;
                _hadError = false;
                _lblStatus.Foreground = _pal.Muted;
                _lblStatus.Text = I18n.T("status_cancelled");
                UpdateButtons();
            }
            catch (Exception ex)
            {
                _running = false;
                _hadError = true;
                _progressFill.Background = _pal.Error;
                _lblStatus.Foreground = _pal.Error;
                _lblStatus.Text = I18n.F("status_error", ex.Message);
                UpdateButtons();
            }
            finally
            {
                if (_cts != null) { _cts.Dispose(); _cts = null; }
            }
        }
    }
}
