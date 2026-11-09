; AOCRI — installateur du module Moyen (RapidOCR) (phase 7)
;
; R13 : dépendances propres au module (venv séparé → worker autonome buildé
;       depuis le venv du module, jamais depuis l'env du noyau).
; R15 : même AppId que le noyau — installation/mise à jour du module sans
;       réinstaller le noyau. Conséquence documentée de l'AppId partagé :
;       les fichiers du module sont ajoutés au journal de désinstallation du
;       noyau ; la désinstallation via « Applications et fonctionnalités »
;       retire le noyau ET les modules (le retrait d'un module seul se fait
;       en supprimant {userappdata}\Programs\AOCRI\modules\medium).
; R11 : signer worker_medium.exe (scripts/sign.py) avant compilation.
;
; Contenu attendu (préparé par scripts/make_module.py --module medium) :
;     dist\modules\medium\module.json        (sha256 réel du worker)
;     dist\modules\medium\worker_medium.exe  (--onedir : + _internal\...)
;
; Compilation : ISCC.exe installers\medium.iss   (depuis la racine du dépôt).
; Les chemins relatifs sont résolus par rapport à CE fichier (installers\).

#define MyAppName "AOCRI Module Moyen"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Kriesty Solutions"
; AppId constant partagé avec core.iss / advanced.iss.
#define MyAppId "{{8A0C4D5E-1F2B-4C3D-9E4F-A0C21E5F6001}}"

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={userappdata}\Programs\AOCRI\modules\medium
; AppId partagé avec le noyau : sans cette directive, l'installateur du module
; pré-remplirait la page de dossier avec le dossier du noyau (dernière
; installation enregistrée) et installerait les fichiers du module à la racine
; du noyau (R15). Le module s'installe toujours dans SON dossier.
UsePreviousAppDir=no
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
OutputBaseFilename=AOCRI-medium-setup
OutputDir=dist\installers
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; Réinstallation = mise à jour du module (R15) : les fichiers du module sont
; remplacés, le noyau et les autres modules ne sont pas touchés.

[Files]
; Worker du module (--onedir, R3) + module.json vérifié par ModuleRegistry
; (scan + sha256) au démarrage de l'application.
Source: "..\dist\modules\medium\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

; Pas de section [Run] : le worker est piloté par l'application via le
; protocole JSON (stdin/stdout), il n'a rien à lancer en fin d'installation
; (l'ancien « Lancer le worker Moyen » bloquait sur stdin).
