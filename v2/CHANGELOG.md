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

## [0.1.0-alpha.0] — 2026-11-10

- Version initiale du nouveau cycle v2 (branche `v2`).
- Base fonctionnelle héritée de la v1.0 (`53870e8`) : OCR Simple réel, mode Avancé Docling,
  mode Approfondi, exports DOCX/XLSX/PDF, installateurs Inno Setup (non compilés).
