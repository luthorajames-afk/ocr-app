; Squelette — installateur module Avancé (Inno Setup)
; Règle R1 : pas de PyTorch dans le noyau ; module Avancé = dépendances lourdes.
; Règle R15 : prévoir la désinstallation / montée de version de module sans réinstaller le noyau.

[Setup]
AppName=OCRApp Module - Advanced
AppVersion=1.0.0
DefaultDirName={userappdata}\Programs\OCRApp\modules\advanced
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible

[Files]
Source: "dist\modules\advanced\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs

[Run]
Filename: "{app}\worker_advanced.exe"; Description: "Lancer le worker Avancé"; Flags: waituntilterminated
