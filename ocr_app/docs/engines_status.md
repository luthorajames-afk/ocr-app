# État des moteurs OCR — préparation de l'évaluation (phase 2)

Date du diagnostic : **2026-11-09**
Machine : Windows 11 (10.0.26220), AMD64
Interpréteur : **Python 3.14.8 64 bits** (`C:\Python314\python.exe`), pip 26.2.1
Environnement : pas de venv projet ; paquets installés dans le site utilisateur
`C:\Users\lutho\AppData\Roaming\Python\Python314\site-packages`.

Déjà présents : `numpy 2.5.4`, `pillow 13.0.0`, `onnxruntime 1.31.1`,
`pypdfium2 5.15.1`, `pymupdf 2.0.0`, `pytest 9.2.0`.
Aucun binaire `tesseract.exe` sur la machine (PATH, Program Files, registre,
winget : aucun résultat).

Diagnostic reproductible :

```bash
python scripts/check_engines.py
python scripts/check_engines.py --self-test --json docs/engines_status.json
```

Le script ne télécharge et n'installe rien. Il ne lance pas les imports lourds
(`docling`, `paddleocr`, `easyocr`) : seule leur présence est vérifiée.

## Tableau de disponibilité

| Moteur | Disponible | Version | Licence | Taille approx. | Mode | Remarques hors-ligne (R4) |
|---|---|---|---|---|---|---|
| **tesserocr** | **Oui** — installé et testé | 2.12.0 ; Tesseract 5.5.3 | MIT (wrapper) ; Apache-2.0 (Tesseract) | ~24 Mo (wheel Windows) + tessdata ~5 Mo | Simple | Aucun téléchargement. Embarque Tesseract/Leptonica ; nécessite des `.traineddata` locaux (`eng`, `fra` présents). |
| **tesseract.exe** (binaire) | Non — introuvable | — | Apache-2.0 | ~60–100 Mo installé + tessdata | Simple, Moyen | Installation locale, aucun téléchargement runtime. Non requis par tesserocr. |
| **pytesseract** | Non — paquet absent, binaire absent | — (PyPI 0.3.13) | Apache-2.0 | ~0,02 Mo + Tesseract requis | Simple, Moyen | Wrapper CLI : sans `tesseract.exe`, inutilisable. Aucun téléchargement propre. |
| **rapidocr-onnxruntime** | Non — incompatible Python 3.14 | — (PyPI 1.4.4) | Apache-2.0 | ~14 Mo + OpenCV + onnxruntime | Moyen | Modèles embarqués dans le wheel : bon pour R4. `requires-python <3.13`. |
| **rapidocr** (successeur) | Non — non installé, compatible 3.14 | — (PyPI 3.10.0) | Apache-2.0 | ~26 Mo (modèles inclus) + OpenCV + onnxruntime | Moyen | Modèles embarqués : aucun téléchargement runtime. |
| **docling** | Non — non installé | — (PyPI 2.149.0) | MIT (code et `docling-ibm-models`) | ~3–5 Go avec PyTorch et modèles | Avancé | Télécharge les modèles layout/table depuis Hugging Face au premier usage. À neutraliser via `docling-tools models download` + `DOCLING_ARTIFACTS_PATH`. |
| **paddleocr** | Non — bloqué sur Python 3.14 | — (PyPI 3.7.0) | Apache-2.0 | ~1 Go (paddlepaddle + modèles) | Moyen, Avancé | `paddlepaddle 3.3.1` n'a pas de wheel cp314 Windows. Modèles téléchargés par PaddleX au premier usage. |
| **easyocr** | Non — non installé | — (PyPI 1.7.2) | Apache-2.0 | ~3 Go (PyTorch) + modèles | Moyen | Télécharge ses modèles au premier lancement ; hors R4 sans pré-provisionnement. |

Les tailles sont des ordres de grandeur (wheel + dépendances + modèles), pas des
mesures d'occupation disque.

## Détail par moteur

### tesserocr 2.12.0 — seul moteur opérationnel

- Installé pendant ce diagnostic avec un wheel binaire (~24 Mo, 2 s, **aucune
  compilation**) :
  `python -m pip install --only-binary=:all: --no-deps tesserocr`
- Le wheel Windows embarque **Tesseract 5.5.3** et **Leptonica 1.87.0** dans
  `tesserocr.cp314-win_amd64.pyd`. Aucun `tesseract.exe` séparé n'est nécessaire.
- Le wheel **ne contient pas de tessdata**. Téléchargement manuel de
  `fra.traineddata` (1,08 Mo) et `eng.traineddata` (3,92 Mo) depuis
  `tessdata_fast` vers `ocr_app/models/tessdata/`.
- `tesserocr.get_languages("models/tessdata")` retourne bien `['eng', 'fra']`.
- Emplacement : `C:\Users\lutho\AppData\Roaming\Python\Python314\site-packages\tesserocr`.
- Limites : pas d'analyse de mise en page ni de tableaux ; adapté au mode Simple.

### tesseract.exe — absent

- Absent du PATH, de `C:\Program Files\Tesseract-OCR`, du registre Windows et
  de winget. Conséquence : `pytesseract` ne peut pas fonctionner, même installé.
- Pour une installation classique : `winget install UB-Mannheim.TesseractOCR`
  (5.4.0) ou `winget install tesseract-ocr.tesseract` (5.5.3), puis vérifier
  `tesseract --version`.
- Non nécessaire au mode Simple si `tesserocr` est retenu.

### pytesseract — absent

- Wrapper ligne de commande autour de `tesseract.exe` ; aucune valeur ajoutée
  par rapport à `tesserocr` si le binaire est absent. Il ajoute un processus par
  appel, alors que `tesserocr` reste dans le worker (R2/R5).

### rapidocr-onnxruntime — incompatible avec Python 3.14

- Dernière version 1.4.4 : `requires-python >=3.6,<3.13`. L'installation échoue
  sur Python 3.14. Le `pyproject.toml` du projet le liste pourtant dans l'extra
  `medium` : **incohérence à corriger** pour la phase 2.
- Modèles inclus dans le wheel (bon point pour R4), dépend de `opencv-python` et
  `onnxruntime` (déjà présent en 1.31.1).

### rapidocr (nouveau nom) — compatible Python 3.14, non installé

- Version 3.10.0, wheel pur Python de 26 Mo, modèles embarqués, `requires-python
  >=3.8,<4`. Dépend de `opencv-python-headless` (wheel abi3, compatible 3.14) et
  d'`onnxruntime`.
- C'est le candidat **Moyen** sur l'environnement Python 3.14 actuel.

### docling — non installé

- `docling 2.149.0` dépend de `docling-slim[standard]`, qui tire PyTorch,
  torchvision, `docling-ibm-models`, RapidOCR, scipy, etc. Compter 3 à 5 Go.
- Compatible Python 3.14 sur le papier (wheels torch cp314 disponibles), mais
  l'installation est lourde et doit rester dans le module Avancé (R1, R13).
- **R4** : par défaut, Docling télécharge ses modèles depuis Hugging Face au
  premier usage. Pour un fonctionnement hors ligne :
  1. pré-télécharger avec `docling-tools models download` (ou
     `docling models download`) ;
  2. fixer `DOCLING_ARTIFACTS_PATH` vers le dossier parent des modèles, ou
     passer `--artifacts-path` / `PdfPipelineOptions(artifacts_path=...)`.
  Si `artifacts_path` n'est pas fourni, Docling télécharge les modèles
  automatiquement au premier usage : le chemin doit être explicite.

### paddleocr — bloqué sur Python 3.14

- `paddlepaddle 3.3.1` publie des wheels Windows cp39 à **cp313 seulement**,
  pas de cp314. L'installation standard `paddleocr` + `paddlepaddle` échoue donc
  sur l'interpréteur 3.14 de la machine.
- Solution pour l'évaluation : venv **Python 3.12** (interpréteur présent via
  `py -3.12`), qui dispose de wheels cp312 pour paddlepaddle.
- **R4** : PaddleOCR/PaddleX télécharge les modèles au premier usage dans
  `~/.paddlex`. Prévoir `PADDLE_PDX_CACHE_HOME`, des `model_dir` locaux et
  `PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True` (le contrôle de connectivité peut
  bloquer le démarrage hors ligne). Comportement à valider version par version.

### easyocr — non installé

- Dépend de PyTorch + torchvision (~3 Go) et télécharge ses modèles au premier
  lancement. Hors R4 sans `model_storage_directory` explicite et
  `download_enabled=False`.
- Optionnel : ne fait pas partie du triplet Docling / PaddleOCR / Tesseract à
  évaluer, peut servir de baseline supplémentaire si le temps le permet.

## Test trivial réalisé

Image générée avec Pillow (police Segoe UI 56, fond blanc, texte
`Bonjour éàç`), puis OCR avec `tesserocr` et `tessdata_fast` (`lang=fra`) :

| Moteur | Attendu | Obtenu | Verdict | Durée |
|---|---|---|---|---|
| tesserocr 2.12.0 / Tesseract 5.5.3 | `Bonjour éàç` | `Bonjour éàç` | **Identique** | ~0,2 s |

Les autres moteurs n'ont pas été testés : absents, incompatibles, ou écartés car
ils téléchargent des modèles (docling, paddleocr, easyocr) — voir la sortie de
`--self-test`. Ce test ne prouve que le fonctionnement de base : il ne préjuge
pas de la qualité sur le jeu de test (R10).

## Recommandation préliminaire par mode

Ces recommandations sont **provisoires** : le choix final doit se faire sur le
jeu de test interne, pas sur un benchmark public (R10).

| Mode | Moteur recommandé | Justification | Réserves |
|---|---|---|---|
| **Simple** | **tesserocr 2.12.0** (Tesseract 5.5.3) | Seul moteur déjà opérationnel et validé sur le test trivial ; léger, aucun téléchargement runtime, tessdata local. | Pas de layout/tableaux ; qualité Tesseract à confirmer sur les documents réels. |
| **Moyen** | **RapidOCR** (`rapidocr` 3.x sur Python 3.14 ; `rapidocr-onnxruntime` 1.4.4 sur Python 3.12) + OpenCV | Modèles embarqués (R4), bon compromis vitesse/précision sur scans, dépendances déjà à moitié présentes (`onnxruntime`). | Non installé à ce jour ; qualité à mesurer sur le jeu de test. PaddleOCR est l'alternative si un venv 3.12 est retenu. |
| **Avancé** | **Docling** | Seul candidat installable pour layout, tableaux et structure complexe ; licence MIT. | Très lourd (~3–5 Go) ; téléchargement Hugging Face à neutraliser (R4). PaddleOCR PP-Structure (Python 3.12) en repli si Docling est trop lourd ou instable. |

## À installer pour la vraie évaluation phase 2

### Recommandation d'architecture

Créer **un venv par module** (R13), plutôt que d'installer dans le Python global.
**Python 3.12** est le plus sûr comme base commune :

- `tesserocr` : wheel cp312 disponible ;
- `rapidocr-onnxruntime` : exige `<3.13` ;
- `paddlepaddle` : wheels cp312 disponibles ;
- `docling` : supporte 3.12 ;
- `easyocr` / PyTorch : wheels cp312 disponibles.

L'interpréteur `py -3.12` (3.12.10, 64 bits) est déjà présent sur la machine.

### Option A — rester sur Python 3.14 (le moins de travail)

| Besoin | Commande | Poids indicatif |
|---|---|---|
| Simple | Déjà fait : `python -m pip install --only-binary=:all: --no-deps tesserocr` | ~24 Mo |
| Moyen | `python -m pip install rapidocr` | ~26 Mo + OpenCV 42 Mo + onnxruntime |
| Avancé | `python -m pip install docling` puis `docling-tools models download` | ~3–5 Go |
| PaddleOCR | **Impossible** sur 3.14 (pas de wheel cp314) | — |
| easyocr (option) | `python -m pip install easyocr` | ~3 Go |

### Option B — venv Python 3.12 par module (recommandé pour l'évaluation)

```bat
py -3.12 -m venv .venv-eval-simple
.venv-eval-simple\Scripts\python -m pip install -U pip
.venv-eval-simple\Scripts\python -m pip install pillow tesserocr
```

```bat
py -3.12 -m venv .venv-eval-medium
.venv-eval-medium\Scripts\python -m pip install -U pip
.venv-eval-medium\Scripts\python -m pip install rapidocr-onnxruntime==1.4.4
```

```bat
py -3.12 -m venv .venv-eval-advanced
.venv-eval-advanced\Scripts\python -m pip install -U pip
.venv-eval-advanced\Scripts\python -m pip install docling
.venv-eval-advanced\Scripts\docling-tools models download
```

```bat
py -3.12 -m venv .venv-eval-paddle
.venv-eval-paddle\Scripts\python -m pip install -U pip
.venv-eval-paddle\Scripts\python -m pip install paddlepaddle paddleocr
```

Pour `pytesseract`, il faudrait en plus installer le binaire Tesseract
(UB Mannheim) et ses tessdata ; ce n'est pas nécessaire si `tesserocr` est retenu.

### Préparation hors ligne (R4)

| Moteur | Action avant livraison |
|---|---|
| tesserocr / pytesseract | Embarquer `tessdata` (`eng`, `fra`) et définir `TESSDATA_PREFIX` explicitement. Aucun téléchargement automatique. |
| rapidocr | Rien à faire : modèles inclus dans le wheel. |
| docling | `docling-tools models download`, puis `DOCLING_ARTIFACTS_PATH` (chemin absolu) ou `--artifacts-path`. Sans chemin explicite, téléchargement HF. |
| paddleocr | Pré-remplir le cache PaddleX (`PADDLE_PDX_CACHE_HOME`) ou passer des `model_dir` locaux ; `PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True` pour éviter le contrôle réseau au démarrage. |
| easyocr | `Reader(..., model_storage_directory="...", download_enabled=False)` avec modèles déjà présents. |

### Points à corriger dans le projet

1. `pyproject.toml`, extra `medium` : `rapidocr-onnxruntime` est incompatible
   avec `requires-python >=3.10` du projet si l'interpréteur est 3.14. De plus,
   cet extra associe `rapidocr-onnxruntime` (qui tire `opencv-python`) à
   `opencv-python-headless` : les deux paquets fournissent le même module `cv2`
   et ne doivent pas coexister. Passer à `rapidocr` (nouveau nom) résout les
   deux problèmes, car il dépend directement de `opencv-python-headless`.
2. `models/tessdata` (5 Mo de binaires) n'est pas dans `.gitignore` : décider
   s'il est versionné ou fourni par l'installeur (R4 exige des chemins explicites).
3. Le `tesserocr` installé pendant ce diagnostic est dans le site utilisateur
   global, pas dans un venv. Pour l'évaluation, repartir d'un venv propre (R13).

## Références

- Docling, options hors ligne et `DOCLING_ARTIFACTS_PATH` :
  https://docling-project.github.io/docling/usage/advanced_options/
- Docling, dépôt et téléchargement des modèles :
  https://github.com/docling-project/docling
- PaddleOCR FAQ (cache et fonctionnement hors ligne) :
  https://www.paddleocr.ai/main/en/FAQ.html
- Versions, licences et tailles : pages PyPI de `tesserocr`, `pytesseract`,
  `rapidocr-onnxruntime`, `rapidocr`, `docling`, `paddleocr`, `easyocr`.
