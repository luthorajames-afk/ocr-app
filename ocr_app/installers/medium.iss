; Squelette — installateur module Moyen (Inno Setup)
; Règle R13 : un environnement virtuel par module.
; Règle R14 : tester les chemins avec accents et espaces dès le début.

[Setup]
AppName=OCRApp Module - Medium
AppVersion=1.0.0
DefaultDirName={userappdata}\Programs\OCRApp\modules\medium
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible

[Files]
Source: "dist\modules\medium\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs

[Run]
Filename: "{app}\worker_medium.exe"; Description: "Lancer le worker Moyen"; Flags: waituntilterminated
