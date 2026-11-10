# CHANGELOG — AOCRI

Format : [SemVer](https://semver.org/lang/fr/). Types : Ajouté / Modifié / Corrigé / Sécurité.

## [Non publié]

### Ajouté
- Amorçage du dépôt v2 : gouvernance `AGENTS.md`, structure `src/core/{gui,pipeline,io}`,
  `src/workers/{medium,advanced}`, `tests/{unit,integration,data}`.
- `requirements-dev.txt` (pytest, pytest-qt, pytest-socket, ruff, mypy, bandit, pydantic, pip-audit).
- Jeu de test v2 : 30 documents (factures, formulaires, scans, multi-colonnes) + `truth.txt`.
- ADR-0001 : structure v2 et réutilisation du corpus v1 comme source de vérité.
- T1.1 : contrats `OcrEngine` et `ScannerBackend` (typage strict, dataclasses gelées),
  capacités moteur, résultat OCR structuré ; 6 tests de contrat, ruff + mypy verts.
- T1.2 : `TesseractEngine` (tesserocr, tessdata explicite R4) + CLI `core.cli`
  (process/info, texte brut par page).
- T1.3 : `load_document` via pypdfium2 — rendu 300 DPI max, texte natif
  prioritaire avec seuil de significativité ; 14 tests (unit + intégration).
- T2.1 : `tests/bench/bench_runner.py` — CER, WER, agrégats par thème, rapport JSON.
- T2.2 : `core/pipeline/image_quality.py` — variance du Laplacien, contraste RMS,
  inclinaison par profils de projection (sans cv2) ; 10 tests.
- T2.3 : `core/pipeline/preprocess.py` — redressement automatique, binarisation
  Sauvola locale (moyenne/écart-type par image intégrale), suréchantillonnage DPI
  (150–300), décision pilotée par le triage ; 9 tests.
- T2.4 : `core/pipeline/second_pass.py` — seconde passe ciblée sur les lignes
  sous confiance + vote bi-moteur (garde-fou chiffres, tie-break accents) ;
  Tesseract expose désormais texte/confiance par ligne (TSV).
- T2.5 : `core/pipeline/lexicon.py` — post-correction lexique juridique/gestion,
  références (articles, décrets, dates, montants, IBAN, e-mails) intouchables ; 8 tests.
- T2.6 : analyse d'ablation `--ablation` (brut / prétraité / +lexique) ;
  seuils ajustés sur mesures : binarisation opt-in, deskew auto > 2° —
  CER scans 56,29 % → 49,91 % (−6,38 pts) ; rapport `docs/T2.6_ablation.md`.
- T3.1 : `core/pipeline/protocol.py` — messages JSON Lines validés par Pydantic
  (discriminated union, champs stricts, erreurs explicites).
- T3.2 : `workers/worker_main.py` — progression par étape, pause/reprise
  coopératives, annulation immédiate (< 2 s) même pendant l'OCR.
- T3.3 : `core/pipeline/job_store.py` — `page_schema` versionné, écriture
  atomique page par page, reprise après panne (pages déjà sauvegardées).
  Tests : 8 unitaires protocole/store + 4 intégration subprocess.
- T3b.1/T3b.2 : `core/io/scanner.py` — backends WIA (pywin32, COM),
  TWAIN 64 bits et eSCL ; sélection automatique WIA → TWAIN → eSCL ;
  eSCL désactivé par défaut (étanchéité réseau, AGENTS.md §1).
- T3b.3 : détection de pages blanches par ratio d'encre — pages
  signalées mais jamais supprimées ; 6 tests.
- Tests : pytest-socket activé sur toute la suite (aucun accès réseau).
- T4.1/T4.2 : IHM PySide6 v2 — fenêtre principale (modes, liste de tâches,
  progression par étape, statuts), WorkerClient QProcess (JSON Lines,
  pause/reprise/annulation), TaskRunner un document à la fois.
- T5.1/T5.2 : visionneuse (image de page réelle + repli texte) et onglets
  Rendu / Markdown-HTML / Texte brut ; 5 tests pytest-qt offscreen.
- T6.1 : `core/io/clipboard.py` + `core/gui/clipboard_qt.py` — presse-papiers
  riche text/plain + text/html (tableaux) + text/markdown, échappement
  HTML systématique ; bouton Copier câblé ; 6 tests.
- T5.2 complément : édition du texte avant copie (R9) — sauvegarde atomique
  du `page_NNN.json`, marqueur « ● » dans le titre, test dédié.
- Module Moyen réel : `workers/medium/engine.py` (RapidOCR 3.10, venv 3.12
  dédié via `AOCRI_MEDIUM_VENV` ou `.venv-eval-medium`), bouton Moyen
  activé seulement si l'environnement existe ; test d'intégration réel.
- Correctif Windows : préchargement des DLL (pypdfium2/numpy/rapidocr) dans
  le thread principal du worker avant la lecture stdin — évite le blocage
  du chargeur DLL (même classe que Docling v1).
- T2.4/T2.5 branchés dans le worker : `--second-pass` et `--lexicon`
  (options opt-in) ; correctif TSV Tesseract (`GetTSVText(0)`) — les
  blocs par ligne et la confiance sont désormais réellement extraits.
- Mode Avancé Docling branché dans l'IHM (`workers/advanced/engine.py`, venv
  dédié via `AOCRI_ADVANCED_VENV`), préchargement du convertisseur contre le
  deadlock du chargeur DLL ; boutons Avancé/Approfondi activés si le venv existe.
- Aperçu : page entière ajustée à la vue (fitInView) + navigation ‹ › par page.
- Mise en page : segmentation native en blocs (colonnes regroupées par x,
  titres détectés par hauteur) ; onglet Rendu structuré (titres h2, colonnes).
- Prétraitement automatique T2.3 appliqué avant l'OCR dans le worker
  (deskew > 2°, binarisation opt-in).
- Tableaux natifs reconstruits avant le découpage en colonnes (grille 6×6
  sur fact_001), rendus HTML et copiés en HTML/Markdown.
- Aperçu comparatif Original (image) / Résultat (texte rendu) + navigation
  entre pages.
- Étalonnage qualité OCR : `core/pipeline/calibration.py` (CER/WER,
  confiance moyenne, blocs sous seuil, verdict) + dialogue « Étalonnage… »
  avec export JSON ; tests unitaires et IHM.
- Colonnes : texte reconstruit dans l'ordre de lecture (pas l'ordre brut
  de la couche PDF) ; rendu côte à côte via tableau HTML (QTextBrowser
  ne supporte pas flexbox).
- Aperçu : le worker écrit `page_NNN.png` (rendu 300 DPI) à côté du JSON —
  l'aperçu « Original » affiche enfin le document (plus de carré blanc).

## Backlog v2 terminé (T0.1 → T6.1)

## [0.1.0-alpha.0] — 2026-11-10

- Version initiale du nouveau cycle v2 (branche `v2`).
- Base fonctionnelle héritée de la v1.0 (`53870e8`) : OCR Simple réel, mode Avancé Docling,
  mode Approfondi, exports DOCX/XLSX/PDF, installateurs Inno Setup (non compilés).
