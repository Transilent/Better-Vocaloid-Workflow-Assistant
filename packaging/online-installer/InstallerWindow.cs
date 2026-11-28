using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Net.Http;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Shapes;

namespace VwaOnlineInstaller
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

    internal sealed class InstallerWindow : Window
    {
        private readonly string _pinnedVersion;
        private readonly string _manifestBase;
        private bool _dark = true;
        private ThemePalette _pal = ThemePalette.Create(true);
        private string _selectedVariant = "full";
        private ReleaseInfo _release;
        private bool _installing;
        private bool _installed;
        private bool _hadError;
        private CancellationTokenSource _cts;

        // UI controls kept as fields for instant i18n & theme updates
        private Border _rootBorder, _headerBar, _footerBar, _fullCard, _jpnCard, _configCard, _progressCard;
        private Border _releasePill, _fullBadgeBox, _jpnBadgeBox, _fullSizeBox, _jpnSizeBox, _freeSpacePill, _upgradeBanner;
        private Ellipse _fullDotOuter, _fullDotInner, _jpnDotOuter, _jpnDotInner;
        private TextBlock _lblAppName, _lblSubtitle, _lblReleasePill, _lblChoose;
        private TextBlock _lblFullTitle, _lblFullBadge, _lblFullDesc, _lblFullSize;
        private TextBlock _lblJpnTitle, _lblJpnBadge, _lblJpnDesc, _lblJpnSize;
        private TextBlock _lblInstallDir, _lblFreeSpace, _lblUpgrade, _lblRoute, _lblProxy;
        private TextBlock _lblPct, _lblSpeed, _lblRoutePill, _lblStatus, _lblFooterInfo;
        private TextBox _txtTargetDir, _txtProxyUrl;
        private ComboBox _cmbRoute, _cmbProxy;
        private CheckBox _chkStartMenu, _chkDesktop, _chkLaunch;
        private Button _btnTheme, _btnLang, _btnRefresh, _btnBrowse, _btnCancel, _btnOpenDir, _btnPrimary, _btnReleases;
        private Border _progressTrack, _progressFill;
        private Border[] _stagePills = new Border[4];
        private TextBlock[] _stageLabels = new TextBlock[4];
        private int _currentStage;

        private static readonly string[] RouteKeys = { "auto", "mirror", "direct", "gitproxy", "gh-proxy", "ghfast" };
        private static readonly string[] RouteI18n = { "route_auto", "route_mirror", "route_direct", "route_gitproxy", "route_ghproxy", "route_ghfast" };
        private static readonly string[] ProxyKeys = { "system", "direct", "custom" };
        private static readonly string[] ProxyI18n = { "proxy_system", "proxy_direct", "proxy_custom" };

        public InstallerWindow(string pinnedVersion, string manifestBase)
        {
            _pinnedVersion = pinnedVersion ?? "";
            _manifestBase = manifestBase ?? "";
            Width = 790;
            Height = 740;
            MinWidth = 720;
            MinHeight = 680;
            WindowStartupLocation = WindowStartupLocation.CenterScreen;
            FontFamily = new FontFamily("Microsoft YaHei UI, Segoe UI, Yu Gothic UI");
            FontSize = 13;

            BuildUi();
            ApplyTheme();
            ApplyLanguage();
            UpdateVariantCards();
            RefreshDirMeta();

            SourceInitialized += delegate { WindowsIntegration.ApplyTitlebarTheme(this, _dark); };
            Loaded += delegate { var _ = RefreshReleaseAsync(); };
            Closing += delegate (object s, System.ComponentModel.CancelEventArgs e)
            {
                if (_installing && _cts != null) _cts.Cancel();
            };
        }

        private void BuildUi()
        {
            _rootBorder = new Border();
            var mainGrid = new Grid();
            mainGrid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto }); // Header
            mainGrid.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) }); // Body
            mainGrid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto }); // Footer
            _rootBorder.Child = mainGrid;

            // --- Header ---
            _headerBar = new Border { Padding = new Thickness(22, 16, 22, 16), BorderThickness = new Thickness(0, 0, 0, 1) };
            var hdrGrid = new Grid();
            hdrGrid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
            hdrGrid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            hdrGrid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });

            var logoBadge = new Border
            {
                Width = 42, Height = 42, CornerRadius = new CornerRadius(11), Margin = new Thickness(0, 0, 14, 0),
                Background = new LinearGradientBrush(
                    (Color)ColorConverter.ConvertFromString("#5D8BFF"),
                    (Color)ColorConverter.ConvertFromString("#8D6BFF"),
                    new Point(0, 0), new Point(1, 1))
            };
            logoBadge.Child = new TextBlock
            {
                Text = "V", FontSize = 22, FontWeight = FontWeights.Bold, Foreground = Brushes.White,
                HorizontalAlignment = HorizontalAlignment.Center, VerticalAlignment = VerticalAlignment.Center
            };
            hdrGrid.Children.Add(logoBadge);

            var titleStack = new StackPanel { VerticalAlignment = VerticalAlignment.Center };
            var titleTop = new StackPanel { Orientation = Orientation.Horizontal, VerticalAlignment = VerticalAlignment.Center };
            _lblAppName = new TextBlock { FontSize = 17, FontWeight = FontWeights.SemiBold, VerticalAlignment = VerticalAlignment.Center };
            _releasePill = new Border { CornerRadius = new CornerRadius(10), Padding = new Thickness(9, 2, 9, 2), Margin = new Thickness(10, 0, 0, 0), VerticalAlignment = VerticalAlignment.Center };
            _lblReleasePill = new TextBlock { FontSize = 11.5, FontWeight = FontWeights.SemiBold };
            _releasePill.Child = _lblReleasePill;
            titleTop.Children.Add(_lblAppName);
            titleTop.Children.Add(_releasePill);
            _lblSubtitle = new TextBlock { FontSize = 12, Margin = new Thickness(0, 3, 0, 0), TextTrimming = TextTrimming.CharacterEllipsis };
            titleStack.Children.Add(titleTop);
            titleStack.Children.Add(_lblSubtitle);
            Grid.SetColumn(titleStack, 1);
            hdrGrid.Children.Add(titleStack);

            var hdrActions = new StackPanel { Orientation = Orientation.Horizontal, VerticalAlignment = VerticalAlignment.Center };
            _btnRefresh = MakePillButton(delegate { var _ = RefreshReleaseAsync(); });
            _btnTheme = MakePillButton(delegate { _dark = !_dark; ApplyTheme(); WindowsIntegration.ApplyTitlebarTheme(this, _dark); });
            _btnLang = MakePillButton(CycleLanguage);
            _btnRefresh.Margin = new Thickness(0, 0, 6, 0);
            _btnTheme.Margin = new Thickness(0, 0, 6, 0);
            hdrActions.Children.Add(_btnRefresh);
            hdrActions.Children.Add(_btnTheme);
            hdrActions.Children.Add(_btnLang);
            Grid.SetColumn(hdrActions, 2);
            hdrGrid.Children.Add(hdrActions);

            _headerBar.Child = hdrGrid;
            mainGrid.Children.Add(_headerBar);

            // --- Scrollable Body ---
            var scroll = new ScrollViewer { VerticalScrollBarVisibility = ScrollBarVisibility.Auto, Padding = new Thickness(22, 16, 22, 14) };
            var body = new StackPanel();
            scroll.Content = body;
            Grid.SetRow(scroll, 1);
            mainGrid.Children.Add(scroll);

            // Section 1: Edition cards
            _lblChoose = new TextBlock { FontSize = 13, FontWeight = FontWeights.SemiBold, Margin = new Thickness(0, 0, 0, 8) };
            body.Children.Add(_lblChoose);

            var cardsGrid = new Grid { Margin = new Thickness(0, 0, 0, 14) };
            cardsGrid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            cardsGrid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(12) });
            cardsGrid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });

            _fullCard = BuildEditionCard("full", out _fullDotOuter, out _fullDotInner, out _lblFullTitle, out _fullBadgeBox, out _lblFullBadge, out _lblFullDesc, out _fullSizeBox, out _lblFullSize);
            _jpnCard = BuildEditionCard("jpn", out _jpnDotOuter, out _jpnDotInner, out _lblJpnTitle, out _jpnBadgeBox, out _lblJpnBadge, out _lblJpnDesc, out _jpnSizeBox, out _lblJpnSize);
            Grid.SetColumn(_fullCard, 0);
            Grid.SetColumn(_jpnCard, 2);
            cardsGrid.Children.Add(_fullCard);
            cardsGrid.Children.Add(_jpnCard);
            body.Children.Add(cardsGrid);

            // Section 2: Install Dir & Network Config Card
            _configCard = new Border { CornerRadius = new CornerRadius(12), BorderThickness = new Thickness(1), Padding = new Thickness(16, 14, 16, 14), Margin = new Thickness(0, 0, 0, 14) };
            var cfgStack = new StackPanel();
            _configCard.Child = cfgStack;

            // Dir Row
            var dirHeader = new DockPanel { Margin = new Thickness(0, 0, 0, 6) };
            _lblInstallDir = new TextBlock { FontWeight = FontWeights.SemiBold, VerticalAlignment = VerticalAlignment.Center };
            _freeSpacePill = new Border { CornerRadius = new CornerRadius(8), Padding = new Thickness(8, 2, 8, 2), HorizontalAlignment = HorizontalAlignment.Right };
            _lblFreeSpace = new TextBlock { FontSize = 11.5 };
            _freeSpacePill.Child = _lblFreeSpace;
            DockPanel.SetDock(_freeSpacePill, Dock.Right);
            dirHeader.Children.Add(_freeSpacePill);
            dirHeader.Children.Add(_lblInstallDir);
            cfgStack.Children.Add(dirHeader);

            var dirRow = new Grid { Margin = new Thickness(0, 0, 0, 8) };
            dirRow.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            dirRow.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(8) });
            dirRow.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
            _txtTargetDir = new TextBox { Text = WindowsIntegration.DefaultInstallDir(), Padding = new Thickness(10, 7, 10, 7), VerticalContentAlignment = VerticalAlignment.Center };
            _txtTargetDir.TextChanged += delegate { RefreshDirMeta(); };
            _btnBrowse = MakePillButton(OnBrowseFolder);
            _btnBrowse.Padding = new Thickness(14, 7, 14, 7);
            Grid.SetColumn(_txtTargetDir, 0);
            Grid.SetColumn(_btnBrowse, 2);
            dirRow.Children.Add(_txtTargetDir);
            dirRow.Children.Add(_btnBrowse);
            cfgStack.Children.Add(dirRow);

            _upgradeBanner = new Border { CornerRadius = new CornerRadius(8), Padding = new Thickness(10, 6, 10, 6), Margin = new Thickness(0, 0, 0, 10), Visibility = Visibility.Collapsed };
            _lblUpgrade = new TextBlock { FontSize = 12, TextWrapping = TextWrapping.Wrap };
            _upgradeBanner.Child = _lblUpgrade;
            cfgStack.Children.Add(_upgradeBanner);

            // Network Row (2 cols)
            var netGrid = new Grid { Margin = new Thickness(0, 2, 0, 10) };
            netGrid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            netGrid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(12) });
            netGrid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });

            var routeCol = new StackPanel();
            _lblRoute = new TextBlock { FontSize = 12, Margin = new Thickness(0, 0, 0, 4) };
            _cmbRoute = new ComboBox { Height = 32 };
            for (int i = 0; i < RouteKeys.Length; i++) _cmbRoute.Items.Add(RouteKeys[i]);
            _cmbRoute.SelectedIndex = 0;
            routeCol.Children.Add(_lblRoute);
            routeCol.Children.Add(_cmbRoute);
            Grid.SetColumn(routeCol, 0);
            netGrid.Children.Add(routeCol);

            var proxyCol = new StackPanel();
            _lblProxy = new TextBlock { FontSize = 12, Margin = new Thickness(0, 0, 0, 4) };
            _cmbProxy = new ComboBox { Height = 32 };
            for (int i = 0; i < ProxyKeys.Length; i++) _cmbProxy.Items.Add(ProxyKeys[i]);
            _cmbProxy.SelectedIndex = 0;
            _cmbProxy.SelectionChanged += delegate
            {
                _txtProxyUrl.Visibility = _cmbProxy.SelectedIndex == 2 ? Visibility.Visible : Visibility.Collapsed;
            };
            proxyCol.Children.Add(_lblProxy);
            proxyCol.Children.Add(_cmbProxy);
            Grid.SetColumn(proxyCol, 2);
            netGrid.Children.Add(proxyCol);
            cfgStack.Children.Add(netGrid);

            _txtProxyUrl = new TextBox { Padding = new Thickness(10, 6, 10, 6), Margin = new Thickness(0, 0, 0, 10), Visibility = Visibility.Collapsed };
            cfgStack.Children.Add(_txtProxyUrl);

            // Options Checkboxes
            var optWrap = new WrapPanel { Margin = new Thickness(0, 2, 0, 0) };
            _chkStartMenu = new CheckBox { IsChecked = true, Margin = new Thickness(0, 0, 18, 4) };
            _chkDesktop = new CheckBox { IsChecked = true, Margin = new Thickness(0, 0, 18, 4) };
            _chkLaunch = new CheckBox { IsChecked = true, Margin = new Thickness(0, 0, 0, 4) };
            optWrap.Children.Add(_chkStartMenu);
            optWrap.Children.Add(_chkDesktop);
            optWrap.Children.Add(_chkLaunch);
            cfgStack.Children.Add(optWrap);
            body.Children.Add(_configCard);

            // Section 3: Progress & 4-Stage Stepper Card
            _progressCard = new Border { CornerRadius = new CornerRadius(12), BorderThickness = new Thickness(1), Padding = new Thickness(16, 14, 16, 14) };
            var progStack = new StackPanel();
            _progressCard.Child = progStack;

            var stageGrid = new Grid { Margin = new Thickness(0, 0, 0, 12) };
            for (int i = 0; i < 4; i++)
            {
                stageGrid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
                var pill = new Border { CornerRadius = new CornerRadius(8), Padding = new Thickness(8, 5, 8, 5), Margin = new Thickness(i == 0 ? 0 : 6, 0, 0, 0) };
                var tb = new TextBlock { FontSize = 11.5, FontWeight = FontWeights.SemiBold, TextAlignment = TextAlignment.Center, TextTrimming = TextTrimming.CharacterEllipsis };
                pill.Child = tb;
                Grid.SetColumn(pill, i);
                stageGrid.Children.Add(pill);
                _stagePills[i] = pill;
                _stageLabels[i] = tb;
            }
            progStack.Children.Add(stageGrid);

            _progressTrack = new Border { Height = 10, CornerRadius = new CornerRadius(5), ClipToBounds = true, Margin = new Thickness(0, 0, 0, 8) };
            _progressFill = new Border { Height = 10, CornerRadius = new CornerRadius(5), HorizontalAlignment = HorizontalAlignment.Left, Width = 0 };
            _progressTrack.Child = _progressFill;
            _progressTrack.SizeChanged += delegate { UpdateProgressBarWidth(_lastPercent); };
            progStack.Children.Add(_progressTrack);

            var metaGrid = new Grid { Margin = new Thickness(0, 0, 0, 6) };
            metaGrid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            metaGrid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
            metaGrid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
            _lblPct = new TextBlock { FontSize = 12, Text = "0%" };
            _lblSpeed = new TextBlock { FontSize = 12, Margin = new Thickness(12, 0, 12, 0) };
            _lblRoutePill = new TextBlock { FontSize = 12 };
            Grid.SetColumn(_lblPct, 0);
            Grid.SetColumn(_lblSpeed, 1);
            Grid.SetColumn(_lblRoutePill, 2);
            metaGrid.Children.Add(_lblPct);
            metaGrid.Children.Add(_lblSpeed);
            metaGrid.Children.Add(_lblRoutePill);
            progStack.Children.Add(metaGrid);

            _lblStatus = new TextBlock { FontSize = 12.5, TextWrapping = TextWrapping.Wrap };
            progStack.Children.Add(_lblStatus);
            body.Children.Add(_progressCard);

            // --- Footer ---
            _footerBar = new Border { Padding = new Thickness(22, 12, 22, 14), BorderThickness = new Thickness(0, 1, 0, 0) };
            var ftrGrid = new Grid();
            ftrGrid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            ftrGrid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });

            var ftrLeft = new StackPanel { Orientation = Orientation.Horizontal, VerticalAlignment = VerticalAlignment.Center };
            _btnReleases = MakeLinkButton(delegate
            {
                WindowsIntegration.OpenUrl(_release != null ? _release.ReleaseUrl : ReleaseCatalog.ReleasesBase);
            });
            _lblFooterInfo = new TextBlock { FontSize = 11.5, VerticalAlignment = VerticalAlignment.Center, Margin = new Thickness(10, 0, 0, 0) };
            ftrLeft.Children.Add(_btnReleases);
            ftrLeft.Children.Add(_lblFooterInfo);
            ftrGrid.Children.Add(ftrLeft);

            var ftrRight = new StackPanel { Orientation = Orientation.Horizontal, VerticalAlignment = VerticalAlignment.Center };
            _btnOpenDir = MakePillButton(delegate { WindowsIntegration.OpenFolder(_txtTargetDir.Text.Trim()); });
            _btnOpenDir.Visibility = Visibility.Collapsed;
            _btnOpenDir.Margin = new Thickness(0, 0, 8, 0);
            _btnCancel = MakePillButton(OnCancelClicked);
            _btnCancel.Visibility = Visibility.Collapsed;
            _btnCancel.Margin = new Thickness(0, 0, 8, 0);
            _btnPrimary = MakePrimaryButton(OnPrimaryClicked);
            ftrRight.Children.Add(_btnOpenDir);
            ftrRight.Children.Add(_btnCancel);
            ftrRight.Children.Add(_btnPrimary);
            Grid.SetColumn(ftrRight, 1);
            ftrGrid.Children.Add(ftrRight);

            _footerBar.Child = ftrGrid;
            Grid.SetRow(_footerBar, 2);
            mainGrid.Children.Add(_footerBar);

            Content = _rootBorder;
        }

        private double _lastPercent;

        private Border BuildEditionCard(
            string variant,
            out Ellipse dotOuter, out Ellipse dotInner,
            out TextBlock lblTitle, out Border badgeBox, out TextBlock lblBadge,
            out TextBlock lblDesc, out Border sizeBox, out TextBlock lblSize)
        {
            var card = new Border
            {
                CornerRadius = new CornerRadius(12),
                BorderThickness = new Thickness(1.5),
                Padding = new Thickness(15, 13, 15, 13),
                Cursor = Cursors.Hand
            };
            card.MouseLeftButtonUp += delegate
            {
                if (_installing) return;
                _selectedVariant = variant;
                UpdateVariantCards();
            };

            var stack = new StackPanel();
            var top = new DockPanel { Margin = new Thickness(0, 0, 0, 8) };

            var radioGrid = new Grid { Width = 18, Height = 18, Margin = new Thickness(0, 0, 9, 0), VerticalAlignment = VerticalAlignment.Center };
            dotOuter = new Ellipse { Width = 18, Height = 18, StrokeThickness = 2 };
            dotInner = new Ellipse { Width = 8, Height = 8, HorizontalAlignment = HorizontalAlignment.Center, VerticalAlignment = VerticalAlignment.Center };
            radioGrid.Children.Add(dotOuter);
            radioGrid.Children.Add(dotInner);

            lblTitle = new TextBlock { FontSize = 15, FontWeight = FontWeights.SemiBold, VerticalAlignment = VerticalAlignment.Center };
            badgeBox = new Border { CornerRadius = new CornerRadius(9), Padding = new Thickness(8, 2, 8, 2), HorizontalAlignment = HorizontalAlignment.Right, VerticalAlignment = VerticalAlignment.Center };
            lblBadge = new TextBlock { FontSize = 11, FontWeight = FontWeights.SemiBold };
            badgeBox.Child = lblBadge;

            DockPanel.SetDock(radioGrid, Dock.Left);
            DockPanel.SetDock(badgeBox, Dock.Right);
            top.Children.Add(radioGrid);
            top.Children.Add(badgeBox);
            top.Children.Add(lblTitle);
            stack.Children.Add(top);

            lblDesc = new TextBlock { FontSize = 12, TextWrapping = TextWrapping.Wrap, LineHeight = 18, MinHeight = 54, Margin = new Thickness(0, 0, 0, 10) };
            stack.Children.Add(lblDesc);

            sizeBox = new Border { CornerRadius = new CornerRadius(7), Padding = new Thickness(9, 4, 9, 4), HorizontalAlignment = HorizontalAlignment.Left };
            lblSize = new TextBlock { FontSize = 11.5, FontWeight = FontWeights.SemiBold };
            sizeBox.Child = lblSize;
            stack.Children.Add(sizeBox);

            card.Child = stack;
            return card;
        }

        private static ControlTemplate MakeRoundedButtonTemplate(double radius)
        {
            var tmpl = new ControlTemplate(typeof(Button));
            var borderFactory = new FrameworkElementFactory(typeof(Border), "bd");
            borderFactory.SetValue(Border.CornerRadiusProperty, new CornerRadius(radius));
            borderFactory.SetBinding(Border.BackgroundProperty, new System.Windows.Data.Binding("Background") { RelativeSource = new System.Windows.Data.RelativeSource(System.Windows.Data.RelativeSourceMode.TemplatedParent) });
            borderFactory.SetBinding(Border.BorderBrushProperty, new System.Windows.Data.Binding("BorderBrush") { RelativeSource = new System.Windows.Data.RelativeSource(System.Windows.Data.RelativeSourceMode.TemplatedParent) });
            borderFactory.SetBinding(Border.BorderThicknessProperty, new System.Windows.Data.Binding("BorderThickness") { RelativeSource = new System.Windows.Data.RelativeSource(System.Windows.Data.RelativeSourceMode.TemplatedParent) });
            borderFactory.SetBinding(Border.PaddingProperty, new System.Windows.Data.Binding("Padding") { RelativeSource = new System.Windows.Data.RelativeSource(System.Windows.Data.RelativeSourceMode.TemplatedParent) });

            var presenter = new FrameworkElementFactory(typeof(ContentPresenter));
            presenter.SetValue(ContentPresenter.HorizontalAlignmentProperty, HorizontalAlignment.Center);
            presenter.SetValue(ContentPresenter.VerticalAlignmentProperty, VerticalAlignment.Center);
            borderFactory.AppendChild(presenter);
            tmpl.VisualTree = borderFactory;

            var hoverTrigger = new Trigger { Property = UIElement.IsMouseOverProperty, Value = true };
            hoverTrigger.Setters.Add(new Setter(UIElement.OpacityProperty, 0.88, "bd"));
            tmpl.Triggers.Add(hoverTrigger);

            var disabledTrigger = new Trigger { Property = UIElement.IsEnabledProperty, Value = false };
            disabledTrigger.Setters.Add(new Setter(UIElement.OpacityProperty, 0.45, "bd"));
            tmpl.Triggers.Add(disabledTrigger);
            return tmpl;
        }

        private Button MakePillButton(Action onClick)
        {
            var btn = new Button
            {
                Padding = new Thickness(12, 6, 12, 6),
                Cursor = Cursors.Hand,
                BorderThickness = new Thickness(1),
                FontSize = 12,
                Template = MakeRoundedButtonTemplate(8)
            };
            btn.Click += delegate { onClick(); };
            return btn;
        }

        private Button MakePrimaryButton(Action onClick)
        {
            var btn = new Button
            {
                Padding = new Thickness(22, 9, 22, 9),
                Cursor = Cursors.Hand,
                BorderThickness = new Thickness(0),
                FontSize = 13,
                FontWeight = FontWeights.SemiBold,
                Template = MakeRoundedButtonTemplate(9)
            };
            btn.Click += delegate { onClick(); };
            return btn;
        }

        private Button MakeLinkButton(Action onClick)
        {
            var btn = new Button
            {
                Padding = new Thickness(4, 2, 4, 2),
                Cursor = Cursors.Hand,
                BorderThickness = new Thickness(0),
                Background = Brushes.Transparent,
                FontSize = 12,
                FontWeight = FontWeights.SemiBold,
                Template = MakeRoundedButtonTemplate(4)
            };
            btn.Click += delegate { onClick(); };
            return btn;
        }

        private void CycleLanguage()
        {
            if (I18n.CurrentLanguage == "zh-CN") I18n.CurrentLanguage = "en";
            else if (I18n.CurrentLanguage == "en") I18n.CurrentLanguage = "ja";
            else I18n.CurrentLanguage = "zh-CN";
            ApplyLanguage();
            UpdateReleaseDisplay();
            RefreshDirMeta();
        }

        private void ApplyTheme()
        {
            _pal = ThemePalette.Create(_dark);
            Background = _pal.Base;
            Foreground = _pal.Text;
            _rootBorder.Background = _pal.Base;
            _headerBar.Background = _pal.Surface;
            _headerBar.BorderBrush = _pal.Border;
            _footerBar.Background = _pal.Surface;
            _footerBar.BorderBrush = _pal.Border;
            _configCard.Background = _pal.Surface;
            _configCard.BorderBrush = _pal.Border;
            _progressCard.Background = _pal.Surface;
            _progressCard.BorderBrush = _pal.Border;

            StyleComboBox(_cmbRoute);
            StyleComboBox(_cmbProxy);

            _lblAppName.Foreground = _pal.Text;
            _lblSubtitle.Foreground = _pal.Muted;
            _releasePill.Background = _pal.AccentSoft;
            _lblReleasePill.Foreground = _pal.Accent;
            _lblChoose.Foreground = _pal.Text;
            _lblInstallDir.Foreground = _pal.Text;
            _lblRoute.Foreground = _pal.Muted;
            _lblProxy.Foreground = _pal.Muted;
            _freeSpacePill.Background = _pal.Raised;
            _lblFreeSpace.Foreground = _pal.Muted;
            _upgradeBanner.Background = _pal.AccentSoft;
            _lblUpgrade.Foreground = _pal.Accent;

            StyleTextBox(_txtTargetDir);
            StyleTextBox(_txtProxyUrl);
            StylePillButton(_btnRefresh);
            StylePillButton(_btnTheme);
            StylePillButton(_btnLang);
            StylePillButton(_btnBrowse);
            StylePillButton(_btnCancel);
            StylePillButton(_btnOpenDir);

            _btnPrimary.Background = _installed ? _pal.Success : _pal.Accent;
            _btnPrimary.Foreground = _pal.AccentInk;
            _btnReleases.Foreground = _pal.Accent;
            _lblFooterInfo.Foreground = _pal.Muted;

            _chkStartMenu.Foreground = _pal.Text;
            _chkDesktop.Foreground = _pal.Text;
            _chkLaunch.Foreground = _pal.Text;

            _progressTrack.Background = _pal.InputBg;
            _progressFill.Background = _hadError ? _pal.Error : (_installed ? _pal.Success : _pal.Accent);
            _lblPct.Foreground = _pal.Muted;
            _lblSpeed.Foreground = _pal.Muted;
            _lblRoutePill.Foreground = _pal.Muted;
            _lblStatus.Foreground = _hadError ? _pal.Error : (_installed ? _pal.Success : _pal.Text);

            _btnTheme.Content = _dark ? "☀" : "☾";
            UpdateVariantCards();
            UpdateStagePills(_currentStage);
        }

        private void StyleTextBox(TextBox tb)
        {
            tb.Background = _pal.InputBg;
            tb.Foreground = _pal.Text;
            tb.BorderBrush = _pal.Border;
            tb.CaretBrush = _pal.Accent;
            tb.BorderThickness = new Thickness(1);
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

        private void StyleComboBox(ComboBox cb)
        {
            cb.Background = _pal.InputBg;
            cb.Foreground = _pal.Text;
            cb.BorderBrush = _pal.Border;
            var itemStyle = new Style(typeof(ComboBoxItem));
            itemStyle.Setters.Add(new Setter(Control.BackgroundProperty, _pal.Surface));
            itemStyle.Setters.Add(new Setter(Control.ForegroundProperty, _pal.Text));
            itemStyle.Setters.Add(new Setter(Control.PaddingProperty, new Thickness(10, 6, 10, 6)));
            var hover = new Trigger { Property = ComboBoxItem.IsHighlightedProperty, Value = true };
            hover.Setters.Add(new Setter(Control.BackgroundProperty, _pal.AccentSoft));
            hover.Setters.Add(new Setter(Control.ForegroundProperty, _pal.Accent));
            itemStyle.Triggers.Add(hover);
            cb.ItemContainerStyle = itemStyle;

            // Clean XAML ControlTemplate for flat dark/light rounded ComboBox
            string xaml =
                "<ControlTemplate xmlns='http://schemas.microsoft.com/winfx/2006/xaml/presentation' " +
                "xmlns:x='http://schemas.microsoft.com/winfx/2006/xaml' TargetType='ComboBox'>" +
                "<Grid>" +
                "<ToggleButton Name='ToggleButton' Focusable='False' ClickMode='Press' " +
                "IsChecked='{Binding Path=IsDropDownOpen, Mode=TwoWay, RelativeSource={RelativeSource TemplatedParent}}'>" +
                "<ToggleButton.Template><ControlTemplate TargetType='ToggleButton'>" +
                "<Border CornerRadius='8' Background='" + (_dark ? "#10121A" : "#F9F8FC") + "' " +
                "BorderBrush='" + (_dark ? "#2A3044" : "#D5D2E0") + "' BorderThickness='1'>" +
                "<Grid><Grid.ColumnDefinitions><ColumnDefinition Width='*'/><ColumnDefinition Width='28'/></Grid.ColumnDefinitions>" +
                "<TextBlock Grid.Column='1' Text='▾' HorizontalAlignment='Center' VerticalAlignment='Center' " +
                "Foreground='" + (_dark ? "#98A2B8" : "#64607A") + "' FontSize='12'/>" +
                "</Grid></Border></ControlTemplate></ToggleButton.Template></ToggleButton>" +
                "<ContentPresenter Name='ContentSite' IsHitTestVisible='False' Content='{TemplateBinding SelectionBoxItem}' " +
                "ContentTemplate='{TemplateBinding SelectionBoxItemTemplate}' Margin='11,0,30,0' VerticalAlignment='Center' HorizontalAlignment='Left'/>" +
                "<Popup Name='Popup' Placement='Bottom' IsOpen='{TemplateBinding IsDropDownOpen}' AllowsTransparency='True' Focusable='False' PopupAnimation='Fade'>" +
                "<Border MinWidth='{TemplateBinding ActualWidth}' MaxHeight='{TemplateBinding MaxDropDownHeight}' Margin='0,4,0,0' " +
                "CornerRadius='8' Background='" + (_dark ? "#161923" : "#FFFFFF") + "' " +
                "BorderBrush='" + (_dark ? "#2A3044" : "#D5D2E0") + "' BorderThickness='1' Padding='2'>" +
                "<ScrollViewer><StackPanel IsItemsHost='True'/></ScrollViewer></Border></Popup>" +
                "</Grid></ControlTemplate>";
            cb.Template = (ControlTemplate)System.Windows.Markup.XamlReader.Parse(xaml);
        }

        private void StylePillButton(Button btn)
        {
            btn.Background = _pal.Raised;
            btn.Foreground = _pal.Text;
            btn.BorderBrush = _pal.Border;
        }

        private void UpdateVariantCards()
        {
            StyleCard(_fullCard, _fullDotOuter, _fullDotInner, _lblFullTitle, _fullBadgeBox, _lblFullBadge, _lblFullDesc, _fullSizeBox, _lblFullSize, _selectedVariant == "full");
            StyleCard(_jpnCard, _jpnDotOuter, _jpnDotInner, _lblJpnTitle, _jpnBadgeBox, _lblJpnBadge, _lblJpnDesc, _jpnSizeBox, _lblJpnSize, _selectedVariant == "jpn");
        }

        private void StyleCard(
            Border card, Ellipse outer, Ellipse inner, TextBlock title, Border badgeBox, TextBlock badge,
            TextBlock desc, Border sizeBox, TextBlock size, bool selected)
        {
            card.Background = selected ? _pal.AccentSoft : _pal.Surface;
            card.BorderBrush = selected ? _pal.Accent : _pal.Border;
            outer.Stroke = selected ? _pal.Accent : _pal.Muted;
            inner.Fill = selected ? _pal.Accent : Brushes.Transparent;
            title.Foreground = _pal.Text;
            badgeBox.Background = selected ? _pal.Accent : _pal.Raised;
            badge.Foreground = selected ? _pal.AccentInk : _pal.Muted;
            desc.Foreground = _pal.Muted;
            sizeBox.Background = _pal.InputBg;
            size.Foreground = selected ? _pal.Accent : _pal.Text;
        }

        private void UpdateStagePills(int stage)
        {
            _currentStage = stage;
            for (int i = 0; i < 4; i++)
            {
                int idx = i + 1;
                if (_installed || stage > idx)
                {
                    _stagePills[i].Background = _pal.Raised;
                    _stageLabels[i].Foreground = _pal.Success;
                }
                else if (stage == idx)
                {
                    _stagePills[i].Background = _pal.AccentSoft;
                    _stageLabels[i].Foreground = _pal.Accent;
                }
                else
                {
                    _stagePills[i].Background = _pal.InputBg;
                    _stageLabels[i].Foreground = _pal.Muted;
                }
            }
        }

        private void ApplyLanguage()
        {
            Title = I18n.T("window_title");
            _lblAppName.Text = I18n.T("app_name");
            _lblSubtitle.Text = I18n.T("subtitle");
            _lblChoose.Text = I18n.T("step_choose");
            _lblFullTitle.Text = I18n.T("full_title");
            _lblFullBadge.Text = I18n.T("full_badge");
            _lblFullDesc.Text = I18n.T("full_desc");
            _lblJpnTitle.Text = I18n.T("jpn_title");
            _lblJpnBadge.Text = I18n.T("jpn_badge");
            _lblJpnDesc.Text = I18n.T("jpn_desc");
            _lblInstallDir.Text = I18n.T("install_dir");
            _btnBrowse.Content = I18n.T("browse");
            _lblRoute.Text = I18n.T("network_route");
            _lblProxy.Text = I18n.T("proxy_mode");
            _txtProxyUrl.ToolTip = I18n.T("proxy_placeholder");
            _chkStartMenu.Content = I18n.T("opt_startmenu");
            _chkDesktop.Content = I18n.T("opt_desktop");
            _chkLaunch.Content = I18n.T("opt_launch");
            _stageLabels[0].Text = I18n.T("stage_download");
            _stageLabels[1].Text = I18n.T("stage_verify");
            _stageLabels[2].Text = I18n.T("stage_extract");
            _stageLabels[3].Text = I18n.T("stage_finish");
            _btnRefresh.Content = I18n.T("btn_refresh");
            _btnLang.Content = I18n.CurrentLanguage == "zh-CN" ? "中 / EN" : (I18n.CurrentLanguage == "en" ? "EN / 日" : "日 / 中");
            _btnCancel.Content = I18n.T("btn_cancel");
            _btnOpenDir.Content = I18n.T("btn_open_dir");
            _btnReleases.Content = I18n.T("footer_releases");
            _lblFooterInfo.Text = I18n.T("footer_info");

            int rSel = Math.Max(0, _cmbRoute.SelectedIndex);
            _cmbRoute.Items.Clear();
            for (int i = 0; i < RouteI18n.Length; i++) _cmbRoute.Items.Add(I18n.T(RouteI18n[i]));
            _cmbRoute.SelectedIndex = rSel;

            int pSel = Math.Max(0, _cmbProxy.SelectedIndex);
            _cmbProxy.Items.Clear();
            for (int i = 0; i < ProxyI18n.Length; i++) _cmbProxy.Items.Add(I18n.T(ProxyI18n[i]));
            _cmbProxy.SelectedIndex = pSel;

            if (!_installing && !_installed && !_hadError)
                _lblStatus.Text = I18n.T("status_ready");
            UpdatePrimaryButtonState();
        }

        private void UpdatePrimaryButtonState()
        {
            if (_installed)
            {
                _btnPrimary.Content = I18n.T("btn_launch");
                _btnPrimary.Background = _pal.Success;
                _btnPrimary.IsEnabled = true;
                _btnOpenDir.Visibility = Visibility.Visible;
                _btnCancel.Visibility = Visibility.Collapsed;
            }
            else if (_installing)
            {
                _btnPrimary.Content = I18n.T("btn_install");
                _btnPrimary.IsEnabled = false;
                _btnCancel.Visibility = Visibility.Visible;
                _btnCancel.IsEnabled = true;
                _btnOpenDir.Visibility = Visibility.Collapsed;
            }
            else
            {
                _btnPrimary.Content = _hadError ? I18n.T("btn_retry") : I18n.T("btn_install");
                _btnPrimary.Background = _pal.Accent;
                _btnPrimary.IsEnabled = (_release != null);
                _btnCancel.Visibility = Visibility.Collapsed;
                _btnOpenDir.Visibility = Visibility.Collapsed;
            }
        }

        private void UpdateReleaseDisplay()
        {
            if (_release == null)
            {
                _lblFullSize.Text = I18n.T("size_loading");
                _lblJpnSize.Text = I18n.T("size_loading");
                return;
            }
            _lblReleasePill.Text = string.IsNullOrEmpty(_pinnedVersion)
                ? I18n.F("release_ready", _release.Version)
                : I18n.F("release_pinned", _release.Version);
            _lblFullSize.Text = FormatPlanSize(_release.FullPlan);
            _lblJpnSize.Text = FormatPlanSize(_release.JpnPlan);
        }

        private static string FormatPlanSize(PortableVariantPlan p)
        {
            if (p == null) return "—";
            string sz = ReleaseCatalog.FormatBytes(p.TotalBytes);
            return p.Parts.Count > 1 ? I18n.F("size_parts", sz, p.Parts.Count) : I18n.F("size_single", sz);
        }

        private void RefreshDirMeta()
        {
            string dir = (_txtTargetDir.Text ?? "").Trim();
            long free = WindowsIntegration.GetAvailableFreeSpace(dir);
            _lblFreeSpace.Text = free >= 0 ? I18n.F("free_space", ReleaseCatalog.FormatBytes(free)) : "—";
            string existing = WindowsIntegration.DetectExistingVersion(dir);
            if (!string.IsNullOrEmpty(existing))
            {
                _lblUpgrade.Text = I18n.F("upgrade_detected", existing);
                _upgradeBanner.Visibility = Visibility.Visible;
            }
            else
            {
                _upgradeBanner.Visibility = Visibility.Collapsed;
            }
        }

        private void OnBrowseFolder()
        {
            string picked = WindowsIntegration.PickFolder(this, _txtTargetDir.Text.Trim());
            if (!string.IsNullOrEmpty(picked))
            {
                string leaf = System.IO.Path.GetFileName(picked.TrimEnd('\\', '/'));
                if (!string.Equals(leaf, "VWA", StringComparison.OrdinalIgnoreCase) && !File.Exists(System.IO.Path.Combine(picked, "version.json")))
                    picked = System.IO.Path.Combine(picked, "VWA");
                _txtTargetDir.Text = picked;
            }
        }

        private string SelectedRouteKey()
        {
            int idx = _cmbRoute.SelectedIndex;
            return (idx >= 0 && idx < RouteKeys.Length) ? RouteKeys[idx] : "auto";
        }

        private string SelectedProxyKey()
        {
            int idx = _cmbProxy.SelectedIndex;
            return (idx >= 0 && idx < ProxyKeys.Length) ? ProxyKeys[idx] : "system";
        }

        private async Task RefreshReleaseAsync()
        {
            if (_installing) return;
            _btnRefresh.IsEnabled = false;
            _lblReleasePill.Text = I18n.T("checking_release");
            _lblFullSize.Text = I18n.T("size_loading");
            _lblJpnSize.Text = I18n.T("size_loading");
            try
            {
                using (HttpClient client = DownloadEngine.CreateHttpClient(SelectedProxyKey(), _txtProxyUrl.Text))
                {
                    _release = await ReleaseCatalog.FetchReleaseAsync(
                        client, _pinnedVersion, SelectedRouteKey(), _manifestBase, CancellationToken.None);
                }
                if (_release.FullPlan == null && _release.JpnPlan != null) _selectedVariant = "jpn";
                UpdateReleaseDisplay();
                UpdateVariantCards();
                _hadError = false;
                _lblStatus.Text = I18n.T("status_ready");
                _lblStatus.Foreground = _pal.Text;
            }
            catch (Exception ex)
            {
                _release = null;
                _lblReleasePill.Text = I18n.T("release_fail");
                _lblStatus.Text = I18n.F("status_error", ex.Message);
                _lblStatus.Foreground = _pal.Error;
            }
            finally
            {
                _btnRefresh.IsEnabled = true;
                UpdatePrimaryButtonState();
            }
        }

        private void OnCancelClicked()
        {
            if (_cts != null && !_cts.IsCancellationRequested)
            {
                _btnCancel.Content = I18n.T("btn_cancelling");
                _btnCancel.IsEnabled = false;
                _cts.Cancel();
            }
        }

        private async void OnPrimaryClicked()
        {
            if (_installed)
            {
                WindowsIntegration.LaunchVwa(_txtTargetDir.Text.Trim());
                Close();
                return;
            }
            if (_installing) return;
            if (_release == null)
            {
                _lblStatus.Text = I18n.T("err_no_release");
                _lblStatus.Foreground = _pal.Error;
                return;
            }
            PortableVariantPlan plan = _selectedVariant == "jpn" ? _release.JpnPlan : _release.FullPlan;
            if (plan == null)
            {
                _lblStatus.Text = I18n.T("err_no_release");
                _lblStatus.Foreground = _pal.Error;
                return;
            }

            _installing = true;
            _hadError = false;
            _cts = new CancellationTokenSource();
            SetInputsEnabled(false);
            UpdatePrimaryButtonState();
            _progressFill.Background = _pal.Accent;
            _lblStatus.Foreground = _pal.Text;

            var opts = new InstallOptions
            {
                Variant = _selectedVariant,
                TargetDir = _txtTargetDir.Text.Trim(),
                RouteSource = SelectedRouteKey(),
                ProxyMode = SelectedProxyKey(),
                ProxyUrl = _txtProxyUrl.Text.Trim(),
                Workers = 4,
                CreateStartMenu = _chkStartMenu.IsChecked == true,
                CreateDesktop = _chkDesktop.IsChecked == true,
                LaunchAfter = _chkLaunch.IsChecked == true,
                ManifestBaseOverride = _manifestBase
            };

            try
            {
                using (HttpClient client = DownloadEngine.CreateHttpClient(opts.ProxyMode, opts.ProxyUrl))
                {
                    await DownloadEngine.ExecuteInstallAsync(client, plan, opts, OnProgressReport, _cts.Token);
                }
                _installing = false;
                _installed = true;
                UpdateStagePills(5);
                UpdateProgressBarWidth(100);
                _progressFill.Background = _pal.Success;
                _lblStatus.Foreground = _pal.Success;
                _lblStatus.Text = I18n.F("status_done", plan.Version);
                _lblSpeed.Text = "";
                UpdatePrimaryButtonState();
                RefreshDirMeta();
                if (opts.LaunchAfter)
                {
                    WindowsIntegration.LaunchVwa(opts.TargetDir);
                }
            }
            catch (OperationCanceledException)
            {
                _installing = false;
                _hadError = true;
                _lblStatus.Text = I18n.T("status_cancelled");
                _lblStatus.Foreground = _pal.Muted;
                _lblSpeed.Text = "";
                SetInputsEnabled(true);
                UpdatePrimaryButtonState();
            }
            catch (Exception ex)
            {
                _installing = false;
                _hadError = true;
                _progressFill.Background = _pal.Error;
                _lblStatus.Foreground = _pal.Error;
                _lblStatus.Text = I18n.F("status_error", ex.Message);
                _lblSpeed.Text = "";
                SetInputsEnabled(true);
                UpdatePrimaryButtonState();
            }
        }

        private void SetInputsEnabled(bool enabled)
        {
            _txtTargetDir.IsEnabled = enabled;
            _btnBrowse.IsEnabled = enabled;
            _cmbRoute.IsEnabled = enabled;
            _cmbProxy.IsEnabled = enabled;
            _txtProxyUrl.IsEnabled = enabled;
            _chkStartMenu.IsEnabled = enabled;
            _chkDesktop.IsEnabled = enabled;
            _btnRefresh.IsEnabled = enabled;
        }

        private void OnProgressReport(InstallProgress p)
        {
            Dispatcher.BeginInvoke(new Action(delegate
            {
                UpdateStagePills(p.Stage);
                UpdateProgressBarWidth(p.Percent);
                _lblStatus.Text = p.Message;
                if (p.Stage == 1 || p.Stage == 2)
                {
                    _lblPct.Text = string.Format(CultureInfo.InvariantCulture, "{0:0.0}% ({1} / {2})",
                        p.Percent, ReleaseCatalog.FormatBytes(p.DoneBytes), ReleaseCatalog.FormatBytes(p.TotalBytes));
                }
                else
                {
                    _lblPct.Text = string.Format(CultureInfo.InvariantCulture, "{0:0}%", p.Percent);
                }
                if (p.Stage == 1 && p.SpeedBps > 0)
                {
                    string spd = ReleaseCatalog.FormatBytes((long)p.SpeedBps) + "/s";
                    long rem = Math.Max(0, p.TotalBytes - p.DoneBytes);
                    int etaSec = (int)Math.Min(359999, rem / p.SpeedBps);
                    TimeSpan ts = TimeSpan.FromSeconds(etaSec);
                    string etaText = ts.Hours > 0
                        ? string.Format(CultureInfo.InvariantCulture, "{0:00}:{1:00}:{2:00}", ts.Hours, ts.Minutes, ts.Seconds)
                        : string.Format(CultureInfo.InvariantCulture, "{0:00}:{1:00}", ts.Minutes, ts.Seconds);
                    _lblSpeed.Text = spd + " · " + I18n.F("eta_fmt", etaText);
                }
                else
                {
                    _lblSpeed.Text = "";
                }
                _lblRoutePill.Text = !string.IsNullOrEmpty(p.ActiveRoute)
                    ? I18n.F("route_pill", p.ActiveRoute, p.Workers)
                    : "";
            }));
        }

        public void SetThemeDark(bool dark)
        {
            _dark = dark;
            ApplyTheme();
            WindowsIntegration.ApplyTitlebarTheme(this, _dark);
        }

        public void SaveScreenshot(string pngPath)
        {
            UpdateLayout();
            int w = Math.Max(1, (int)Math.Round(ActualWidth));
            int h = Math.Max(1, (int)Math.Round(ActualHeight));
            var rtb = new System.Windows.Media.Imaging.RenderTargetBitmap(w, h, 96, 96, PixelFormats.Pbgra32);
            rtb.Render(_rootBorder);
            var encoder = new System.Windows.Media.Imaging.PngBitmapEncoder();
            encoder.Frames.Add(System.Windows.Media.Imaging.BitmapFrame.Create(rtb));
            using (var fs = new FileStream(pngPath, FileMode.Create, FileAccess.Write))
                encoder.Save(fs);
        }

        private void UpdateProgressBarWidth(double percent)
        {
            _lastPercent = Math.Max(0.0, Math.Min(100.0, percent));
            double trackW = _progressTrack.ActualWidth;
            if (trackW > 0) _progressFill.Width = trackW * (_lastPercent / 100.0);
        }
    }
}
