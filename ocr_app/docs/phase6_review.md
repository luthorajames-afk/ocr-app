# Revue Phase 6 — presse-papiers riche et exports réels (DOCX / XLSX / PDF)

**Date :** 2026-11-09
**Périmètre :** `core/exports.py` (réécrit), `core/clipboard.py` (nouveau),
`ui/clipboard_qt.py` (nouveau), `ui/export_dialog.py` (nouveau),
`ui/main_window.py`, `ui/widgets/status_bar.py`.
**Hors périmètre (non modifiés) :** `tests/`, `core/data_model|task_queue|
worker|protocol|pipeline|rendering|ocr_engine.py`, `workers/`, `data/`,
`edit_session.py`, `widgets/preview.py`, `widgets/results_panel.py`.
**Méthode :** lecture du code, harnais de revue hors dépôt
(`D:\projects\AOCRI\.phase6_review_harness\`) : génération d'un gros document
synthétique (50 pages / 500 blocs / 2 000 cellules de tableau), relecture
programmatique des exports (python-docx, openpyxl, pypdfium2), vérification
MIME du presse-papiers sur plateforme Windows réelle, parcours Qt headless
(`QT_QPA_PLATFORM=offscreen`), benchmark per-format (médiane de 3 exécutions).
**Deux protocoles de mesure** : « A » avec `tracemalloc` actif (utilisé pour
la comparaison stub ↔ codeur ↔ après correctifs — identique des trois côtés,
mais il gonfle les temps du code intensif en allocations) et « B » propre,
sans tracemalloc, temps et mémoire mesurés séparément (chiffres finaux).
Sonde par sonde pour les cas limites (fusions, colonnes étroites, injection,
dimensions corrompues, accents étendus).
**Timing :** revue lancée pendant que le codeur écrivait ; fichiers relus à
chaque livraison (exports puis clipboard/clipboard_qt/export_dialog, puis
main_window/status_bar) ; corrections appliquées après stabilisation, avec une
re-vérification finale après la dernière retouche du codeur (fix
`paragraph_cls`, retrait d'un import inutilisé) — aucun conflit.

## 1. Conformité aux règles

- **R9 (édition avant copie)** : les `Page` sont partagées entre
  `EditSession`, `TaskRunner`, la copie et l'export (`_current_document()`
  part de `edit_session.document`). Vérifié en headless ET sur le vrai
  presse-papiers Windows : une édition de bloc se retrouve dans le texte, le
  HTML **et** le Markdown copiés (3/3 formats, harnais MIME).
- **R2 (aucun OCR dans l'UI)** : `grep ocr_engine|pipeline|tesserocr|docling|
  rapidocr` sur les six fichiers de la phase → vide (seul un mot de
  docstring). La copie ne fait qu'assembler du texte déjà extrait ; l'export
  écrit un fichier (aucun calcul lourd hors thread GUI, cf. § Qt).
- **R14 (chemins accentués)** : exports DOCX/XLSX/PDF vérifiés vers
  `Éléments à vérifier (2026)/résumé & notes …` — écritures explicites UTF-8,
  `QFileDialog` retourne de l'unicode. Le dialogue d'export conserve le nom
  saisi avec accents (« export éàç »).
- **R15/R13** : python-docx, openpyxl et reportlab restent des dépendances
  optionnelles importées localement (`ImportError` → `RuntimeError`
  explicite), sans impact sur le noyau.

## 2. Constats et corrections

Gravités : **C** = critique, **É** = élevée, **M** = moyenne, **F** = faible.
« avant » = code du codeur au moment de la revue ; « après » = fichiers
corrigés (revue + codeur confondus, attributions indiquées).

| # | Fichier:ligne avant → après | Grav. | Constat | Correction |
|---|---|---|---|---|
| 1 | `core/exports.py` `_pdf_table` (version 18:51) → corrigé par le codeur (18:57, paramètre `paragraph_cls`) | **C** | `Paragraph` utilisé dans `_pdf_table` sans être défini dans sa portée (import local à `save_pdf`) : **tout export PDF contenant un tableau levait `NameError`** — y compris la page démo. Corrigé par le codeur pendant la revue ; vérifié ensuite par les harnais (PDF démo/edge/big complets). | Paramètre `paragraph_cls` transmis par `save_pdf`. |
| 2 | `core/exports.py:81-90` → `104-108` | **É** | `_layout` borne les fusions sur les dimensions **déclarées** (`Table.rows`/`cols`) sans plafond : un JSON corrompu déclarant 10⁸ alloue une matrice 10¹⁶ cellules → blocage/OOM (sonde : > 60 s sans réponse avant kill). | Budget `_MAX_LAYOUT_CELLS = 1_000_000` : au-delà, repli sur l'étendue réelle de la grille. Sonde après : **0,0 ms** pour le même JSON. |
| 3 | `core/exports.py:442, 421` → `439-450, 479` | **É** | openpyxl classe toute chaîne commençant par « = » comme **formule** (`data_type='f'`, vérifié par sonde) : du texte OCR « =1+1 doit rester du texte » ou « =cmd\|/c calc!A0 » était écrit en formule — cellules cassées et tentative d'évaluation (voire DDE) à l'ouverture par Excel. Contenu sécurité (objectif 1) et XLSX (objectif 4). | Fabrique `put()` dans `save_xlsx` : toute cellule classée `f` est forcée en `data_type='s'`. Relecture : `=cmd\|/c calc!A0` et `=1+1…` stockés en chaînes, y compris pour un titre de document « =Sonde… ». |
| 4 | `core/exports.py:618` → `645-726` | **M** | Largeur de colonne PDF = `available_width / cols` : dès ~40 colonnes, un `Paragraph` reçoit une largeur négative → **`ValueError: negative availWidth`** (sonde 60 colonnes : crash). | Seuil `_MIN_PDF_PARAGRAPH_COL = 12 pt` (marges internes d'un `Paragraph`) + stratégie hybride : **texte nu** (beaucoup moins coûteux) quand chaque ligne tient dans la colonne (mesure `stringWidth`), `Paragraph` seulement sinon ; sous 12 pt/colonne, texte nu + police 5 + paddings réduits (grille conservée, contenu serré). Sondes après : 20/60 colonnes, mot de 3 000 caractères, 200 lignes → OK. |
| 5 | `core/exports.py:366-373` → `392-412` | **M (perf)** | `t.cell(r, c)` de python-docx **reconstruit la liste complète des cellules à chaque appel** (parcours XML + vMerge/gridSpan) : 2 000 accès = 1,6 s de profil sur 500 blocs. | Une seule capture `t._cells` (repli public `[cell for row in t.rows …]` si l'API privée disparaît), indexation positionnelle pour fusions et remplissage. Mesuré : DOCX 500 blocs **6,07 s → 1,37-1,64 s**. |
| 6 | `ui/main_window.py:530-532` → `531-538` | **M** | `QCoreApplication.processEvents()` (complet) pendant l'export : les clics sont traités → un second clic sur « Exporter » pendant l'écriture relance un export **en réentrance**. | `processEvents(ExcludeUserInputEvents)` (le curseur d'attente et l'UI se peignent, les entrées sont ignorées) + toast « Export … en cours » : feedback immédiat (objectif 7) sans réentrance. |
| 7 | `core/exports.py:348, 378` → `47, 384, 393` | **F** | `\r` / `\r\n` non normalisés côté DOCX : un retour chariot résiduel d'OCR part tel quel dans le XML (`&#13;`, rendu aléatoire Word). | `_split_lines()` partagé (normalisation CRLF/CR). Vérifié : aucun `\r` résiduel dans les paragraphes re-lus. |
| 8 | `core/exports.py:178` → `206-208` | **F** | Même problème côté XLSX (`\r` écrit tel quel dans la cellule). | `\r\n`/`\r` → `\n` dans `_xlsx_text`. |
| 9 | `ui/export_dialog.py:97` → `97-102` | **F** | `output.with_suffix(fmt.suffix)` **écrase** un suffixe saisi inconnu : « rapport v1.2 » + filtre Word → « rapport v1.docx » (nom amputé). | Suffixe **ajouté** quand le nom en porte déjà un (`rapport v1.2.docx`), substitué seulement si le nom est nu. Un suffixe connu saisi explicitement l'emporte toujours (choix du codeur « l'utilisateur a le dernier mot », conservé). |

### Points vérifiés sans correction (implémentation du codeur saine)

- **MIME presse-papiers (objectif 2)** — 11/11 au harnais sur presse-papiers
  Windows réel : `text/plain` + `text/html` + `text/markdown` posés ensemble
  (`ui/clipboard_qt.build_mime_data`), **aucune ancre ni lien d'édition** dans
  le HTML copié (les ancres `block-N` de l'aperçu ne passent plus : les
  chargeurs passent par `clean_page_html` / `clean_document_html`), vrais
  `<table>` avec `colspan`/`rowspan`, `<br>` pour les retours à la ligne,
  échappement `&`/`<`/`>` vérifié sur contenu piégeux, R9 reflété dans les
  trois formats.
- **Architecture presse-papiers** : `core/clipboard.py` sans Qt (payload
  `{html, text, markdown}` testable seul) + pont Qt minimal ; repli
  « texte brut » conservé pour la plateforme `offscreen` des tests (crash Qt
  connu à la destruction d'un `QMimeData` offscreen).
- **DOCX (objectif 3)** : hiérarchie Title → Heading 1 (« Page N ») →
  Heading 2 (blocs titre), tableaux « Table Grid » réguliers (grilles
  complètes, trous et lignes courtes bordés), fusions python-docx correctes
  (vérifié en relecture : cellules recouvertes pointent l'ancre), unicode
  intégral (œ, €, °, emoji) ; tableau vide → notice, jamais de tableau vide
  cassé ; repli si le style « Table Grid » manque.
- **XLSX (objectif 4)** : titres de feuilles assainis dans tous les cas
  sondés (vide/blanc → `Feuille1`, `[]:*?/\` → `_`, 40 chars → 31, apostrophe
  finale retirée), `merge_cells` cohérents avec la grille (relecture openpyxl
  : `A4:B5`, `C4:D4`), aucune cellule `None` cassée (trous → chaîne vide).
- **PDF (objectif 5)** : pagination reportlab automatique — toutes les
  sections « Page N » du modèle présentes dans le texte extrait (le flux
  continu regroupe les pages courtes, choix sain) ; couche texte
  sélectionnable : **œ Œ € ° É ç préservés**, caractères hors WinAnsi
  (日本語 αβγδ 🔒) remplacés par « ? » sans crash ; grilles complètes
  (`GRID` + en-tête fondé) avec `SPAN` pour les fusions, `repeatRows=1`
  (l'en-tête de tableau se répète en haut de page après coupure — vérifié
  sur un tableau de 200 lignes) ; pieds de page numérotés.
- **HTML / Markdown / TXT** : échappement vérifié sur contenu piégeux
  (`<annuelle>`, `&`, pipes Markdown), pas de `<table>` vide, tableaux
  équilibrés, pas d'ancres d'édition dans l'export fichier.
- **Qt (objectif 7)** — 10/10 au harnais headless : export de bout en bout
  via `_export_document` (dialogue monkeypatché), curseur d'attente
  **restauré** dans `finally` (nominal et après échec), `QMessageBox.warning`
  sur erreur avec message complet, menu Copier à deux entrées, tooltip mis à
  jour ; aucun import OCR (R2).
- **UX (objectif 8)** : « Copier la page affichée » / « Copier tout le
  document » accessibles depuis le bouton Copier ; bouton Exporter ouvre une
  boîte unique format + chemin ; `_copy_document` et `_copy_page` fonctionnels
  headless.

## 3. Mesures avant / après

Machine : Windows 11, Python 3.14.8, python-docx 1.2.0, openpyxl, reportlab.
Document de mesure : 50 pages, 500 blocs (dont 100 tableaux 5×4), 2 000
cellules. Médiane de 3 exécutions ; la machine étant partagée avec le codeur
pendant la revue, on donne aussi les minima (moins sensibles à la charge).

### Protocole A — avec tracemalloc actif (comparaison stub ↔ codeur ↔ après)

Même protocole des trois côtés, chiffres donc comparables entre eux ; ils
surestiment les temps absolus (voir protocole B).

| Mesure (gros doc 500 blocs) | Stub Phase 5 (avant Phase 6) | Codeur, avant correctifs revue | Après correctifs revue |
|---|---|---|---|
| DOCX | 5 307 ms (sans hiérarchie ni fusions) | 6 069 ms | **1 374-1 641 ms** (min 1 357) |
| XLSX | 179 ms | 296 ms | **199 ms** (min 182) |
| PDF | 349 ms (sans tableaux ni couche réelle) | **crash** (`NameError`) puis 5 011 ms | **3 822 ms** (min 3 353) |
| Pic mémoire tracé (export le plus lourd) | 11 Mo | 11,5 Mo | **8,0-11,5 Mo** |
| PDF profil cProfile (500 blocs) | — | 3,60 s (4 906 `Paragraph.wrap`) | **2,40 s (-33 %, 499 wraps)** |
| `_layout` sur JSON corrompu (10⁸ × 10⁸) | n/a (phase 5 sans fusions) | **blocage > 60 s** | 0,0 ms (bornage) |
| Injection XLSX « =cmd\|/c calc!A0 » | n/a | écrit en **formule** | chaîne (`data_type='s'`) |

### Protocole B — propre, sans tracemalloc (état final)

Temps et mémoire mesurés séparément ; médiane de 3 exécutions.

| Export (gros doc 500 blocs) | Temps médian | Pic mémoire | Taille |
|---|---|---|---|
| TXT | 2,5 ms | 0,5 Mo | 83 KiB |
| Markdown | 5,0 ms | 0,6 Mo | 106 KiB |
| HTML | 9,6 ms | 0,7 Mo | 135 KiB |
| **DOCX** | **1 145 ms** (min 1 053) | 2,4 Mo | 57 KiB |
| **XLSX** | **115 ms** (min 110) | 1,6 Mo | 37 KiB |
| **PDF** | **1 255 ms** (min 1 141) | 3,5 Mo | 90 KiB |

Chaque format est très largement sous le budget de 5 s (l'export utilisateur
porte sur un format à la fois).

### Harnais

| | Baseline stub | Codeur, avant correctifs | Après correctifs |
|---|---|---|---|
| Vérifications exports | 50/56 (écarts attendus du stub) | 45/53 (crash PDF) | **62/62** |
| Presse-papiers (MIME, Windows réel) | 7/9 (markdown absent, ancres) | — | **11/11** |
| Qt headless (export, curseur, erreurs) | — | — | **10/10** |
| pytest | 73 vertes | 73 vertes | **73 vertes** |

## 4. Vérifications exécutées

- `python -m pytest tests/ -q` → **73 passed** (aucune modification de
  `tests/`, signatures publiques inchangées).
- **Relecture programmatique des exports** (harnais 62/62) sur page démo,
  page « cas limites » (tableau vide, tableau à trous, cellules fusionnées,
  tableau tout-`None`, tableau sans grille, blocs vides, hors-latin-1) **et**
  gros document 50 pages / 500 blocs : DOCX re-lu (python-docx : dimensions,
  régularité, heading levels, cellules), XLSX re-lu (openpyxl : titres de
  feuilles, types, merges), PDF (header `%PDF`, `%%EOF`, pagination, texte
  extrait via pypdfium2, bounding boxes **toutes dans la page** — plus
  aucun débordement, contre 4/9 hors page avec le stub).
- **Presse-papiers** : `QApplication.clipboard().mimeData()` sur plateforme
  Windows réelle (avec sauvegarde/restauration du contenu utilisateur) —
  formats, absence d'ancres, structure de tableau, échappement, R9.
- **Qt headless** : export nominal + échec simulé (disque plein), curseur,
  QMessageBox, copies page/document, menu.
- Sondes unitaires : fusions DOCX/XLSX/HTML, titres de feuilles (8 cas),
  accents étendus PDF (œ Œ € °), mot de 3 000 caractères, 60 colonnes,
  200 lignes, CRLF, injection « = », `_layout` corrompu.
- R14 : exports vers chemin accentué « Éléments à vérifier (2026) ».

## 5. Risques restants

1. **PDF : 1,1-1,8 s sur 500 blocs** (protocole propre) : format le plus
   lourd des six, très sous le budget ; le coût restant est inhérent à
   reportlab (mesure de chaque cellule pour les hauteurs de lignes). Leviers
   suivants si besoin : cache de `stringWidth`, ou génération canvas directe
   sans platypus (au prix de la pagination automatique).
2. **Tableaux PDF très étroits** (< 12 pt par colonne, soit ≥ ~40 colonnes) :
   grille complète mais contenu en texte nu serré (police 5 pt) — lisible
   seulement en zoom ; cas marginal pour de l'OCR réel.
3. **`t._cells` est une API privée python-docx 1.2.0** : le repli public
   `row.cells` est codé mais ~2× plus lent ; à surveiller lors d'une montée
   de version majeure.
4. **XLSX : tout est écrit en texte** (les nombres OCR ne sont pas typés) —
   Excel affiche l'avertissement « nombre au format texte ». Choix assumé
   (fidélité au texte OCR, pas de réinterprétation silencieuse) ; à réévaluer
   si le client veut des colonnes numériques triables.
5. **openpyxl / python-docx construisent le fichier en mémoire** avant
   écriture (1,6-3,5 Mo pour 500 blocs) : une centrifugeuse 10× plus grosse
   resterait sous ~50 Mo, mais un document extrême (10 000 pages) exigerait
   un flux par morceaux (hors périmètre).
6. **`text/markdown`** : peu d'applications lisent ce format MIME aujourd'hui
   (Word l'ignore) ; le format est inoffensif pour elles et prêt pour celles
   qui le supportent.
7. **Suffixe connu saisi l'emporte sur le filtre** (comportement codé et
   documenté) : taper « notes.txt » avec le filtre Word exporte en TXT — à
   garder à l'esprit en support.

## 6. Fichiers modifiés par la revue

- `src/ocr_app/core/exports.py` — budget `_MAX_LAYOUT_CELLS` (bornage des
  dimensions déclarées), fabrique XLSX anti-formule, normalisation CRLF
  (DOCX + XLSX), capture unique des cellules python-docx, seuil + stratégie
  hybride « texte nu / Paragraph » des tableaux PDF étroits.
- `src/ocr_app/ui/main_window.py` — `processEvents(ExcludeUserInputEvents)`
  + toast « Export en cours » (anti-réentrance, feedback immédiat).
- `src/ocr_app/ui/export_dialog.py` — suffixe ajouté et plus substitué.
- (Codeur, relus sans retouche après stabilisation : `core/clipboard.py`,
  `ui/clipboard_qt.py`, `ui/widgets/status_bar.py` ; fix `paragraph_cls`
  dans `exports.py` appliqué par le codeur pendant la revue.)
