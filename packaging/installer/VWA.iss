; Inno Setup 6 Per-User Installer Template for VWA (Vocaloid Workflow Assistant)
; Builds a non-admin Windows installer alongside the portable ZIP distribution.
; Usage: iscc /DAppVersion=2.1.0 /DSourceDir=path\to\staged\VWA packaging\installer\VWA.iss

#ifndef AppVersion
  #define AppVersion "2.1.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\..\dist\VWA"
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
OutputBaseFilename=VWA-Setup-{#AppVersion}-x64
WizardStyle=modern
DisableProgramGroupPage=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "japanese"; MessagesFile: "compiler:Languages\Japanese.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; Never overwrite existing user config, jobs, or presets on in-place upgrade
Source: "{#SourceDir}\*"; DestDir: "{app}"; Excludes: "config.json,jobs\*,user-presets\*,cache\*"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#SourceDir}\config.example.json"; DestDir: "{app}"; DestName: "config.json"; Flags: onlyifdoesntexist

[Icons]
Name: "{group}\VWA"; Filename: "{app}\VWA.exe"
Name: "{group}\VWA Diagnostics"; Filename: "{app}\tools\Start-Diagnostics.bat"
Name: "{autodesktop}\VWA"; Filename: "{app}\VWA.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\VWA.exe"; Description: "{cm:LaunchProgram,VWA}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Keep user jobs/, user-presets/, and config.json intact unless user removes folder manually.
Type: filesandordirs; Name: "{app}\cache"
