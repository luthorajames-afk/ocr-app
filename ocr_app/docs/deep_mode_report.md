# Mode Approfondi — rapport d'implémentation

Mode « Approfondi » (4ᵉ mode) : documents difficiles (taches d'encre,
défauts papier) — backend moteur Avancé (Docling) + prétraitement interactif
+ zones d'exclusion dessinées par l'utilisateur. Base : commit `3db0b26`
(246 tests verts, socle aperçu/Avancé livré).

## Fichiers créés

| Fichier | Rôle |
| --- | --- |
| `src/ocr_app/core/preprocessing.py` | Filtres purs numpy + Pillow (sans Qt, sans cv2) : `apply_filters`, `apply_preset`, `normalize_params`, `preset_params`, `params_differ_from_defaults`, `list_presets`, `PRESETS = {ink, yellowed, low_contrast, phone_photo}` (libellés FR). |
| `src/ocr_app/ui/exclusion_store.py` | Persistance `%LOCALAPPDATA%\AOCRI\deep\<sha1(chemin)[:12]>\page_NNN.json` = `{"zones": [...], "filters": {...}}` ; env `AOCRI_DEEP_DIR` ; écriture atomique (temp + `os.replace`) ; lecture corrompue → défauts ; création paresseuse. |
| `src/ocr_app/ui/widgets/deep_panel.py` | Panneau repliable : presets (`SubModeButton`), 4 curseurs (Luminosité 50–200, Contraste 50–200, Suppr. ombres 0–100, Débruitage 0/3/5), binarisation adaptative, bascule Original/Filtré, bouton primaire « Appliquer et relancer l'OCR de la page ». Signaux `filters_changed(dict)` (debounce 250 ms), `view_mode_changed(bool)`, `apply_requested()`. |

## Fichiers modifiés

- `core/pipeline.py` — `_MODES += "approfondi"` (worker = `AdvancedWorker`,
  même backend) ; `PipelineConfig.excluded_zones` (rectangles points PDF,
  normalisés) + `PipelineConfig.filter_params` (normalisés via
  `preprocessing.normalize_params`, import paresseux de numpy) ; l'image
  envoyée au moteur est préparée (`_prepare_image` : filtres puis **masque
  blanc papier** des zones — jamais noir) à dimensions identiques ; blocs
  dont ≥ 50 % de la surface intersecte une zone retirés
  (`_prune_excluded_blocks`, natifs et Docling) ; sans filtre ni zone, le
  document ORIGINAL est remis au moteur (chemin Avancé historique).
- `core/advanced_engine.py` — `convert_document` accepte une **image PIL
  préparée** : PNG temporaire écrit avec le DPI porté par
  `image.info["dpi"]` (posé par le pipeline sur le rendu 300 DPI) afin que
  les bboxes Docling restent en points PDF cohérents avec le document
  d'origine (vérifié : bboxes ≤ taille page en points) ; temporaire
  supprimé en `finally`.
- `workers/advanced.py` — cache R5 par chemin converti (le document
  original n'est converti qu'une fois) ; une image PIL préparée est
  convertie à la volée (une page rasterisée par appel) ; repli
  document-à-une-page pour que la relance d'une page recueille ses blocs.
- `workers/worker_main.py` — `MODES += ("approfondi",)` ; `--preset`,
  `--filter-json` (prioritaire sur le preset), `--exclude
  "x0,y0,x1,y1;…"` (points PDF) ; `PipelineConfig` alimenté ; préimport
  Docling étendu à l'approfondi (thread principal, contrainte DLL stdin
  inchangée) ; erreurs d'usage → code 2 avec message clair.
- `ui/worker_commands.py` — `DEEP_MODE`, `DEEP_UNAVAILABLE_MESSAGE`
  (« Le mode Approfondi requiert le backend Avancé (module Docling). »),
  `resolve_deep_backend()` (même chaîne de résolution que `advanced` :
  manifeste → venv `.venv-eval-advanced` → dev, avec `--mode approfondi`).
  `resolve_worker_commands` garde son contrat historique (simple/medium/
  advanced uniquement) — l'entrée `approfondi` est ajoutée par la fenêtre.
- `ui/task_format.py` — `MODE_LABELS["approfondi"] = "Approfondi"`.
- `ui/widgets/toolbar.py` — `MODES += ("approfondi", "Approfondi")`
  (tooltip « Analyse approfondie — filtres et zones d'exclusion ») ;
  `set_mode_enabled(mode, enabled, tooltip)` pour le grisage.
- `ui/widgets/preview.py` — `PreviewTool.{SELECT, ZONE, ERASE}`,
  `set_tool()` + overrides `mousePress/Move/Release` sur `PreviewView`
  (Ctrl+molette et sélection de blocs préservés en SELECT) ; signaux
  `zone_drawn(QRectF)` (scène), `zone_deleted(int)`, `zone_changed(int)` ;
  `ExclusionZoneItem` (fill `RED_SOFT` alpha 60, bord tirets `RED`, tag
  « excl », poignée de redimension bas-droite) ; API
  `set_exclusion_zones(list[BBox])` / `get_exclusion_zones()` (points PDF
  via l'échelle de page active, `PAGE_SCALE` par défaut) ;
  `set_page_image(QPixmap)` (bascule Original/Filtré) ; boutons d'outils
  dans l'en-tête (`set_deep_tools_visible`).
- `ui/task_runner.py` — `set_task_deep_params(task_id, zones,
  filter_params)` ; au démarrage, uniquement vers un worker réel
  (`accepts_input_document`, jamais le fake_worker) : `extra_args +=
  ("--exclude", "…")` et `("--filter-json", json.dumps(...))` à la suite
  de `--input/--page/--lang/--dpi`.
- `ui/main_window.py` — commande `approfondi` dérivée du backend Avancé
  (ou message d'indisponibilité) ; Approfondi grisé sans backend (tooltip
  du message) ; panneau inséré entre l'en-tête aperçu et la vue, visible
  uniquement en approfondi ; filtrage d'aperçu en **QThreadPool**
  (`_FilterPreviewRunnable`, QImage hors thread GUI, QPixmap côté GUI,
  jeton anti-résultat périmé) ; zones tracées/supprimées/redimensionnées
  → persistance immédiate (store) ; `Appliquer…` → `create_page_task(
  source, "approfondi", N)` + `set_task_deep_params` + file ; état par
  page (zones + filtres) rechargé du store à chaque affichage.
- `ui/theme.py` — QSS `DeepPanel` (fond window, bord), `DeepTitle`,
  `DeepSection`, `DeepValue`, `DeepHint` (AMBER_SOFT/AMBER) et état
  `ModeButton:disabled`. Aucune couleur hors palette existante
  (zones = RED/RED_SOFT, blocs = ACCENT, hint = AMBER, actifs = ACCENT).

## Vérifications

1. **Suite complète** : `python -m pytest tests/ -q` → **246 passed**
   (ré-exécutée après tous les changements ; `tests/` non modifié).
2. **`apply_preset`** sur image A4 bruitée PIL 2480×3508 (300 DPI) :
   dims identiques, sortie RGB, image modifiée — best-of-3 :
   `ink` 409 ms, `yellowed` 293 ms, `low_contrast` 264 ms,
   `phone_photo` 470 ms (< 500 ms).
3. **Worker réel** (`.venv-eval-advanced`) :
   `--mode approfondi --input data/test_suite/scans/scan_001.pdf --page 1
   --preset ink --exclude "100,100,400,200" --work-dir "%TEMP%/aocri deep
   éàç"` → préimport Docling OK, `page_done` (page 1, chemin réel écrit),
   **19 blocs, aucun bloc intersectant la zone** (ratio ≥ 50 %), bboxes en
   points PDF (échelle DPI propagée, x ≤ 553 / y ≤ 426 pt), JSON + PNG
   écrits dans le dossier accentué. Chemin « sans filtre/zone » vérifié
   aussi (document original → 25 blocs). Erreurs d'usage (`--filter-json`
   illisible, `--preset bogus`, `--exclude "1,2,3"`) → code 2, messages
   clairs.
4. **UI offscreen** (`QT_QPA_PLATFORM=offscreen`) : zone tracée à la
   souris (press/move/release synthétiques) → store (`zones` + `filters`
   du preset ink persistés) ; rechargement dans une seconde fenêtre
   (zones + curseurs restaurés) ; bascule Original/Filtré → l'image de
   fond change (QThreadPool) puis revient à l'original ;
   `extra_args` d'une relance inspectés : `--input`, `--page 1`,
   `--exclude "x0,y0,x1,y1"`, `--filter-json <objet>` avec
   `--mode approfondi` ; mode Approfondi **grisé** sans backend Avancé
   (tooltip « …requiert le backend Avancé (module Docling) »), Simple
   reste actif.
5. **Suite complète finale** : 246 passed (cf. 1).

## Écarts / limites documentés

- **Performance des filtres** : le filtre médian Pillow natif est ~10× trop
  lent à 300 DPI (2,7 s pour 3×3 RVB). La médiane est donc calculée par un
  réseau de tri min/max vectorisé numpy sur la **luminance** :
  débruitage 3 = médiane 3×3 exacte ; débruitage 5 = médiane 3×3 +
  lissage boîte 3×3 (approximation d'une 5×5, le réseau 25 éléments
  dépasserait le budget). La chaîne de nettoyage (débruitage et/ou
  suppression d'ombres) produit une image en **niveaux de gris** (reconvertis
  RGB en sortie) : le mode prépare la page pour l'OCR, la teinte n'y
  contribue pas, et une réécriture couleur du delta coûterait ~600 ms de
  plus. Suppression d'ombres : facteur de correction calculé sur 1/8 de
  résolution puis rééchantillonné. Luminosité+contraste fusionnés en une
  LUT (équation exacte des `ImageEnhance`). Binarisation : Sauvola
  simplifié avec écart-type estimé par MAD (lissages boîte séparables),
  ~0,5 s.
- **Preset `phone_photo`** : débruitage 3 (et non 5) pour tenir le budget
  < 500 ms sur toutes les machines de mesure observées.
- **Zones : redimension** disponible en mode Sélection (les modes Zone et
  Gomme interceptent le clic gauche pour tracer/effacer) ; la
  persistance du redimensionnement passe par `zone_changed(int)`.
- **Relance multi-pages** : avec filtres/zones, chaque page est
  rasterisée puis convertie séparément par Docling (une conversion par
  page au lieu d'une par document) — cohérent avec des réglages par page ;
  sans filtre ni zone, le chemin historique (une conversion par document)
  est conservé.
- **CLI (`cli.py`)** : `--mode` n'expose pas `approfondi` (hors périmètre
  de la mission, qui couvre la résolution worker + UI) ; `PipelineConfig`
  accepte le mode programmatiquement.
- **Menu contextuel « Relancer cette page en… »** inchangé (simple/medium/
  advanced) : la relance Approfondi passe par le bouton dédié du panneau.
- **Aucun nouveau test dans `tests/`** (testeur après review) — les
  vérifications ci-dessus ont été exécutées hors dépôt
  (`%TEMP%/verify_deep/`).
