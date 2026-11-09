# Phase 8 — Finitions v1.0

Date : 2026-11-09 · Branche : `main` · Base : `b2634e5` (phase 7)

## Périmètre

Phase 8 = dernière phase avant v1.0 :

1. traitement par lots (« Ajouter un dossier », enchaînement séquentiel,
   compteur global « Lot : X/N terminés », bouton « Tout traiter ») ;
2. relance d'une page dans un autre mode (menu contextuel de l'aperçu →
   tâche ciblée `--input` + `--page`, statut distinct « Relance page N ») ;
3. réglages OCR persistants (langues fra/eng, DPI 150/200/300) ouverts depuis
   la barre d'outils et passés aux workers (`--lang`, `--dpi`) ;
4. recette d'acceptation automatisée `scripts/acceptance.py` ;
5. branding « v1.0 » (barre de titre, à-propos, dialogue de réglages).

## Fichiers créés

| Fichier | Rôle |
|---|---|
| `src/ocr_app/ui/app_settings.py` | Réglages persistants (module sans Qt, réutilisable par la recette) : `AppSettings`, `load_settings`/`save_settings` dans `%LOCALAPPDATA%\AOCRI\settings.json`, UTF-8 explicite (R14), valeurs par défaut robustes (fichier absent/corrompu/keys invalides → fra+eng, 300 DPI), DPI borné à 300 (R7) |
| `src/ocr_app/ui/settings_dialog.py` | `SettingsDialog` : cases à cocher fra/eng (« au moins une » : bouton OK désactivé sinon), DPI 150/200/300 par boutons radio (défaut 300), OK/Annuler, boutons annexes (Dossier de travail, Modules OCR…, Aperçu de démonstration, À propos) émis en signaux, pied « AOCRI v1.0 » |
| `scripts/acceptance.py` | Recette v1.0 automatisée (détail plus bas) |

## Fichiers modifiés

| Fichier | Changements |
|---|---|
| `src/ocr_app/workers/worker_main.py` | `--page N` : traite UNE seule page du document réel (`Pipeline.process_page` dans le thread daemon surveillé — cancel < 2 s conservé), même protocole, un `page_done` avec le numéro réel, total affiché = 1 ; hors bornes → erreur protocolaire propre (exit 1). `--lang fra+eng` : validation par expression régulière (exit 2 sinon), appliqué au mode Simple par injection d'un moteur `TesseractEngine(language=...)` dans le pipeline (`pipeline._worker`), sans modification de `core/*` ni du protocole. Simulation : `--page N` produit la page N seule (`page_042.json`, numéro réel) |
| `src/ocr_app/ui/widgets/toolbar.py` | Bouton « Ajouter un dossier » (signal `add_folder_requested`) ; indicateur « FR + EN · 300 DPI » dynamique (`set_settings_indicator`) |
| `src/ocr_app/ui/widgets/status_bar.py` | Compteur de lot « Lot : 2/5 terminés » (`set_batch_progress`, masqué hors lot) ; bouton « Tout traiter » (signal `process_all_clicked`, visible seulement si des tâches PENDING hors file attendent) ; `set_task(..., title_override=...)` pour afficher « Relance page N » au lieu de « Page N / 1 » |
| `src/ocr_app/ui/task_runner.py` | `set_worker_settings(languages, dpi)` → `--lang/--dpi` ajoutés aux `extra_args` uniquement pour les workers qui les comprennent (`accepts_input_document` : worker_main + exécutables packagés ; le worker factice est épargné). Relance : `create_page_task(source, mode, page)` (total=1, `pages_done` borné à 1 pour que la fin de worker passe DONE), `relaunch_page(id)`, `is_document_busy(source)`. Lots : `begin_batch(ids)`, comptage dans `_emit_task_changed` à chaque état terminal (DONE/CANCELLED/ERROR, une seule fois par tâche), signal `batch_progress(done, total)` ; `enqueue_all_pending()`, `pending_count()` |
| `src/ocr_app/ui/main_window.py` | `_choose_folder` (QFileDialog.getExistingDirectory + QMessageBox avec case « Inclure les sous-dossiers »), `_collect_folder_documents` (récursif optionnel, tri alphabétique insensible à la casse) ; `add_paths` démarre un lot si ≥ 2 fichiers valides ; menu contextuel de l'aperçu « Relancer la page N en… » (Simple/Moyen/Avancé) désactivé pendant un traitement en cours sur le même document ou si le module du mode n'est pas installé (tooltip explicatif) ; `_open_settings` (dialogue → persistance → `_apply_settings` : indicateur + réglages workers) ; info d'aperçu avec DPI des réglages (« Relance page 3 · Moyen · 300 DPI ») ; titre « … — v1.0 » (marque « ● » conservée), à-propos « Version 1.0.0 · v1.0 » |

## Garanties de la relance de page

- Jamais bloquant : la relance passe par la file `TaskRunner` (QProcess, R2)
  et démarre quand le worker est libre.
- Le JSON d'origine n'est jamais écrasé : chaque tâche a son propre dossier de
  travail (`task_work_dir` = `stem_id8`), la relance écrit son propre
  `page_NNN.json` — vérifié par test fonctionnel (fichiers séparés, page
  d'origine intacte).
- Si le mode échoue (ex. module Moyen non installé), la tâche passe en erreur
  avec message clair dans la barre de statut et le résultat d'origine reste
  affiché/consultable.

## Recette `scripts/acceptance.py`

16 vérifications en 3 étapes + rapport global PASS/FAIL, exit 0/1 :

1. `python -m pytest tests/ -q` (suite complète) ;
2. CLI `process --mode simple` sur un natif facile (`fact_001.pdf`), un scan
   (`scan_001.pdf`) et un mixte (`mixed_001.pdf`) : exit 0, fichiers attendus
   (`result.txt/md/html`, `document.json`, `page_NNN.json` au nombre attendu),
   texte > 200 caractères non vide, ≥ 10 accents (R14), JSON de page valide ;
3. `worker_main --mode simple --input fact_002.pdf --page 1` dans un dossier
   de travail accentué/espacé : pilotage protocole (start → messages → EOF),
   exit 0, stdout JSON pur, un `page_done` page 1, total de progression = 1,
   `page_001.json` valide dans le dossier accentué.

## Vérifications exécutées (résultats)

| Vérification | Résultat |
|---|---|
| `python -m pytest tests/ -q` | **181 passed** (baseline inchangée, plusieurs passages) |
| Lot de 3 vrais PDF noms accentués (« b_rapport éàç.pdf », « a_facture üö.pdf », « c_note àï.pdf ») hors écran | 3 tâches enchaînées **DONE**, compteur `batch_progress` = (0,3) → (1,3) → (2,3) → (3,3) |
| `worker_main --mode simple --input fact_002.pdf --page 1 --work-dir <accentué>` | exit 0, 1 `page_done` **page 1 réelle**, `page_001.json` accentué, total de progression 1 ; `--page 999` → erreur protocolaire propre (exit 1) ; `--lang fra` OK ; `--lang invalide` → exit 2 propre ; simulation `--page 42` → `page_042.json` |
| Réglages : `AppSettings(languages=["eng"], dpi=150)` sauvegardé puis 2 instanciations de `MainWindow` | indicateur « EN · 200 DPI » rechargé aux deux démarrages ; JSON corrompu → défauts ; DPI 600 → borné à 300 (R7) ; dialogue : OK désactivé sans langue |
| Relance page | Simple → DONE dans son propre dossier (`page_001.json` séparé, original intact) ; Moyen (module absent) → erreur claire « Module « Moyen » non installé… », original intact |
| « Tout traiter » | tâche PENDING hors file → bouton actif → clic → DONE, bouton masqué |
| `python scripts/acceptance.py` | **16/16 PASS — RECETTE GLOBALE : PASS** (exit 0) |

## Écarts / limites

- `--lang` n'a d'effet que sur le mode Simple : les moteurs Moyen/Avancé sont
  des squelettes (modules non installés, R13) ; l'option est acceptée et
  journalisée sans effet. Le moteur est branché par injection dans le pipeline
  (`pipeline._worker`) pour ne pas modifier `core/*` (périmètre verrouillé).
- La relance crée une tâche indépendante : son résultat s'affiche dans
  l'aperçu (tâche auto-sélectionnée) mais n'est pas fusionné dans le document
  d'origine (choix conservatif conforme à la contrainte « ne jamais écraser le
  résultat d'origine sans distinction ») ; l'export « tout le document » de la
  tâche d'origine reste sur les pages d'origine.
- Les badges du panneau latéral ne portent pas le libellé « Relance page N »
  (la ligne affiche le nom du fichier) ; le statut distinct est porté par
  l'en-tête d'aperçu, la barre inférieure et les toasts — `file_sidebar.py`
  n'étant pas dans le périmètre autorisé.
- Les textes de scan OCR renvoient des accents de façon variable selon la
  qualité du scan ; la recette utilise un seuil prudent (≥ 10 accents) calibré
  sur le jeu de test (scan_001 : 27 accents mesurés).
- « Inclure les sous-dossiers » n'est pas mémorisé entre deux ajouts
  (case décochée par défaut : parcours racine seul).

## Conformité

R2 (OCR toujours en sous-processus — y compris la relance), R4 (tessdata
explicite, langues filtrées par les fichiers disponibles), R6 (page écrite dès
finie, aussi en relance), R7 (DPI ≤ 300 aux trois niveaux : dialogue, réglages
persistés, worker), R14 (UTF-8 de bout en bout, chemins accentués testés),
protocole JSON inchangé pour le reste.
