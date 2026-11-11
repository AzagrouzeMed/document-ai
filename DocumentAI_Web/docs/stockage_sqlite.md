# Stockage SQLite autonome

Le stockage reste un module autonome ; le worker web l'appelle apres extraction.
Bibliotheque standard uniquement pour SQLite.
La base par defaut est `DocumentAI_Web/data/document_ai.db`.
Chaque fonction accepte `chemin_base` pour cibler une base temporaire.
L'import du module et celui de l'essai manuel n'ouvrent aucune connexion.

## Tables

- `factures` : une fiche par document, identites, ICE, adresses, dates, devise,
  statut, totaux, chemin source et JSON brut. Exemple : facture FICTIF-001.
- `lignes_facture` : les articles relies par `facture_id`, ordonnes par
  `numero_ligne`. Exemple : clavier puis souris de FICTIF-001. HT, TTC et
  montants generiques restent dans des colonnes differentes.
- `validations` : historique des corrections, ancienne et nouvelle valeur,
  auteur facultatif, date UTC. Exemple : Alice remplace total_ht 12.30 par 20.50.

## Fonctions publiques

- `ouvrir_connexion(chemin_base=None)` : connexion SQLite avec cles etrangeres
  actives. L'appelant doit la fermer.
- `initialiser_base(chemin_base=None)` : cree les tables manquantes et ajoute
  les colonnes absentes. Retourne None. Reexecutable, aucune table supprimee.
- `enregistrer_facture(document, chemin_base=None, *, traitement_id=None,
  chemin_original=None)` : dict ou texte JSON vers identifiant entier. La base
  doit avoir ete initialisee. Les deux nouveaux arguments sont facultatifs et
  nommes ; les anciens appels restent compatibles.
- `lire_facture(id, chemin_base=None)` : dictionnaire avec `lignes`, ou None.
- `lire_articles(id, chemin_base=None)` : liste ordonnee, vide si aucun article.
- `lister_factures(chemin_base=None)` : liste de fiches, par id croissant.
- `ajouter_article(id, numero_ligne, designation, quantite, prix_unitaire,
  montant, chemin_base=None)` : ajout manuel, renvoie l'id de ligne.
- `corriger_champ(id, champ, valeur, auteur=None, chemin_base=None)` : renvoie
  l'id de correction. Les champs autorises sont declares explicitement dans
  `CHAMPS_MODIFIABLES`. Utiliser `date_facture` pour corriger la date.
- `lire_validations(id, chemin_base=None)` : historique ordonne des corrections.

## Nombres et absences

Les conversions utilisent Decimal. Les nombres sont relus sous forme de texte
exact : appeler Decimal(valeur) pour les manipuler, sans passer par float.
Quantites, prix et montants des articles ne sont jamais arrondis ni calcules.
Les absences restent NULL, y compris le statut absent. Aucun statut de
confirmation ne peut etre attribue par la fonction de correction.

Les totaux exacts restent en TEXT pour toutes les devises. Pour MAD/EUR seulement,
les trois anciennes colonnes en centimes sont aussi alimentees : 12.30 devient
1230. Une valeur 0.001 est refusee, sans arrondi, pour les totaux HT, TVA, TTC
et pour le total generique `total_document`. Ce dernier reste separe du HT/TTC. Les centimes sont NULL pour
les autres devises ou une devise absente ; aucune convention monetaire ne leur
est imposee. Changer la devise recalcule seulement cette representation technique
et echoue si un total existant est incompatible avec MAD/EUR.
Les booleens dans les champs numeriques et les nombres non finis sont refuses.

Pour conserver exactement espaces et notation du JSON, passer son texte original
au stockage. Les nombres sont alors analyses avec Decimal. Pour un dict deja
charge, un float a pu perdre de la precision avant le stockage : preferer des
chaines decimales ou Decimal. Les objets Decimal du dict sont serialises en
chaines exactes ; le dict d'origine n'est jamais modifie. Les champs supplementaires
et metadonnees de l'application restent accessibles dans `json_brut`.

## Transactions et migration

L'insertion de la facture et de tous ses articles est une seule transaction.
Si le deuxieme article est invalide, SQLite annule aussi le premier et la fiche.
Une correction et sa ligne d'historique sont egalement inseparables. Si
l'historique echoue, la valeur de la facture reste inchangee. Le verrou decriture
est acquis avant de lire l'ancienne valeur pour eviter un historique incoherent.
Le JSON brut ne change jamais lors d'une correction. Une correction ne confirme
pas la facture ; le controle arithmetique et la confirmation restent distincts.

La migration ajoute les colonnes manquantes et complete ces nouvelles colonnes
avec les JSON historiques, sans reecrire les colonnes deja presentes. Les anciens
centimes sont conserves. Si le JSON ne fournit pas un ancien total deja stocke en
centimes, ce total est repris en representation decimale, sans calcul de montant.
Pour l'ancien statut NOT NULL, la colonne est renommee `statut_legacy` puis
une colonne `statut` nullable est ajoutee et recoit les anciens statuts.
La colonne historique est conservee pour ne supprimer aucune donnee. Elle
n'est plus utilisee par les fonctions de stockage. Cette adaptation permet
aux nouvelles factures sans statut de conserver NULL.
Les donnees historiques restent inchangees lors des initialisations suivantes.
La migration elle-meme est transactionnelle. Aucun lancement de migration sur
la base reelle n'a ete effectue pendant cette intervention.

## Commandes PowerShell depuis la racine du projet

Tests autonomes, bases temporaires uniquement :

```powershell
.\.venv-documentai\Scripts\python.exe -B -m unittest discover -s DocumentAI_Web/tests -p test_stockage.py -v
```

Initialisation explicite de la base reelle, sans ajout de facture :

```powershell
.\.venv-documentai\Scripts\python.exe -B -m DocumentAI_Web.stockage
```

Essai manuel facultatif : attention, il ajoute une facture fictive dans la base
reelle a chaque lancement. Il n'a pas ete execute pendant les tests :

```powershell
.\.venv-documentai\Scripts\python.exe -B -m DocumentAI_Web.essai_stockage
```

Les fichiers SQLite et auxiliaires sont deja ignores par la regle Git `data/`.
## Integration au traitement web

Le trajet des donnees est le suivant :

1. `worker()` lance le traitement existant dans un sous-processus.
2. Le traitement produit `resultats/DOC-.../document.json` et ses controles.
3. Le worker lit les octets UTF-8 du JSON final, sans le reecrire ni repasser ses
   nombres par une serialisation float. `sauvegarder_resultat()` transmet ce
   texte a `stockage.enregistrer_facture()`.
4. Le stockage conserve la facture et tous ses articles dans une transaction.
   Le statut extrait reste intact et aucune validation humaine n'est creee.
5. Le worker termine le PDF et publie le resultat avec un bloc `sauvegarde`
   dans `statut.json`, aussi accessible par `GET /api/traitements/{id}` et
   par l'historique. Une erreur PDF est independante d'une erreur de stockage.

La base web est `DATA/document_ai.db`, soit `DocumentAI_Web/data/document_ai.db`
par defaut. L'eventuelle configuration `DOCUMENTAI_DATA_DIR` de l'application
s'applique aussi a cette base, ce qui permet d'isoler les tests.
L'initialisation est reexecutee au moment de sauvegarder. Un echec SQLite ne
bloque donc pas le demarrage du serveur ni la consultation des fichiers.
Aucune connexion SQLite globale : chaque operation ouvre et ferme sa connexion.

### Lien durable et prevention des doublons

La migration ajoute `factures.traitement_id` et un index UNIQUE. Les anciennes
factures sans traitement associe gardent NULL. L'identifiant web est le nom du
dossier UUID du traitement, different du `document_id` genere par l'extraction.
Le numero de facture n'est pas une cle unique : deux traitements differents
peuvent porter le meme numero.

Avant de chercher un traitement deja enregistre, l'enregistrement prend un
verrou d'ecriture avec BEGIN IMMEDIATE. S'il existe, son identifiant SQLite est
retourne sans remplacer ses donnees ni reinserer ses articles. Sinon, la facture
et ses articles sont inseres ensemble. L'index unique ajoute une garantie dans
la base, meme si plusieurs connexions tentent de sauvegarder simultanement.
Une reinitialisation ou la perte du retour dans statut.json ne perd pas ce lien.
Les GET de consultation, telechargement et rafraichissement ne sauvegardent rien.
Une nouvelle tentative doit passer par la meme fonction avec le meme traitement_id.

`chemin_original` recoit le chemin reel du fichier importe (`source.pdf`, etc.).
Le JSON brut garde son `fichier_source` produit par l'extraction. Le nom importe
par l'utilisateur est affiche dans l'API de resultat et dans le PDF, en memoire,
sans reecrire ce JSON. Aucun champ brut n'est ajoute pour porter les identifiants.

### Reussite et echec distincts de l'extraction

Exemple dans le statut public :

```json
"sauvegarde": {
  "etat": "enregistre",
  "facture_id": 3,
  "message": "Extraction enregistree. Les valeurs restent a verifier.",
  "diagnostic": null
}
```

Si SQLite ou la conversion d'une valeur echoue, le traitement reste `termine`
avec son `resultat_statut` reel, et `sauvegarde.etat` vaut `echec`.
Le message est affiche dans les alertes existantes. Le diagnostic est conserve
dans `sauvegarde.diagnostic` et dans `stockage_erreur.log` si ce fichier peut
etre ecrit. JSON, controles, preuves, texte, original et eventuel PDF restent
accessibles. Meme un echec du journal ne masque pas l'extraction.

Il n'y a pas de nouveau formulaire, d'assistant ou d'endpoint de relance.
Les anciens resultats ne sont pas importes automatiquement lors des GET.
Le formulaire de montants preexistant n'est pas modifie et n'est pas raccorde
aux corrections SQLite pendant cette etape.

### Verification sans OCR ni API

Depuis la racine du projet, executer les trois suites ciblees en une commande :

```powershell
.\.venv-documentai\Scripts\python.exe -B -m unittest DocumentAI_Web.tests.test_stockage DocumentAI_Web.tests.test_stockage_web DocumentAI_Web.tests.test_validation_montants_web -v
```

Les tests d'integration fabriquent un JSON fictif dans un dossier temporaire et
remplacent le sous-processus d'extraction ainsi que l'export PDF. Le client ASGI
appelle l'application en memoire, sans requete HTTP vers un serveur.
Ils verifient les articles, le statut partiel, les decimaux longs, les fins de
ligne du JSON, le lien original, les GET repetes, les tentatives simultanees,
les numeros identiques de traitements distincts, les echecs SQLite, la reprise
et l'independance des erreurs PDF. Les 17 tests de stockage restent applicables.
La base reelle n'est ni initialisee ni modifiee par ces tests.

Pour verifier un traitement apres utilisation normale de l'application, consulter
son statut public : `sauvegarde.etat` et `sauvegarde.facture_id` indiquent le
resultat de stockage. `lire_facture(facture_id)` permet ensuite de verifier le
lien `traitement_id`, le statut, le chemin original, les articles et le JSON brut.

