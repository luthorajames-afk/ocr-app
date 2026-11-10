# Revue — Mode Approfondi (prétraitement, zones d'exclusion, panneau, worker)

Revue et durcissement du travail du codeur (rapport codeur : `docs/deep_mode_report.md`,
base `3db0b26`, 246 tests verts). Méthode : lecture des fichiers et diffs,
mesures perf numpy (2480×3508), exécutions réelles du worker Approfondi via le
venv `.venv-eval-advanced` (`--preset`, `--filter-json`, `--exclude`, relance
`--page`, annulation), batterie UI offscreen (tracé de zones → store →
rechargement, mapping géométrique, outils souris, panneau, bouton Appliquer),
grep des imports croisés.

## 1. Constats

| # | Gravité | Fichier:ligne | Constat | Sort |
|---|---|---|---|---|
| C1 | **Majeur** | `ui/widgets/preview.py` (`ExclusionZoneItem.mouseMoveEvent`, `_rebuild_zone_items`) | **Les zones n'étaient pas clippées à la page** : la poignée de redimension laissait tirer une zone bien au-delà du papier (vérifié offscreen : zone de 1383×1660 pt sur une page 595×842 pt) et une zone chargée du store hors page se dessinait hors papier — la mission exige « zones clippées à la page ». Le tracé (`ZONE`), lui, était déjà clippé (`_page_scene_rect().intersected`). | **Corrigé** (reviewer) : bornes de page passées à l'item (poignée clampée), intersection au rechargement, zones entièrement hors page ignorées à l'affichage. Re-vérifié offscreen : zone clampée exactement à (595, 842) pt. |
| C2 | Moyen | `core/preprocessing.py` (`apply_filters`) | **Crash sur image 0×0** : `np.pad` lève `ValueError: can't extend empty axis` dès qu'un filtre de nettoyage est actif (preset ink). Jamais atteint par le pipeline actuel, mais contredit « params validés / JSON hostile → défauts ». | **Corrigé** (reviewer) : garde early-return (`_to_rgb`), mêmes dimensions/mode. |
| C3 | Moyen | `core/pipeline.py` (`_normalize_zones`), `workers/worker_main.py` (`parse_exclude_zones`), `ui/exclusion_store.py` | **Zones NaN/inf non rejetées** : `float("nan")` se parse comme un float valide, et toutes les comparaisons d'ordre (`x0 > x1`, `x1 <= x0`) sont fausses avec NaN → la zone passait toutes les gardes, jusqu'à `ImageDraw.rectangle` (plantage du worker en erreur protocole). `json` de Python écrit/lit en plus `NaN`/`Infinity` non standard JSON. | **Corrigé** (reviewer) : filtre `math.isfinite` aux trois niveaux — `--exclude` → exit 2 propre (vérifié : 0 ligne sur stdout), `PipelineConfig` → `ValueError` claire, store → zone ignorée à l'écriture et à la lecture (fichier hostile avec littéraux `NaN`/`Infinity` testé). |
| C4 | Mineur | `core/preprocessing.py` (`normalize_params`) | `binarize` trop permissif : `bool("0")`, `bool("false")` → `True` (JSON hostile inversé). | **Corrigé** (reviewer) : booléen strict (bool tel quel, 0/1 numériques acceptés, sinon défaut `False`). |
| C5 | Mineur | `ui/widgets/preview.py` (`ExclusionZoneItem.paint`, tracé) | Fond rouge codé en dur `QColor(179, 38, 30, 60)` au lieu de dériver de `theme.RED` : valeur juste aujourd'hui (= #b3261e) mais fragile — les couleurs de la maquette sont non négociables. | **Corrigé** (reviewer) : `QColor(theme.RED)` + `setAlpha(60/40)` aux deux endroits. |
| C6 | Majeur (perf) → résolu | `core/preprocessing.py` | Version vue en début de revue (intégrales float64 + réécriture couleur du delta) : **preset ink 810–843 ms** sur 2480×3508, binarisation 1498–1654 ms — cible mission < 500 ms non tenue. Le codeur a réécrit le module pendant la revue (sortie **niveaux de gris** pour la chaîne de nettoyage, facteur d'ombre calculé en 1/8 résolution, binarisation par lissage boîte + MAD au lieu des intégrales) : **preset ink 400–465 ms** (machine calme). Le reviewer a ajouté le **réseau médian par blocs de lignes** (bit-identique, vérifié sur 6 jeux dont PIL `MedianFilter(3)`) : 227 → 145 ms sur le filtre médian, restant le poste dominant. | Corrigé (codeur + reviewer) |
| C7 | Info (R5) | `workers/advanced.py` (`process_page`, `_convert_document`) | Approfondi avec filtres/zones = **une conversion Docling par page** (nouveau `DoclingEngine` + `initialize` par image préparée, ~6 s de chargement de convertisseur par page). En contradiction avec l'esprit de R5 si un document ENTIERS était traité avec filtres ; en pratique l'interface n'attache les paramètres approfondi qu'aux **relances mono-page** (`create_page_task` + `set_task_deep_params`), le flux conçu (« Appliquer et relancer l'OCR de la page ») n'encaisse donc qu'un seul init. Sans filtre ni zone, le document original est remis au moteur (une conversion, R5 respecté). | Documenté — surveiller si un jour les réglages approfondi s'appliquent à un document complet |
| C8 | Info | `workers/worker_main.py` (`main`) | `--filter-json`/`--exclude`/`--preset` invalides → **exit 2 sans message protocole sur stdout** (stderr seulement, 0 ligne hors protocole vérifié). L'UI afficherait « traitement incomplet » plutôt que la cause — acceptable : l'interface ne produit jamais d'arguments invalides (store + `normalize_params` en amont), et les messages d'usage restent lisibles en stderr/CLI. | Signalé |
| C9 | Info | `workers/worker_main.py` (`_prewarm_engine`) + `ui/worker_client.py` | Pendant le préchargement Docling (import + construction du convertisseur, ~0,5→6,6 s), le lecteur stdin n'est pas encore démarré (contournement documenté du verrou DLL Windows) : une annulation envoyée dans cette fenêtre n'est lue qu'à la fin du préchargement (**7,15 s** mesurés). La montre côté interface (`WorkerClient.request_cancel` : grace 1 s → terminate 400 ms → kill) garantit l'annulation **< 2 s réel** (tâche marquée annulée via `_cancelling`). Pendant la conversion elle-même : **1,21 s** mesuré (checkpoint `POLL_INTERVAL` = 20 ms). | Conforme — comportement inchangé vs mode Avancé |
| C10 | Info | `ui/exclusion_store.py` | Persisté par `sha1(chemin)[:12]` : stable accents/espaces (vérifié), déplacement du document = état perdu (par conception, documenté). Écriture atomique mkstemp+`os.replace` dans le dossier cible, `.tmp` nettoyé sur `OSError`, création paresseuse (aucun dossier avant la première sauvegarde). | Conforme |
| C11 | Info | `core/pipeline.py` (`_zone_overlap_ratio`) | Règle 50 % : ratio calculé par **UNE zone** (max), pas l'union des zones qui se chevauchent — un bloc à cheval sur deux zones à 40 %+40 % n'est pas retiré. Documenté dans le docstring et l'en-tête du module ; cohérent avec la mission (« règle 50 % documentée »). | Documenté |
| C12 | Test (info) | `tests/` | Aucun nouveau test pour le mode Approfondi (246 tests = suite historique, verte avant et après revue). Les gardes ajoutées (NaN, 0×0, clamp, binarize strict) seraient de bons candidats de tests côté testeur. | Signalé |

## 2. Vérifications dynamiques (toutes vertes)

**Worker Approfondi via venv (`.venv-eval-advanced`, Python 3.12.10, Docling 2.149) :**

- `--mode approfondi --input fact_001.pdf --page 1 --preset ink --exclude "42.2,21,198,42.8"` :
  exit 0, `page_done` propre ; le bloc 0 du run Avancé (« SARL Dupont & Fils »,
  bbox couverte par la zone) est **retiré** (24 → 22 blocs) ;
- `--mode approfondi` **sans** preset/zone : 24 blocs = parité exacte avec le
  mode Avancé (document original remis au moteur, chemin historique) ;
- `--exclude "nan,0,100,100"` → exit 2, **stdout vide** (JSON pur préservé),
  message d'usage sur stderr ; idem `--filter-json "{bad"`, preset inconnu,
  `--exclude "abc,1,2,3"` ;
- `--filter-json '{"contrast": 999, "brightness": "x"}'` → clamp normal
  (999→200, "x"→100), exit 0 ;
- annulation t=4 s (préchargement) : 7,15 s worker seul — couverte par la
  montre UI < 2 s ; annulation t=9 s (conversion) : **1,21 s** < 2 s ✓.

**Mapping géométrique UI ↔ PDF (offscreen) :**

- tracé d'une zone à un rect scène connu → `get_exclusion_zones()` =
  exactement rect ÷ `PAGE_SCALE` (points PDF, origine haut-gauche) ;
- rechargement `set_exclusion_zones` (points du store) → même géométrie
  affichée (écart < 0,3 pt) ; store → UI → store aller-retour fidèle ;
- **aucun** usage de `get_pos()` pypdfium2 dans l'UI (grep) — le piège
  d'inversion y (origine bas-gauche) n'est pas présent : les zones vivent en
  coordonnées haut-gauche comme les bboxes, conversions ×/÷ `point_scale`
  (dérivé du DPI de `page_NNN.png`, même échelle que les surcouches de blocs) ;
- outils : tracé clippé à la page ✓, gomme supprime la zone cliquée et
  réindexe ✓, la poignée redimensionne et émet `zone_changed` ✓, le corps
  d'une zone ignore les clics (la sélection de blocs en dessous fonctionne) ✓,
  Ctrl+molette zoom inchangé ✓ ;
- `MainWindow` : bouton Approfondi grisé avec tooltip si le backend Avancé est
  absent, panneau + outils visibles uniquement en approfondi (clic réel),
  « Appliquer » crée une tâche relance `--page N` avec `--exclude`/`--filter-json`
  attachés et persiste le store (vérifié de bout en bout hors worker).

**Isolation backend (R1/R2/R13) :**

- `docling` importé paresseusement à un seul endroit : `core/advanced_engine.py:136`
  (`import_document_converter`) ; non importable dans l'env principal (vérifié) ;
- `tesserocr` importé paresseusement à un seul endroit : `core/ocr_engine.py:202` ;
  le mode Approfondi ne le touche pas (`--lang` injecté uniquement en mode simple) ;
  inversement le mode Simple ne touche pas docling (imports locaux du pipeline) ;
- `core/preprocessing.py` : numpy + Pillow uniquement, zéro Qt, zéro cv2 →
  utilisable tel quel dans le worker ET l'aperçu ;
- `ui/exclusion_store.py` : stdlib uniquement (testable headless) ;
- venv worker : `import ocr_app` isolé (`-I`) opérationnel (`pip install -e .
  --no-deps` en place) + `PYTHONPATH=src` injecté par `WorkerClient` (double
  filet) — la commande `resolve_deep_backend()` retargete `--mode approfondi`
  sur la chaîne Avancé (manifeste → venv → dev), vérifiée.

**UI/Qt :**

- aperçu filtré exécuté dans `QThreadPool` (`QRunnable`, `autoDelete`) —
  jamais numpy lourd dans le thread GUI ; `QImage` produite hors thread,
  `QPixmap.fromImage` au récepteur (thread GUI) ; token anti-péremption ;
- debounce réel 250 ms (`QTimer` single-shot redémarré à chaque toucher) ;
  `blockSignals` pour toute mise à jour programmatique (pas de boucles de
  signaux) ; signaux `zone_drawn`/`zone_deleted`/`zone_changed` connectés une
  seule fois ; aucun `QThread` (donc aucune fuite) ; menus `deleteLater`
  conservés.

## 3. Mesures perf (2480×3508, numpy 2.5.4 / Pillow 13.0 / Python 3.14.8)

Machine de développement **partagée avec l'agent codeur pendant la revue** →
forte variance (même code mesuré 417 ms puis 1618 ms selon la charge) ; les
comparaisons ci-dessous sont entrelacées (A/B dans le même processus, médiane).

| Mesure | Avant (v1 codeur : intégrales f64 + delta couleur) | Après (codeur : gris + BoxBlur MAD + facteur 1/8) | Après + médiane par blocs (reviewer) |
|---|---|---|---|
| preset ink (d3+ombres 65+LUT+AC) | 810–843 ms | 400–465 ms calme / 635–734 ms chargée | 454–583 ms |
| `denoise` 5 seul | 683–707 ms | 552 ms | ~420 ms (médiane 1,6× plus rapide) |
| `shadow_removal` 100 seul | 542–549 ms | 324–369 ms | idem (non modifié) |
| `binarize` w25 | 1498–1654 ms | 653–677 ms | idem (non modifié) |
| tout maxé (preset + binarize) | 2357–2627 ms | 1554–1600 ms | ~1,4 s |

- Cible mission (< 500 ms / page A4 300 DPI) : **tenue pour les presets** sur
  machine calme ; sous forte charge parallèle elle peut être dépassée
  transitoirement (variance machine, pas de changement d'algorithme).
- Médiane par blocs : **bit-identique** au réseau pleine page (vérifié sur
  scan réel, bruit pur, tailles à cheval sur les blocs, images 1×N/N×1/7×5,
  ET sur l'équivalence `ImageFilter.MedianFilter(3)` revendiquée par le
  docstring) — pure optimisation cache (blocs 512 lignes ≈ 15 Mo de tampons),
  227 → 145 ms sur le filtre médian seul.
- Prototypes de l'examinateur non retenus : médiane **séparable** (1,8× plus
  rapide mais 15 % des pixels de texte s'écartent de la médiane exacte —
  risque OCR écarté) et intégrales bloquées transposées (supplantées par la
  réécriture BoxBlur du codeur). La variante in-place du mélange d'ombres
  n'était pas plus rapide que l'original → non appliquée.

## 4. Cohérence maquette (couleurs non négociables)

- Zones : bord **tirets RED** 2 px + fond rouge doux (RED, alpha 60 — dorénavant
  dérivé de `theme.RED`) + tag plein **RED** texte blanc « excl » + poignée —
  conforme à `.oa-zone`/`.oa-ztag` (rouge = exclu) ; 
- tags « excl » même style que les tags blocs (`_tag_rect`/paint identiques au
  `BlockOverlayItem`) ✓ ;
- presets actifs : `SubModeButton:checked` = `ACCENT_SOFT`/`ACCENT` (#eef2fb /
  #2f5bd7) ✓ ; bascule Original/Filtré sur le même vocabulaire ✓ ;
- avertissement zones : `DeepHint` = `AMBER_SOFT`/`AMBER` (#f7f0e0 / #946200) ✓ ;
- 4 curseurs (Luminosité / Contraste / Suppr. ombres / Débruitage) + switch
  « Binarisation adaptative » + bouton primaire « Appliquer et relancer l'OCR
  de la page » ✓ ; outils Sélection / Zone / Supprimer (libellés compactés
  vs maquette « Zone d'exclusion », tooltip complet) ; `theme.py` n'ajoute que
  des réutilisations de la palette existante (aucune couleur hors thème) ✓.

## 5. Corrections appliquées par le reviewer (mêmes fichiers que le codeur)

1. `ui/widgets/preview.py` — clamp des zones à la page (poignée + rechargement
   + dérivations `theme.RED` par alpha).
2. `core/preprocessing.py` — garde image 0×0 ; `binarize` booléen strict ;
   réseau médian par blocs (bit-identique, + `_MEDIAN_TILE_ROWS`).
3. `ui/exclusion_store.py` — zones non finies ignorées à l'écriture et à la
   lecture (`math.isfinite`).
4. `core/pipeline.py` — `_normalize_zones` : `ValueError` sur coordonnées non
   finies (message « coordonnées finies attendues »).
5. `workers/worker_main.py` — `parse_exclude_zones` : même garde (exit 2
   propre avant tout traitement).

`tests/`, `core/data_model.py`, `core/ocr_engine.py`, `core/worker.py`,
`core/protocol.py`, `core/task_queue.py`, `core/exports.py`,
`ui/edit_session.py`, `ui/clipboard*`, `ui/widgets/results_panel.py`,
`ui/app_settings.py`, `installers/`, `data/` : non touchés.

## 6. Tests et risques restants

- `python -m pytest tests/ -q` → **246 passed** après corrections (identique
  avant corrections : le code du codeur était déjà vert à la réception).
- Risques restants :
  - **C7** — approfondi multi-pages avec filtres (hors flux UI) rechargerait
    Docling par page ; à garder en tête si les réglages s'étendent au document
    entier (suggestion : cacher le `DoclingEngine` entre les pages d'un même
    document, convertir par page) ;
  - **perf sous charge** — la cible 500 ms est tenue sur machine calme ;
    binarisation + preset ≈ 1,1–1,6 s (opt-in, interrupteur mockup) ;
  - **binarisation MAD** — approximation de Sauvola (MAD×1,25 vs écart-type)
    documentée ; la qualité OCR réelle se juge sur `data/test_suite` (R10),
    pas sur cette revue ;
  - **C8** — arguments invalides en CLI ne remontent pas au protocole
    (exit 2, stderr seul) ; l'interface est protégée en amont.
