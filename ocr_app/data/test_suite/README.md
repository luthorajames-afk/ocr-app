# Jeu de documents de test — Phase 0

Ce dossier contient le jeu de test synthétique de l'application OCR hors ligne :
**42 documents** (69 pages) accompagnés de leur manifest et de leur vérité
terrain. Il couvre les factures françaises, les formulaires papier, les
documents multi-colonnes longs, les scans dégradés et les PDF mixtes
natif + scanné.

Tous les documents sont **synthétiques** : aucun contenu personnel réel n'est
utilisé. Tous contiennent des accents, des cédilles et des caractères français
(`é è à ç œ É À Ç`), afin de tester l'encodage et la reconnaissance.

## Chiffres clés

| Indicateur | Valeur |
|---|---:|
| Documents | 42 |
| Pages | 69 |
| Difficulté facile / moyenne / difficile | 12 / 15 / 15 |
| PDF / JPG / PNG | 40 / 1 / 1 |
| Pages natives (texte extractible) | 51 |
| Pages scannées (image seule) | 18 |
| Blocs de vérité terrain | 1504 |
| Tableaux annotés | 32 (dans 29 documents) |
| Volume total | environ 67 Mo (polices TrueType embarquées) |

## Arborescence

```text
data/test_suite/
├── manifest.json        # manifest complet et vérité terrain
├── facts_generator.py   # générateur documenté (reproductible)
├── README.md            # ce fichier
├── facts/               # 12 factures (PDF natifs)
├── forms/               # 9 formulaires (PDF natifs)
├── scans/               # 9 scans dégradés (7 PDF, 1 JPG, 1 PNG)
├── mixed/               # 12 PDF multi-colonnes et PDF mixtes
└── truth/               # 42 fichiers .txt, un par document
```

## Régénérer le jeu

Le générateur ne dépend que des bibliothèques déjà installées :
`pypdfium2`, `pillow`, `numpy`.

```bash
cd ocr_app
python data/test_suite/facts_generator.py
```

Options utiles :

```bash
python data/test_suite/facts_generator.py --seed 123     # autre variante
python data/test_suite/facts_generator.py --quiet        # sans progression
python data/test_suite/facts_generator.py --validate-only # vérifie l'existant
```

La génération supprime et recrée uniquement `facts/`, `forms/`, `scans/`,
`mixed/`, `truth/` et `manifest.json`. Elle ne modifie jamais `src/ocr_app`.
Le processus se termine avec un code non nul si une vérification échoue.

## Couverture

| Dossier | Documents | Pages | Facile | Moyen | Difficile | Thème |
|---|---:|---:|---:|---:|---:|---|
| `facts/` | 12 | 14 | 3 | 5 | 4 | `facture` |
| `forms/` | 9 | 9 | 3 | 3 | 3 | `formulaire` |
| `scans/` | 9 | 9 | 2 | 3 | 4 | `scan` |
| `mixed/` | 12 | 37 | 4 | 4 | 4 | `document_multicolonne`, `mixte` |

Les six documents multi-colonnes longs font 2 à 4 pages de corps, en 2 ou
3 colonnes, avec titres de section, pieds de page et, pour les plus difficiles,
une annexe tabulaire. Les six PDF mixtes alternent pages natives et pages
scannées.

## Schéma du manifest

Le fichier `manifest.json` contient une clé racine `items`. Chaque item reprend
le format demandé et ajoute quelques métadonnées utiles à l'évaluation :

```json
{
  "id": "fact_001",
  "filename": "facts/fact_001.pdf",
  "filetype": "pdf",
  "difficulty": "easy",
  "theme": "facture",
  "subtype": "classic",
  "source": "synthetic",
  "language": "fr",
  "orientation": "portrait",
  "pages": 1,
  "page_dimensions": [
    {"page": 1, "width": 1240, "height": 1754, "orientation": "portrait"}
  ],
  "page_modes": ["native"],
  "rotation_degrees": [0.0],
  "expected_blocks": [
    {"type": "title", "text": "FACTURE", "bbox": [851, 59, 1148, 106],
     "role": "document_title", "page": 1}
  ],
  "expected_tables": true,
  "table_count": 1,
  "tables": [
    {"bbox": [92, 402, 1148, 577], "rows": 6, "cols": 6,
     "header": true, "grid": [["Désignation", "Unité", "..."]], "page": 1}
  ],
  "text_file": "truth/fact_001.txt",
  "has_accents": true,
  "notes": "..."
}
```

Champs principaux :

- `id` : identifiant stable (`fact_*`, `form_*`, `scan_*`, `mixed_*`).
- `filename` : chemin relatif au dossier `test_suite/`.
- `filetype` : `pdf`, `jpg` ou `png`.
- `difficulty` : `easy`, `medium` ou `hard` (définition ci-dessous).
- `theme` : `facture`, `formulaire`, `scan`, `document_multicolonne` ou `mixte`.
- `expected_blocks` : blocs dans l'ordre de lecture humain (colonne par
  colonne pour les documents multi-colonnes). Chaque bloc a un `type`
  (`title`, `header`, `paragraph`, `table`, `footer`), un `text`, une `bbox`
  et un numéro de `page`. Le champ `role` précise l'usage (titre, champ de
  formulaire, total, signature, etc.). Les cases à cocher portent
  `checked: true/false`.
- `expected_tables` : vrai si le document contient au moins un tableau.
- `tables` : grille texte complète (`grid`), nombre de lignes/colonnes, bbox.
- `page_modes` : `native` ou `scan` pour chaque page ; permet de vérifier
  qu'une page native expose bien du texte et qu'une page scannée n'en expose
  pas.
- `rotation_degrees` : rotation appliquée à chaque page (0 pour les pages
  natives).
- `text_file` : vérité terrain texte lisible.
- `notes` : description du contenu, de la mise en page et des dégradations.

### Coordonnées

Les bboxes sont exprimées en **pixels**, origine en **haut à gauche**, sur le
rendu à **150 DPI** :

- page A4 portrait : `1240 × 1754` px ;
- page A4 paysage : `1754 × 1240` px.

Pour les scans, les bboxes sont transformées après rotation (boîte englobante
alignée sur les axes), puis rognées aux bords de la page. Une évaluation
spatiale doit donc tolérer une marge de quelques pixels et l'effet de la
rotation sur les boîtes.

### Vérité terrain texte

Chaque fichier `truth/<id>.txt` contient les textes des blocs dans l'ordre de
lecture, séparés par un saut de ligne. Les pages sont séparées par un
**form feed** (`\f`, code 12). Pour les tableaux, une ligne de texte représente
une ligne du tableau, les cellules étant séparées par `" | "` :

```text
Désignation | Unité | Qté | P.U. HT | TVA | Montant HT
Écran 27 pouces LED | unité | 2 | 189,00 € | 20 % | 378,00 €
```

Normalisations conseillées avant comparaison (CER, alignement) :

- espaces insécables (`U+00A0`, `U+202F`) → espace ordinaire ;
- trait d'union conditionnel (`U+00AD`) → trait d'union ou suppression ;
- espaces multiples et sauts de ligne → espace unique.

## Définition des difficultés

| Niveau | Caractéristiques |
|---|---|
| `easy` | Document natif propre ou scan peu dégradé ; une colonne ou tableau simple ; corps ≥ 10 pt ; rotation ≤ 1°. |
| `medium` | En-tête sur deux colonnes, tableau de 9 à 12 lignes, document de presse en 2–3 colonnes ; rotation 1–3° ; contraste 0,68–0,90 ; JPEG 45–78. |
| `hard` | Formulaire très dense sur 3 colonnes, facture de 18 à 24 lignes, petit corps 7–8,6 pt ; rotation 4–8° ; contraste 0,38–0,52 ; flou, bruit, éclairage inégal, tampon, basse résolution ou PNG bruité. |

Les difficultés sont équilibrées par dossier autant que par thème :
3/5/4 pour les factures, 3/3/3 pour les formulaires, 2/3/4 pour les scans et
4/4/4 pour les documents multi-colonnes et mixtes.

## Dégradations des scans

| ID | Rotation | Dégradations principales |
|---|---:|---|
| `scan_001` | +0,4° | bruit léger, JPEG q=82 |
| `scan_002` | −0,8° | JPEG q=78, sortie JPG |
| `scan_003` | +2,2° | contraste 0,72, flou 0,6 px, bruit, JPEG q=55 |
| `scan_004` | −2,8° | éclairage inégal, contraste 0,68, poussières, JPEG q=48 |
| `scan_005` | +1,7° | page de revue 2 colonnes, contraste 0,78, JPEG q=45 |
| `scan_006` | +4,5° | facture dense, contraste 0,45, flou, tampon DUPLICATA, JPEG q=30 |
| `scan_007` | −6,0° | contraste 0,40, vignettage, éclairage inégal, bruit, JPEG q=28 |
| `scan_008` | +8,0° | basse résolution (×0,62), niveaux de gris, flou 1,2 px, JPEG q=30 |
| `scan_009` | −4,2° | niveaux de gris, contraste 0,42, bruit, poussières, tampon, PNG |

Les angles sont donnés dans le sens anti-horaire visuel. Les poussières, le
bruit gaussien, les rayures et les tampons sont générés avec la graine du
générateur, donc reproductibles.

## PDF natifs, scannés et mixtes

- Les pages **natives** sont de vrais PDF vectoriels : les textes sont ajoutés
  via l'API d'édition de PDFium et les polices TrueType Windows (Arial, Times
  New Roman, Courier) sont **embarquées** dans le fichier. Le texte est
  sélectionnable et extractible, accents compris.
- Les pages **scannées** sont des images uniques encodées en JPEG dans le PDF
  (ou en JPG/PNG pour `scan_002` et `scan_009`). Elles ne contiennent
  volontairement aucune couche texte.
- Les PDF **mixtes** assemblent les deux types de pages ; `page_modes` indique
  le type de chaque page.

Remarques d'extraction PDFium, utiles pour l'évaluation :

- les espaces peuvent être extraits en espace insécable (`U+00A0`) et certains
  traits d'union en trait d'union conditionnel (`U+00AD`) ;
- sur un document multi-colonnes, l'extraction brute peut entrelacer les
  colonnes ligne par ligne. La vérité terrain, elle, suit l'ordre de lecture
  humain (colonne par colonne). Il faut donc comparer le contenu et les
  positions, pas l'ordre brut du flux extrait.

## Validation

Le générateur valide automatiquement le jeu après production. La commande
suivante revalide un jeu existant sans le régénérer :

```bash
python data/test_suite/facts_generator.py --validate-only
```

Vérifications effectuées :

- 30 à 50 items, identifiants et noms de fichiers uniques ;
- au moins 8 items par niveau de difficulté et présence des trois formats ;
- existence et taille non nulle de chaque document et de chaque fichier truth ;
- bbox dans les limites de la page, blocs non vides, cohérence
  `expected_tables` / `tables` ;
- nombre de pages et dimensions des PDF (tolérance 1,5 pt) ;
- présence du texte natif page par page (rappel par mots ≥ 75 % par bloc) ;
- absence de texte extractible sur les pages scannées ;
- présence effective des accents dans les documents annoncés.

Résultat attendu sur le jeu livré :

```text
Validation : 42 documents, 69 pages, 1100/1100 blocs natifs retrouvés
             (mots : 17780/17780).
Terminé : 42 documents, 69 pages, difficultés {'easy': 12, 'hard': 15, 'medium': 15}.
```

## Limites connues

- Documents entièrement synthétiques : pas d'écriture manuscrite, pas de
  formulaires réellement remplis à la main, pas de photos de documents.
- Les longs documents réutilisent un corpus de 18 paragraphes ; le contenu se
  répète d'une page à l'autre. Cela ne change rien à la vérité terrain, mais
  ce n'est pas un corpus journalistique réel.
- Les polices dépendent de Windows (`Arial`, `Times New Roman`, `Courier`) ;
  le générateur se rabat sur les polices standard PDF si un fichier TTF
  manque. Les PDF déjà générés restent lisibles.
- Les bboxes sont exactes à quelques pixels près (arrondis de rendu, métriques
  de police, rotation). Une évaluation IoU doit utiliser une tolérance.
- Le texte des tableaux est volontairement linéarisé avec `" | "` ; la grille
  structurée complète est dans `tables[].grid` pour un calcul TEDS.
- Le jeu ne mesure pas les performances (temps, mémoire) : il sert à comparer
  la qualité de reconnaissance, notamment le CER et le TEDS en phase 2.

## Utilisation en phase 2

1. Charger `manifest.json` et parcourir `items`.
2. Ouvrir `data/test_suite/<filename>` avec le mode OCR à évaluer.
3. Comparer les blocs produits aux `expected_blocks` (texte et bbox), et
   calculer le CER sur `truth/<id>.txt`.
4. Pour les tableaux, comparer `tables[].grid` au résultat structuré du moteur
   (TEDS).
5. Utiliser `page_modes` pour vérifier le respect de la règle R8 : un PDF
   natif doit être traité par extraction de texte, pas par OCR d'image.

Le générateur est reproductible : une même graine (`--seed`) produit les mêmes
textes, les mêmes dégradations et les mêmes bboxes.
