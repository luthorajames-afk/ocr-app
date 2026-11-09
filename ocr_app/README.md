# AOCRI — Advanced Optical Character Recognition for Itamba

OCR avancé avec conservation de la mise en page — documents PDF, JPG, PNG.

Éditeur : Kriesty Solutions · Version : 1.0 · Date : 9 novembre 2026

## Présentation

Application de bureau Windows, installable et entièrement hors ligne. Elle applique un OCR avancé à des documents PDF, JPG et PNG et restitue un résultat copiable qui conserve la mise en page, les tableaux et tous les éléments de texte d'origine.

## Fonctionnalités v1.0

- Trois modes de traitement (Simple, Moyen, Avancé) exécutés en sous-processus
- Traitement par lots : « Ajouter un dossier » (récursif optionnel), compteur global « Lot : X/N terminés », « Tout traiter » / « Tout annuler »
- Relance d'une page dans un autre mode (menu contextuel de l'aperçu), sans jamais écraser le résultat d'origine
- Réglages OCR persistants (langues fra/eng, DPI 150/200/300) dans `%LOCALAPPDATA%\AOCRI\settings.json`
- Édition avant copie (annuler/rétablir), copie riche et exports DOCX/XLSX/PDF/HTML/MD/TXT
- Recette d'acceptation automatisée : `python scripts/acceptance.py`

## Architecture (squelette v1)

- Interface PySide6 (Qt) — ne fait aucun calcul d'OCR, pilote les processus de travail
- Gestionnaire de tâches — lance le worker selon le mode (Simple, Moyen, Avancé)
- Workers — un exécutable par mode, avec ses propres dépendances (isolé)
- Stockage — un dossier de travail par document, une image + un JSON par page
- Protocole worker — lignes JSON sur stdin/stdout (`progress`, `page_done`, `error`)
- Moteur d'export — HTML, Markdown, DOCX, XLSX, TXT, PDF depuis un même modèle de données
- Détection de modules — `module.json` par module, installateurs Inno Setup

## Trois modes (profil de pipeline)

| Mode | Usage | Tableaux | Moteurs indicatifs |
|------|-------|----------|-------------------|
| Simple | Documents propres, texte courant | Non | Tesseract |
| Moyen | Scans, mise en page modérée | Simple | Tesseract, RapidOCR, OpenCV |
| Avancé | Colonnes, tableaux, formulaires complexes | Complète, cellules fusionnées | Docling, modèles de layout/tableaux |

Le choix final du moteur Avancé (Docling vs PaddleOCR PP-Structure) se fera par test sur le jeu de documents réels (phase 2).

## Règles applicables (session de travail)

Consultez `PROJECT.md` pour les 15 règles en vigueur (issu des « pièges à éviter » du plan). Elles s'appliquent à tout le développement.

## Démarrage rapide (squelette)

```bash
cd ocr_app
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
python -m pytest            # suite de tests (181 tests)
python -m ocr_app.cli --help # ligne de commande
python scripts/acceptance.py # recette d'acceptation v1.0
```

## Plan de travail (8 phases)

0. Préparation — jeu de test (30–50 documents réels)
1. Cœur sans interface — pipeline CLI 3 modes
2. Évaluation — CER, TEDS, comparaison des moteurs
3. Worker et protocole — processus séparé, JSON, pause/annulation
4. Interface de base — PySide6, liste de fichiers, progression
5. Prévisualisation — image avec surcouches, édition bloc ↔ texte
6. Copie et exports — presse-papiers riche, DOCX/XLSX/PDF/HTML/MD/TXT
7. Modules et installateurs — Inno Setup, signature, mises à jour de modules
8. Finitions — traitement par lots, réglages → v1.0