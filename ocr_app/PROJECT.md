# PROJECT.md — Règles applicables à la session de travail

**Produit :** AOCRI — Advanced Optical Character Recognition for Itamba
**Éditeur :** Kriesty Solutions

Ce document synthétise les règles en vigueur pour le développement de l'application OCR hors ligne (Windows). Elles sont issues des « pièges à éviter » listés dans le plan de travail v1 (15 items) et doivent être respectées par tous les contributeurs pendant cette session.

## Règles techniques

**R1 — Pas de PyTorch dans le noyau.** Les dépendances lourdes (PyTorch, transformers…) restent dans le module Avancé, derrière un exécutable séparé. Le noyau (`core/`) ne contient que des dépendances légères.

**R2 — L'OCR s'exécute toujours dans un sous-processus.** Jamais de calcul d'OCR dans le processus de l'interface. La fenêtre ne doit jamais se geler et l'annulation doit rester possible.

**R3 — PyInstaller en mode `--onedir`, jamais `--onefile`.** Démarrage rapide, moins de faux positifs antivirus.

**R4 — Chemins de modèles explicites, tout téléchargement bloqué à l'exécution.** Aucune bibliothèque ne doit tenter de télécharger un modèle au premier lancement (hors ligne).

**R5 — Le worker Avancé reste vivant pendant tout le document.** Pas de rechargement par page (coût de plusieurs secondes par page).

**R6 — Sauvegarde page par page, dès qu'une page est terminée.** Pas de sauvegarde unique à la fin (évite la perte en cas de plantage).

**R7 — Traitement PDF page par page, 300 DPI maximum.** Ne pas charger tout un PDF en mémoire.

**R8 — Utiliser le texte natif des PDF quand disponible.** Ne pas faire d'OCR sur un PDF qui contient déjà du texte (plus lent et moins fiable).

**R9 — Édition avant copie obligatoire.** Le lien bloc ↔ texte est conservé et indispensable ; aucune fidélité parfaite n'est promise.

**R10 — Choix du moteur sur le jeu de test propre, jamais sur des benchmarks publics.**

**R11 — Signature de code des binaires.** Sans certificat, Windows SmartScreen affiche un avertissement qui décourage l'installation.

**R12 — Interface découplée du moteur.** Une couche d'abstraction commune permet de changer de moteur OCR sans réécrire l'application.

**R13 — Un environnement virtuel par module.** Pas de mélange des dépendances entre modules.

**R14 — Tester les chemins avec accents et espaces dès le début.** Noms de fichiers/dossiers Windows en français.

**R15 — Prévoir la désinstallation et les mises à jour de modules.** Montée de version d'un module sans réinstaller le noyau.

## Étapes de validation de cette session

- Squelette importable : `python -c "import ocr_app"`
- CLI répond : `python -m ocr_app.cli --help`
- Tests smoke passent : `python -m pytest`
- Les 15 règles sont listées dans ce fichier.
