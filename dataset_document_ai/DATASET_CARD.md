# Document AI FR / AR — Dataset synthétique v1

Ce corpus constitue un point de départ reproductible pour le projet Document AI.
Il est entièrement synthétique et ne contient aucune facture client collectée.
Les noms, adresses, ICE, coordonnées, logos, cachets et signatures sont fictifs.
La présence de 15 chiffres ne certifie pas qu'un ICE existe. Le RIB de zéros est
un placeholder. Les taux de TVA servent à des cas arithmétiques synthétiques,
sans affirmer leur applicabilité fiscale à un produit.

## Composition

- 300 factures indépendantes, une page chacune.
- 100 en français, 100 en arabe et 100 bilingues FR/AR.
- 200 documents de développement, 100 de test.
- Dans le test : 33 FR, 33 AR, 34 bilingues (67 AR/bilingues).
- 12 profils de mise en page : 8 en développement, 4 réservés au test.
- 100 images propres, 100 simulations de scans, 100 simulations de photos.
- 300 PDF propres de référence, 300 images JPEG et 300 annotations JSON.
- 5 questions avec source par facture : total TTC, ICE, date, fournisseur et numéro.
- Une question sans preuve par facture pour tester l'abstention.

Chaque facture n'a qu'une seule image dans ce corpus. Les trois qualités ne
sont PAS trois documents indépendants provenant de la même facture.
Les images « photo » sont simulées (rotation, ombre, baisse de résolution,
flou, JPEG), et ne sont pas des prises de vue réelles. Elles n'incluent pas
encore la perspective, les plis, les occultations ou des fonds de bureau.

## Diversité

Le générateur varie les fournisseurs/clients, villes, ICE, dates, délais,
références, quantités, prix, nombres d'articles (1 à 6), taux synthétiques,
couleurs, polices, formes de logos et de cachets, position et présence des
marques. Le cachet et la signature ne sont volontairement pas systématiques.
Le téléphone fictif et le RIB de zéros sont fixes : ne pas présenter leurs
scores d'extraction comme une démonstration de généralisation.

Les 12 profils combinent 3 dispositions d'en-tête et 4 configurations des
blocs fournisseur/client, ainsi que des variations des totaux et signatures.
Ils partagent un même moteur HTML et une même famille graphique. Une séparation
par profil réduit la fuite directe des templates sans garantir une diversité
équivalente à celle de factures réelles de nombreux logiciels.

## Fichiers et sources

`data/dataset_v1/manifest.csv` liste les fichiers et leurs caractéristiques.
`dev/` et `test/` contiennent chacun `images/`, `pdf/`, `annotations/`.
`summary.json` donne les distributions observées.
`validation_report.json` contient le résultat des contrôles automatiques.

Les JPEG sont les entrées à utiliser pour comparer OCR et VLM. Les PDF sont des
références propres avec du texte embarqué. Pour tester une approche sur PDF
scanné, créer un PDF à partir du JPEG plutôt que lire le texte du PDF propre.
Ne jamais donner les annotations au modèle évalué.

Les coordonnées sont des rectangles `[x_min,y_min,x_max,y_max]` dans le JPEG
1588×2246 pixels, origine en haut à gauche. Les zones sont transformées lors de
la rotation. Les rectangles de cachet et signature comprennent l'emprise du
conteneur SVG/HTML et ne sont pas des masques précis de l'encre.
Les zones incluent le texte visible pour aider une évaluation OCR par champ.
`texte_reference` représente le texte du DOM HTML ; l'ordre de lecture diffère
selon le moteur OCR. Pour CER/WER, privilégier les blocs alignés et expliciter
la normalisation des espaces, chiffres, accents et diacritiques arabes.

ATTENTION : les zones de l'image dégradée ne correspondent pas directement au
PDF propre. Pour y reporter une zone, appliquer l'inverse de la rotation, puis
l'échelle (largeur PDF en points / 1588, hauteur PDF en points / 2246).
Un redressement ultérieur de l'image doit aussi conserver sa transformation.

## Contrôles réalisés et revue humaine

Le validateur vérifie les calculs, formats principaux, dates, existence des
fichiers, pages PDF, dimensions, limites des zones, sources des questions,
doublons exacts des images et séparation des profils/groupes dev/test.
Un échantillon couvrant les 12 profils et les langues a été inspecté visuellement.
Cela NE constitue PAS la vérification humaine exhaustive des 100 documents test.

Le cahier des charges exige un test annoté/vérifié manuellement. La colonne
`validation_humaine` des annotations est donc `a_verifier` et doit être mise à
jour après revue. `revue_humaine_test.csv` est fourni pour organiser cette revue.
Revoir chaque champ, zone et réponse attendue, notamment les documents dégradés.
Ne pas modifier les prompts ou paramètres après évaluation sur le test final.
Conserver les graines, modèles et paramètres des expériences.

## Limites scientifiques

Ce corpus sert à démarrer et benchmarker un prototype, pas à établir les
performances sur de vrais documents marocains. Ajouter ensuite des factures
réelles anonymisées avec accord, et rapporter leurs résultats séparément.
Les documents sont tous d'une page, imprimés, sans manuscrit libre, sans
multi-page ni montants volontairement incohérents. Les signatures sont des
paraphes vectoriels fictifs : ce n'est pas un dataset d'authentification.
Les questions portent sur des champs simples : une extension devra inclure
les lignes d'articles, les calculs et les questions entre plusieurs documents.
Les JSON automatiquement générés ne sont pas un corpus d'entraînement d'un
modèle à partir de zéro. Commencer avec des modèles préentraînés et une baseline.
