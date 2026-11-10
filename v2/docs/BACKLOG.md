# BACKLOG AOCRI v2 — exécution séquentielle

Chaque tâche est validée (tests + linters) avant la suivante.
Rôles : [Planificateur] → [Implémenteur] → [Testeur] → [Relecteur Sécurité] → [Documentaliste].

## Phase 0 : Préparation, Outils & Initialisation

- [x] **T0.1** `requirements-dev.txt` : pytest, pytest-qt, pytest-socket, ruff, mypy, bandit, pydantic, pip-audit.
- [x] **T0.2** Arborescence `src/`, `tests/`, `CHANGELOG.md` à `0.1.0-alpha.0`, `AGENTS.md`.
- [x] **T0.3** `tests/data/` : 30 documents (factures, formulaires, scans, multi-colonnes) + `truth.txt`.

## Phase 1 : Pipeline en Ligne de Commande (Cœur Sans IHM)

- [x] **T1.1** Interfaces abstraites `OcrEngine` et `ScannerBackend` (typage strict).
- [ ] **T1.2** Script CLI de base avec Tesseract local (Mode Simple), texte brut sans mise en page.
- [ ] **T1.3** Extraction de texte natif PDF via `pypdfium2` (contournement OCR).

## Phase 2 : Évaluation, Algorithme Adaptatif & Banc de Test

- [ ] **T2.1** Module de benchmarking autonome `tests/bench/bench_runner.py` (CER + WER).
- [ ] **T2.2** Triage qualité d'image : variance du Laplacien, angle d'inclinaison OpenCV, contraste.
- [ ] **T2.3** Prétraitement adaptatif : redressement, binarisation Sauvola locale, suréchantillonnage DPI.
- [ ] **T2.4** Seconde passe sur lignes sous seuil de confiance + vote bi-moteur (Moyen/Avancé).
- [ ] **T2.5** Post-correction lexique juridique/gestion avec exclusion des numéros d'articles et références.
- [ ] **T2.6** Analyse d'ablation complète sur le banc + rapport d'ajustement des seuils.

## Phase 3 : Infrastructure Asynchrone du Worker

- [ ] **T3.1** Protocole JSON Lines via stdin/stdout.
- [ ] **T3.2** Signaux : traitement par page, progression, pause, reprise, annulation (`abort`).
- [ ] **T3.3** Sauvegarde prévoyante page par page (`page_schema.json`) + reprise sur panne.

## Phase 3b : Couche d'Acquisition Matérielle (Scanner Physique)

- [ ] **T3b.1** Backend WIA prioritaire, thread COM dédié.
- [ ] **T3b.2** Secours TWAIN (64 bits) et eSCL AirScan (HTTP local asynchrone).
- [ ] **T3b.3** Lot avec détection de pages blanches (validation utilisateur, pas de suppression auto).

## Phase 4 & 5 : Interface PySide6 & Visionneuse (mockup d'abord)

- [ ] **T4.1** MOCKUP fenêtre principale → validation → implémentation (IHM non bloquante, liste tâches, statuts, progressions, mode).
- [ ] **T4.2** Thread d'écoute asynchrone des flux worker.
- [ ] **T5.1** MOCKUP visionneuse → validation → image originale + surcouche géométrique des blocs.
- [ ] **T5.2** MOCKUP onglets → validation → Rendu / Markdown-HTML / Texte brut éditable avant copie.

## Phase 6 : Exports & Presse-Papiers

- [ ] **T6.1** Presse-papiers HTML riche (tableaux collables dans Word et Excel).
