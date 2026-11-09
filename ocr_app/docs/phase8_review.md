# Revue Phase 8 — finitions v1.0 (lots, relance de page, réglages, recette)

**Date :** 2026-11-09
**Périmètre (code du codeur, revu puis durci) :** `workers/worker_main.py`
(`--page`/`--lang`), `ui/settings_dialog.py` et `ui/app_settings.py` (nouveaux),
`ui/main_window.py`, `ui/widgets/toolbar.py`, `ui/widgets/status_bar.py`,
`ui/task_runner.py`, `scripts/acceptance.py` (nouveau).
**Hors périmètre (non modifiés) :** `tests/`, `core/*`, `fake_worker.py`,
`edit_session.py`, `clipboard*`, `exports.py`, `export_dialog.py`,
`widgets/preview.py`, `widgets/results_panel.py`, `worker_commands.py`,
`modules_info.py`, `installers/`, `data/`. Retouche mineure : `README.md`.
**Méthode :** lecture croisée des fichiers au fil de leur stabilisation
(suivi de l'écriture du codeur), harnais stdin/stdout hors dépôt contre
`worker_main --page/--lang`, harnais PySide6 offscreen (6 scénarios, 41
contrôles : lots, annulations, relance, réglages), tests de chronométrage
pause/cancel sur scan réel, exécution complète de la recette, suite pytest
avant et après chaque modification.

## 1. Conclusion par objectif

| Objectif | Verdict |
| --- | --- |
| 1. Lots | **Conforme après durcissement** — enchaînement séquentiel sans double démarrage (garde double : `_current_id` + `WorkerClient.start()` False → ERROR ; mesuré : 3 démarrages pour 3 tâches, jamais 2 tâches actives simultanées), compteur de lot exact (0/3 → 3/3, une incrémentation par tâche via `_batch_counted`), annulation d'une tâche ≠ arrêt du lot (mesuré). Deux durcissements : **« Tout annuler » explicite ajouté** (constat 6) et **annonce des fichiers non pris en charge d'un dossier** (constat 5). |
| 2. Relance de page | **Conforme** — `--page N` validé au worker (≥ 1 sinon exit 2 d'usage ; > total → erreur protocolaire exit 1 « page N hors document »), `page_done` porte le numéro réel (jamais 1), total de progression = 1, JSON d'origine **jamais écrasé** (chaque tâche a son propre `task_work_dir` `stem_id8` — hash mesuré identique après relance), pas de relance concurrente (worker unique séquentiel + `is_document_busy` → items grisés avec raison + re-vérification au clic), échec de relance → message clair et résultat d'origine intact (mesuré : relance Moyen sans module → ERROR « Module « Moyen » non installé… »). |
| 3. Réglages | **Conforme après correction** — `settings.json` dans `%LOCALAPPDATA%\AOCRI` (surcharge test `AOCRI_SETTINGS_DIR`), lecture robuste (JSON corrompu / clé absente / liste vide → défauts, mesuré sans crash), au moins une langue (OK désactivé sinon + repli fra+eng à la lecture), DPI borné ≤ 300 aux trois niveaux (600 → 300 mesuré, R7), aucune création du fichier à l'import ni à la lecture (lazy, mesuré), chemins accentués OK (settings + dossiers de travail accentués, mesurés). Correction : **écriture atomique** temp + `os.replace` (constat 1). |
| 4. Protocole | **Conforme** — `--page`/`--lang` n'ajoutent aucun type de message nouveau ; pause (gel mesuré 1,5 s muette), resume, EOF et annulation inchangés ; **cancel mesuré à 0,06–0,07 s en pleine OCR avec `--page`** (R2 < 2 s) ; le worker factice ne reçoit ni `--page`, ni `--lang`, ni `--dpi` (filtrés par `accepts_input_document`) ; **181 tests verts avant et après les modifications** ; `fake_worker.py` et le contrat JSON intacts. |
| 5. Recette `acceptance.py` | **Conforme après corrections** — exit code fiable (0 = PASS, 1 = FAIL), vérifications réellement mesurées (exit, fichiers, texte > 200 caractères, ≥ 10 accents, JSON valide, `page_done` page réelle, total = 1, dossier accentué respecté), travail dans `%TEMP%` avec nettoyage. Trois durcissements : **SKIP rapporté si tesserocr absent** (constat 3), **blindage exceptions** (constat 4) et correction du pilote sans commande `start` (constat 2, corrigé par le codeur pendant la revue). Recette complète : **17/17 PASS**. |
| 6. Qt | **Conforme** — dialogue modal correct (`setModal(True)`, parenté, `deleteLater` dans les deux branches), connexions uniques (`_connect_signals` appelé une fois), réglages rechargés sans fuite (nouveau dialogue par ouverture, résultat copié dans `self._settings`), menus `deleteLater` après `exec`. Deux durcissements : tooltips du menu de relance rendus visibles (constat 7) et QMessageBox des boutons annexes parentés au dialogue modal (constat 9). |
| 7. Cohérence v1.0 | **Conforme** — version `1.0.0` unique (`pyproject.toml`, `ocr_app.__version__`, À propos), « v1.0 » dans le titre de fenêtre (marque « ● » conservée sur édition) et le pied du dialogue ; README retouché (fonctionnalités v1.0 + recette). |

## 2. Constats et corrections

Gravités : **C** = critique, **É** = élevée, **M** = moyenne, **F** = faible.
« Avant » = état du codeur à la stabilisation, « après » = fichier corrigé par
la revue.

| # | Fichier:ligne avant → après | Grav. | Constat | Correction / mesure |
|---|---|---|---|---|
| 1 | `ui/app_settings.py:158` → `146-181` | **É** | `save_settings` écrivait `settings.json` par `write_text` direct : un arrêt brutal (coupure, disque plein) en pleine écriture laissait un JSON tronqué — rechargé comme corrompu, les réglages de l'utilisateur retombaient silencieusement aux défauts (exigence « écriture atomique »). | Fichier temporaire `settings.json.tmp` dans le **même dossier** puis `os.replace` (atomique sur Windows) ; le `.tmp` est supprimé si le renommage échoue (disque plein, verrou antivirus), l'ancien réglage restant intact. Mesuré : réécriture + relecture fidèle, aucun `.tmp` résiduel. |
| 2 | `scripts/acceptance.py:197-207` (version intermédiaire 22:01) → corrigé par le codeur à 22:02 (`_drive_worker`, 187-246) | **É** | L'étape 3 lançait le worker avec `subprocess.run(..., input="")` : stdin fermé **sans commande `start`** — le protocole exige `start`, le worker sort 0 sans aucun message et toutes les vérifications `page_done` auraient été FAIL systématiques. Mesure faite pendant la revue : sans `start`, exit 0 et stdout vide. | Corrigé par le codeur pendant la stabilisation (repéré par la revue avant l'exécution) : pilotage `Popen` avec envoi de `start`, lecture des lignes jusqu'à `page_done`, stdin fermé ensuite pour un arrêt propre (docstring exacte : « un EOF serait interprété comme une annulation »). Revalidé par la recette complète (17/17). |
| 3 | `scripts/acceptance.py` (avant) → `66-84,110-114,410-413` | **M** | Aucun moteur manquant n'était anticipé : sans tesserocr, `CLI scan/mixte` et `worker --page 1` passaient en **FAIL** (exigence « moteur manquant → skip rapporté »). | `tesserocr_available()` sonde tesserocr **dans un sous-processus lancé comme les enfants de la recette** (même interpréteur + `PYTHONPATH` enfant) — une détection `find_spec` dans le parent donnait une fausse négation sous `python -I` (observé pendant la revue) ; les vérifications qui exigent un OCR réel (scan, mixte, worker) sont rapportées `[SKIP]`, listées dans le rapport final, et la recette reste PASS. Le document natif (R8, sans OCR) reste toujours vérifié. Mesuré : simulation moteur absent → 3 SKIP, 4/4 PASS, exit 0. |
| 4 | `scripts/acceptance.py:85-93,113-127,197` → `82-107,163-183,379-400` | **M** | Une exception (`TimeoutExpired`, OSError de lancement) cassait la recette avec traceback **sans rapport** ; les dossiers `%TEMP%` créés (`mkdtemp`) n'étaient jamais nettoyés (pollution au fil des exécutions). | Chaque étape est blindée (`try/except` → FAIL propre avec message, rapport imprimé) ; `shutil.rmtree(..., ignore_errors=True)` en `finally` sur les dossiers de la recette, après les vérifications (le résultat est lu avant suppression). |
| 5 | `ui/main_window.py:301-315` → `294-335` | **M** | `_collect_folder_documents` écartait les fichiers non pris en charge d'un dossier **en silence** (aucune trace s'il reste des fichiers valides) — contrariait « fichiers non supportés → ERROR clair sans bloquer le reste du lot ». | Retour `(documents, nb_ignorés)` ; si des fichiers ont été écartés, toast d'erreur explicite « N fichier(s) ignoré(s) : format non pris en charge (PDF, JPG, PNG attendus). » après le lancement du lot — le lot continue inchangé. Mesuré : dossier avec `note.txt` + 3 PDF → lot complet + annonce. |
| 6 | `ui/task_runner.py` + `ui/widgets/status_bar.py` + `ui/main_window.py` → `task_runner.py:381-404`, `status_bar.py:49,154-168,326-332`, `main_window.py:607-621` | **M** | Aucune action « tout annuler » : l'annulation d'une tâche laisse le lot continuer (voulu) mais la seule façon d'arrêter un lot de N fichiers était de fermer l'application — le « sauf « tout annuler » explicitement » de l'exigence lots n'avait pas de support. | `TaskRunner.cancel_all()` (tâches PENDING → CANCELLED, file vidée, tâche courante annulée via `cancel_current`, retour = nombre annulé) ; bouton « Tout annuler » symétrique de « Tout traiter » (visible seulement si des tâches attendent, signal `cancel_all_clicked`, handler `_cancel_all` avec garde démo). Mesuré : lot de 3 → 1 démarrage puis 3 CANCELLED, compteur (3,3), aucun nouveau démarrage. |
| 7 | `ui/main_window.py:607-625` → `648-658` | **F** | Les raisons de désactivation du menu de relance (module non installé, traitement en cours) étaient portées par `setToolTip` — mais `QMenu` n'affiche pas les tooltips par défaut : l'action grisée restait **inexpliquée**. | `submenu.setToolTipsVisible(True)` avant la création des items. |
| 8 | `ui/main_window.py:550-555` → `553-560` | **F** | `_on_error` appelait `set_task(task)` sans `title_override` : une relance en erreur affichait « Page 1 / 1 — erreur » (sans sexe) au lieu de « Relance page N — erreur ». | `title_override=self._relaunch_title(task)` comme dans `_on_task_changed` / `_on_page_loaded`. |
| 9 | `ui/main_window.py:814-816` → `865-866,905,919` | **F** | Les QMessageBox « Modules OCR » / « À propos », déclenchés depuis les boutons annexes du dialogue **modal** de réglages, étaient parentés à la fenêtre principale : risque d'empilement sous le dialogue modal (fenêtre figée en apparence). | `_show_modules(parent=None)` / `_show_about(parent=None)` ; les signaux du dialogue passent le dialogue comme parent (lambdas). |
| 10 | `ui/task_runner.py:182-187` → `175-192` | **F** | `set_worker_settings` codait l'ordre canonique des langues en dur (`"fra", "eng"`) — doublon fragile de `app_settings.AVAILABLE_LANGUAGES` (une langue ajoutée un jour serait ordonnée différemment dans l'indicateur et l'argument `--lang`). | Ordre issu d'`AVAILABLE_LANGUAGES` (import, module sans Qt) ; toute langue inconnue reste conservée en fin (tri alphabétique) au lieu d'être perdue. |

Autres points vérifiés sans correction : garde anti double-démarrage double
(`_start_next` refuse si `_current_id` est posé ; `WorkerClient.start()`
retourne False si un process vit encore → tâche ERROR consultable) ;
comptage du lot réalisé dans `_emit_task_changed` (une seule incrémentation
par tâche grâce à `_batch_counted`, y compris pour les ERROR immédiates
« module non installé » sans worker) ; `pages_done` d'une relance borné à 1
pour que la fin de worker passe la tâche en DONE proprement ; le worker
valide les bornes de `--page` AVANT `Pipeline.process_page` (ce dernier
replie silencieusement hors bornes sur la page 1 — jamais atteint depuis le
worker) ; `--lang` accepté en Moyen/Avancé sans effet (moteurs squelettes
R13) et journalisé ; détection d'un PDF corrompu → erreur protocolaire →
tâche ERROR sans arrêter le lot (harnais phase 7, inchangé) ;
`dialog.exec()` → `deleteLater` dans les deux branches (aucune fuite) ;
`_choose_folder` : QMessageBox avec case « sous-dossiers », boutons
« Ajouter »/« Annuler » renommés.

## 3. Vérifications exécutées (mesures)

| Vérification | Résultat |
| --- | --- |
| `python -m pytest tests/ -q` | **181 passed** — avant les modifications (baseline), après chaque vague de modifications et en final. |
| Harnais offscreen (6 scénarios, 41 contrôles) | **41/41 PASS** : (A) lot de 3 → 3 DONE, 3 démarrages uniques, compteur (0,3)→(3,3), jamais 2 tâches actives simultanées ; (B) annulation de la tâche 2 en cours → lot continue, tâche 3 DONE, compteur (3,3) ; (C) `cancel_all` → 3 CANCELLED, 1 seul démarrage, compteur (3,3) ; (D) relance page 1 de `fact_001.pdf` → `page_done` page 1, dossier de travail distinct (`fact_001_c2f082ac` vs `fact_001_7f58bb87`), **hash du JSON d'origine identique** ; (E) relance Moyen sans module → ERROR « Module « Moyen » non installé… », original intact ; (F) settings corrompu → défauts, DPI 600 → 300, liste vide → fra+eng, lazy (aucun fichier créé à la lecture), écriture atomique sans `.tmp` résiduel. |
| Protocole `--page`/`--lang` (harnais stdin/stdout) | `--page 1` sur `fact_001.pdf` → protocole complet, 1 `page_done` **page 1**, `total = 1` ; `--page 99` → erreur protocolaire « page 99 hors document (1 page(s)) », exit 1 ; `--page 0` → exit 2 d'usage sans ligne protocolaire (contrat argparse inchangé, cohérent avec le test `test_unknown_mode_rejected_cleanly`) ; `--lang fra` → moteur injecté, journalisé, traitement complet. |
| R2 en relance (chronométrage sur `scan_001.pdf`, OCR réel) | Avec `--page 1` : pause → **gel réel** (1,5 s sans aucun message), resume → fin normale ; cancel en pleine OCR → **0,07 s** (0,06 s avec `--lang fra+eng --dpi 200`), exit 0, message protocolaire « annulé : traitement interrompu par l'interface ». |
| Recette `python scripts/acceptance.py` (complète) | **17/17 vérifications PASS — RECETTE GLOBALE : PASS (exit 0)**, y compris l'étape pytest (181 tests). Travail exclusivement dans `%TEMP%`, dossiers nettoyés (aucun résidu `aocri_acceptation*`/`aocri worker*`). |
| Recette sans moteur (simulation tesserocr absent) | 4/4 PASS, **3 SKIP listés** dans le rapport, exit 0 — pas de FAIL, pas de crash. |
| Sans commande `start` (ancien pilote, mesure) | exit 0, **stdout vide** — confirme que la version intermédiaire `input=""` de la recette aurait FAIL l'étape 3 (constat 2). |

## 4. Risques résiduels

- **`--page` invalide en CLI direct** (ex. `--page 0`) : exit 2 sans message
  protocolaire (stdout vide) — l'interface l'afficherait comme « traitement
  incomplet ». Contrat argparse volontairement inchangé (cohérent avec le
  mode inconnu et le test existant) ; l'UI valide toujours avant de lancer.
  Risque faible (aucun chemin de l'UI ne produit `--page < 1`).
- **Compteur de lot** : CANCELLED et ERROR sont comptés dans « Lot : X/N
  terminés » — le comptage des tâches arrivées à un état terminal est exact,
  mais le libellé « terminés » est approximatif si l'utilisateur annule
  (« traités » serait plus juste). Choix conservateur conservé.
- **Relance non fusionnée** : le résultat d'une relance est une tâche
  indépendante (aperçu auto-sélectionné, JSON séparé) ; il n'est pas injecté
  dans le document d'origine (choix du codeur, conforme à « jamais écrasé
  sans distinction »). L'export « tout le document » d'une relance ne porte
  donc que les pages de cette tâche.
- **`--lang` sans effet en Moyen/Avancé** : moteurs squelettes (R13) —
  l'option est acceptée et journalisée, le contrat est stable.
- **Exécutables packagés** : `--page`/`--lang`/`--dpi` non testés ici contre
  un `worker_<mode>.exe` (aucun build dans la revue) ; tous les exécutables
  produits par `scripts/build.py` dérivent de `worker_main`, le comportement
  est attendu identique.
- **Panneau latéral** : la ligne d'une relance affiche le nom du fichier
  sans le distinctif « Relance page N » (`file_sidebar.py` hors périmètre) ;
  le distinctif reste porté par l'en-tête d'aperçu, la barre inférieure et
  les toasts.
