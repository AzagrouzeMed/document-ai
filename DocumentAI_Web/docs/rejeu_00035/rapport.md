# Rejeu local bilingue DEMO-2026-00035

Archive : C:\Users\Victus\Desktop\document-ai\DocumentAI_Web\data\622fdb6a76cb428ba5565adf55d80248\resultats\DOC-3A36DDAA90FE

Rejeu par process_pages avec les deux lectures FR/AR archivees, sans image source. PaddleOCR, Gemini, vision, sous-processus et reseau interdits par gardes.

Les 15 cellules numeriques sont distinctes : blocs 31-33, 36-38, 41-43, 46-48 et 51-53. Le bloc 28 est le seul en-tete numerique fusionne. Coordonnees et preuves completes dans rejeu.json.

Trois colonnes recurrentes : debuts x autour de 825, 966 et 1179. Le Q initial du bloc 28 (x=829) et le libelle arabe de quantite ancrent la premiere colonne. Le calcul departage ensuite prix et montant sans intervertir quantite et prix. Sans ancrage Q, cellules absentes ou doubles, alignement insuffisant, produit ambigu ou incoherent : null et alerte. Aucune valeur manquante calculee.

Le libelle du montant indique HT ; celui du prix ne permet pas de choisir HT/TTC. La valeur est donc stockee dans prix_unitaire ; prix_unitaire_ht et prix_unitaire_ttc restent null. Les controles standards par base fiscale restent non verifiables ; les cinq produits ci-dessous sont controles separement.

Comparaison visuelle avec source.jpg du traitement original :

| Article original | Quantite | Prix unitaire | Montant HT | Ecart produit |
|---|---:|---:|---:|---:|
| Souris sans fil | 7.0 | 238.01 | 1666.07 | 0.0 |
| Cartouche toner noir | 15.0 | 516.31 | 7744.65 | 0.0 |
| Cable reseau 5 m | 10.0 | 29.96 | 299.6 | 0.0 |
| Classeur a levier | 17.0 | 16.56 | 281.52 | 0.0 |
| Maintenance informatique | 13.0 | 570.71 | 7419.23 | 0.0 |

Les cinq triplets sont conformes a la facture originale. Les designations restent celles de l OCR : Souris sans fil, Cartouche toner noir et Cable reseau 5 m restent tronques ou degrades en francais. Aucune restauration depuis la reference visuelle.

En-tete et totaux identiques au traitement archive. Facture francaise de regression : sortie complete strictement identique aux trois articles reussis. Les cas 00042 FR/AR et 00065 sont couverts par les tests existants.

Des lignes et des informations fiscales restant partielles, l alerte generale devient "Tableau OCR local partiellement extrait". Les alertes detaillees et la revue des roles heuristiques restent presentes.

Aucun commit ni push. Les archives d origine sont preservees (SHA256 dans rejeu.json).
