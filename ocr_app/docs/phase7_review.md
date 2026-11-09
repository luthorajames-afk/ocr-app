# Revue Phase 7 — workers par mode, packaging, détection de modules

**Date :** 2026-11-09
**Périmètre (code du codeur, revu puis durci) :** `workers/worker_main.py`,
`ui/worker_commands.py`, `ui/task_runner.py`, `ui/worker_client.py`,
`ui/modules_info.py`, `ui/main_window.py`, `scripts/build.py`,
`scripts/make_module.py`, `scripts/sign.py`, `installers/*.iss`.
**Hors périmètre (non modifiés) :** `tests/`, `core/*`, `fake_worker.py`,
`edit_session.py`, `clipboard*`, `exports.py`, `widgets/*`, `data/`,
`modules/module_manifest.py`.
**Méthode :** lecture croisée des fichiers au fil de leur stabilisation,
harnais stdin/stdout hors dépôt contre `worker_main` (document réel,
R14, pause, cancel, EOF, fichiers invalides), exécution réelle de l'exécutable
packagé, script de contrôle croisé des 3 `.iss`, suite pytest complète.

## 1. Conclusion par objectif

| Objectif | Verdict |
| --- | --- |
| Protocole worker (`worker_main.py`) | **Conforme** — mêmes garanties que `fake_worker` durci (une ligne JSON par message, verrou de sérialisation, diagnostics stderr, UTF-8 forcé, pause réelle, EOF propre, erreurs protocolaires). Un durcissement appliqué : **cancel < 2 s garanti même en pleine étape OCR** (voir constat 1). |
| Mapping par mode | **Conforme après corrections** — simple toujours disponible, messages « module non installé » clairs, `module.json` corrompu → mode grisé (`verify_sha256` False → ignoré, vérifié). Trois corrections pour les exécutables packagés (constats 5-7) et une pour la détection du dossier d'installation (constat 8). |
| `make_module.py` | **Conforme après corrections** — sha256 réellement calculé sur le worker copié ; `--force` ajouté (constat 15) ; version validée (constat 16) ; path traversal impossible (vérifié côté génération et côté registre). |
| `build.py` | **Conforme après corrections** — `--onedir` strict (aucun chemin vers `--onefile`), noms `worker_<mode>` cohérents, codes d'erreur propres, build réel réussi (26 s, exe fonctionnel). Refus de medium/advanced sans dépendances ajouté (constat 12). |
| Inno Setup | **Conforme après correction** — AppId partagé identique ×3, `PrivilegesRequired=lowest` ×3, chemins `{userappdata}\Programs\AOCRI` cohérents, pas de chemin absolu codé en dur, pas de `[Run]` lançant un worker (bloquait sur stdin). Correction R15 majeure : `UsePreviousAppDir=no` (constat 18). Compilation impossible ici (ISCC absent) : validé par lecture croisée + script. |
| `sign.py` | **Conforme** — sans certificat : guide d'installation affiché, code 2, aucun téléchargement, `build.py --sign` continue avec avertissement. |
| R15 (montée de version d'un module) | **Conforme après correction** — les modules s'installent dans `{userappdata}\Programs\AOCRI\modules\<mode>` (jamais la racine du noyau), le noyau ne liste que ses propres fichiers ; `min_app_version` désormais honoré (constat 9). |
| Robustesse (fichier inexistant/corrompu, R14) | **Conforme** — erreurs protocolaires propres, mesurées (voir §4). |

## 2. Constats et corrections

Gravités : **C** = critique (perte de données / objectif de phase rompu),
**É** = élevée, **M** = moyenne, **F** = faible. « Avant » = état du codeur à
la stabilisation, « après » = fichier corrigé par la revue.

| # | Fichier:ligne avant → après | Grav. | Constat | Correction / mesure |
|---|---|---|---|---|
| 1 | `workers/worker_main.py:371-377` → `348-406` | **É** | Le pipeline réel tournait dans le thread principal : le cancel n'était vu qu'**entre** deux étapes. Pendant un OCR Tesseract de plusieurs secondes, `cancel` attendait la fin de l'étape : **2,43 s** mesurés sur `scan_001.pdf` (> 2 s, R2). | Pipeline déplacé dans un thread d'assistance **daemon** surveillé par la boucle principale toutes les 20 ms (`POLL_INTERVAL`) : annulation/EOF → abandon immédiat (pas de `close()` du moteur pendant l'abandon, le thread meurt avec le processus). Mesuré après : **0,04–0,08 s** sur 3 essais. Le close() est conservé sur les chemins normaux et d'erreur document. |
| 2 | `workers/worker_main.py:561` → supprimé | F | `main()` affichait deux fois l'erreur de mode sur stderr (`_log` + `print` dupliqués). | Ligne dupliquée supprimée (`_log` suffit). |
| 3 | `workers/worker_main.py:200,360` → supprimé | F | Attribut mort `_document_total` (écrit, jamais lu). | Supprimé. |
| 4 | `ui/task_runner.py:163-172` → `160-180` | **É** | Une tâche dont le mode n'a **ni commande ni message d'indisponibilité** était retirée de la file **sans statut** (PENDING pour toujours, jamais traitée, aucun message) — contredisait le contrat documenté du `TaskRunner` (repli `fake_worker`). | Le repli historique `command=None` (worker factice de développement) est restauré ; seuls les modes avec message d'indisponibilité passent en erreur immédiate sans lancer de worker. |
| 5 | `ui/worker_commands.py:38-41` → `52-67,131` | **É** | La commande résolue était toujours `sys.executable -u -m ocr_app.workers.worker_main`. En application **gelée**, `sys.executable` est `OCRApp.exe` : l'interface se serait relancée elle-même comme « worker ». Les exécutables installés (`worker_<mode>.exe` livrés par `core.iss` et les manifestes `module.json`) n'étaient jamais utilisés. | `_core_worker_command()` (app gelée : `<dossier de l'exe>\workers\worker_<mode>\worker_<mode>.exe`, layout du `core.iss`) et `_manifest_worker_command()` (exécutable du manifeste vérifié via `find_installed_module`). En développement, la commande `python -m` reste la valeur par défaut (tests inchangés). |
| 6 | `ui/worker_commands.py:44-52` → `82-97` | **É** | `accepts_input_document` ne reconnaissait que le nom de module : un **exécutable packagé** n'aurait jamais reçu `--input` et aurait simulé N pages placeholder au lieu d'OCR le document. | Reconnaît aussi un `.exe` existant en tête de commande (tous les exécutables produits par `build.py` dérivent de `worker_main`). Vérifié : `True` pour module et exe, `False` pour `fake_worker`/`None`. |
| 7 | `ui/worker_commands.py` (commandes exe) → `64-66,135-140` | **É** | Un exécutable lancé sans `--mode` tourne en mode **simple** (défaut de `worker_main`) : `worker_medium.exe` aurait OCR avec Tesseract (ou échoué, le venv du module n'ayant pas tesserocr). | `--mode <mode>` toujours passé explicitement avec l'exécutable. Vérifié : commande résolue `('...\worker_medium.exe', '--mode', 'medium')`. |
| 8 | `ui/modules_info.py:64-79` → `64-85` | **C** | `registry_dirs()` ne scannait **jamais** le dossier d'installation livré par les `.iss` (`{userappdata}\Programs\AOCRI\modules`) : un module installé par `medium.iss`/`advanced.iss` restait invisible → mode « non installé » malgré l'installation (objectif détection de modules rompu en production). | Ajout de `<dossier de l'exécutable>/modules` (app gelée = dossier d'installation du noyau ; en développement le chemin à côté du venv n'existe pas et est ignoré). Priorité : surcharge env > installation > AppLocalData > sorties de build > manifests de dev. |
| 9 | `ui/modules_info.py:105` → `87-100,129-131` | **É** | Compatibilité de version : `registry.is_compatible(id, APP_VERSION)` compare `manifest.version >= version_app` (mauvais champ, mauvais sens) ; `min_app_version` du manifeste était ignoré. | `_version_tuple()` (comparaison numérique 1.10 > 1.9, préfixe v, suffixe pré-version) et test `_version_tuple(min_app_version) <= _version_tuple(APP_VERSION)`. Vérifié : manifeste `min_app_version=99.0.0` → module ignoré ; `1.0.0` → retenu. |
| 10 | `ui/modules_info.py:33` → `35` | F | `APP_VERSION` dupliqué en dur (« 1.0.0 »). | Import de `ocr_app.__version__` (source unique). |
| 11 | `scripts/build.py:116-120` → `72-90,155-170` | **É** | medium/advanced étaient construits dans l'environnement courant avec un simple **avertissement** (R1/R13) : un exécutable cassé au premier OCR pouvait être produit en silence. | `check_module_dependencies()` (find_spec, sans import lourd) : build **refusé** (code 1, message explicite citant le venv du module) si aucun des paquets attendus n'est importable. Mesuré : `--worker medium` refusé dans l'env noyau. |
| 12 | `scripts/build.py:39` + ajout `92-101,165-169` | M | (a) La source `--add-data` était relative au CWD (corrigée par le codeur en chemin absolu pendant la revue — vérifié). (b) Aucune vérification que `models/tessdata` existe avant le build. | Ajout de `check_tessdata()` : build **refusé** si `models/tessdata` est absente ou sans `*.traineddata` (R4, message explicite). |
| 13 | `scripts/make_module.py:99-102` → `94-105` | **É** | `module.json` existant était **écrasé silencieusement** (exigence de revue : `--force`). | Refus avec message + option `--force` ; le contrôle est effectué **avant toute copie** (worker/modèles) pour ne jamais modifier le dossier cible à moitié en cas de refus. Testé : refus sans `--force`, réécriture avec `--force` (1.0.0 → 1.1.0). |
| 14 | `scripts/make_module.py:64-66` → `50-58,87-93` | M | Version non validée : `"abc"` serait devenue `(0,0,0)` dans les comparaisons → module silencieusement incompatible. | `_validate_version()` appliquée à `--version` et `--min-app-version` (format X[.Y[.Z]] numérique, préfixe v/V et suffixe de pré-version tolérés). Testé : `--version abc` refusé (exit 1). |
| 15 | `installers/medium.iss`, `installers/advanced.iss` → `:36` / `:38` | **C** | AppId partagé + `UsePreviousAppDir` (défaut : oui) : l'installateur du module pré-remplit la page de dossier avec la **dernière installation enregistrée** — le dossier du **noyau** — et y aurait copié `worker_*.exe` + `module.json` + `models` à la racine (pollution/écrasement du noyau, R15 rompu). | `UsePreviousAppDir=no` dans les deux `.iss` de module (commentaire explicite) : le module s'installe toujours dans `{userappdata}\Programs\AOCRI\modules\<mode>`. |
| 16 | En-têtes des 3 `.iss` → commentaires corrigés | M | Les en-têtes affirmaient une « désinstallation indépendante » du module. Avec un AppId partagé, Inno **ajoute** les fichiers de chaque installation au **même** journal de désinstallation (doc `setup_appid` : « Setup will only append to an uninstall log if the AppId … is the same ») : la désinstallation via « Applications et fonctionnalités » retire le noyau **et** les modules. | Comportement réel documenté dans les 3 `.iss` (retrait d'un module seul = suppression du dossier `modules\<mode>` ; `core.iss` ne liste que ses propres fichiers, donc une réparation/réinstallation du noyau n'efface pas les modules). |
| 17 | `installers/core.iss:15` | F | Le commentaire de sortie contredisait la consigne de compilation (« installers\dist\installers » vs chemins relatifs au fichier). | Commentaires alignés (Source/Output relatifs au dossier du `.iss`). |
| 18 | `scripts/build.py:49,58` | F | Notes de build avec majuscule manquante après point dans le message de refus. | Capitalisées. |

Autres points vérifiés sans correction : valeur AppId `{{…}}` → `{8A0C…6001}}`
(accolade finale double ; sans conséquence : chaîne **identique dans les 3
fichiers**, donc même clé de registre partagée) ; `Check: FileExists(ExpandConstant(...))`
est une syntaxe Inno valide (fonction de support Pascal, paramètre non
pré-développé → `ExpandConstant` requis et présent) ; `mot de passe .pfx`
passé à signtool via `/p` (visible dans la liste des processus le temps de la
signature — inhérent à signtool, documenté) ; `make_module` n'importe quel
fichier peut servir de « worker » (le sha256 est exact, mais la validation que
l'exe est un vrai build reste un geste de release).

## 3. Mesures avant / après

Machine : Windows 11, Python 3.14.8, PySide6 6.12.0, PyInstaller 6.22.3.

| Mesure | Avant | Après |
|---|---|---|
| Cancel en pleine étape OCR réel (`scan_001.pdf`, 300 DPI) | **2,43 s** (fin d'étape attendue) | **0,04 / 0,08 / 0,08 s** (3 essais, thread daemon abandonné) |
| Cancel en simulation (200 pages, delay 0.05) | — | **0,02 s**, message « annulé » émis, exit 0 |
| EOF propre après `start` | — | exit 0 en **0,03 s**, aucun traceback |
| Pause réelle | — | 0 message pendant 1,2 s figée, reprise effective |
| Fichier inexistant | — | exit 1, `error` « erreur de document : document introuvable : … » |
| PDF corrompu | — | exit 1, `error` « erreur de document : PDF illisible ou corrompu : … » |
| Suite pytest | 131 passés | **131 passés** (aucune régression) |
| Build PyInstaller `--worker simple` | — | exit 0 en 26 s, `dist/worker_simple/worker_simple/worker_simple.exe` (--onedir, R3) |

## 4. Vérifications exécutées

1. **Protocole via stdin/stdout** (harnais hors dépôt, `PYTHONUTF8=1`,
   `PYTHONPATH=src`) : 11/11 contrôles OK — document réel, R14, cancel,
   pause, EOF, fichiers invalides, pureté stdout (0 ligne hors protocole).
2. **Worker simple sur `fact_001.pdf`** → `page_001.json` avec texte OCR
   réel (1290 caractères, « SARL Dupont & Fils », « FA-2026-0101 » ; R8 :
   texte natif utilisé sans rendu). Idem via un chemin **accentué et espacé**
   (`Dossiers AOCRI éàç\facture n°101.pdf`, R14).
3. **Exécutable packagé** (`worker_simple.exe`) lancé depuis un dossier
   accentué hors dépôt, avec `--input` : protocole complet, `page_001.json`
   réel — la tessdata embarquée par `--add-data` est bien résolue au runtime
   (R4), le CWD du dépôt n'est pas nécessaire.
4. **`make_module.py`** : `module.json` écrit avec sha256 réel ;
   `ModuleRegistry(...).scan()` + `verify_sha256()` → True ; manifeste
   corrompu (`worker=..\..\evil.exe`) → `verify_sha256` False → module
   ignoré (pas de path traversal) ; écrasement refusé sans `--force` ;
   version invalide refusée.
5. **Chaîne complète de détection** (`AOCRI_MODULES_DIR` vers le module
   préparé) : `detect_modules` → medium « module.json v1.1.0 (worker
   vérifié, …) » ; `resolve_worker_commands` → commande
   `worker_medium.exe --mode medium` ; advanced reste « non installé » avec
   son message clair.
6. **`build.py`** : `--worker medium` dans l'env noyau → **refusé** (exit 1,
   R13) ; `--worker simple` → build réel réussi, exe testé (voir 3).
7. **`sign.py`** sans signtool ni certificat : guide d'installation affiché
   (Windows SDK, certificats, variables AOCRI_*), exit **2**, aucun
   téléchargement ; `build.py --sign` reste vert dans ce cas (avertissement).
8. **Installateurs** : contrôle croisé automatisé des 3 `.iss`
   (19 vérifications, toutes OK) : AppId identique, échappement `{{`,
   `PrivilegesRequired=lowest` ×3, `DefaultDirName` sous
   `{userappdata}\Programs\AOCRI`, aucun chemin absolu codé en dur, noms
   `AOCRI-{core,medium,advanced}-setup` cohérents avec le chaînage `[Run]`
   du noyau, aucun lancement de worker en `[Run]`, `UsePreviousAppDir=no`
   sur les modules. Compilation impossible sur cette machine (ISCC.exe
   absent) — voir risques.

## 5. Risques restants

1. **Désinstallation fusionnée (AppId partagé, imposé)** : « Applications et
   fonctionnalités » retire le noyau ET les modules (journal unique). Le
   retrait d'un module seul passe par la suppression de son dossier. À faire
   valider comme choix produit (alternative standard : AppId distinct par
   module, mais alors 3 entrées distinctes).
2. **`.iss` non compilés ici** (ISCC absent) : la syntaxe est standard mais
   le chaînage `[Run]` (`{src}`, `Check: FileExists`) et `UsePreviousAppDir`
   restent à valider sur une machine avec Inno Setup 6.3+
   (`ArchitecturesAllowed=x64compatible` l'exige).
3. **Pause pendant une longue étape OCR** : effective entre deux étapes
   (pas d'interruption interne de tesserocr) — conforme à la granularité de
   `fake_worker` ; le cancel, lui, est immédiat (constat 1).
4. **Pages simulées** (sans `--input`) en mode simple : confiance 0,97
   (`_PLACEHOLDER_CONFIDENCE`) — elles ressemblent à de l'OCR réel dans
   l'UI ; la distinction reste le texte « simulation Simple » du bloc.
5. **Tessdata dupliquée** dans le produit noyau (embarquée dans
   `worker_simple.exe` **et** copiée dans `{app}\models\tessdata` par
   `core.iss`) : coût disque (~10 Mio), toléré pour la robustesse (R4).
6. **`make_module.py`** n'exige pas que le fichier fourni soit un vrai
   build `worker_main` : la vérification fonctionnelle de l'exécutable reste
   un geste du processus de release (sha256 exact, lui, garanti).
7. **R11 sans certificat** : SmartScreen affichera l'avertissement ; le
   parcours complet (certificat de test → signature → vérification
   `signtool verify`) reste à exécuter sur une machine équipée.

## 6. Travaux appliqués aux fichiers (récapitulatif)

- `src/ocr_app/workers/worker_main.py` : pipeline réel en thread daemon
  surveillé (cancel < 2 s), nettoyages (doublon stderr, attribut mort).
- `src/ocr_app/ui/worker_commands.py` : commandes exécutables (frozen +
  manifeste) avec `--mode`, `accepts_input_document` étendu aux `.exe`.
- `src/ocr_app/ui/modules_info.py` : dossier `modules` de l'installation
  scanné, `min_app_version` honoré (comparaison numérique locale),
  `APP_VERSION` dédupliqué.
- `src/ocr_app/ui/task_runner.py` : repli `fake_worker` restauré (aucune
  tâche abandonnée en PENDING).
- `scripts/build.py` : refus medium/advanced sans dépendances (R1/R13),
  pré-vérification tessdata (R4), capitalisation.
- `scripts/make_module.py` : `--force` + validation de version, contrôle
  avant toute copie.
- `installers/medium.iss`, `installers/advanced.iss` :
  `UsePreviousAppDir=no` (R15). `installers/*.iss` : commentaires alignés
  sur le comportement réel de l'AppId partagé.
- `docs/phase7_review.md` : le présent rapport (le rapport du codeur est
  `docs/phase7_report.md`).

Scripts de vérification (hors dépôt, temporaires) : harnais de protocole,
harnais d'annulation réelle, harnais de l'exécutable packagé, script de
contrôle croisé des `.iss`.
