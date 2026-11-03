# DocumentAI V5 - lecture image et OCR

Cette version complete remplace le moteur applicatif; les scripts V6 historiques restent inchanges.
Les scores des anciens benchmarks ne constituent pas une evaluation de cette version.

## Installation Windows

Arretez le serveur (Ctrl+C). Sauvegardez votre dossier DocumentAI_Web.
Extrayez cette archive : elle contient un dossier DocumentAI_Web complet.
Copiez son contenu dans C:\Users\User\document-ai\DocumentAI_Web.
Conservez le dossier data existant : ne le supprimez pas.

Depuis C:\Users\User\document-ai :

```powershell
.\.venv-ocr\Scripts\python.exe -m pip install -r .\DocumentAI_Web\requirements_web.txt
ollama list
```

Le modele qwen2.5vl:3b doit apparaitre. S'il manque uniquement :

```powershell
ollama pull qwen2.5vl:3b
```

Ouvrez Ollama. Si son service ne tourne pas, lancez dans un autre terminal :

```powershell
ollama serve
```

Puis lancez l'application :

```powershell
.\.venv-ocr\Scripts\python.exe .\DocumentAI_Web\lancer_web.py
```

Ouvrez http://127.0.0.1:8000, Ctrl+F5.
La version indique « v5 vision ». Choisissez Nouveau document puis une facture.
Laissez DECOCHEE l'option « Traiter sans le modele local Ollama ».
Pour le premier essai sur la facture francaise, choisissez Francais.
Les anciens resultats ne sont pas recalcules : importez de nouveau le fichier.

## Ce qui change

Chaque page de facture est envoyee comme image au modele local avec les blocs OCR
et leurs positions. Le modele extrait en-tetes ET articles selon un schema commun,
sans exiger une disposition fixe. Les devis de dimensions deja reconnus gardent
leur route dediee. Les fichiers restent sur le poste; l'appel modele vise uniquement
127.0.0.1:11434. Les poids peuvent necessiter un telechargement initial. Le mode JSON simple est utilise pour eviter les erreurs de grammaire rencontrees avec le schema contraint.

Les sorties gardent les memes champs, avec null si inconnu. Adresse de facturation,
adresse de livraison, echeance et taux TVA sont ajoutes. Les articles distinguent
prix HT explicites et prix sans base fiscale indiquee.

Les pages sont regroupees de facon prudente : champs contradictoires => null et alerte;
les totaux de pages ne sont jamais additionnes. Les lignes repetees sont conservees.
Un PDF contenant plusieurs factures independantes doit etre separe en documents.

Les reponses brutes et images transmises sont archivees sous vision/page_N dans
le dossier du resultat, pour diagnostiquer les erreurs. Preuves et alertes sont exportees.
Un texte cite et un bloc OCR ne garantissent ni l'identite ni le role semantique.
Les lectures visuelles sans corroboration OCR sont conservees et signalees.
L'absence de preuve exploitable ou une valeur absente de sa citation provoque un rejet.
Les nombres et dates proposes sont controles sans recalculer les valeurs imprimees.

Si Ollama echoue ou si la reponse est invalide, le mode de secours est signale et
le resultat reste partiel. Ne pas confondre « traitement termine » avec « donnees validees ».
Sur les pages denses, des lignes peuvent manquer : verifier le document original.
Le temps de traitement et la memoire necessaires augmentent avec la vision.

## Validation de cette livraison

Tests logiciels : validation du schema, rejet de propositions non justifiees,
encodage image, branche multimodale avec reponse SIMULEE, panne du service et secours.
Les essais simules ne mesurent PAS la precision du modele.
La generation Qwen reelle et le rendu navigateur doivent etre confirmes sur votre PC.
Aucune garantie de reconnaissance de toute facture, aucune nouvelle precision mesuree.
Comparez plusieurs factures nouvelles au document original avant utilisation.
Commencez par Pro cleaners, puis Joanna Binet : elles servent maintenant au diagnostic,
pas a une evaluation independante. Gardez d'autres documents jamais corriges pour tester.

## Correctif V5 : propositions et rapprochement OCR separes
Le modele propose uniquement les donnees, sans generer de preuves. Python normalise
les dates et la devise, rapproche les libelles alignes, les regions de facturation/livraison
et les cellules d'articles. Les propositions non rapprochees restent null avec alerte.
Ce rapprochement heuristique peut aussi se tromper; controlez les preuves et l'original.
Les adresses proposees par le modele sont remplacees par les regions OCR lorsqu'elles
sont identifiees; sinon elles sont rejetees. Les valeurs des totaux sont lues, jamais calculees.
Les reponses brutes restent archivees. Aucune instruction imprimee ne doit etre executee.

Tests V5 : fixture geometrique synthetique (adresses separees, trois lignes, rejet d'un
prix non corrobore) et OCR reel Pro cleaners. La fixture n'est pas l'OCR reel Joanna Binet
et ne prouve pas une precision sur cette facture. La generation Qwen V5 reelle n'a pas
ete executee ici. Importer de nouveau la facture pour valider le resultat sur votre PC.

## Correction des libelles fusionnes et cellules de TVA
La V5 accepte les libelles et valeurs regroupes dans un meme bloc OCR lorsque
le separateur deux-points et le libelle sont explicites. Les synonymes de date
de facturation et numero de facture sont reconnus. TVA, taux et montant peuvent
occuper des cellules differentes sur la meme ligne. Les identites de modele
NOM SOCIETE et PRENOM NOM restent null; les adresses renseignees restent extraites.
Les telephones sont exclus des regions d'adresse.

Pour les nombres monetaires du type 48.938.25, le dernier separateur est traite
comme decimal uniquement si les groupes precedents ont trois chiffres et la partie
decimale deux chiffres. Une alerte conserve la graphie OCR exacte et demande une revue.
Ce traitement ne calcule jamais le TTC depuis HT/TVA et ne change aucun chiffre.
Des graphies comme 12.34.56 et 1,350 restent refusees comme ambigues.

Validation V5 : blocs OCR reels du modele facture-excel en MAD, propositions de
champs fournies comme fixture logicielle; numero, dates, deux adresses, TVA 15%,
HT/TTC et six lignes verifiees. Regression Joanna sur geometrie synthetique et
Pro cleaners sur OCR archive. Pas de nouvelle inference Qwen executee ici.
Ces documents sont desormais des cas de diagnostic, pas un jeu de test independant.

Installation : fermer le serveur, sauvegarder DocumentAI_Web, copier les fichiers
de cette archive dans le dossier existant, accepter le remplacement, CONSERVER data.
Aucune dependance supplementaire par rapport a la V4. Relancer avec lancer_web.py,
Ctrl+F5 et Nouveau document. Les anciens resultats restent inchanges.
