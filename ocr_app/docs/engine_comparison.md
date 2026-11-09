# Comparaison des moteurs OCR — Phase 2

Date : 9 novembre 2026 · Jeu de test : `data/test_suite/` (42 documents, 69 pages, vérité terrain)
Protocole : échantillon stratifié par difficulté (easy / medium / hard), CER = distance d'édition / longueur de la vérité terrain, accents conservés. TedS-lite = fraction des cellules de tableau retrouvées dans la sortie.

## Résultats mesurés

### Reconnaissance brute (OCR forcé sur les 12 premiers documents de l'échantillon)

| Moteur | Version | CER moyen | Temps moyen/doc | Remarque |
|---|---|---:|---:|---|
| **Tesseract** | 5.5.3 (tesserocr 2.12) | **14,40 %** | ~6 s | Meilleur CER brut, rapide, léger |
| RapidOCR | 3.x (PP-OCRv6, onnxruntime) | 19,16 % | ~13 s | Plus lent, CER supérieur de ~5 points |
| Docling | 2.149.0 | 32,39 % | 15–80 s | OCR interne plus faible sur ces documents, très lent à froid |

> Échantillon Docling limité à 8 documents (temps CPU). Les documents sont des PDF rasterisés synthétiques : Tesseract y est avantagé par rapport à un moteur orienté documents natifs.

### Qualité de structure (mode Avancé)

| Moteur | Rappel cellules tableaux | Remarque |
|---|---:|---|
| **Docling** | **97,4 – 100 %** | Tableaux reconstruits en grilles Markdown complètes |
| Tesseract (mode Simple) | 83,8 % (aplatissement) | Cellules présentes mais structure perdue |
| RapidOCR | non mesuré | Pas de reconstruction de tableaux |

### Mode Simple complet (pipeline réel, 42 documents)

- CER moyen global : **18,61 %** ; cellules retrouvées : **83,83 %**
- PDF natifs (texte natif, R8) : **1,5 – 8,7 %** — quasi parfait
- Formulaires : 3,2 – 22,8 %
- Scans dégradés/pivotés : **65 – 74 %** — limite du mode Simple sans prétraitement
- Mixtes : 1,5 – 36 %

## Décision par mode (R10 — décidée sur ce jeu de test)

| Mode | Moteur retenu | Justification |
|---|---|---|
| **Simple** | **Tesseract** (tesserocr 2.12 / 5.5.3) | Meilleur CER brut, ~6 s/doc, aucun modèle lourd, tient dans le noyau (R1) |
| **Moyen** | **Tesseract + prétraitement OpenCV** (redressement, contraste, débruitage) | RapidOCR n'apporte pas de gain CER sur ce jeu ; le prétraitement attaquera les 65–74 % des scans. RapidOCR conservé comme moteur secondaire optionnel du module. |
| **Avancé** | **Docling** | Seul moteur à reconstruire les tableaux (97–100 % de cellules) ; structure de mise en page supérieure. OCR brut plus faible → combiner avec le texte natif (R8) et, si besoin, Tesseract comme OCR de secours. |

> PaddleOCR PP-Structure n'a pas pu être installé sur Python 3.14 (pas de wheel `paddlepaddle` cp314) ; il reste une alternative à réévaluer si le module Avancé cible Python 3.12.

## Contraintes hors ligne (R4)

| Moteur | Téléchargements au premier usage | Préparation requise |
|---|---|---|
| Tesseract | Aucun | Embarquer `models/tessdata` (fra/eng, ~5 Mo) |
| RapidOCR | Aucun (modèles embarqués dans le paquet) | Rien de plus |
| Docling | **Oui** — modèles Hugging Face par défaut | `docling-tools models download` puis `DOCLING_ARTIFACTS_PATH` explicite |

## Environnements (R13)

| Venv | Python | Moteurs installés |
|---|---|---|
| `.venv-eval-medium` | 3.12.10 | rapidocr 3.x, onnxruntime, pypdfium2, pillow |
| `.venv-eval-advanced` | 3.12.10 | docling 2.149.0 |
| environnement projet | 3.14.8 | tesserocr 2.12, pypdfium2, pillow (noyau Simple) |

## Reproduction

```bat
python scripts\evaluate.py --mode simple --output data\eval
.venv-eval-medium\Scripts\python scripts\compare_engines.py --engine rapidocr --limit 12 --no-native-first
.venv-eval-advanced\Scripts\python scripts\compare_engines.py --engine docling --limit 8 --no-native-first
python scripts\compare_engines.py --engine tesseract --limit 12 --no-native-first
```

Rapports détaillés : `data/eval/eval_simple.{json,md}`, `data/eval/engine_{tesseract,rapidocr,docling}.json`.
