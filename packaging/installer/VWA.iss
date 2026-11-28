; Inno Setup 6 Per-User Installer for VWA (Vocaloid Workflow Assistant)
; Supports >2 GiB Full packages via 1900 MiB DiskSpanning slices compatible with GitHub Releases.
; Usage:
;   iscc /DAppVersion=3.0.0 /DSourceDir=path\to\staged\VWA /DVariantSuffix= packaging\installer\VWA.iss
;   iscc /DAppVersion=3.0.0 /DSourceDir=path\to\staged\VWA-JPN /DVariantSuffix=-JPN packaging\installer\VWA.iss

#ifndef AppVersion
  #define AppVersion "3.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\..\dist\VWA"
#endif
#ifndef VariantSuffix
  #define VariantSuffix ""
#endif
#ifndef OutputDir
  #define OutputDir "..\..\dist\installer"
#endif

[Setup]
AppId={{8D6E3D7A-5D24-4C94-9C68-8B3F5E4D2A10}
AppName=VWA - Vocaloid Workflow Assistant
AppVersion={#AppVersion}
AppPublisher=Transilent
AppPublisherURL=https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant
AppSupportURL=https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant/issues
DefaultDirName={localappdata}\VWA
DefaultGroupName=VWA
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
Compression=lzma2/fast
SolidCompression=no
DiskSpanning=yes
SlicesPerDisk=1
DiskSliceSize=1992294400
DisableDirPage=no
DisableProgramGroupPage=yes
CloseApplications=yes
RestartApplications=no
OutputDir={#OutputDir}
OutputBaseFilename=VWA-Setup-{#AppVersion}-x64{#VariantSuffix}
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
#if FileExists(CompilerPath + "Languages\ChineseSimplified.isl")
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
#endif
#if FileExists(CompilerPath + "Languages\Japanese.isl")
Name: "japanese"; MessagesFile: "compiler:Languages\Japanese.isl"
#endif

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; Never overwrite existing user config, jobs, presets, accounts, or optional downloaded models on upgrade
Source: "{#SourceDir}\*"; DestDir: "{app}"; Excludes: "config.json,jobs\*,user-presets\*,cache\*,publish-packages\*,dependencies\optional\*"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#SourceDir}\config.example.json"; DestDir: "{app}"; DestName: "config.json"; Flags: onlyifdoesntexist

[Icons]
Name: "{group}\VWA"; Filename: "{app}\VWA.exe"
Name: "{group}\VWA Diagnostics"; Filename: "{app}\tools\Start-Diagnostics.bat"
Name: "{autodesktop}\VWA"; Filename: "{app}\VWA.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\VWA.exe"; Description: "{cm:LaunchProgram,VWA}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Keep user jobs/, user-presets/, config.json, cache/accounts/, and dependencies/optional/ intact.
Type: filesandordirs; Name: "{app}\cache\updates"
Type: filesandordirs; Name: "{app}\work"
