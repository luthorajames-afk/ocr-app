# ADR-0001 — Structure du dépôt v2 et réutilisation du corpus v1

- Statut : Accepté
- Date : 2026-11-10
- Décideurs : [Planificateur] + utilisateur humain

## Contexte

La v1.0 d'AOCRI est livrée (branche `main`, commit `53870e8`) : OCR Simple réel,
mode Avancé Docling, mode Approfondi, exports, installateurs. La v2 est un
nouveau cycle avec une architecture cible imposée par `AGENTS.md`.

## Décision

1. La v2 vit dans le sous-dossier `v2/` du dépôt, sur la branche `v2`.
   La v1 reste intacte sur `main`.
2. L'arborescence suit strictement `src/core/{gui,pipeline,io}`,
   `src/workers/{medium,advanced}`, `tests/{unit,integration,data}`.
3. Le corpus de test v1 (`ocr_app/data/test_suite`, 42 documents + vérité terrain)
   est la **source de vérité**. `scripts/prepare_test_data.py` en dérive
   les 30 documents exigés (T0.3) dans `tests/data/`, avec leurs `truth.txt`.
4. Aucune duplication de code v1 → v2 sans raison : les briques validées
   (protocole JSON Lines, exporteurs, preprocessing) sont ré-implémentées
   dans la structure v2 avec typage strict, pas copiées telles quelles.

## Conséquences

- Deux projets coexistent dans un même dépôt (v1 stable, v2 en développement).
- Les tests v2 sont exécutés depuis `v2/` avec leur propre `pyproject.toml`.
- Toute évolution du corpus passe par le générateur v1 puis extraction v2.
