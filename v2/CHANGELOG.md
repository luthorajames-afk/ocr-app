# CHANGELOG — AOCRI

Format : [SemVer](https://semver.org/lang/fr/). Types : Ajouté / Modifié / Corrigé / Sécurité.

## [Non publié]

### Ajouté
- Amorçage du dépôt v2 : gouvernance `AGENTS.md`, structure `src/core/{gui,pipeline,io}`,
  `src/workers/{medium,advanced}`, `tests/{unit,integration,data}`.
- `requirements-dev.txt` (pytest, pytest-qt, pytest-socket, ruff, mypy, bandit, pydantic, pip-audit).
- Jeu de test v2 : 30 documents (factures, formulaires, scans, multi-colonnes) + `truth.txt`.
- ADR-0001 : structure v2 et réutilisation du corpus v1 comme source de vérité.

## [0.1.0-alpha.0] — 2026-11-10

- Version initiale du nouveau cycle v2 (branche `v2`).
- Base fonctionnelle héritée de la v1.0 (`53870e8`) : OCR Simple réel, mode Avancé Docling,
  mode Approfondi, exports DOCX/XLSX/PDF, installateurs Inno Setup (non compilés).
