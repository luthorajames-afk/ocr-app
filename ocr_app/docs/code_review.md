# Revue de code — modules cœur AOCRI

**Date :** 2026-11-09
**Périmètre :** `core/data_model.py`, `core/protocol.py`, `core/task_queue.py`,
`core/worker.py`, `core/exports.py`, `modules/module_manifest.py`, `cli.py`.
**Hors périmètre (non modifiés) :** `ui/`, `core/pipeline.py`, `workers/`,
`tests/`, `data/`, `docs/engines_status.md`, `scripts/`.
**Méthode :** lecture du code, reproduction des cas limites sur Python 3.14.8
(Windows 11), tests existants + nouveaux tests étendus, benchmarks avant/après,
vérification manuelle des sous-processus et de la CLI.

## 1. Constats et corrections

Gravités : **C** = critique (perte de données / plantage), **É** = élevée,
**M** = moyenne, **F** = faible. Statut : **Corrigé**, **Documenté**.

| # | Fichier:ligne (final) | Grav. | Constat | Action | Statut |
|---|---|---|---|---|---|
| 1 | `core/data_model.py:283` | É | `Block.to_dict` appelait `asdict()` (copie récursive de tout le tableau, cellules incluses) puis `table.to_dict()` une seconde fois. Coût O(2 × cellules) et pic mémoire inutile. | Construction explicite du dictionnaire ; plus aucune copie profonde. | Corrigé |
| 2 | `core/data_model.py:124` | C | `Page.from_dict` faisait `Cell(**c)` : `TypeError` sur une clé supplémentaire, `merged_from` restait une liste (type incohérent), `"grid": null` levait `TypeError`. | `_normalise_grid` + `Cell.from_dict` tolérants : `None` conservé, dict/cellule/texte acceptés, `merged_from` normalisé en tuple, index déduits de la position. | Corrigé |
| 3 | `core/data_model.py:296` | C | `Block.from_dict` exigeait `type` (`KeyError` sinon) et rejetait un type inconnu (`ValueError`), ce qui empêchait de relire une page produite par une version future du worker (R15). | Champs optionnels avec valeurs par défaut ; type inconnu converti en `PARAGRAPH`. | Corrigé |
| 4 | `core/data_model.py:41-121` | É | Aucune validation des types JSON : `bbox` mal formée, `confidence` `NaN`/`Inf` (JSON invalide), `number` texte, `text` `None`. | Helpers `_as_int`, `_as_float` (rejette NaN/Inf), `_as_text`, `_as_bbox`, `_as_merged_from`. | Corrigé |
| 5 | `core/data_model.py:422` | É | `Document` avait `save_json` mais ni `from_dict` ni `load_json` (le test smoke contournait avec `Document.__new__`). | Ajout de `Document.from_dict` et `Document.load_json`. | Corrigé |
| 6 | `core/data_model.py:447` | É | Écriture JSON directe : fichier partiel possible en cas de plantage (R6), pas de création du dossier parent, pas de `\n` explicite. | `_atomic_write_json` : `mkstemp` dans le dossier cible, écriture UTF-8, `flush`, `os.replace`, nettoyage du temporaire en cas d'erreur. | Corrigé |
| 7 | `core/data_model.py:468` | M | `Page.load_json` laissait fuiter `JSONDecodeError`/`UnicodeDecodeError` sans contexte ; fichier absent peu explicite. | `_load_json_object` : `FileNotFoundError` contextualisé, `ValueError` avec fichier/ligne/colonne, lecture `utf-8-sig` (BOM toléré). | Corrigé |
| 8 | `core/data_model.py:322` | É | Au moment de la revue, `pipeline.py` (hors périmètre) fournissait des blocs `dict` ; `page.blocks` les conservait tels quels et tous les exports plantaient ensuite sur `block.type`. | `Page.__post_init__` convertit défensivement les blocs `dict` en `Block` et copie la liste. L'agent codeur a depuis corrigé `pipeline.py` pour construire des `Block` ; la tolérance reste utile pour les données anciennes. | Corrigé (défensif) |
| 9 | `core/data_model.py:225/322/385` | M | Mutation partagée possible : les listes `blocks`, `pages` et les lignes de `grid` étaient conservées par référence. | Copie superficielle des listes à la construction ; les objets `Cell` restent partagés (voulu), `merged_from` est immuable (tuple). | Corrigé |
| 10 | `core/exports.py:106` | É | `to_markdown` ajoutait un `\n` par bloc puis joignait avec `\n` : lignes vides accumulées, surtout autour des tableaux vides. Un tableau `grid=[[]]` produisait `\|  \|` / `\|` (tableau Markdown invalide). | Composition en sections jointes par `\n\n`, parties vides filtrées ; `_table_to_md` renvoie `[tableau vide]` si aucune ligne non vide et padde les lignes irrégulières. | Corrigé |
| 11 | `core/exports.py:47` | M | Une cellule contenant `\|` ou un saut de ligne cassait le tableau Markdown ; le HTML n'échappait ni `&`, ni `<`, ni `>`. | `_md_cell` échappe `\|`, `\\` et remplace les sauts par `<br>` ; `_html_text` utilise `html.escape`. Chemin rapide pour les cellules sans caractère spécial. | Corrigé |
| 12 | `core/exports.py:75/247` | É | `save_xlsx` plantait sur un titre contenant `[ ] : * ? / \` (`ValueError: Invalid character ... found in sheet title`) ; les caractères de contrôle (`\x00`…) faisaient échouer l'écriture. | `_xlsx_sheet_title` assainit et tronque à 31 caractères ; `_xlsx_text` retire les caractères illégaux. | Corrigé |
| 13 | `core/exports.py:233` | M | `_docx_table` pouvait appeler `add_table(cols=0)` pour un tableau sans cellule. | Lignes/colonnes minimales à 1, message `[tableau vide]` si aucune ligne. | Corrigé |
| 14 | `core/exports.py:83/278` | M | `save_pdf` plantait sur un caractère non encodable en WinAnsi (emoji, CJK) et dessinait hors page au-delà d'une page. | `_pdf_text` convertit en cp1252 avec `errors="replace"` ; pagination simple (`showPage`). Non exécuté ici (reportlab absent). | Corrigé (non testé) |
| 15 | `core/exports.py:197` | F | `save_docx`/`xlsx`/`pdf` ne créaient pas le dossier parent, contrairement aux exports texte. | `_ensure_parent` appliqué à tous les formats. Écritures texte explicites UTF-8/`\n`. | Corrigé |
| 16 | `core/exports.py:92` | F | `to_plain_text` perdait le contenu des tableaux (`[tableau]`), comportement figé par les tests étendus. | Contrat conservé : marqueur `[tableau]` (y compris tableau vide/absent) ; les formats structurés portent le contenu. | Documenté |
| 17 | `core/protocol.py:85` | É | `parse_line` acceptait une liste/scalaire JSON ; les appelants faisaient ensuite `data.get(...)` → `AttributeError` et mort de la boucle de lecture. | Validation stricte : objet JSON obligatoire, sinon `ValueError` ; BOM et lignes vides gérés ; erreurs contextualisées. | Corrigé |
| 18 | `core/task_queue.py:54` | M | Aucune synchronisation : le thread de lecture du worker et l'interface pouvaient muter `_tasks`/`_order` simultanément. | `threading.RLock` sur toutes les opérations ; callbacks `on_change` appelés **hors verrou** (pas d'interblocage). Champs numériques bornés/typés. | Corrigé |
| 19 | `core/task_queue.py` | F | Pas de machine à états : `pause` après `done`, `resume` après `cancelled`, double `cancel` sont acceptés. | Comportement volontairement conservé (les tests étendus le documentent). À décider côté produit. | Documenté |
| 20 | `core/worker.py:292` | C | `_read_loop` lisait stdout séquentiellement dans le thread appelant : `run_subprocess` bloquait l'interface jusqu'à la fin du worker. | Lecture dans un thread dédié (`start_subprocess`) ; `run_subprocess` retourne immédiatement par défaut, `wait()` pour attendre. | Corrigé |
| 21 | `core/worker.py:277` | C | `stderr=subprocess.PIPE` n'était jamais vidé : un worker bavard pouvait remplir le pipe et se bloquer (interblocage). | Thread `_drain_stderr`, buffer borné à 200 lignes, `stderr_text()` pour le diagnostic. | Corrigé |
| 22 | `core/worker.py:204` | C | `stop()` pouvait laisser un processus orphelin : pas de fermeture de stdin, pas de `wait()` après `kill()`, threads et flux non fermés. | Séquence : fermeture stdin (EOF propre) → `wait` → `terminate` → `wait` → `kill` → `wait`, puis jointure des threads et fermeture des flux. `stop()` idempotent. | Corrigé |
| 23 | `core/worker.py:316` | É | `_read_loop` accédait à `data["page"]`/`data["total"]`/`data["stage"]` : un message incomplet levait `KeyError` et tuait la lecture. | `_dispatch` valide type et champs (`_is_int`, `str`) ; message invalide ignoré et journalisé. | Corrigé |
| 24 | `core/worker.py:316` | M | Une exception dans un callback utilisateur tuait le thread de lecture. | Callbacks encapsulés dans `try/except` + `logger.exception` ; la lecture continue. | Corrigé |
| 25 | `core/worker.py:189` | F | `send_command` levait `BrokenPipeError`/`OSError` si stdin était fermé. | Retourne `False` et repasse `_running=False`. | Corrigé |
| 26 | `core/worker.py:292` | M | `_proc` non initialisé dans `__init__` ; au redémarrage rapide, l'ancien thread pouvait remettre `_running=False` sur le nouveau worker. | `_proc` initialisé ; garde de génération (`self._proc is proc`) avant dispatch et dans `finally`. Test de redémarrage manuel OK. | Corrigé |
| 27 | `core/worker.py:99/151` | M | Encodage/pipe : pas de `-u`, pas de `errors="replace"`, un exécutable packagé aurait été lancé via l'interpréteur. | `-u` pour les `.py`, exécution directe d'un exécutable, UTF-8 explicite avec `errors="replace"`, `bufsize=1`. `start_subprocess` documente que les callbacks arrivent sur un thread de fond (à marshaler côté Qt). | Corrigé |
| 28 | `modules/module_manifest.py:25/178` | É | `is_compatible` comparait `m.version >= app_version` sur des chaînes : `"1.10.0" >= "1.9.0"` est **faux** en lexicographique, contrairement à l'intention numérique. | `_version_key` (1.10 > 1.9, pré-version ignorée) ; comparaison numérique. Comportement conforme aux tests étendus (`version module >= version app`). | Corrigé |
| 29 | `modules/module_manifest.py:125` | É | `scan()` plantait sur un `module.json` non UTF-8 (`UnicodeDecodeError` non captée) ; le registre était vidé avant le scan (perte partielle sur erreur) ; `iterdir` non protégé. | Scan dans un dict local puis remplacement atomique ; `OSError`/`UnicodeDecodeError`/`JSONDecodeError`/`ValueError` par module ignorés et journalisés ; `is_dir()` vérifié. | Corrigé |
| 30 | `modules/module_manifest.py:67` | M | `from_dict` acceptait un JSON non-objet ou des champs vides/non-strings (fuite `TypeError`). | Validation des champs requis (`id`, `version`, `mode`, `worker`, `sha256`), messages explicites. | Corrigé |
| 31 | `modules/module_manifest.py:95` | M | `verify_sha256` levait `OSError` (fichier illisible), acceptait un dossier, et un `worker` pouvait sortir du dossier du module (`../`). | `is_file()`, `OSError` → `False`, nom de worker limité à un basename, comparaison insensible à la casse, préfixe `sha256:` toléré. | Corrigé |
| 32 | `modules/module_manifest.py:189` | F | `sha256_file` lisait par blocs de 8 Kio. | Blocs de 1 Mio, accepte `os.PathLike`. | Corrigé |
| 33 | `cli.py:138` | É | `python -m ocr_app.cli` ne faisait **rien** : absence de garde `if __name__ == "__main__"`, alors que le README documente cette commande. | Garde ajoutée (`raise SystemExit(main())`). `python -m ocr_app.cli --help` fonctionne. | Corrigé |
| 34 | `cli.py:80` | M | `on_stage` était défini mais jamais branché ; seul un `[page N] done` post-traitement s'affichait. | `pipeline.on_stage(_on_stage)` avant `process_document` ; les 8 étapes sont affichées. | Corrigé |
| 35 | `cli.py:93` | É | Aucune sauvegarde JSON par page (R6) et aucune trace du document. | `page_NNN.json` pour chaque page + `document.json` ; dossier `work/` configuré dans `PipelineConfig`. Sauvegarde encore post-traitement car `pipeline.process_document` retourne le document complet (API hors périmètre). | Corrigé (partiel) |
| 36 | `cli.py:50` | M | Erreurs non gérées : `mkdir`, pipeline, exports laissaient une trace Python ; `--dpi` négatif accepté ; entrée non vérifiée comme fichier. | Messages stderr + codes de sortie (1/2), `--dpi <= 0` refusé, `> 300` limité avec note (R7), `is_file()` vérifié, `KeyboardInterrupt` → 130. | Corrigé |
| 37 | `cli.py:77` | F | `PipelineConfig.work_dir` n'était jamais renseigné. | `output_dir/work` créé et transmis. | Corrigé |

## 2. Optimisations appliquées et mesures

Machine : Windows 11, Python 3.14.8, disque local. Jeu de mesure : 120 blocs,
dont 60 tableaux de 20 × 30 cellules (36 000 cellules), texte de 200 caractères
par bloc.

| Opération | Avant | Après | Gain |
|---|---:|---:|---:|
| `Page.to_dict()` (sérialisation en mémoire) | **415 ms** | **15–21 ms** | **~20–28×** |
| `json.dumps(page.to_dict())` | 87 ms (à froid) | 48–87 ms | inchangé (dominé par `json`) |
| `Exporter.to_markdown()` | 13,5 ms | 15–19 ms | +1,5–5 ms (échappement/normalisation ajoutés) |
| `Exporter.to_html()` | 16,4 ms | 21–27 ms | +5–11 ms (échappement HTML ajouté) |
| Pic mémoire `Page.to_dict()` | non mesuré (double copie `asdict`) | 7,1 Mo | plus de copie profonde |

Détail des optimisations :

- `Block.to_dict` : suppression de `dataclasses.asdict`, qui copiait récursivement
  chaque `Cell` du tableau puis reconstruisait le dictionnaire. La sérialisation
  ne fait plus qu'une passe.
- `Table.to_dict` / `Cell.to_dict` : construction explicite, aucune copie
  profonde des tuples `merged_from`.
- `Exporter._table_to_md` : concaténation via `list` + `"\n".join` (plus de
  `md +=` en boucle, O(n²) sur les grands tableaux) ; chemin rapide sans
  remplacement pour les cellules sans `|`, `\` ni saut de ligne.
- `_table_rows` : copie des lignes uniquement (pas des cellules) et filtrage des
  lignes vides.
- `sha256_file` : blocs de 1 Mio au lieu de 8 Kio.
- `Page.save_json` atomique : coût d'un `mkstemp` + `os.replace` par page,
  négligeable devant le rendu OCR et nécessaire à R6.

Les exports HTML/Markdown sont légèrement plus lents qu'avant parce qu'ils
échappent désormais le contenu et normalisent les tableaux : c'est le prix de la
correction des bugs de lignes vides et d'injection HTML, pas une régression de
complexité.

## 3. Vérifications exécutées

| Vérification | Résultat |
|---|---|
| `python -m pytest tests/ -q` | **35 passed** (dernier passage ; 33 tests cœur/worker + 2 tests UI) |
| `python -m compileall src/ocr_app/core src/ocr_app/modules src/ocr_app/cli.py -q` | **exit 0** |
| Roundtrip `Page` avec tableau, cellules `None`, `merged_from` tuple → JSON fichier → `Page.load_json` | OK, `merged_from` redevient un tuple, `None` conservés |
| `"grid": null`, `grid=[]`, `grid=[[]]`, cellule avec clé inconnue, type de bloc inconnu | OK, aucun plantage |
| `Document.save_json` / `Document.load_json` sur `sous dossier éà/doc éà.json` | OK, accents littéraux (`ensure_ascii=False`) |
| Fichier absent / JSON invalide / BOM UTF-8 | `FileNotFoundError` contextualisée / `ValueError` avec ligne-colonne / BOM accepté |
| `confidence=NaN` et `Inf` | remplacés par les valeurs par défaut, JSON valide |
| `python -m ocr_app.cli --help`, `info`, `process` | OK ; `process` produit `page_001.json`, `document.json`, `result.{txt,md,html}` |
| CLI : entrée absente, `--dpi 0`, dossier de sortie invalide | codes 1 / 2 / 1 avec message stderr |
| Worker factice : 3 pages, 21 messages `progress`, 3 `page_done`, code 0 | OK, fichiers `page_00N.json` valides |
| Worker qui ignore stdin : `stop(timeout=0.5)` | terminé en **0,51 s**, `poll() != None`, aucun orphelin, `stop()` idempotent |
| Redémarrage immédiat d'un worker après la fin du précédent | OK (garde de génération) |
| Messages protocole invalides (non-JSON, liste, champs manquants/mauvais types) | ignorés, messages valides délivrés, aucun crash |
| 300 lignes stderr | seules les 200 dernières conservées, pas de blocage |
| `ModuleRegistry.is_compatible` : module 1.10.0 vs app 1.9.0 / 2.0.0 | `True` / `False` (comparaison numérique) |
| Manifeste non UTF-8, JSON corrompu, manifeste incomplet, dossier absent | module invalide ignoré, les autres restent détectés |
| XLSX : titre `T[]:*?/\…`, cellule `A\x00B`, tableau vide | fichier valide, titre assaini, caractère de contrôle retiré |
| Exports tableau vide / `grid=[[]]` / cellules `None` / `<script>` | `[tableau vide]`, pas de ligne Markdown parasite, HTML échappé |

## 4. Risques et points signalés (non appliqués)

1. **`core/pipeline.py` (hors périmètre, agent en cours)** : la construction de
   blocs est désormais correcte (`Block`), mais `process_document` ne traite
   qu'une page placeholder et n'expose pas de flux page par page. La CLI
   sauvegarde donc les JSON après `process_document` ; pour une vraie
   sauvegarde R6 *dès* la fin de chaque page, le pipeline devra exposer un
   callback de page terminée (ou les workers piloteront directement la
   sauvegarde).
2. **`ui/` (hors périmètre)** : non modifié. Pendant la session, un import
   transitoire de `QMimeData` depuis `PySide6.QtGui` a cassé la collecte pytest ;
   l'agent UI l'a corrigé et le dernier passage complet est vert. `TaskRunner`
   utilise `Page.load_json` et `TaskQueue` : les signatures publiques sont
   conservées.
3. **`workers/` (hors périmètre)** : non modifié ; le worker factice valide le
   protocole et R6.
4. **Exports PDF/DOCX non exécutés** : `reportlab` et `python-docx` ne sont pas
   installés sur la machine. Le code est protégé (`RuntimeError` explicite),
   assainit le texte PDF et crée les dossiers parents, mais n'a pas été validé à
   l'exécution.
5. **Processus enfant** : `stop()` termine le worker direct. Si un futur worker
   Avancé lance des processus enfants, il faudra un Job Object Windows (ou
   `taskkill /T`) pour tuer l'arbre complet.
6. **`run_subprocess` change de comportement** : non bloquant par défaut. Aucun
   appelant dans le dépôt ; l'UI utilise son propre `WorkerClient` QProcess.
   Les appelants doivent utiliser `wait()` s'ils veulent l'ancien comportement
   bloquant.
7. **`is_compatible`** : la méthode compare `manifest.version >= app_version`
   (contrat des tests étendus). `min_app_version` n'est pas utilisée par cette
   méthode ; si la sémantique produit doit être « version app ≥ minimum requis »,
   il faudra trancher et adapter tests + code.
8. **Types de blocs inconnus** : convertis en `PARAGRAPH` pour la compatibilité
   ascendante (R15). Un type métier futur (`formula`, `handwriting`…) serait
   relu comme du texte, sans erreur mais sans distinction.
9. **`Page.__post_init__`** ignore silencieusement les entrées de `blocks` qui
   ne sont ni `Block` ni `dict`. Les données corrompues sont ainsi écartées ;
   un journal côté appelant serait souhaitable.
10. **`TaskQueue` sans machine à états** : les transitions invalides restent
    permises (comportement codifié par les tests). Une validation produit
    pourra être ajoutée plus tard sans casser les tests actuels si elle est
    configurable.
11. **Écritures atomiques** : le temporaire est créé dans le dossier cible ;
    le dossier doit être inscriptible. Sur Windows, `os.replace` échoue si le
    fichier cible est ouvert par un autre processus (comportement attendu).
12. **Export texte brut** : conserve le marqueur `[tableau]` (contrat des tests)
    au lieu du contenu des cellules ; les exports Markdown/HTML/DOCX/XLSX/PDF
    portent le contenu.
13. **Cellules fusionnées** : `Cell.merged_from` est correctement sérialisé et
    relu (tuple), mais aucun export ne génère encore de `colspan`/`rowspan` ou
    de fusion DOCX/XLSX. La sémantique exacte de `merged_from` (cellule d'origine
    ou plage couverte) reste à figer avant d'implémenter le rendu des fusions.

## 5. Cohérence avec les règles R1–R15

- **R1 (noyau léger)** : `data_model` (json, math, os, tempfile, pathlib,
  dataclasses, enum), `protocol` (json), `task_queue` (threading, time, uuid),
  `worker` (subprocess, threading, logging), `exports` (html, re, pathlib),
  `module_manifest` (hashlib, json, re, pathlib). Aucune dépendance lourde ;
  les imports optionnels (`docx`, `openpyxl`, `reportlab`) restent locaux et
  protégés.
- **R2 (sous-processus)** : `BaseWorker` lance toujours un processus séparé ;
  la lecture est asynchrone, l'interface n'exécute aucun OCR.
- **R6 (sauvegarde page/page)** : `Page.save_json` est atomique et crée les
  dossiers ; le worker factice écrit toujours `page_NNN.json` à l'étape `save` ;
  la CLI écrit un JSON par page (après retour du pipeline, limitation d'API).
- **R14 (accents/espaces)** : tous les fichiers JSON/manifestes sont lus/écrits
  en UTF-8 explicite (`utf-8-sig` en lecture), les sous-processus forcent
  UTF-8, et les tests utilisent des dossiers `travail accentué avec espaces`.
- **R15 (modules/versions)** : comparaison numérique des versions, manifestes
  invalides isolés, types de blocs inconnus tolérés.

## 6. Fichiers modifiés

- `src/ocr_app/core/data_model.py`
- `src/ocr_app/core/protocol.py`
- `src/ocr_app/core/task_queue.py`
- `src/ocr_app/core/worker.py`
- `src/ocr_app/core/exports.py`
- `src/ocr_app/modules/module_manifest.py`
- `src/ocr_app/cli.py`

Aucun fichier de `ui/`, `workers/`, `tests/`, `data/`, `scripts/` ni
`core/pipeline.py` n'a été modifié. Les tests existants et étendus passent.
