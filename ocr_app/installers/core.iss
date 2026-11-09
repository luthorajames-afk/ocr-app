; Squelette — installateur noyau (Inno Setup)
; Règle R3 : le noyau doit contenir Tesseract, pypdfium2, mode Simple.
; Règle R6 : sauvegarde page par page.

[Setup]
AppName=OCRApp
AppVersion=1.0.0
DefaultDirName={userappdata}\Programs\OCRApp
DefaultGroupName=OCRApp
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible

[Files]
Source: "dist\OCRApp\OCRApp.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\OCRApp"; Filename: "{app}\OCRApp.exe"
