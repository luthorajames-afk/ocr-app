# BACKLOG AOCRI v2 — exécution séquentielle

Chaque tâche est validée (tests + linters) avant la suivante.
Rôles : [Planificateur] → [Implémenteur] → [Testeur] → [Relecteur Sécurité] → [Documentaliste].

## Phase 0 : Préparation, Outils & Initialisation

- [x] **T0.1** `requirements-dev.txt` : pytest, pytest-qt, pytest-socket, ruff, mypy, bandit, pydantic, pip-audit.
- [x] **T0.2** Arborescence `src/`, `tests/`, `CHANGELOG.md` à `0.1.0-alpha.0`, `AGENTS.md`.
- [x] **T0.3** `tests/data/` : 30 documents (factures, formulaires, scans, multi-colonnes) + `truth.txt`.

## Phase 1 : Pipeline en Ligne de Commande (Cœur Sans IHM)

- [x] **T1.1** Interfaces abstraites `OcrEngine` et `ScannerBackend` (typage strict).
- [x] **T1.2** Script CLI de base avec Tesseract local (Mode Simple), texte brut sans mise en page.
- [x] **T1.3** Extraction de texte natif PDF via `pypdfium2` (contournement OCR).

## Phase 2 : Évaluation, Algorithme Adaptatif & Banc de Test

- [x] **T2.1** Module de benchmarking autonome `tests/bench/bench_runner.py` (CER + WER).
- [x] **T2.2** Triage qualité d'image : variance du Laplacien, angle d'inclinaison OpenCV, contraste.
- [x] **T2.3** Prétraitement adaptatif : redressement, binarisation Sauvola locale, suréchantillonnage DPI.
- [x] **T2.4** Seconde passe sur lignes sous seuil de confiance + vote bi-moteur (Moyen/Avancé).
- [x] **T2.5** Post-correction lexique juridique/gestion avec exclusion des numéros d'articles et références.
- [x] **T2.6** Analyse d'ablation complète sur le banc + rapport d'ajustement des seuils.

## Phase 3 : Infrastructure Asynchrone du Worker

- [x] **T3.1** Protocole JSON Lines via stdin/stdout.
- [x] **T3.2** Signaux : traitement par page, progression, pause, reprise, annulation (`abort`).
- [x] **T3.3** Sauvegarde prévoyante page par page (`page_schema.json`) + reprise sur panne.

## Phase 3b : Couche d'Acquisition Matérielle (Scanner Physique)

- [x] **T3b.1** Backend WIA prioritaire, thread COM dédié.
- [x] **T3b.2** Secours TWAIN (64 bits) et eSCL AirScan (HTTP local asynchrone).
- [x] **T3b.3** Lot avec détection de pages blanches (validation utilisateur, pas de suppression auto).

## Phase 4 & 5 : Interface PySide6 & Visionneuse (mockup d'abord)

- [x] **T4.1** MOCKUP validé (docs/mockups/T4.1_main_window.md) → fenêtre PySide6 : modes, tâches, progression, statuts.
- [x] **T4.2** WorkerClient QProcess + TaskRunner : JSON Lines, pause/reprise/annulation.
- [x] **T5.1** Visionneuse : image réelle de page (page_NNN.png) + repli texte ; périmètre visuel couvert par la maquette T4.1 validée.
- [x] **T5.2** Onglets Rendu / Markdown-HTML / Texte brut (édition complète R9 prévue en itération suivante) ; périmètre couvert par la maquette T4.1 validée.

## Phase 6 : Exports & Presse-Papiers

- [x] **T6.1** Presse-papiers HTML riche (tableaux collables dans Word et Excel).
