; T7.3 — Installateur module Moyen (RapidOCR) — par utilisateur, sans admin.
; AppId partagé ; UsePreviousAppDir=no pour ne jamais écrire dans le noyau.

[Setup]
AppId={{A0C21E5F-6001-4C3D-9E4F-8A0C4D5E1F2B}
AppName=AOCRI
AppVersion=2.0.0
DefaultDirName={localappdata}\Programs\AOCRI\modules\medium
DefaultGroupName=AOCRI
PrivilegesRequired=lowest
UsePreviousAppDir=no
ArchitecturesAllowed=x64compatible

[Files]
Source: "..\dist\worker_medium\worker_medium\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Run]
Filename: "{app}\module.json"; Description: "Manifeste du module Moyen"; Flags: shellexec skipifsilent
