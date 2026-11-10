# AOCRI v2 — Advanced Optical Character Recognition for Itamba

Éditeur : Kriesty Solutions · Version cycle : `0.1.0-alpha.0` · Branche : `v2`

Application Windows de bureau, **100 % hors ligne**, Python + PySide6.
Documents de gestion (factures, rapports) et juridiques (décrets, arrêtés, lois,
décisions de justice) : multi-colonnes, signatures, visas, notes de bas de page,
tableaux complexes.

## Démarrage

```bat
cd v2
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
python -m pytest
```

## Règles

Lire `AGENTS.md` avant toute contribution (mockup d'abord, rôles séparés,
versionnage strict, économie de tokens).

## Backlog

Voir `docs/BACKLOG.md` — phases 0 à 6, exécution strictement séquentielle.
