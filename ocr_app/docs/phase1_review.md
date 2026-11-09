# Revue Phase 1 — couche worker / UI subprocess

**Date :** 2026-11-09
**Périmètre :** `core/worker.py`, `workers/fake_worker.py`, `ui/worker_client.py`,
`ui/task_runner.py`.
**Hors périmètre (non modifiés) :** `workers/simple.py`, `workers/medium.py`,
`workers/advanced.py`, `core/pipeline.py`, `core/rendering.py`, `cli.py`,
`tests/`, `data/`.
**Méthode :** lecture du code, mesures avant/après sur Python 3.14.8
(Windows 11, PySide6 6.12.0), harnais headless (`QT_QPA_PLATFORM=offscreen`)
pour `WorkerClient`/`TaskRunner`/`BaseWorker`, vérification des processus avec
`tasklist`, suite pytest complète.

## 1. Conclusion R2 / R5

- **R2 (aucun OCR dans l'interface)** : `TaskRunner` ne fait que piloter un
  `QProcess` et relire les JSON de page déjà écrits par le worker
  (`Page.load_json`). Aucun moteur OCR n'est importé ni exécuté dans le
  processus UI.
- **R5 / un processus par document** : vérifié par PID sur deux documents de
  3 pages enchaînés. Chaque document utilise un seul PID, conservé pour les
  3 pages (`SEQ one_process_per_doc True`) ; les deux documents utilisent deux
  PID distincts. `WorkerClient.start()` n'est appelé que dans
  `TaskRunner._start_next()`, jamais dans `page_done`.
- Le worker reste vivant pendant la pause (état `PAUSED`, `QProcess` toujours
  `Running`) et reprend sans sauter d'étape.

## 2. Constats et corrections

Gravités : **C** = critique (perte de données / blocage), **É** = élevée,
**M** = moyenne, **F** = faible. Les lignes « avant » renvoient au commit HEAD
au début de la revue ; les lignes « après » au fichier corrigé.

| # | Fichier:ligne avant → après | Grav. | Constat | Correction |
|---|---|---|---|---|
| 1 | `ui/worker_client.py:177-182` → `313-318` | É | `readAllStandardOutput()` était décodé **morceau par morceau** en UTF-8 avec `errors="replace"`. Une séquence accentuée coupée entre deux `readyRead` produisait des U+FFFD : `result_path` corrompu, page illisible (R14). | Décodeur **incrémental** `codecs.getincrementaldecoder("utf-8")` pour stdout et stderr, réinitialisé à chaque démarrage. Mesuré : 2 caractères de remplacement avant, 0 après sur un flux découpé octet par octet. |
| 2 | `ui/worker_client.py:122-127` → `208-220` | É | `request_cancel()` laissait 3000 ms au worker avant `terminate` : un worker qui ignore stdin était arrêté en **3,41 s**, au-delà de la cible < 2 s. | Grâce ramenée à **1000 ms**, puis `terminate` (400 ms) et `kill` (500 ms) ; `_kill_process()` attend la fin de l'état `Starting` avant de tuer. Mesuré : **1,40 s**. |
| 3 | `ui/task_runner.py:205-252` → `239-304` | É | Après un message `error`, un `progress` ou `page_done` tardif remettait la tâche en `RUNNING` (`status = PAUSED if ... else RUNNING`), ce qui pouvait masquer l'échec et finir en `DONE`. | Gardes d'état terminal : `progress` et `page_done` n'écrivent plus le statut si la tâche est `ERROR`, `CANCELLED` ou `DONE`. Les pages valides restent enregistrées et émises. Mesuré : `error -> error -> error -> error`. |
| 4 | `ui/task_runner.py:254-266` → `306-336` | É | L'annulation était détectée uniquement par la sous-chaîne `"annul"` ; une erreur pendant l'annulation passait en `ERROR`, et un message « stopped »/« interrupted » n'était pas reconnu. | Priorité à `self._cancelling` et à l'état `CANCELLED` ; détection textuelle limitée aux préfixes `annul`/`cancel` ; la première erreur n'est plus réécrite par les suivantes. Mesuré : `CANCEL_ERROR_STATUS cancelled`. |
| 5 | `ui/task_runner.py:271-317` → `339-391` | M | Le document suivant était démarré **à l'intérieur du signal `finished` du QProcess**. Stress test : un démarrage intermittent échouait sur Windows (`worker arrêté`, 0 page). | Démarrage différé d'un tour de boucle (`QTimer.singleShot(0, self._start_next)`). 12 cycles de 2 documents et 3 passes du harnais complet : 0 échec. |
| 6 | `ui/task_runner.py:362` → `438-451` | É | `shutdown()` n'était appelé que par `MainWindow.closeEvent` ; un arrêt Qt par un autre chemin pouvait laisser le `QProcess` vivant. | Connexion à `QCoreApplication.aboutToQuit` dans `TaskRunner` **et** dans `WorkerClient` ; `shutdown()` idempotent. Mesuré : worker actif arrêté en **0,285 s**, PID disparu, seconde émission sans erreur. |
| 7 | `ui/worker_client.py:68-112` → `54-65`, `79-92`, `137-195` | É | Le client lançait **toujours** `ocr_app.workers.fake_worker` ; `task.mode` était ignoré, donc les workers Simple/Moyen/Avancé ne pouvaient pas être branchés. | API `worker_command` (constructeur) et `start(command=...)`, plus `TaskRunner(worker_commands={mode: commande})`. Par défaut, le worker factice reste utilisé pour ne pas casser la phase 4. |
| 8 | `core/worker.py:204-257` → `252-311` | É | `stop()` attendait `timeout` **à chaque phase** (jusqu'à 3 × 3 s = 9 s), ne réinitialisait pas `_proc`, et ne traitait pas les threads d'un worker précédent. | Budget global `timeout` + escalade plafonnée (~1 s), `_lifecycle` sérialise start/stop, `_close_streams()`, `_proc` remis à `None`, `stop()` idempotent. |
| 9 | `core/worker.py:292-318` → `347-384` | M | Le thread de lecture capturait `self._proc` au démarrage (risque de course au redémarrage) et ne récupérait pas le code de sortie après EOF (zombie POSIX possible). | `proc` passé explicitement en argument aux threads (`args=(proc,)`), garde d'identité `self._proc is proc`, `proc.poll()` en `finally`, jointure de stderr dans `wait()`. |
| 10 | `core/worker.py:189-202` → `226-250` | É | Aucune API `cancel()` côté superviseur : `stop()` fermait stdin et supprimait les callbacks, donc le message final d'annulation du worker était perdu. | `BaseWorker.cancel(timeout=1.0)` écrit `cancel`, attend la sortie, laisse le thread de lecture délivrer l'`error` final, puis nettoie. Mesuré : 0,021 s (worker coopératif) ; 1,015 s (worker qui ignore stdin). |
| 11 | `ui/worker_client.py:181`, `241` → `313-325`, `398-417` | M | Tampons stdout/stderr non bornés : une ligne sans fin faisait croître la mémoire de l'interface sans limite. | Bornes `MAX_PROTOCOL_BUFFER = 1 Mio` et `MAX_STDERR_BUFFER = 256 Kio`, ligne trop longue journalisée puis écartée/tronquée. |
| 12 | `ui/worker_client.py:257-261` → `430-444` | F | `QTimer.singleShot(400, self._force_kill)` utilisait un callback statique après une éventuelle destruction du client. | Timer `QTimer` possédé par le client (`_terminate_timer`), arrêté dans `_finalize()` et `stop()`. |
| 13 | `ui/worker_client.py:184-235` → `327-333`, `398-410` | F | Découpage des lignes par `split("\n", 1)` en boucle : recopie O(n²) du tampon restant à chaque ligne. | `split("\n")` en une passe + affectation étoilée du reliquat. Mesuré sur 20 000 messages (1,28 Mio) : **19,7–27,0 ms → 2,3 ms** (environ 9–11×). |
| 14 | `workers/fake_worker.py:71-80` → `72-92` | F | `_ProtocolOutput.send()` n'était pas thread-safe : deux threads pouvaient entrelacer deux lignes JSON si un futur worker émettait depuis un thread auxiliaire. | Verrou `threading.Lock` autour de l'écriture + flush. |
| 15 | `workers/fake_worker.py:120-154` → `127-175` | M | Une exception pendant le traitement d'une page (ex. sauvegarde impossible) laissait une trace Python et un code de sortie non nul **sans message `error`** sur stdout. | `except Exception` → diagnostic stderr + `ErrorMessage(page, "erreur interne : ...")` + code 1. Vérifié avec un dossier `page_001.json` conflictuel. |
| 16 | `workers/fake_worker.py:156-177` → `177-205` | F | EOF avant `start` (fermeture volontaire de stdin) émettait « annulé » même sans commande `cancel` ; attribut `_eof` mort. | EOF sans annulation = arrêt silencieux journalisé ; `_eof` supprimé. |
| 17 | `ui/worker_client.py:143-145` → `258-264` | M | Si une annulation était demandée pendant le démarrage, `_on_started()` envoyait quand même `start` avant que le timer ne tue le processus. | Si le timer d'annulation est actif, `start` n'est pas envoyé : `cancel` est transmis au worker. |
| 18 | `ui/worker_client.py:196-235` → `345-391` | M | Validation faible des messages : `int()`/`str()` acceptaient des valeurs de mauvais type (`"abc"` → exception évitée, mais `None` → `"None"`), pages négatives acceptées. | Validation stricte `page >= 1`, `total >= 1`, `stage`/`result_path`/`message` de type `str`, chemin non vide ; message invalide journalisé et ignoré, sans crash. |
| 19 | `core/worker.py:368-374` → `449-481` | F | `WorkerProcess` était une classe abstraite vide (uniquement une docstring) présentée comme superviseur. | Implémentation concrète : `initialize()` démarre le script/exécutable, `shutdown()` l'arrête, `process_page()` délègue explicitement au sous-processus. |

## 3. Mesures avant / après

Machine : Windows 11, Python 3.14.8, PySide6 6.12.0. Sauf mention contraire,
worker factice, 3 pages, `--stage-delay 0.05`, dossier de travail accentué et
espacé. Les harnais de mesure sont des scripts temporaires hors dépôt.

| Mesure | Avant | Après |
|---|---:|---:|
| Annulation d'un worker qui ignore stdin (`WorkerClient.request_cancel`) | **3,41 s** | **1,40 s** (cible < 2 s) |
| Annulation d'un worker sans réponse (`BaseWorker.cancel(timeout=1.0)`) | budget `stop()` jusqu'à 9 s (3 × timeout) | **1,02 s** |
| Annulation coopérative depuis une pause (pause → sortie du processus) | 0,015–0,077 s | 0,026–0,043 s |
| Décodage UTF-8 d'une ligne accentuée découpée octet par octet | 2 U+FFFD, accent perdu | 0 U+FFFD, chemin exact |
| Tampon stdout pour 5 Mio sans fin de ligne | **5 242 880 caractères retenus** (pic 5,2 Mo) | **max 1 048 576** (cap), pic 2,3 Mo |
| Découpage de 20 000 lignes (1,28 Mio) | 19,7–27,0 ms | **2,3–2,4 ms** (environ 9–11×) |
| Flux réel de 20 000 messages UTF-8 via `QProcess` | non mesuré | 0,43–0,55 s, 20 000 `progress`, 1 `page_done`, 0 erreur, 0 perte |
| 2 documents × 3 pages séquentiels | 2 PID distincts, 1 PID/document, 1,09 s/document | identique, 1 PID/document, aucune fuite |
| Threads après 6 cycles start/cancel | 1 → 1 | 1 → 1 (delta 0) |
| Processus `python.exe` après harnais et pytest | 0 orphelin | 0 orphelin (`tasklist` avant/après) |

## 4. Vérifications exécutées

| Vérification | Résultat |
|---|---|
| `python -m pytest tests/ -q` | **43 passed** (dernier passage). À l'ouverture de la revue : 35 passed ; l'agent testeur a ajouté des tests pendant la session. Aucun test n'a été modifié par cette revue. |
| `python -m compileall` sur les 4 fichiers | OK |
| `TaskRunner` headless, 2 documents × 3 pages | Les deux `DONE`, 3 `page_loaded` par document, 1 PID par document, PID distincts |
| Pause | Statut `paused`, worker vivant, pages/étape inchangées pendant 500 ms, reprise OK |
| Annulation depuis la pause | `cancel` → sortie du processus en 0,026–0,043 s, code 0, tâche `CANCELLED` |
| Message `error` puis `progress` tardif | La tâche reste `ERROR` (plus de résurrection) |
| Erreur pendant `_cancelling` | Tâche `CANCELLED`, pas `ERROR` |
| Lignes malformées (non-JSON, liste, champs manquants, octets invalides, `page_done` vide) | Ignorées, aucun crash, les messages valides suivants sont délivrés |
| `BaseWorker` : pause/reprise/annulation | Progression figée pendant la pause, `error` d'annulation reçu, `_proc` remis à `None`, `stop()` idempotent |
| `BaseWorker` : redémarrage après arrêt et après crash | OK dans les deux cas, messages valides après redémarrage |
| `aboutToQuit` avec worker actif | Arrêt en 0,285–0,365 s, PID disparu, seconde émission sans erreur |
| `tasklist` avant/après harnais et pytest | 0 processus `python.exe` résiduel |

## 5. Risques restants

1. **Workers réels non branchés** : `simple.py`, `medium.py` et `advanced.py`
   n'exposent pas encore de point d'entrée CLI/protocole. Tant qu'ils ne le
   font pas, `TaskRunner` utilise le worker factice pour tous les modes. La
   couture est prête : `TaskRunner(worker_commands={"simple": [...],
   "medium": [...], "advanced": [...]})`.
2. **Contrat CLI des futurs workers** : `WorkerClient.start()` ajoute
   `--pages`, `--work-dir` et `--stage-delay`. Un exécutable réel doit accepter
   ces options ou être enveloppé.
3. **Arbre de processus** : `QProcess.terminate()`/`kill()` ne tue que le
   processus direct. Un worker Avancé qui lancerait des processus enfants
   (runtime ML) nécessitera un Job Object Windows (`taskkill /T` ou équivalent).
4. **Pause/annulation coopératives** : un worker réel doit vérifier les
   commandes entre pages et entre étapes. Une opération bloquante dans un
   moteur natif ne peut pas être interrompue avant le `kill` de secours
   (~1,4 s).
5. **Chargement des pages dans le thread GUI** : `Page.load_json()` est appelé
   depuis `_on_page_done` (thread Qt). Ce n'est pas de l'OCR (R2 respecté),
   mais une page très volumineuse pourrait provoquer une micro-saccade. À
   déporter dans un `QThreadPool` si nécessaire.
6. **Intégrité des modules** : `ModuleManifest.verify_sha256()` existe mais
   n'est pas branché au lancement d'un worker réel (R15/sécurité).
7. **`TaskQueue` sans machine à états** : hors périmètre de cette revue
   (`core/task_queue.py` non modifié) ; les transitions invalides restent
   permises, comme documenté dans `docs/code_review.md`.
8. **Arrêt bloquant** : `WorkerClient.stop()` peut bloquer le thread GUI
   jusqu'à ~1,9 s si le worker est totalement bloqué. Acceptable à la
   fermeture/au changement de démonstration, mais à garder en tête si un arrêt
   non bloquant est ajouté plus tard.

## 6. Cohérence avec les règles

- **R2** : tout calcul reste dans le sous-processus ; l'UI ne lit que les JSON
  de page et ne charge aucun moteur OCR.
- **R5** : un seul processus par document, conservé de la page 1 à la
  dernière page (vérifié par PID) ; aucune relance par page.
- **R6** : le worker factice écrit `page_NNN.json` à l'étape `save` ; le
  `TaskRunner` recharge ces fichiers au fur et à mesure.
- **R7** : `TaskRunner` ne rend aucune page ; le rendu reste dans le worker.
- **R14** : décodage UTF-8 incrémental, chemins accentués testés de bout en
  bout (worker factice et commande worker personnalisée).
- **R15** : commande de worker configurable par mode, sans modifier le noyau ;
  les workers réels pourront être packagés séparément.

## 7. Fichiers modifiés

- `src/ocr_app/core/worker.py`
- `src/ocr_app/workers/fake_worker.py`
- `src/ocr_app/ui/worker_client.py`
- `src/ocr_app/ui/task_runner.py`
- `docs/phase1_review.md` (ce rapport)

Aucun fichier de `tests/`, `data/`, `scripts/`, `core/pipeline.py`,
`core/rendering.py`, `workers/simple.py`, `workers/medium.py`,
`workers/advanced.py` ni `cli.py` n'a été modifié par cette revue. Des
modifications concurrentes d'autres agents sur `cli.py`, `core/pipeline.py` et
`workers/simple.py` sont présentes dans l'arbre de travail mais hors de ce
périmètre.
