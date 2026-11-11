# Recherche par mots dans les lectures OCR

FTS5 verifie : SQLite 3.49.1 dans .venv-documentai. Recherche lexicale, sans
modele, comprehension semantique ou reponse generee. Les quatre questions
structurees restent disponibles.

## Stockage et provenance

Le traitement archive deja ocr/page_N_fr.json et ocr/page_N_ar.json. On lit
rec_texts et page_index (zero-based), eventuellement dans une enveloppe res.
Le fichier texte_ocr.txt agrege n'est pas analyse pour deviner les langues.
Aucun OCR n'est relance.

Le worker utilise l'id retourne par sa sauvegarde et verifie traitement_id avant
d'ajouter les textes. Aucun rapprochement par numero de facture. Les anciennes
factures sans lien restent sans textes ; aucun import retrospectif automatique.

pages_document contient id, facture_id, numero_page (a partir de 1), langue
(fr/ar), texte_ocr, source relative. Cle unique (facture_id,numero_page,langue).
Les lectures FR/AR sont separees, les lots transactionnels, une lecture deja
archivee n'est pas remplacee lors d'une nouvelle sauvegarde.

initialiser_base() cree table, index FTS5 et triggers de facon reexecutable,
sans perte de factures, articles, corrections ou JSON. Elle est appelee a la
sauvegarde d'un traitement, jamais a la consultation. La base reelle n'a pas
ete migree pendant les tests. Une erreur d'archivage OCR ne masque pas la facture
ou l'extraction. Le statut sauvegarde.textes_ocr indique enregistre/absent/echec ;
le diagnostic technique reste dans stockage_ocr_erreur.log.

## Recherche et limites

GET /api/factures/{id}/recherche?q=... ouvre la base en lecture seule. Les
parametres SQL sont lies. Les mots Unicode sont extraits et cites dans MATCH.
Ponctuation et jokers sont ignores ; les operateurs saisis restent des mots
ordinaires. Tous les mots doivent apparaitre sur une meme lecture d'une page.

Les resultats affichent uniquement des extraits OCR, leur page, leur langue et
leur source relative avec textContent et dir=auto. Aucun chemin local ou JSON
brut. Messages distincts : Aucun texte OCR enregistre / Aucun passage trouve.
Facture absente : 404. Erreur de lecture : 503 public, sans diagnostic interne.

Limites : 200 caracteres, 20 mots, 20 passages maximum, environ 32 mots par
extrait. Mots entiers, aucune correction des erreurs OCR, synonymie, traduction,
lemmatisation ou analyse linguistique arabe specialisee. Unicode61 traite casse
et diacritiques latins. Les extraits peuvent etre coupes par une ellipse. Aucun
resultat ne prouve l'exactitude des donnees.

FTS5 absent : sauvegarde des textes maintenue, recherche avec un etat explicite
fts5_indisponible. Quand FTS5 revient, reexecuter initialiser_base() reconstruit
l'index depuis les textes si necessaire. Aucun paquet n'est installe. Un index
absent sur une ancienne base ne provoque pas de migration pendant un GET.

Les sources CLI ocr_cache sans langue connue ne sont pas devinees. Seules les
archives FR/AR nommees du resultat du traitement web sont importees.

## Tests

Depuis la racine, bases temporaires et extraction simulee :

```powershell
.\.venv-documentai\Scripts\python.exe -B -m unittest DocumentAI_Web.tests.test_stockage DocumentAI_Web.tests.test_stockage_web DocumentAI_Web.tests.test_factures_web DocumentAI_Web.tests.test_corrections_factures_web DocumentAI_Web.tests.test_questions_factures_web DocumentAI_Web.tests.test_recherche_document DocumentAI_Web.tests.test_validation_montants_web -v
node --check DocumentAI_Web/static/app.js
node DocumentAI_Web/tests/test_factures_ui.cjs
```

Tests : FR/AR, source/page, isolation, caracteres speciaux, absences, absence
d'ecriture en recherche, doublons, migration, JSON inchange, reprise d'index,
worker simule et fonctions existantes. Le DOM JavaScript est simule : verifier
l'apparence dans un navigateur.

## Demonstration sans OCR dans une base temporaire

Dans un nouveau terminal PowerShell, depuis la racine, ces commandes ajoutent
des textes fictifs uniquement dans une base temporaire separee :

```powershell
$env:DOCUMENTAI_DATA_DIR = Join-Path ([IO.Path]::GetTempPath()) ('documentai-recherche-' + [guid]::NewGuid().ToString('N'))
@'
import os
from pathlib import Path
from DocumentAI_Web import stockage as s
base=Path(os.environ['DOCUMENTAI_DATA_DIR'])/'document_ai.db'
s.initialiser_base(base)
f=s.enregistrer_facture({'numero_document':'DEMO-RECHERCHE','statut':'a_verifier','lignes':[]},base,traitement_id='demo-recherche')
s.enregistrer_pages_document(f,[
    dict(numero_page=1,langue='fr',texte_ocr='Facture fictive. Livraison de cafe et materiel.',source='ocr/page_1_fr.json'),
    dict(numero_page=1,langue='ar',texte_ocr='\u0641\u0627\u062a\u0648\u0631\u0629 \u062a\u0633\u0644\u064a\u0645 \u0627\u0644\u0642\u0647\u0648\u0629',source='ocr/page_1_ar.json'),
    dict(numero_page=2,langue='fr',texte_ocr='Conditions de garantie du materiel.',source='ocr/page_2_fr.json'),
],base,traitement_id='demo-recherche')
'@ | .\.venv-documentai\Scripts\python.exe -B -
.\.venv-documentai\Scripts\python.exe -B DocumentAI_Web/lancer_web.py --sans-navigateur
```

1. Ouvrir http://127.0.0.1:8000, actualiser Factures enregistrees.
2. Ouvrir DEMO-RECHERCHE et Rechercher dans le document.
3. Chercher cafe : francais, page 1, source ocr/page_1_fr.json.
4. Chercher تسليم : arabe, page 1.
5. Chercher garantie : francais, page 2.
6. Chercher introuvable : Aucun passage trouve.
7. Une facture sans textes donne Aucun texte OCR enregistre ; les textes d'une
   autre facture ne lui sont jamais attribues.
8. Verifier les quatre questions et le bouton Modifier.

Les sources de demonstration sont des etiquettes fictives. Pour les vrais
traitements, elles designent les archives produites. Ctrl+C arrete le serveur ;
fermer ce terminal abandonne la configuration temporaire. Les donnees habituelles
restent intactes. Les commandes ci-dessus n'ont pas ete lancees sur la base reelle.
