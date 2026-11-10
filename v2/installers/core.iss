; T7.3 — Installateur noyau AOCRI v2 (Inno Setup, par utilisateur, sans admin)
; AppId partagé avec medium.iss / advanced.iss ; aucune élévation de droits.

[Setup]
AppId={{A0C21E5F-6001-4C3D-9E4F-8A0C4D5E1F2B}
AppName=AOCRI
AppVersion=2.0.0
DefaultDirName={localappdata}\Programs\AOCRI
DefaultGroupName=AOCRI
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
UninstallDisplayIcon={app}\OCRApp.exe

[Files]
Source: "..\dist\OCRApp\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\models\tessdata\*.traineddata"; DestDir: "{app}\models\tessdata"; Flags: ignoreversion
Source: "..\dist\worker_simple\worker_simple\*"; DestDir: "{app}\workers\worker_simple"; Flags: ignoreversion recursesubdirs createallsubdirs

[Tasks]
Name: "desktopicon"; Description: "Créer un raccourci sur le Bureau"; GroupDescription: "Raccourcis:"

[Icons]
Name: "{group}\AOCRI"; Filename: "{app}\OCRApp.exe"
Name: "{autodesktop}\AOCRI"; Filename: "{app}\OCRApp.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\OCRApp.exe"; Description: "Lancer AOCRI"; Flags: nowait postinstall skipifsilent
