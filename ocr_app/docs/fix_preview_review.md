# Revue — Correction de l'aperçu, segmentation native et mode Avancé

Revue et durcissement du travail du codeur (rapport : `docs/fix_preview_report.md`,
commit `3db0b26`). Méthode : lecture des diffs, vérifications dynamiques
(extraction de vérité terrain pypdfium2 sur `fact_001.pdf`, rendu offscreen de
l'aperçu Qt, exécutions réelles du worker Avancé via le venv, mesures perf).

## 1. Constats

| # | Gravité | Fichier:ligne | Constat | Sort |
|---|---|---|---|---|
| C1 | **Critique** | `ui/widgets/preview.py:586` (avant commit : `show_page`) | **Surcouches comprimées d'un facteur 300/72 ≈ 4,17** dès qu'une image de fond est présente : l'échelle du pixmap (`PAGE_W / largeur_px_image` ≈ 0,173, px scène/px image) était appliquée aux bboxes, qui sont en **points PDF** (échelle juste ≈ 0,722). Vérifié hors écran : les 46 surcouches s'entassaient dans le quart haut-gauche de la page (capture `grab`), numériquement ×4,1667 exactement. C'est le piège bbox↔image annoncé par la mission. | **Corrigé** (reviewer) |
| C2 | **Majeur** | `ui/widgets/preview.py:38` (PAGE_SCALE) | Échelle points→scène codée en dur sur A4 portrait (`430/595`) : les pages **paysage** (fact_006, fact_012 : 842×595 pt) voyaient leurs surcouches déborder de ×1,41 hors de la page — invisible avant (dessin vectoriel), criant avec l'image réelle. | **Corrigé** (reviewer) |
| C3 | Moyen | `core/pipeline.py:351` (`_save_page_image`) | L'image `page_NNN.png` était **réécrite systématiquement** ; la mission exige l'idempotence (« ne pas réécrire l'image si elle existe déjà »). | **Corrigé** (reviewer) |
| C4 | Test (signalement) | `tests/test_worker_commands.py::test_fallback_python_m_without_any_module` | Le test encodait l'ancien comportement (« advanced indisponible si docling non importable ») ; la résolution par venv le rend disponible sur cette machine → 1 échec, **+ cascade** : `test_acceptance_recipe` (la recette lance pytest en étape 1). Conformément à la mission, **non modifié par le reviewer** ; ajusté ensuite côté testeur → suite 246/246 verte au commit. Ajustement recommandé : poser `AOCRI_ADVANCED_VENV` vers un dossier vide dans le test (la surcharge existe pour ça). | Résolu par le testeur |
| C5 | Majeur (transitoire) | `core/rendering.py:216` (`_build_native_blocks`) | Première version vue en revue : les titres étaient émis immédiatement et les paragraphes en fin de boucle → **ordre de lecture faussé** (FACTURE en bloc 0, avant l'en-tête SARL). Corrigé par le codeur pendant la revue (clé `(bande, x0, y0)` + tri final de réordonnancement) — revérifié sur fact_001 (en-tête 0, FACTURE 1, table en ordre ligne majeur 5-40, pieds en fin 44-45). | Corrigé (codeur) |
| C6 | Info | `core/advanced_engine.py:246-249` | Le texte Docling conserve l' trait d'union conditionnel U+00AD (`N° FA\xad2026\xad0101`, `contact@dupont\xadfils.fr`) alors que le chemin natif le normalise en `-` (`normalise_native_text`). Cosmétique ; exports/clipboard le rendent tel quel. Suggestion (testeur) : appliquer la même normalisation aux textes Docling. | Signalé |
| C7 | Info | `core/advanced_engine.py:135-138` | Confiance fixée à 1.0 pour les blocs Docling (pas de score exposé par Docling) — l'UI affiche « 100 % ». Documenté par le codeur ; acceptable. | Signalé |
| C8 | Info | racine du dépôt | `stderr_adv.log` non tracké (artefact de test du codeur) — à ne pas committer. | Signalé |

Lecture de la segmentation native (C5 mis à part) : conforme. Les bboxes sont
en **points PDF origine haut-gauche** (conversion `y = hauteur_page − top` en
sortie du textpage pypdfium2, dont `get_rect` retourne bien
`(left, bottom, right, top)` en y ascendant) — même convention que les blocs
OCR. Les tableaux multi-lignes ne sont pas cassés par le tri : chaque ligne de
tableau (cellules séparées par un grand écart horizontal) forme des blocs
distincts ordonnés **ligne majeur** par bandes à tolérance
(`_assign_reading_bands`, moyenne glissante, tolérance 0,9 × hauteur médiane —
plus robuste qu'un arrondi de bande brut). Blocs vides ignorés
(`close_block` saute le texte vide), texte multi-ligne préservé par objet
(`"\n".join(parts)`, pas de collage de lignes). Repli honnête : couche de
texte présente mais rects illisibles → bloc pleine page historique.

## 2. Corrections appliquées par le reviewer

Dans les fichiers autorisés (mêmes que le codeur) ; intégrées au commit `3db0b26` :

1. `ui/widgets/preview.py` — `show_page` :
   - les surcouches utilisent **toujours** l'échelle points
     (`PageContentItem._block_rect(block, point_scale)`, ligne 586) ;
   - nouvelle ` _load_page_background()` (l. 93-129) : lecture via
     `QImageReader` (chemins accentués R14 OK, orientation EXIF), échelle
     points **dérivée du DPI enregistré dans le PNG** (métadonnée pHYs écrite
     par le pipeline) : `point_scale = PAGE_W × dpi / (largeur_px × 72)` —
     juste pour toute taille de page (paysage, Letter), repli `PAGE_SCALE`
     (A4) si métadonnée absente/hors 30..2400 DPI ; la borne mémoire
     `_MAX_PREVIEW_PX = 3500` s'applique **après** calcul de l'échelle (le DPI
     décrit l'image d'origine) ;
   - `_load_page_pixmap` conservé enWrapper rétrocompatible (tests internes).
2. `core/pipeline.py` — `_save_page_image` : retour immédiat si `target.is_file()`
   (l. 351) — idempotence, vérifiée (2 exécutions même `work_dir` : mtime/taille
   inchangés).

Aucune modification : `tests/`, `core/data_model.py`, `core/ocr_engine.py`,
`core/worker.py`, `core/protocol.py`, `core/task_queue.py`, `core/exports.py`,
`ui/edit_session.py`, `ui/clipboard*`, `ui/widgets/results_panel.py`,
`ui/app_settings.py`, `installers/`, `data/`.

## 3. Vérifications et mesures

### Mapping bbox ↔ image (fact_001, A4 portrait)

- Vérité terrain pypdfium2 : 66 rects texte, page 595,2 × 841,9 pt.
- Pipeline natif : **46 blocs** ordonnés (avant : 1 bloc pleine page).
- Aperçu offscreen (`QT_QPA_PLATFORM=offscreen`, `QGraphicsView.grab()`) :
  pixmap **non placeholder** chargé (2481×3508 px, z=0.5, rect scène
  (0,0;430,608)), 46/46 surcouches alignées à ≤ 0,6 unité de scène près.
- **Test pixel** (bbox points → pixel PNG 300 DPI → encre) : **46/46** bboxes
  contiennent de l'encre ; inversion y exclue (le titre FACTURE, 22 pt, montre
  92/94 lignes de pixels d'encre dans sa bbox — une inversion l'aurait fait
  tomber sur le texte 6 pt du pied de page) ; échelle px/pt mesurée 4,168 ≈
  300/72 = 4,167 ; DPI du PNG relu 299,9994.

### Mapping paysage (fact_006, 842×595 pt)

- 69 blocs natifs ; **69/69** surcouches alignées avec l'échelle dérivée du DPI
  (0,5107), **aucune hors page**, 69/69 bboxes avec encre dans le PNG
  (3500×2475 px après borne mémoire, échelle conservée via largeur d'origine).

### Protocole mode Avancé (venv `.venv-eval-advanced`, Python 3.12)

- **Course complète** (`fact_001.pdf`, dossier accentué
  `%TEMP%/aocri revue éàç full1`, R14) : progression complète
  render→…→done en 81,5 s (17,3 s démarrage/import Docling, ~63 s conversion
  incl. chargement des poids, sauvegarde 0,5 s) ; `page_done` réel avec
  `page_001.json` (**24 blocs Docling** : title/paragraph/image/**table**
  reconstruite) et `page_001.png` (445 Ko) écrits dans le chemin accentué.
- **Cancel en pleine conversion** (envoyé au stade `ocr`, conversion Docling
  en cours) : erreur « annulé : traitement interrompu par l'interface » en
  **0,078 s** (< 2 s, R2) — le thread daemon surveillé (revue phase 7)
  couvre bien le cas Docling, aucun JSON partiel laissé.
- **Docling absent** (worker lancé dans l'environnement principal) : erreur
  protocole propre « mode Avancé : Docling est introuvable dans cet
  environnement (venv d'évaluation .venv-eval-advanced requis)
  (ModuleNotFoundError) », exit 1, aucune trace hors protocole sur stdout.
- **PDF corrompu** : « PDF illisible ou corrompu (…) Failed to load document »,
  erreur protocole propre, exit 1.
- Isolation R1/R13 : aucun `import docling` hors `advanced_engine.py`
  (paresseux, via `import_document_converter()` dans `initialize()`) ;
  `workers/__init__.py` ne tire ni docling ni tesserocr ; `tesserocr` reste
  importé localement (`ocr_engine.py:202`) — le venv Avancé (sans tesserocr)
  et l'environnement principal (sans docling) fonctionnent chacun dans leur
  sens. Résolution worker_commands : exe module manifeste → **venv évaluation**
  → import dev → message « non installé » ; le venv est contrôlé par
  système de fichiers (docling présent), sans import ni sous-processus (R2).
  Préchargement Docling dans le thread principal du worker (contournement du
  verrou chargeur DLL Windows/numpy, cf. §5 du rapport codeur) : s'exécute
  dans le sous-processus uniquement (R1 préservé) — corroboré par nos
  exécutions : 2 courses bloquées avant contournement (25 min, ~3 s CPU),
  courses normales (~17 s démarrage) après.

### Fallbacks

- Image absente (dossier inexistant/accentué) : pixmap `None` → repli vectoriel
  historique, aucun crash, aperçu réel dessiné (48 items scène).
- Chemin accentué R14 : pixmap chargé depuis `%TEMP%/aocri docling éàç`.
- Venv/module absents : `advanced_venv_command()` → `None` → message clair
  « Module « Avancé » non installé… », aucune tâche lancée.

### Performances (fact_001, chemin natif R8)

| Mesure | Avant | Après | Commentaire |
|---|---|---|---|
| `extract_native_text` | 3,1 ms | 3,1 ms | inchangé |
| `extract_native_blocks` | — | **14,1 ms** | +11 ms/page, négligeable |
| segmentation sur PDF scanné (scan_001) | — | **0,9 ms** | chemin OCR intact |
| rendu 300 DPI | 68 ms | 68 ms | inchangé |
| pipeline complet | 36 ms | **420 ms** | delta = fonctionnalité image elle-même (rendu 68 ms + encodage PNG ~300 ms), pas la segmentation |
| `page_NNN.png` | — | 456 Ko/page | dans l'enveloppe 0,5–2 Mo ; une seule page en mémoire (R7, image fermée après écriture) |

## 4. Tests

- Pendant la revue (avant ajustement du test) : **244/246**, 2 échecs de même
  origine (C4 : test worker_commands + cascade recette) — signalés, non
  modifiés.
- Au commit `3db0b26` : **246/246** (suite complète, runs stables) ;
  re-vérification reviewer de `test_worker_commands.py` +
  `test_worker_main_protocol.py` : 21/21.

## 5. Risques restants

1. **Préchargement Docling** (`workers/worker_main.py:328-338`) : si le
   préchargement échoue silencieusement (log seulement) alors que docling est
   présent, la conversion repartie du thread pipeline pourrait retomber sur le
   blocage DLL Windows. Probabilité faible (échec d'import = docling absent de
   toute façon) ; à surveiller si un jour « advanced tourne mais reste muet ».
2. **Rechargement image par page** : changer de page relit un PNG de
   0,5–2 Mo (~50-100 ms de décodage) ; fluide en usage normal, perceptible en
   défilement rapide d'un gros lot. Pas de cache LRU volontaire (R7 : une page
   à la fois).
3. **Ordre de lecture en colonnes serrées** : les bandes à tolérance
   0,9 × médiane entrelacent deux colonnes dont les lignes sont quasi alignées
   (limite classique) ; non observé sur le jeu de test (fact_001/006,
   mixed_001 : recette CLI PASS), à garder à l'œil sur des PDF éditoriaux.
4. **Attribut transient `work_dir`** : non sérialisé — si un objet `Page` est
   reconstruit (p. ex. rechargé depuis JSON sans repasser par le runner), la
   surcouche image disparaît et l'aperçu retombe au vectoriel (dégradation
   gracieuse, aucun crash) ; vérifié.
5. **U+00AD dans les textes Docling** (C6) : cosmétique, harmonisation
   possible côté testeur.
6. `stderr_adv.log` à la racine : à supprimer/ignorer avant tout packaging
   (`.gitignore` ne le couvre pas).
