# AGENTS.md — Loi de programmation AOCRI v2

Éditeur : Kriesty Solutions. Application Windows de bureau, **100 % hors ligne**.
Stack : Python 3.10+, PySide6, pypdfium2, Tesseract ; modules lourds isolés.

## 1. Périmètre

- Documents de gestion : factures, rapports.
- Documents juridiques : décrets, arrêtés, lois, décisions de justice.
- Préservation de la mise en page : multi-colonnes, signatures, visas, notes de bas de page, tableaux complexes.
- **Étanchéité réseau absolue** : aucun appel réseau dans le code (vérifié par `pytest-socket`).
- Noyau léger **sans PyTorch** ; moteurs lourds dans des workers optionnels.
- IHM et OCR strictement séparés : JSON Lines sur stdin/stdout, asynchrone.

## 2. Politique « Mockup d'abord »

Aucune modification de l'IHM (code PySide6) sans validation préalable du **Mockup Conceptuel** (ascii-art / structure de layouts Qt). L'implémentation ne commence qu'après approbation explicite de l'utilisateur humain.

## 3. Versionnage strict (SemVer)

- Application et modules : versions coordonnées dans `metadata.py` et `module.json`.
- Noyau et modules ont des cycles de vie distincts, tracés dans `CHANGELOG.md`.
- Toute évolution de `page_schema.json` incrémente la version du schéma **et** fournit une migration automatique des données existantes.

## 4. Rôles des agents (non cumulables sur une même tâche)

| Rôle | Mission | Règle d'or |
|---|---|---|
| **[Planificateur]** | Découpe en micro-tâches (< ½ journée), plan, mockups, impact versionnage | Ne modifie aucun fichier |
| **[Implémenteur]** | Code Python typé strict (mypy), contrats d'interface | Ne touche qu'aux fichiers listés dans le brief |
| **[Testeur]** | Tests unitaires/intégration (pytest, pytest-qt, pytest-socket) depuis les specs | Ne lit pas le code de l'Implémenteur |
| **[Relecteur Sécurité]** | Audit du diff : injections HTML, formules XLSX, traversées `..` | Signale par écrit, ne corrige pas |
| **[Documentaliste]** | Docs techniques, guide utilisateur, `CHANGELOG.md`, ADR | —

## 5. Économie de tokens (obligatoire)

- **Ciblage chirurgical** : lire uniquement des plages de lignes, jamais un dossier entier.
- **Modifications en Unified Diff** ; jamais de réécriture complète de fichier.
- **Sorties d'outils filtrées** : uniquement l'erreur / les lignes en échec.
- **Finitude** : résumé technique ≤ 5 lignes après livraison. Pas de politesses ni préambules.

## 6. Structure racine

```text
AOCRI/
├── AGENTS.md
├── CHANGELOG.md
├── src/
│   ├── core/               # Noyau léger (IHM, pypdfium2, Tesseract)
│   │   ├── gui/            # IHM (aucun calcul OCR)
│   │   ├── pipeline/       # Orchestrateur asynchrone, gestionnaire de tâches
│   │   └── io/             # ScannerBackend (WIA) et exports
│   └── workers/
│       ├── medium/         # RapidOCR + OpenCV
│       └── advanced/       # Docling / modèles de mise en page
└── tests/
    ├── unit/
    ├── integration/
    └── data/               # Décrets, factures (PNG/PDF) + truth.txt
```

## 7. Backlog séquentiel

Référence : backlog v2 (phases 0 à 6) — voir `docs/BACKLOG.md`. Chaque tâche est validée par tests + linters avant passage à la suivante.
