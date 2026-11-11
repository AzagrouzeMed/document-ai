# Consultation des factures enregistrees

La sauvegarde automatique existante du worker est conservee. Les nouveaux GET
ne lancent aucune extraction, initialisation ou migration SQLite.

- GET /api/factures : id, numero_document, fournisseur, client, date_facture,
  devise, statut.
- GET /api/factures/{id} : champs metier et lignes ordonnees ; 404 si absente.
- Une base absente donne une liste vide sans creer de fichier ou de dossier.
- Une base illisible, invalide ou incompatible donne 503 avec un message public.
- Le stockage existant accepte lecture_seule=True : connexion mode=ro et
  query_only. Les appels existants restent compatibles.
- Chemins locaux, JSON brut et diagnostics ne sont transmis par aucun de ces GET.

Parcours : SQLite -> stockage.py -> routes FastAPI -> fetch JavaScript ->
textContent. Les champs absents sont affiches par un tiret cadratin. Les cellules
utilisent dir=auto pour les textes francais et arabes. Les nombres SQLite sont
conserves sous forme de texte, sans arrondi dans cette section.

Tests depuis la racine (bases temporaires uniquement) :

```powershell
.\.venv-documentai\Scripts\python.exe -B -m unittest DocumentAI_Web.tests.test_stockage DocumentAI_Web.tests.test_stockage_web DocumentAI_Web.tests.test_factures_web DocumentAI_Web.tests.test_validation_montants_web DocumentAI_Web.tests.test_validation_donnees -v
node --check DocumentAI_Web/static/app.js
node DocumentAI_Web/tests/test_factures_ui.cjs
```

Verification manuelle sans extraction :

1. Lancer le serveur :
   .\.venv-documentai\Scripts\python.exe -B DocumentAI_Web/lancer_web.py --sans-navigateur
2. Ouvrir http://127.0.0.1:8000 et descendre a Factures enregistrees.
3. Cliquer sur Actualiser. Sans facture deja stockee, verifier le message vide.
4. Avec des factures deja stockees, cliquer sur un numero (ou le tiret si absent).
   Verifier champs, articles, accents, arabe et valeurs absentes.
5. Dans les outils reseau du navigateur, verifier les GET /api/factures et
   /api/factures/{id}, sans POST de traitement. /api/factures/999999999 doit
   retourner 404 si cet identifiant est absent.
6. Couper le serveur puis cliquer sur Actualiser : verifier le message de lecture
   impossible. Relancer le serveur pour reprendre la consultation.

Pour essayer une base vide sans toucher aux donnees habituelles, definir
DOCUMENTAI_DATA_DIR vers un dossier temporaire avant de lancer le serveur.
Aucun exemple fictif n'est ajoute a la base reelle par cette fonctionnalite.

Limites : pas de pagination, de reprise de sauvegarde ni d'import
retroactif des anciens fichiers d'extraction. Un ancien schema incompatible
necessite une initialisation explicite hors consultation. Les corrections de
montants preexistantes dans les fichiers du traitement ne sont pas synchronisees
avec SQLite par cette fonctionnalite. Le test JavaScript utilise un DOM simule ;
il ne remplace pas une verification visuelle dans un navigateur.


## Correction des en-tetes et historique

Dans le detail, Modifier ouvre un formulaire pour un champ a la fois :
fournisseur, client, ICE fournisseur/client, numero du document, date de facture,
echeance et les trois adresses. Le serveur publie la liste autorisee, issue d'un
sous-ensemble explicite des champs autorises par le stockage. Les montants
conservent le formulaire preexistant. Les articles restent en consultation.

POST /api/factures/{id}/corrections accepte uniquement champ et nouvelle_valeur,
avec la protection POST existante. Le serveur controle les types, les longueurs,
les caracteres de controle et les dates reelles au format AAAA-MM-JJ. Les ICE
restent du texte pour conserver les zeros initiaux ; aucun controle externe
sur leur existence n'est effectue. Une valeur vide, blanche ou null devient NULL.
Les autres textes sont limites a 500 caracteres, les adresses a 4000.

La route appelle stockage.corriger_champ() : correction et historique sont une
transaction unique. Le statut, le JSON brut et les articles restent inchanges.
Le nom de champ est controle par des listes internes avant toute construction
SQL ; les valeurs passent par des parametres SQLite.

GET /api/factures/{id}/historique appelle lire_validations(lecture_seule=True).
Les anciennes/nouvelles valeurs et dates sont affichees via textContent, avec
dir=auto et un tiret cadratin pour NULL. L'historique public omet les eventuelles
corrections techniques de chemins. Une erreur de lecture est signalee sans
masquer le detail de la facture. Les erreurs SQLite internes ne sont pas publiees.

Verification manuelle complementaire :

1. Ouvrir une facture deja enregistree, puis cliquer sur Modifier.
2. Choisir Fournisseur, saisir une nouvelle valeur, enregistrer.
3. Verifier le champ dans le detail, la liste et l'historique (avant/apres/date).
4. Recharger la page et rouvrir la facture : la correction doit rester presente.
5. Modifier Client, laisser la valeur vide et enregistrer : affichage par un tiret.
6. Essayer une date impossible (2026-02-30) : refus clair, saisie conservee.
7. Verifier que le statut reste partiel ou a verifier et que les articles restent
   accessibles en consultation.

Tests complementaires sur base temporaire :

```powershell
.\.venv-documentai\Scripts\python.exe -B -m unittest DocumentAI_Web.tests.test_stockage DocumentAI_Web.tests.test_stockage_web DocumentAI_Web.tests.test_factures_web DocumentAI_Web.tests.test_corrections_factures_web DocumentAI_Web.tests.test_validation_montants_web -v
node DocumentAI_Web/tests/test_factures_ui.cjs
```

Une correction SQLite ne reecrit pas les fichiers JSON/PDF de l'extraction ni
les corrections de montants du traitement. Le formulaire corrige un champ a la
fois et n'attribue aucun statut de validation globale. Le test JavaScript utilise
un DOM simule, sans verification visuelle dans un navigateur.


## Questions structurees sur une facture

Le detail propose quatre boutons : fournisseur, client, date de facture et total
TTC. Il s'agit d'une consultation structuree de SQLite, sans modele ni RAG.

GET /api/factures/{id}/questions/{code_question} accepte uniquement les codes
fournisseur, client, date_facture et total_ttc. Une autre question retourne 422 ;
une facture absente retourne 404. Une erreur de lecture retourne le message public
503 existant, sans diagnostic interne.

La route reutilise exiger_facture_enregistree(), lire_factures_enregistrees() et
stockage.lire_facture(lecture_seule=True). Chaque clic relit les valeurs SQLite
actuelles, y compris apres correction. Aucune initialisation, migration,
sauvegarde, validation ni extraction n'est declenchee.

La reponse contient le numero de facture, le statut et la valeur demandee. Une
valeur absente donne exactement Information non disponible. Le total TTC est
repris sans arrondi, avec sa devise si disponible ; il n'est jamais remplace par
le total generique ou recalcule a partir des articles. Les statuts partiel et
a_verifier donnent un avertissement explicite. Un statut absent est signale.
Chemins locaux, JSON brut et diagnostics ne sont pas exposes.

JavaScript affiche la reponse avec textContent dans une zone dir=auto. Changer de
facture ou poser une nouvelle question empeche une ancienne reponse en retard de
remplacer l'affichage actuel. L'interface montre les erreurs sans details internes.

Tests depuis la racine, bases temporaires uniquement :

```powershell
.\.venv-documentai\Scripts\python.exe -B -m unittest DocumentAI_Web.tests.test_stockage DocumentAI_Web.tests.test_stockage_web DocumentAI_Web.tests.test_factures_web DocumentAI_Web.tests.test_corrections_factures_web DocumentAI_Web.tests.test_questions_factures_web DocumentAI_Web.tests.test_validation_montants_web -v
node --check DocumentAI_Web/static/app.js
node DocumentAI_Web/tests/test_factures_ui.cjs
```

Scenario de demonstration (serveur local lance avec la commande ci-dessus) :

1. Ouvrir une facture deja enregistree dans Factures enregistrees.
2. Dans Poser une question sur cette facture, cliquer sur les quatre questions.
   Verifier la valeur demandee, le numero, le statut et l'avertissement de revue.
3. Pour un champ absent, verifier Information non disponible.
4. Avec Modifier, corriger le fournisseur et enregistrer. Reposer la question :
   la nouvelle valeur doit etre utilisee. Cette etape ecrit une correction ; les
   questions elles-memes restent sans ecriture.
5. Essayer une valeur francaise ou arabe et verifier son affichage.
6. Dans les outils reseau du navigateur, verifier que les questions font seulement
   des GET vers /api/factures/{id}/questions/{code}, sans traitement ni appel externe.

Limites : quatre questions predeterminees, une facture a la fois, aucune question
libre, recherche documentaire ou verification externe. La reponse reflete les
valeurs SQLite, pas une preuve de leur exactitude. Les tests JavaScript utilisent
un DOM simule ; l'apparence reste a verifier manuellement dans un navigateur.
