; AOCRI — installateur du noyau (phase 7)
;
; R3 : exécutables PyInstaller --onedir (jamais --onefile).
; R4 : tessdata livrée avec l'installateur, aucun téléchargement à l'exécution.
; R11 : signer OCRApp.exe et worker_simple.exe (scripts/sign.py) avant compilation.
; R14 : installation en {userappdata} — aucune élévation de droits ; Inno est
;       compilé en Unicode, les chemins accentués sont gérés nativement.
; R15 : AppId constant partagé avec medium.iss et advanced.iss — les modules
;       s'installent à côté du noyau et se mettent à jour indépendamment
;       (UsePreviousAppDir=no dans les .iss de module pour ne jamais réécrire
;       le dossier du noyau). Conséquence documentée de l'AppId partagé :
;       Inno APPEND les fichiers de chaque installation au MÊME journal de
;       désinstallation (unins000.dat) — la désinstallation via « Applications
;       et fonctionnalités » retire le noyau ET les modules installés ; en
;       contrepartie, réinstaller/réparer le noyau ne supprime pas les modules.
;
; Compilation (si Inno Setup est installé) :
;     ISCC.exe installers\core.iss   (depuis la racine du dépôt ocr_app)
; Les chemins Source/Output relatifs sont résolus par rapport à CE fichier
; (installers\), d'où les « ..\ » vers les sorties de build.
; Sortie : installers\dist\installers\AOCRI-core-setup.exe

#define MyAppName "AOCRI"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Kriesty Solutions"
; AppId unique constant, PARTAGÉ par core.iss / medium.iss / advanced.iss
; (une seule entrée « Applications et fonctionnalités », modules optionnels).
#define MyAppId "{{8A0C4D5E-1F2B-4C3D-9E4F-A0C21E5F6001}}"

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={userappdata}\Programs\AOCRI
DefaultGroupName={#MyAppName}
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
OutputBaseFilename=AOCRI-core-setup
OutputDir=dist\installers
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\OCRApp.exe
; Pas de [UninstallDelete] : l'installateur du noyau ne liste que ses propres
; fichiers — un module toujours présent dans {app}\modules n'est pas effacé
; par une réparation/réinstallation du noyau (R15).

[Tasks]
; NB : contrairement aux directives #define (ISPP), les entrées de section ne
; peuvent PAS être coupées avec « \ » en fin de ligne — une entrée = une ligne.
Name: "desktopicon"; Description: "Créer un raccourci sur le bureau"; Flags: unchecked
Name: "chain_medium"; Description: "Installer le module Moyen (RapidOCR) juste après"; Flags: unchecked
Name: "chain_advanced"; Description: "Installer le module Avancé (Docling) juste après"; Flags: unchecked

[Files]
; Noyau applicatif : OCRApp.exe (UI PySide6) + runtime Python embarqué
; (--onedir, R3). Produit par le build UI (phase packaging application) ;
; ce .iss reste compilable dès que dist\OCRApp existe.
Source: "..\dist\OCRApp\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Données de langue Tesseract (R4 : livrées, jamais téléchargées).
Source: "..\models\tessdata\*.traineddata"; DestDir: "{app}\models\tessdata"; Flags: ignoreversion
; Worker Simple : OCR réel (tesserocr), exécutable --onedir avec tessdata
; embarquée (scripts/build.py --worker simple).
Source: "..\dist\worker_simple\worker_simple\*"; DestDir: "{app}\workers\worker_simple"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\OCRApp.exe"
Name: "{group}\{#MyAppName} (désinstaller)"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\OCRApp.exe"; Tasks: desktopicon

[Run]
; Phase 4 du plan : chaînage optionnel des installateurs de modules depuis
; l'installateur du noyau (cases à cocher [Tasks] ; chaque entrée est ignorée
; si l'installateur correspondant est absent du dossier d'exécution).
Filename: "{src}\AOCRI-medium-setup.exe"; Tasks: chain_medium; Check: FileExists(ExpandConstant('{src}\AOCRI-medium-setup.exe')); StatusMsg: "Installation du module Moyen..."; Flags: waituntilterminated
Filename: "{src}\AOCRI-advanced-setup.exe"; Tasks: chain_advanced; Check: FileExists(ExpandConstant('{src}\AOCRI-advanced-setup.exe')); StatusMsg: "Installation du module Avancé..."; Flags: waituntilterminated
Filename: "{app}\OCRApp.exe"; Description: "Lancer {#MyAppName}"; Flags: nowait postinstall skipifsilent
