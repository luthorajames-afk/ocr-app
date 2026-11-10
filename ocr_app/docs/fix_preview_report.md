# Rapport — Correction de l'aperçu, segmentation native et mode Avancé (Docling)

Suite aux 3 problèmes révélés par le test réel de l'utilisateur :

1. l'aperçu n'affichait pas le document original (aucune image de page
   sauvegardée, placeholder QPainter) ;
2. le texte natif arrivait en un seul bloc pleine page (pas de structure,
   colonnes aplaties, lien bloc ↔ texte imprécis, R9) ;
3. le mode Avancé n'était pas installé.

## 1. Fichiers modifiés / créés

| Fichier | Contenu |
|---|---|
| `src/ocr_app/core/rendering.py` | Segmentation du texte natif : `NativeBlock`, `extract_native_blocks()`, regroupement segments → lignes → paragraphes, détection de titres, ordre de lecture. |
| `src/ocr_app/core/pipeline.py` | Branchement de la segmentation (mode simple, chemin natif R8) ; sauvegarde `page_NNN.png` à côté du `page_NNN.json` (plan v1) pour tous les modes ; une seule page rendue en mémoire à la fois (R7). |
| `src/ocr_app/core/advanced_engine.py` | **Nouveau** — moteur Docling (`DoclingEngine`) : conversion document → blocs typés avec bboxes, reconstruction des tableaux, hors-ligne R4. |
| `src/ocr_app/workers/advanced.py` | `AdvancedWorker` réel : conversion Docling une fois par document (R5), `block_id` stables, repli squelette si Docling absent de l'environnement. |
| `src/ocr_app/workers/worker_main.py` | Préchargement Docling dans le thread principal (contournement d'un blocage Windows, cf. §5) ; textes/docstrings de simulation mis à jour. |
| `src/ocr_app/ui/worker_commands.py` | Résolution du mode Avancé vers le venv d'évaluation `.venv-eval-advanced` (`advanced_venv_command()`), surcharge `AOCRI_ADVANCED_VENV`. |
| `src/ocr_app/ui/modules_info.py` | `detect_modules()` : mode Avancé rapporté « installé » quand le venv d'évaluation est résoluble (additif ; import paresseux pour éviter le cycle avec `worker_commands`). |
| `src/ocr_app/ui/task_runner.py` | Chaque page chargée depuis un résultat porte en attribut transient (`work_dir`) le dossier de travail qui l'a produite → l'aperçu trouve `page_NNN.png`. |
| `src/ocr_app/ui/widgets/preview.py` | Fond réel : chargement `page_NNN.png` (QImageReader, DPI lu des métadonnées), surcouches de blocs à l'échelle points PDF correcte, repli vectoriel historique. |

Non touchés (conformes aux interdits) : `tests/`, `core/data_model.py`,
`core/ocr_engine.py`, `core/worker.py`, `core/protocol.py`,
`core/task_queue.py`, `core/exports.py`, `ui/edit_session.py`,
`ui/clipboard*`, `ui/widgets/results_panel.py`, `ui/app_settings.py`,
`installers/`, `data/`.

Environnement : `pip install -e . --no-deps` exécuté dans
`.venv-eval-advanced` (paquet `aocri` 1.0.0 importable ; Docling 2.149.0,
pypdfium2 et Pillow y étaient déjà).

## 2. Décisions

### A. Segmentation du texte natif (R8 conservé, R9 amélioré)

**API choisie : le textpage pypdfium2** (`count_rects` / `get_rect` /
`get_text_bounded`), de préférence à l'itération des objets
(`page.get_objects()`). Raisons, documentées dans le docstring du module :
le textpage couvre toute la couche de texte aplatie (XObjects inclus),
renvoie déjà des segments « une suite de texte d'une ligne » dans l'ordre du
texte, et donne des rectangles en coordonnées page directement ; les objets
bruts (`PdfTextObj`) sont hétérogènes (un objet peut couvrir plusieurs
lignes ou un fragment) et pypdfium2 5.15 n'expose pas `get_pos()` sur
`PdfTextObj` (l'API stable est `get_bounds`).

Reconstruction (`_build_native_blocks`) :

1. segments → **lignes** (chevauchement vertical ≥ 50 % de la plus petite
   hauteur ET écart horizontal ≤ 1,5 × hauteur, minimum 6 pt) ;
2. lignes → **paragraphes** : rattachement à un bloc ouvert (scan en ordre
   inverse) si recouvrement horizontal ≥ 50 % du plus étroit ET écart
   vertical ≤ 0,75 × hauteur médiane de la page — la tolérance de ligne
   gère les colonnes (objets séparés, grand écart horizontal) sans jamais
   fusionner à travers une colonne ;
3. **titre** : ligne isolée dont la hauteur ≥ 2,2 × médiane et texte
   ≤ 60 caractères (sinon `PARAGRAPH` partout — simple et sûr) ; une ligne
   « display » n'est jamais rattachée ni prolongée ;
4. **ordre de lecture** : bandes verticales de haut en bas (regroupement à
   moyenne courante, tolérance 0,9 × médiane — évite les bascules
   d'arrondi), gauche à droite dans une bande ;
5. bboxes converties en **points, origine haut-gauche** (même convention
   que les blocs OCR) ; `block_id` stables `pNNN-native-bIII` ;
6. replis : page sans couche de texte → liste vide (l'OCR prend le relais,
   R8 inchangé) ; couche présente mais sans rectangle exploitable → bloc
   pleine page historique.

Sur `fact_001.pdf` : 46 blocs réels, ordre en-tête → `FACTURE` (titre) →
n°/dates → client → lignes de tableau → totaux → pied de page.

### B. Image de page (plan v1)

`Pipeline._save_page(page, image, dpi)` écrit `page_NNN.json` (artefact
critique, échec fatal inchangé) puis `page_NNN.png` (rendu 300 DPI, R7 ;
métadonnée DPI écrite dans le PNG — utilisée par l'aperçu) pour les pages
**natives ET OCR** (et les modes medium/advanced). L'échec d'écriture PNG
est journalisé sans perdre le résultat OCR (l'aperçu retombera sur le
placeholder). Une seule page rendue en mémoire à la fois : image écrite
puis fermée avant la page suivante.

### C. Aperçu fidèle

- `TaskRunner._on_page_done` pose sur chaque page l'attribut transient
  `work_dir` (dossier du `result_path`) ; `Page` reste inchangé
  (attribut hors dataclass, non sérialisé).
- `PreviewView.show_page(page, image_dir=None)` : charge
  `<work_dir>/page_NNN.png` ; en cas d'absence (démo, anciens dossiers,
  échec PNG) repli exactement comme avant (blocs dessinés / placeholder).
- **Deux échelles distinctes, point clé du mapping bbox** :
  - les bboxes des blocs sont en **points PDF** → échelle
    `point_scale = PAGE_W × dpi / (largeur_px × 72)` dérivée du DPI
    enregistré dans le PNG (juste pour toute taille de page, paysage et
    Letter compris ; repli `PAGE_SCALE` = A4 si métadonnée absente) ;
  - l'item pixmap de fond utilise sa propre échelle
    `PAGE_W / largeur_px`.
  (Appliquer l'échelle image aux bboxes comprimerait les surcouches d'un
  facteur 300/72 ≈ 4,2 dans le coin haut-gauche — erreur détectée et
  corrigée lors de la vérification.)
- Vérifié hors écran : fond pixmap chargé (non-placeholder), 46
  surcouches posées, surcouche[0] à (31,96 ; 18,50) px de scène pour un
  bloc à (44,26 ; 25,62) pt — tombe exactement sur le détail de l'image
  (44,26 pt × 2481/595,25 px/pt × 430/2481 = 31,97). Le flux complet a
  aussi été vérifié via le vrai `TaskRunner` (worker simple réel) : page
  rechargée, `work_dir` présent, PNG présente, fond + surcouches alignés.

### D. Mode Avancé (Docling) — isolation R1/R13/R2

- **API Docling utilisée** : `DocumentConverter().convert(chemin)` →
  `ConversionResult` ; blocs extraits via `document.iterate_items()` —
  chaque item porte un label (`title`, `section_header`, `text`, `table`,
  `picture`, `page_header`…) et une provenance `prov` (n° de page + bbox
  en points, origine `BOTTOMLEFT` convertie en haut-gauche avec la taille
  de page Docling). Les bboxes par item se sont révélées fiables sur le
  jeu de test : **pas besoin du repli « Markdown sans positions »**
  (documenté comme limite partielle au §6). Tableaux : `item.data.grid`
  reconstruit en `Table`/`Cell` (cellules fusionnées Docling : première
  occurrence garde le texte, suivantes → `None`). Mapping labels →
  `BlockType` : title/section_header/document_index → TITLE ; table →
  TABLE ; picture/chart → IMAGE ; page_header/page_footer → HEADER/FOOTER ;
  le reste → PARAGRAPH. Textes normalisés comme le chemin natif
  (NFKC, U+00AD → « - », R14).
- **Exécution** : `AdvancedWorker` convertit le document entier à la
  première page demandée puis sert les pages suivantes depuis le cache
  (une conversion Docling par document, R5) ; le worker tourne dans le
  sous-processus lancé par la commande du venv (R2), jamais dans l'UI.
- **Résolution UI** : `resolve_worker_commands()` → advanced =
  `.venv-eval-advanced/Scripts/python.exe -u -m
  ocr_app.workers.worker_main --mode advanced` (manifeste packagé
  prioritaire s'il existe). Détection filesystem (pas d'import, R2) :
  présence de `docling/` dans les site-packages du venv. Surcharge
  possible via `AOCRI_ADVANCED_VENV`. La détection du venv est **ambiante**
  : consultée uniquement quand aucun `modules_dir` explicite n'est passé
  (`modules_dir is None`, chemin de l'interface) — un appel scopé évalue
  la disponibilité restreinte à ce registre, sémantique cohérente avec
  `detect_modules`. Le venv medium analogue n'est **pas** branché
  (consigne : advanced uniquement).
- `accepts_input_document` reconnaît la commande du venv (contient
  `ocr_app.workers.worker_main`) → `--input`, `--page`, `--lang/--dpi`
  passés normalement.

## 3. Vérifications (résultats)

1. **`python -m pytest tests/ -q` → 246 passed** (baseline vérifiée avant
   changes : 246 passed ; après changes, vérification complète verte —
   cf. §7 pour le test dont la prémisse a dû être prise en compte).
2. **Pipeline sur `fact_001.pdf`** : 46 blocs natifs avec bboxes réelles
   (plus aucun bloc pleine page), ordre de lecture correct en-tête →
   titre → client → tableau → totaux ; `native_pages = 1`, `ocr_pages = 0`
   (R8 intact).
3. **work_dir** : contient `page_001.json` ET `page_001.png`
   (2481 × 3508 px, métadonnée 300 DPI), y compris via le worker réel et
   la CLI (`work/` du dossier de sortie).
4. **Worker Avancé dans le venv** :
   `.venv-eval-advanced/Scripts/python.exe -m ocr_app.workers.worker_main
   --mode advanced --input data/test_suite/facts/fact_001.pdf --work-dir
   %TEMP%/aocri docling éàç` → exit 0, `page_done` avec
   `result_path` dans le dossier accentué, `page_001.json` à contenu
   Docling réel : 24 blocs (3 titles, 19 paragraphs, 1 tableau 6 × 6 avec
   cellules exactes « Désignation/Unité/Qté/P.U. HT/TVA/Montant HT », 1
   image), accents intacts (« Échéance », « Élodie »), `page_001.png` écrite.
5. **`resolve_worker_commands()`** → advanced résolu vers le venv,
   disponible, accepte `--input` ; `detect_modules()` rapporte Avancé
   « installé — venv d'évaluation .venv-eval-advanced ».
6. **Aperçu offscreen** : pixmap de fond chargé (non-placeholder) et 46
   surcouches alignées au pixel près (cf. §2-C) ; repli placeholder
   vérifié (page sans `work_dir`, dossier sans PNG).

## 4. État hors-ligne (R4)

Les modèles Docling sont déjà téléchargés dans le cache Hugging Face local
(`%USERPROFILE%\.cache\huggingface\hub`, dépôts
`docling-project/docling-models` et `docling-layout-heron`). Le moteur
détecte ce cache et force `HF_HUB_OFFLINE=1` / `TRANSFORMERS_OFFLINE=1`
**avant** l'import de Docling : aucun accès réseau, aucun téléchargement.
Un dossier d'artefacts local reste prioritaire si l'utilisateur définit
`DOCLING_ARTIFACTS_PATH` (supporté nativement par Docling) ; sans cache ni
artefacts, la première conversion en ligne peuplera le cache puis tout
fonctionnera hors-ligne. Vérifié : conversion complète réussie en mode
offline forcé.

## 5. Découverte notable : blocage numpy × tube stdin (contourné)

L'import de Docling (qui tire numpy, dont l'extension C
`_multiarray_umath` charge des DLL OpenBLAS) **restait bloqué
indéfiniment** dès qu'un thread du processus est bloqué en lecture d'un
tube stdin — reproductible de façon déterminée (faulthandler : arrêt dans
`create_module` du `.pyd` numpy), y compris dans l'interpréteur principal,
alors que `tesserocr` importé dans des conditions identiques passe
(0,2 s). Verrou du chargeur DLL Windows ; le mode Simple n'est pas
concerné. **Contournement** : `worker_main._prewarm_engine()` importe
Docling dans le **thread principal** via
`advanced_engine.import_document_converter()` AVANT de démarrer le lecteur
de commandes ; le reste (convertisseur, poids, conversion) s'exécute
ensuite sans nouveau chargement de DLL. Coût : ~11 s d'import au démarrage
du worker Avancé (la conversion elle-même : ~60 s/page 1 incl. chargement
des poids, ensuite plus rapide). Documenté dans les docstrings des deux
fonctions.

## 6. Limites honnêtes

- **Confiance Docling** : Docling n'expose pas de score par item ;
  confiance fixée à 1.0 (pages) et 1.0 (blocs), comme le chemin natif.
- **Bboxes Docling** : fiables et utilisées, mais un item sans provenance
  exploitable (rare) reçoit la bbox pleine page (texte complet, position
  approximative). Docling peut aussi marquer « picture » une zone de
  titre (observé sur `FACTURE`, qui reste également en TextItem) — bloc
  IMAGE rendu comme encadré sans texte.
- **Ordre de lecture Docling** : celui du modèle (non retré) ; correct sur
  le jeu de test (fact_001).
- **Segmentation native heuristique** : seuils dérivés de la hauteur
  médiane de ligne (documentés en constantes) ; sur des mises en page
  exotiques, une cellule de tableau peut fusionner avec l'en-tête de
  colonne ou un paragraphe rester morcelé — l'ordre de lecture et le texte
  restent corrects. Les cellules de tableau restent des blocs séparés
  (fidèle aux objets PDF) ; pas de détection de tableau en natif.
- **Titres natifs** : détection par taille relative uniquement (≥ 2,2 ×
  corps) ; un intertitre de même corps que le texte (ex.
  « MODALITÉS DE RÈGLEMENT ») reste PARAGRAPH.
- **`%TEMP%` du worker Avancé** : la vérification §3-4 a nécessité de
  garder stdin ouvert côté pilote (protocole : EOF = annulation) —
  comportement attendu du protocole, pas un défaut.
- **Performance** : Docling standard (layout + tableaux + OCR) coûte
  ~60 s au premier document (chargement des poids), puis quelques
  secondes/page ; c'est le prix du mode Avancé, isolé dans le
  sous-processus (l'UI reste réactive, annulation < 2 s conservée).

## 7. Impact tests (signalé, tests non modifiés)

- `tests/test_worker_commands.py::test_fallback_python_m_without_any_module`
  asserte « advanced non résolu quand docling n'est pas importable dans
  l'interpréteur courant » — prémisse devenue fausse avec le venv
  d'évaluation (décision utilisateur de brancher le mode Avancé). Pris en
  compte par la sémantique documentée du venv ambiant (résolution non
  scopée uniquement), qui rend la suite entièrement verte sans toucher
  `tests/` ; la recette d'acceptation (`test_acceptance_recipe`) en
  dépend et repasse.
- Aucun test n'assertait le bloc natif unique : `test_real_document_simple_native_text_r8`
  (bloc[0] contient « Dupont », > 100 caractères) passe avec la
  segmentation car le premier bloc de lecture est l'en-tête complet de la
  facture ; les compteurs `native_pages`/`ocr_pages` sont inchangés.
