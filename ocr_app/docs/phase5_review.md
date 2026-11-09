# Revue Phase 5 — édition bloc/texte (R9), undo/redo et lien bloc ↔ texte

**Date :** 2026-11-09
**Périmètre :** `ui/edit_session.py` (nouveau), `ui/widgets/preview.py`,
`ui/widgets/results_panel.py`, `ui/main_window.py` (plus marginalement
`ui/widgets/status_bar.py` et `ui/icons.py`, écrits par le codeur, relus sans
modification).
**Hors périmètre (non modifiés) :** `core/`, `workers/`, `tests/`, `scripts/`,
`data/`, `docs/` (hors ce rapport).
**Méthode :** lecture du code, harnais headless (`QT_QPA_PLATFORM=offscreen`)
à 31 vérifications exécuté avant puis après corrections, mesures de
performances sur fenêtre affichée (Python 3.14.8, Windows 11, PySide6 6.12.0),
suite pytest complète, vérification des processus avec `tasklist`.
**Timing :** revue lancée pendant que le codeur écrivait ; les fichiers ont
été relus à chaque apparition (edit_session, puis preview/results_panel,
puis main_window) et les corrections appliquées après stabilisation du diff.

## 1. Conformité aux règles

- **R9 (édition avant copie, lien bloc ↔ texte conservé)** : les objets
  `Page` sont partagés entre `EditSession`, `TaskRunner` et l'interface — une
  édition est immédiatement visible par la copie et l'export (vérifié en
  headless : le presse-papiers contient le texte édité, B2). Le lien repose
  sur `block_id`, avec repli positionnel `index-N` documenté pour les blocs
  sans identifiant (les blocs ne sont jamais réordonnés). La copie utilise
  `_selected_page()` = la page **affichée** (pas « la dernière reçue »), ce
  qui colle à l'intention R9.
- **R2 (aucun OCR dans l'UI)** : aucun import de moteur dans les fichiers
  d'édition (`grep ocr_engine|pipeline|tesserocr|doctr|docling` → vide). La
  session ne fait que manipuler du texte déjà extrait.
- **Undo/redo exacts** : la session ne mute que `block.text` ; bbox et
  confidence sont intouchés (vérifié par comparaison `Page.to_dict()` complète
  avant/après cycle édition/undo/redo/undo — identique bit à bit, A1a/B1b).
  `reset()` restaure des blocs entiers depuis l'instantané d'origine.
- **Debounce/régénération** : l'éditeur inline ne touche le modèle qu'au
  commit (Ctrl+Entrée, focus-out) — **aucune frappe ne régénère les vues**.
  `documentChanged` déclenche un rafraîchissement complet par commit/undo/
  redo (une action discrète, pas un caractère).

## 2. Constats et corrections

Gravités : **C** = critique, **É** = élevée, **M** = moyenne, **F** = faible.
Les lignes « avant » renvoient au code du codeur au moment de la revue, les
lignes « après » aux fichiers corrigés.

| # | Fichier:ligne avant → après | Grav. | Constat | Correction |
|---|---|---|---|---|
| 1 | `ui/edit_session.py:111-129` → `116-150, 330-345` | **É** | `load_document` re-prend l'instantané « origine » **à chaque réaffichage** : `_show_task` est rappelé à chaque clic dans la liste. Après une édition puis un simple re-clic sur le fichier, `is_dirty()` repasse à faux et `reset()` restaure… le texte déjà édité : l'édition devient indétectable et irrécupérable. Idem après un aller-retour vers une autre tâche. | Cache d'origines **par tâche** (`ORIGIN_CACHE_LIMIT = 12`, ids de tâche uniques), chemin rapide « même tâche réaffichée » qui conserve instantané **et** historique, et `_recompute_dirty()` par comparaison des textes à l'origine. Mesuré (harnais) : 4 vérifications faillies → 0 (F1a-d, A7). |
| 2 | `ui/edit_session.py:185-213` → `210-244` | **M** | `undo`/`redo` n'validaient que l'existence du bloc. Après `adopt_page` (page re-livrée par le worker avec d'autres textes), un pas de pile rejouait son `old_text` **sur le nouveau bloc** : texte re-OCR écrasé silencieusement par un texte périmé. | Validation avant application : `undo` exige `block.text == step.new_text`, `redo` exige `block.text == step.old_text` ; pas divergent ou orphelin abandonné, enchaînement sur le pas suivant. Mesuré : écriture parasite supprimée (`applied=False`, texte re-OCR intact). |
| 3 | `ui/widgets/results_panel.py:553, 617` → `552-570, 625-635, 745-754` | **M** | `set_page` et `clear` appelaient `_close_editor()` : la saisie **non validée** de l'éditeur inline était perdue sans avertissement quand le worker livrait une page ou que l'utilisateur changeait de fichier. | `_flush_editor()` : l'édition en cours est validée (comme un Ctrl+Entrée) avant tout changement de contenu ; réentrance sûre (le commit ferme l'éditeur avant l'émission). Mesuré : saisie conservée à l'arrivée d'une page (F3b). |
| 4 | `ui/widgets/results_panel.py:654-673` → `664-692` | **M** | `set_active_block` régénérait **tout le HTML rendu** à chaque appel, même si le bloc visé était déjà surligné — et il est appelé plusieurs fois par interaction (clic aperçu, clic résultats, re-sélection après commit). | Early-out : l'index réellement rendu est suivi (`_rendered_active`) ; appel redondant = aucun re-rendu. Mesuré : 200 clics sur le même bloc **5 501,7 ms → 0,2 ms** (~27,5 ms → ~0,001 ms par appel). Les clics alternés restent à ~24-27 ms (re-rendu inhérent au surlignage HTML, voir risques). |
| 5 | `ui/main_window.py:260-261` → `260-267` | **M** | Les pages de `demo_data.DEMO_FILES` sont des **singletons de module** enregistrés tels quels dans le runner : une édition en fenêtre démo fuyait vers la fenêtre démo suivante du même processus (contamination inter-tests observée par le testeur). | Copie profonde à l'enregistrement : `Page.from_dict(spec.page.to_dict())` dans `load_demo`. Les singletons ne sont plus exposés ; les fixtures de test qui compensaient restent valables. |
| 6 | `ui/main_window.py:666-671` → `669-686` | **F** | Branche `_reset_edits` inatteignable (`if self._edit_session.is_dirty():` après un `reset()` qui vide toujours l'historique — le commentaire l'admettait). | Suppression du code mort. |
| 7 | `ui/main_window.py:479, 561` → `496, 568` | **F** | `QMenu(self)` reconstruit à chaque ouverture d'export/réglages, jamais détruit : accumulation d'objets Qt parentés pour la vie de la fenêtre. | `menu.deleteLater()` après `exec()` (bloquant, donc sûr). |
| 8 | `ui/widgets/results_panel.py:693-706` → `695-702, 776-782` | **F** | La carte d'édition est positionnée manuellement ; un redimensionnement de fenêtre pendant l'édition laissait la géométrie périmée. | Géométrie factorisée (`_editor_geometry`) + `resizeEvent` qui repositionne la carte ouverte. |

### Points vérifiés sans correction (implémentation du codeur saine)

- **Performance du modèle** : `page_to_markdown` coûte 0,016 ms (page démo) ;
  la régénération des vues n'a lieu qu'au commit/undo/redo, jamais à la
  frappe. La scène d'aperçu est reconstruite à 0,12-1,5 ms — négligeable.
- **Qt** : raccourcis Ctrl+Z / Ctrl+Y / Ctrl+Shift+Z créés **une seule fois**,
  désactivés pendant l'édition inline (`editing_changed`) pour laisser le
  Ctrl+Z local à l'éditeur — pas de conflit de QShortcut. Aucune connexion
  répétée (`_connect_signals` unique, `scene.selectionChanged` connecté une
  fois, drapeau `_programmatic` contre les boucles de sélection). Signaux
  émis côté thread GUI uniquement. `deleteLater` ajouté sur les menus (n° 7).
- **UX (mockup)** : surcouche du bloc actif plus visible (alpha 85, trait
  2,8 px), surlignage texte « fond accent doux + bord gauche accent » lisible
  (texte #1a1d24 sur #eef2fb), curseur main + tooltips « type — confiance »
  sur les surcouches, double-clic annoncé par le bandeau d'aide.
- **Robustesse** : session vide (update/undo/redo/reset sans crash), document
  jamais chargé, bloc sans `block_id` (identité `index-N` stable), pile
  bornée à 50 pas (les plus anciens sont perdus, état cohérent), redo en
  limite de pile, pages manquantes (pas orphelins).

## 3. Mesures avant / après

Machine : Windows 11, Python 3.14.8, PySide6 6.12.0, `QT_QPA_PLATFORM=offscreen`,
fenêtre affichée pour les mesures d'interaction. Harnais hors dépôt.

| Mesure | Avant | Après |
|---|---|---|
| `ResultsPanel.set_page` complet (page démo 3 blocs) | 17,75 ms (médiane) | inchangé (une fois par commit) |
| idem, page 50 blocs | 78,59 ms | inchangé |
| `PreviewView.show_page` (rebuild scène) | 0,12 ms / 1,49 ms | inchangé |
| 100 × `set_page` naïf (si branché à la frappe) | 2 497,6 ms | — (jamais branché ainsi : 0 re-rendu à la frappe) |
| 200 × `set_active_block` **même bloc** | 5 501,7 ms (27,5 ms/clic) | **0,2 ms (0,001 ms/clic)** |
| 200 × `set_active_block` blocs alternés | ~27,5 ms/clic | ~23-27 ms/clic (re-rendu HTML inhérent) |
| 100 commits complets (édition→signal→vues, page démo) | 33-44 ms/commit | identique (une action discrète) |
| 20 commits complets, page 50 blocs | ~104 ms/commit | identique (voir risques) |
| Frappe dans l'éditeur inline | 0 régénération | 0 régénération |
| Harnais de revue (31 vérifications) | 18/27 OK (avant correctifs) | **31/31 OK** |
| pytest | 43 vertes (baseline) | **73 vertes** (43 + 30 du testeur) |
| Processus python après fermeture | 0 | 0 |

## 4. Vérifications exécutées

- `python -m pytest tests/ -q` → **73 passed** (les 30 nouveaux tests du
  testeur, écrits contre l'API publique d'`EditSession`, passent sans
  modification : les signatures et signaux sont inchangés ; seule la
  sémantique de réaffichage de la même tâche a été corrigée).
- Cycle headless **édition → undo → redo → undo** sur la page démo :
  `Page.to_dict()` final **strictement identique** à l'état initial
  (texte + bbox + confidence + block_id), y compris en multi-pas.
- Robustesse : document vide/absent, bloc sans id, undo sur session vide,
  redo en limite, pas orphelins, page re-livrée divergente, aller-retour
  entre tâches — 31/31.
- R9 : copie (presse-papiers) et export reflètent le texte édité.
- `tasklist | grep -ci python` → **0** après fermeture (aucun zombie).

## 5. Risques restants

1. **Surlignage par re-rendu HTML** : changer de bloc actif régénère le HTML
   du « Rendu » (~24-27 ms sur la page démo, ~104 ms sur 50 blocs). C'est un
   coût par clic (discret), pas par frappe ; une approche
   `QTextEdit.setExtraSelections` supprimerait ce coût mais l'API
   `QTextDocument` n'expose pas de recherche d'ancre nommée — à réévaluer si
   des documents réels volumineux rendent le clic perceptible.
2. **Undo/redo sur page volumineuse** (~104 ms l'action sur 50 blocs) :
   acceptable pour une action discrète ; un debounce de rafraîchissement
   serait le levier suivant si besoin.
3. **Identité positionnelle `index-N`** : correcte tant que les blocs ne sont
   jamais réordonnés (documenté dans le module) ; les pas divergents après
   re-livraison de page sont désormais rejetés, mais une réordonnancement
   futur nécessiterait des ids générés et persistés côté modèle.
4. **Édition limitée aux blocs de texte** (titre, paragraphe, en-tête, pied ;
   tableau/image → notice) : conforme à la phase 5, extension prévue plus tard.
5. **Thème clair uniquement** : le surlignage (#eef2fb + accent) est lisible
   dans le thème clair validé ; un thème sombre exigerait des constantes
   dédiées dans `theme.py` (aucune n'existe aujourd'hui).
6. **Changement d'onglet pendant l'édition inline** : la carte d'édition
   reste posée sur la nouvelle vue (le repositionnement ne couvre que le
   resize) — cas d'usage marginal.

## 6. Fichiers modifiés par la revue

- `src/ocr_app/ui/edit_session.py` — cache d'origines par tâche, validation
  de pile undo/redo, `_recompute_dirty`.
- `src/ocr_app/ui/widgets/results_panel.py` — flush de l'éditeur, early-out
  du surlignage, repositionnement au resize.
- `src/ocr_app/ui/main_window.py` — copie profonde des pages démo, code mort
  retiré, `deleteLater` des menus.
- (Codeur, relus sans retouche : `status_bar.py`, `icons.py`, `preview.py`.)
