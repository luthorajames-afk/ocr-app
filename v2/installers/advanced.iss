; T7.3 — Installateur module Avancé (Docling) — par utilisateur, sans admin.
; AppId partagé ; UsePreviousAppDir=no ; artefacts Docling inclus hors ligne (R4).

[Setup]
AppId={{A0C21E5F-6001-4C3D-9E4F-8A0C4D5E1F2B}
AppName=AOCRI
AppVersion=2.0.0
DefaultDirName={localappdata}\Programs\AOCRI\workers\worker_advanced
DefaultGroupName=AOCRI
PrivilegesRequired=lowest
UsePreviousAppDir=no
ArchitecturesAllowed=x64compatible
OutputDir=C:\Users\lutho\AppData\Local\Temp\aocri_setup_out
OutputBaseFilename=Setup-AOCRI-module-advanced
Compression=lzma2/max
SolidCompression=yes

[Files]
Source: "..\dist\OCRApp\workers\worker_advanced\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\models\docling\*"; DestDir: "{app}\models"; Flags: ignoreversion recursesubdirs skipifsourcedoesntexist
