# Phase 7 — Rapport : workers par mode et installateurs

**Date :** 2026-11-09 · **Projet :** AOCRI (Advanced OCR for Itamba, Kriesty Solutions)
**Dépôt :** `D:\projects\AOCRI\ocr_app` · **Branche de travail :** session phase 7

## Objectifs

Worker principal par mode (protocole JSON stdin/stdout) avec OCR réel en mode
Simple, mapping UI mode → worker, manifestes `module.json` réels, build
PyInstaller `--onedir` (R3), installateurs Inno Setup, signature de code (R11),
branchement `modules_info` ↔ `ModuleRegistry`.

## 1. Fichiers créés

| Fichier | Rôle |
| --- | --- |
| `src/ocr_app/workers/worker_main.py` | Worker principal par mode : boucle protocole (style `fake_worker`) + traitement réel via le registre moteur du pipeline |
| `src/ocr_app/ui/worker_commands.py` | Mapping mode → commande `python -m ocr_app.workers.worker_main --mode <mode>` + messages « module non installé » |
| `scripts/make_module.py` | Génère `module.json` réel (id, version, min_app_version, mode, worker, sha256 via `module_manifest.sha256_file`) après build |
| `scripts/sign.py` | Signature signtool (R11) : détection signtool/certificat, guide complet si prérequis absents |
| `docs/phase7_report.md` | Ce rapport |

## 2. Fichiers modifiés

| Fichier | Modification |
| --- | --- |
| `scripts/build.py` | Réécrit : point d'entrée `workers/worker_main.py` pour les 3 workers, `--onedir` (R3), `--add-data models/tessdata` (chemins **absolus** : `--specpath` déplace le CWD du spec), `--sign` (R11), noms `worker_<mode>` |
| `src/ocr_app/ui/modules_info.py` | **Branchement registre complet** : `detect_modules()` combine détection par import (dev) et `ModuleRegistry.scan()` + `verify_sha256()` + compatibilité version (R15) ; helper public `find_installed_module()` pour `worker_commands`. Dossiers scannés : `AOCRI_MODULES_DIR` (surcharge), `<AppLocalDataLocation>/modules` (installé), `<dépôt>/dist/modules` (make_module), `<dépôt>/modules` (dev). Aucun import lourd (R2) |
| `src/ocr_app/ui/task_runner.py` | 2 ajouts : (1) `unavailable_modes` — une tâche d'un mode sans module passe en ERROR avec le message clair, aucun worker lancé ; (2) `--input <source>` ajouté aux args du worker quand la commande cible `worker_main` et que le fichier source existe |
| `src/ocr_app/ui/worker_client.py` | Paramètre `extra_args` sur `WorkerClient.start()` (passé en dernier après `--pages/--work-dir/--stage-delay`) — `fake_worker` reste inchangé |
| `src/ocr_app/ui/main_window.py` | `TaskRunner(self, worker_commands=resolve_worker_commands())` — branchement minimal (3 lignes) |
| `installers/core.iss`, `installers/medium.iss`, `installers/advanced.iss` | Voir §5 |

Non touchés (conformément à la mission) : `tests/`, `core/*` (lecture seule),
`fake_worker.py`, `edit_session.py`, `clipboard*`, `exports.py`,
`export_dialog.py`, `widgets/*`, `data/`.

## 3. Worker principal par mode (`worker_main.py`)

- Boucle commune identique au style `fake_worker` : attente de `start` (avec
  `page` de départ), `pause`/`resume` (le temps simulé ne s'écoule pas), `cancel`
  (message `error` préfixé « annulé : »), progression par étape
  (render → … → save → done), `page_done` avec chemin absolu, sauvegarde
  `page_XXX.json` **dès que la page est terminée** (R6), UTF-8 forcé (R14),
  stdout strictement réservé au protocole.
- **mode simple** : pipeline réel `core/pipeline.py` (tesserocr via
  `SimpleWorker`) ; R8 appliqué (texte natif sans rendu/OCR) ; R7 (300 DPI max) ;
  R5 (worker vivant tout le document).
- **modes medium/advanced** : pipeline réel avec `MediumWorker`/`AdvancedWorker`
  (placeholders explicites en attendant l'installation des modules) — boucle
  protocole complète et fonctionnelle.
- Deux sources de pages : `--input <pdf/jpg/png>` (document réel) ou, sans
  `--input`, simulation de `--pages N` (placeholder). Autres options :
  `--stage-delay` (simulation ; accepté pour compatibilité `WorkerClient`),
  `--dpi`, `--models-dir`.
- Erreurs document/format → `error` de protocole avec préfixe « erreur de
  document : » ; erreurs internes → « erreur interne : » + trace sur stderr.
- `fake_worker.py` **inchangé** : les 13 tests de protocole restent verts.

## 4. Mapping UI par mode (`worker_commands.py`)

- `resolve_worker_commands()` retourne `(commandes, indisponibles)`. Ordre de
  préférence par mode :
  1. exécutable packagé du module (manifeste `module.json` vérifié par
     `ModuleRegistry` — sha256 + compat), avec `--mode` ;
  2. en application gelée (PyInstaller), `workers\worker_<mode>\worker_<mode>.exe`
     installé à côté de l'interface (layout de `core.iss`), avec `--mode` ;
  3. en développement, `python -m ocr_app.workers.worker_main --mode <mode>`.
- Disponibilité : `simple` **toujours disponible** (noyau, R1) ;
  `medium`/`advanced` seulement si `detect_modules` les déclare installés
  (import **ou** `module.json` vérifié), sinon message « Module « … » non
  installé — ajoutez le module « … » (dossier modules/…) pour activer ce
  mode. »
- Le `TaskRunner` ne lance aucun worker pour un mode indisponible : la tâche
  passe en ERROR consultable dans la sidebar (vérifié hors écran).
- `accepts_input_document()` : `--input` n'est passé qu'aux workers qui
  traitent un document réel (`worker_main` module ou exécutable packagé) ;
  `fake_worker` reste utilisable tel quel.
- Sans `worker_commands` (cas des tests), le repli `fake_worker` historique
  est conservé.
- NB : `worker_commands.py` a été enrichi en cours de session (préférence
  pour les exécutables packagés, cf. ordre ci-dessus) ; le helper public
  `modules_info.find_installed_module(key, modules_dir=None)` a été ajouté
  pour le servir (wrapper de la vérification scan + sha256 + compat).

## 5. Installateurs Inno Setup

- **AppId unique constant partagé** : `{{8A0C4D5E-1F2B-4C3D-9E4F-A0C21E5F6001}}`
  (une seule entrée « Applications et fonctionnalités », R15).
- `PrivilegesRequired=lowest`, installation `{userappdata}\Programs\AOCRI`,
  modules dans `{app}\modules\medium` / `{app}\modules\advanced`.
- `core.iss` : `dist\OCRApp\*` (UI + runtime, --onedir), `models\tessdata\*.traineddata`
  (R4), worker simple `dist\worker_simple\worker_simple\*` vers
  `{app}\workers\worker_simple`. Cases à cocher `[Tasks]` optionnelles pour
  chaîner `AOCRI-medium-setup.exe` / `AOCRI-advanced-setup.exe` après
  l'installation (phase 4 du plan ; entrée ignorée si le fichier est absent
  grâce à `Check: FileExists(...)`).
- `medium.iss`/`advanced.iss` : mêmes AppId, contenu `dist\modules\<module>\*`
  (module.json + worker + modèles). **La section `[Run]` « Lancer le worker »
  du squelette a été retirée** : le worker lit stdin (il se serait mis à
  attendre indéfiniment en fin d'installation).
- Chemins `Source` relatifs au fichier `.iss` (`..\dist\...`) — utilisables
  quel que soit le répertoire d'appel d'ISCC.
- Fichiers enregistrés en **UTF-8 avec BOM** (accents français visibles dans
  l'assistant).
- **ISCC (Inno Setup Compiler) absent de la machine** (Program Files,
  Program Files (x86), ProgramData, AppData : vérifiés). Compilation **non**
  exécutée. Validation syntaxique manuelle effectuée (script de contrôle) :
  sections connues, clés `[Setup]` requises, guillemets équilibrés, AppId
  identique dans les 3 fichiers, `PrivilegesRequired=lowest`, dossier
  `{userappdata}\Programs\AOCRI` — **tout OK**.
- Points de syntaxe vérifiés contre la documentation Inno Setup : la
  continuation de ligne `\` n'est valide **que** dans les directives ISPP
  (`#define`), pas dans les entrées de section — les entrées `[Tasks]`/`[Run]`
  sont donc sur une seule ligne chacune.
- À noter : `ArchitecturesAllowed=x64compatible` requiert Inno Setup ≥ 6.3.

## 6. Build PyInstaller et signature

- `python scripts/build.py --worker simple` : **SUCCÈS** (PyInstaller 6.22.3,
  Python 3.14.8). Sortie `dist\worker_simple\worker_simple\worker_simple.exe`
  (`--onedir`, ~133 Mio). Les DLL tesserocr/leptonica (`tesserocr.libs`,
  `numpy.libs`) sont collectées automatiquement : **aucun hook spécial
  n'a été nécessaire**. Le premier essai a échoué sur `--add-data` relatif
  (résolu par chemin absolu dans `build.py`, cf. §2).
- L'exe packagé a été testé bout en bout (protocole + OCR réel
  `scan_001.pdf`, accents OK, `page_001.json` écrit dans un dossier
  accentué).
- medium/advanced **non construits** (aucune dépendance lourde dans l'env
  principal, R1/R13 respectés ; `build.py` affiche un avertissement rappelant
  de construire depuis le venv du module).
- **Signature (R11)** : `scripts/sign.py` fonctionne — `signtool.exe` trouvé
  (`C:\Program Files (x86)\Windows Kits\10\bin\10.0.26100.0\x64\signtool.exe`)
  mais **aucun certificat configuré** (`AOCRI_CERT_FILE/AOCRI_CERT_PASSWORD`
  ou `AOCRI_CERT_SHA1` absents) : le script affiche le guide (certificat OV/EV,
  Azure Trusted Signing, ou auto-signé pour tests) et sort avec le code 2.
  `build.py --sign` intègre ce flux (avertissement sans bloquer le build).
  Les binaires ne sont donc **pas signés** à ce stade.

## 7. Commandes exécutées et résultats

| Commande | Résultat |
| --- | --- |
| `python -m pytest tests/ -q` | **131 passed** (avant et après les modifications) |
| `python -m ocr_app.workers.worker_main --help` | usage propre, toutes options documentées |
| worker_main `--mode simple --input data/test_suite/facts/fact_001.pdf --work-dir "%TEMP%\aocri p7 éàç"` | 8 `progress` + `page_done` + `page_001.json` avec texte réel **accents** (R8 : texte natif, confiance 1.0) |
| worker_main `--mode simple --input data/test_suite/scans/scan_001.pdf` (même dossier accentué) | OCR **tesserocr réel** : « Matériel informatique et bureautique… » avec accents |
| worker_main `--mode medium` (simulé 5 pages) : start → pause (2 s) → resume | 5 `page_done`, 5 fichiers R6, gel prouvé par chronométrage ; texte placeholder « moteur RapidOCR non encore installé » |
| worker_main `--mode advanced` + `cancel` en cours | `error` « annulé : traitement interrompu par l'interface », exit 0, < 2 s |
| worker_main `--mode medium --input fact_001.pdf` | protocole complet sur document réel (placeholder) |
| worker_main sans `start` puis EOF stdin | exit 0, aucun message (arrêt propre) |
| `python scripts/make_module.py --module medium --worker <fichier factice> --version 1.0.0` | `dist\modules\medium\module.json` avec sha256 réel `51937cca…f8b2` ; `ModuleRegistry('dist/modules').scan()` → trouvé ; `verify_sha256()` → **True** ; `is_compatible('medium','1.0.0')` → True (module factice nettoyé après vérification) |
| `python scripts/build.py --worker simple` | build complet (voir §6) |
| `dist\...\worker_simple.exe --mode simple --input scans/scan_001.pdf` | OCR réel par l'exe packagé, accents OK |
| `python scripts/sign.py dist\...\worker_simple.exe` | exit 2 + guide (certificat manquant, signtool détecté) |
| TaskRunner hors écran : tâche `advanced` (module absent) | ERROR + message clair, **aucun worker lancé** ; tâche `simple` sur fact_001 → DONE, page réelle chargée |
| `python -m ocr_app.cli --help` | OK |

Pause/cancel ont été testés via stdin (pilote Python maintenant le tube ouvert,
commandes JSON espacées) — cf. tableau ci-dessus.

## 8. Risques et limites

1. **Binaires non signés (R11)** : SmartScreen affichera un avertissement tant
   qu'aucun certificat n'est configuré. `scripts/sign.py` est prêt ; il suffit
   de renseigner `AOCRI_CERT_FILE`+`AOCRI_CERT_PASSWORD` (ou `AOCRI_CERT_SHA1`).
2. **Installateurs non compilés** (ISCC absent) : installer Inno Setup ≥ 6.3
   puis `ISCC.exe installers\core.iss` ; la syntaxe a été validée manuellement
   mais une première compilation réelle reste à faire (notamment la présence
   de `dist\OCRApp\*`, produit par le futur build UI).
3. **`ModuleRegistry.is_compatible`** compare `manifest.version >= app_version`
   (sémantique du module existant, conservée sans refactor). La valeur utile
   `min_app_version` est portée par le manifeste ; si l'on souhaite le contrôle
   inverse (`app >= min_app_version`), il faudra ajuster `module_manifest.py`
   (signalé, non modifié).
4. **Mapping UI → exécutables** : dès qu'un vrai `worker_<mode>.exe` sera
   livré (module.json + sha256 vérifié), `worker_commands` pointera
   automatiquement sur l'exe du module (préférence n° 1) ; en développement,
   le repli reste `python -m ocr_app.workers.worker_main`. Rien à recâbler.
5. **La pause dans le mode simple s'applique entre les étapes du pipeline** :
   un OCR Tesseract en cours ne peut pas être interrompu au milieu d'une page
   (latence maximale = durée d'une étape). L'annulation, elle, est effective
   immédiatement après l'étape en cours (< 2 s constaté).
6. **PDF natif vs scan** : le contenu de `page_001.json` pour une facture
   générée provient du texte natif (R8, confiance 1.0) ; l'OCR tesserocr réel
   n'intervient que sur les scans (vérifié sur `scan_001.pdf`).
7. `dist/modules` est créé par `make_module.py` ; un module de test laissé en
   place ferait croire l'UI au module installé (le module factice de
   vérification a été supprimé après test).
8. Les chemins du projet contenant espaces/accents ont été utilisés pour les
   dossiers de travail (`%TEMP%\aocri p7 éàç`), conformément à R14.

## 9. État de sortie

- Tests : **131 passed**.
- Livrables de build : `dist\worker_simple\worker_simple\` (exe testé OCR réel).
- Prochaine étape naturelle (phase 4 du plan / packaging UI) : build
  `OCRApp.exe` (UI PySide6) pour compléter `dist\OCRApp\*` attendu par
  `core.iss`, puis compilation des trois installateurs dès qu'ISCC est
  disponible, puis signature.
