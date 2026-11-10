; T7.3 — Installateur module Moyen (RapidOCR) — par utilisateur, sans admin.
; AppId partagé ; UsePreviousAppDir=no pour ne jamais écrire dans le noyau.

[Setup]
AppId={{A0C21E5F-6001-4C3D-9E4F-8A0C4D5E1F2B}
AppName=AOCRI
AppVersion=2.0.0
DefaultDirName={localappdata}\Programs\AOCRI\workers\worker_medium
DefaultGroupName=AOCRI
PrivilegesRequired=lowest
UsePreviousAppDir=no
ArchitecturesAllowed=x64compatible
OutputDir=C:\Users\lutho\AppData\Local\Temp\aocri_setup_out
OutputBaseFilename=Setup-AOCRI-module-medium
Compression=lzma2/max
SolidCompression=yes

[Files]
Source: "..\dist\OCRApp\workers\worker_medium\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
