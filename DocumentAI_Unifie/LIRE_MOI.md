# Document AI - traitement commun experimental 1

Un seul point d'entree pour l'utilisateur : `traitement_document.py`.
Les fichiers dans `moteur` sont des modules internes, pas des commandes a choisir.
La V6 evaluee du projet historique et ses resultats restent inchanges.

## Installation Windows

Extraire le dossier `DocumentAI_Unifie` dans `C:\Users\User\document-ai`.
Conserver son sous-dossier `moteur`. Utiliser l'environnement `.venv-ocr` existant.
Aucune mise a jour de PaddleOCR n'est requise. Pour le traitement financier avec
modele, ouvrir Ollama avec `qwen2.5vl:3b` deja installe.

Depuis PowerShell, dans `C:\Users\User\document-ai` :

```powershell
.\.venv-ocr\Scripts\python.exe .\DocumentAI_Unifie\traitement_document.py --input .\demo\devis_demo.pdf
```

Cette commande accepte PDF (toutes les pages), PNG, JPEG, BMP, TIFF ou WebP.
Le mode automatique tente les lectures FR et AR; il est plus lent que FR seul.
La decision de lecture repose sur une heuristique de caracteres, pas sur la
reference correcte. Une langue peut etre forcee avec `--lang fr` ou `--lang ar`.

### Essai rapide sans nouvel OCR

```powershell
.\.venv-ocr\Scripts\python.exe .\DocumentAI_Unifie\traitement_document.py --ocr-cache .\demo\devis_page_1_ocr.json .\demo\devis_page_2_ocr.json
```

Un OCR archive par page, pour le meme document, avec `page_index` (zero pour la
premiere page). Ce mode ne lance pas PaddleOCR. Les tables financieres peuvent
encore appeler Ollama; ajouter `--sans-llm` pour une verification par regles.

## Resultats

`DocumentAI_Unifie\results\documents\DOC-...` contient :

- `document.json` : identifiant unique, type, champs, lignes et pages.
- `controles.json` : statut, alertes et verification humaine requise.
- `preuves.json` : blocs OCR et page des valeurs retenues.
- `texte_ocr.txt` : toutes les lectures conservees, y compris si le tableau est inconnu.
- `execution.json` : moteur choisi par page, mode demande et empreintes des modules.
- `ocr` : lectures nouvellement produites; `pages` : journaux des extractions financieres.

`numero_document` remplace les noms divergents numero_facture/numero_devis.
Les lignes peuvent porter quantite, prix_unitaire_ht, montant_ht, hauteur_cm,
largeur_cm et leur page. Une valeur non extraite reste `null`.

## Couverture et limites

- Integration des tableaux financiers V6 et du format Wuro de demonstration.
- Devis a colonnes designation/hauteur(cm)/largeur(cm)/quantite, avec regroupement
  multipage et TTC explicitement indique comme dans le devis FK Alu Group.
- Champs clairement libelles et texte brut preserves pour les structures inconnues.
- Aucun montant deduit d'une dimension, aucun taux de TVA suppose.
- Des totaux differents entre pages restent un conflit : ils ne sont ni sommes ni
  remplaces automatiquement par le dernier montant. Les lignes identiques ne sont
  pas dedupliquees automatiquement.
- Les tableaux inconnus, inclines, sans en-tetes, les remises et les tableaux complexes
  ne sont pas garantis. Les champs arabes non reconnus par le moteur existant restent partiels.
- Un regroupement financier multipage n'est pas une extraction generale des documents
  contenant plusieurs factures; fournir un seul document commercial a la fois.
- `a_verifier` signifie que les controles ont produit une sortie a relire, pas que
  tous les champs sont corrects. `partiel` indique notamment une structure refusee.
- Les scores de la V6 ne sont pas ceux de cette chaine unifiee. Pas de nouvel
  entrainement et pas de promesse de couvrir n'importe quel document.

## Verification locale realisee

Sur les OCR fournis : devis de 2 pages -> 36 lignes et TTC 9424 MAD;
Wuro -> 3 lignes, champs d'en-tete et TVA totale 639.52 EUR.
Un exemple de texte sans tableau reste partiel; un cache de page duplique est refuse.
Ces verifications ont ete faites sans nouvelle inference PaddleOCR ou Ollama.
Le lancement complet sur les modeles installes reste a verifier sur le PC Windows.

## Application a construire ensuite

Python pour le moteur; FastAPI pour le service local; HTML/CSS/JavaScript sans React
pour l'interface. `traiter_document()` est appelable par un service en arriere-plan.
Le traitement est bloquant et doit etre execute dans une tache de travail, pas
directement dans une route asynchrone. Prevoir une file/limite de concurrence pour
les modeles. Une fenetre desktop pywebview pourra afficher l'interface locale.
Interface web, executable desktop, export PDF recapitulatif et authentification
ne sont pas inclus dans cette premiere livraison du moteur commun.
