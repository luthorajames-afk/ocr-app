; AOCRI — installateur du module Avancé (Docling) (phase 7)
;
; R1  : PyTorch et dépendances lourdes restent ICI, dans le module Avancé,
;       jamais dans le noyau.
; R13 : venv propre au module → worker autonome buildé depuis ce venv.
; R15 : même AppId que le noyau — mise à jour du module sans réinstaller le
;       noyau. Conséquence documentée de l'AppId partagé : les fichiers du
;       module sont ajoutés au journal de désinstallation du noyau ; la
;       désinstallation via « Applications et fonctionnalités » retire le
;       noyau ET les modules (le retrait d'un module seul se fait en
;       supprimant {userappdata}\Programs\AOCRI\modules\advanced).
; R11 : signer worker_advanced.exe (scripts/sign.py) avant compilation.
;
; Contenu attendu (préparé par scripts/make_module.py --module advanced) :
;     dist\modules\advanced\module.json         (sha256 réel du worker)
;     dist\modules\advanced\worker_advanced.exe (--onedir : + _internal\...)
;     dist\modules\advanced\models\...          (modèles locaux, R4)
;
; Compilation : ISCC.exe installers\advanced.iss   (depuis la racine du dépôt).
; Les chemins relatifs sont résolus par rapport à CE fichier (installers\).

#define MyAppName "AOCRI Module Avancé"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Kriesty Solutions"
; AppId constant partagé avec core.iss / medium.iss.
#define MyAppId "{{8A0C4D5E-1F2B-4C3D-9E4F-A0C21E5F6001}}"

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={userappdata}\Programs\AOCRI\modules\advanced
; AppId partagé avec le noyau : sans cette directive, l'installateur du module
; pré-remplirait la page de dossier avec le dossier du noyau (dernière
; installation enregistrée) et installerait les fichiers du module à la racine
; du noyau (R15). Le module s'installe toujours dans SON dossier.
UsePreviousAppDir=no
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
OutputBaseFilename=AOCRI-advanced-setup
OutputDir=dist\installers
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; Réinstallation = mise à jour du module (R15).

[Files]
; Worker du module (--onedir, R3) + module.json vérifié par ModuleRegistry
; (scan + sha256) au démarrage de l'application + modèles locaux (R4).
Source: "..\dist\modules\advanced\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

; Pas de section [Run] : le worker est piloté par l'application via le
; protocole JSON (stdin/stdout), rien à lancer en fin d'installation.
