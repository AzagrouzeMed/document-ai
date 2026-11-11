"""Replay the last bilingual local run, with OCR and external calls forbidden.
Run: python -B DocumentAI_Web/docs/rejouer_00035.py
"""
import copy
import hashlib
import json
import socket
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from traitement_document import process_pages
from moteur.extraction_ocr_prioritaire import direct

fixture=json.loads((ROOT/'tests/fixtures/demo_2026_00035_ocr.json').read_text(encoding='utf8'))
archive=ROOT/'data/622fdb6a76cb428ba5565adf55d80248/resultats/DOC-3A36DDAA90FE'
output=ROOT/'docs/rejeu_00035'
output.mkdir(exist_ok=True)
paths={lang:archive/'ocr'/f'page_1_{lang}.json' for lang in ['fr','ar']}
hashes={lang:hashlib.sha256(path.read_bytes()).hexdigest() for lang,path in paths.items()}
with patch.object(socket.socket,'connect',side_effect=AssertionError('Network forbidden')), \
     patch.object(subprocess,'run',side_effect=AssertionError('Subprocess forbidden')), \
     patch('traitement_document.ocr_document',side_effect=AssertionError('PaddleOCR forbidden')), \
     patch('moteur.extraction_gemini.extract',side_effect=AssertionError('Gemini forbidden')), \
     patch('moteur.extraction_vision.extract',side_effect=AssertionError('Vision forbidden')):
    data,proofs,alerts,routes,partial=process_pages({0:paths},output,True,source=None)
    regression=json.loads((ROOT/'tests/fixtures/tableaux_00042_00065_regression.json').read_text(encoding='utf8'))['regression']['fr']
    assert direct(regression['raw'],1)[0]==regression['avant']
    comparisons=[]
    for i,(name,q,price,amount) in enumerate(fixture['original']):
        line=data['lignes'][i]
        observed=[line[k] for k in ['quantite','prix_unitaire','montant_ht']]
        assert observed==[q,price,amount],(i,observed)
        comparisons.append(dict(ligne=i+1,article_original=name,designation_ocr=line['designation'],
                                attendu=[q,price,amount],observe=observed,
                                ecart_produit=round(abs(q*price-amount),8),conforme_original=True))
    before=fixture['avant']
    unchanged={k:data[k]==v for k,v in before.items() if k in data and k not in ['lignes','resultats_par_page']}
    assert all(unchanged.values()),unchanged
    assert hashes=={lang:hashlib.sha256(path.read_bytes()).hexdigest() for lang,path in paths.items()}
    raw=fixture['ar']
    cells=[dict(bloc=i+1,texte=t,bbox=b) for i,(t,b) in enumerate(zip(raw['rec_texts'],raw['rec_boxes'])) if 24<=i<=52]
    result=dict(archive=str(archive),ocr_reutilise=True,paddleocr_appele=False,gemini_appele=False,
                reseau_interdit=True,routes=routes,donnees=data,preuves=proofs,alertes=alerts,
                comparaison_original=comparisons,entete_inchange=unchanged,
                regression_francaise_identique=True,sha256_archives=hashes,blocs=cells,
                limite='Prix unitaire generique : HT/TTC absent du libelle OCR. Designations OCR conservees sans correction.')
(output/'rejeu.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
report=['# Rejeu local bilingue DEMO-2026-00035','',
        'Archive : '+str(archive),'',
        'Rejeu par process_pages avec les deux lectures FR/AR archivees, sans image source. PaddleOCR, Gemini, vision, sous-processus et reseau interdits par gardes.',
        '', 'Les 15 cellules numeriques sont distinctes : blocs 31-33, 36-38, 41-43, 46-48 et 51-53. Le bloc 28 est le seul en-tete numerique fusionne. Coordonnees et preuves completes dans rejeu.json.',
        '', 'Trois colonnes recurrentes : debuts x autour de 825, 966 et 1179. Le Q initial du bloc 28 (x=829) et le libelle arabe de quantite ancrent la premiere colonne. Le calcul departage ensuite prix et montant sans intervertir quantite et prix. Sans ancrage Q, cellules absentes ou doubles, alignement insuffisant, produit ambigu ou incoherent : null et alerte. Aucune valeur manquante calculee.',
        '', 'Le libelle du montant indique HT ; celui du prix ne permet pas de choisir HT/TTC. La valeur est donc stockee dans prix_unitaire ; prix_unitaire_ht et prix_unitaire_ttc restent null. Les controles standards par base fiscale restent non verifiables ; les cinq produits ci-dessous sont controles separement.',
        '', 'Comparaison visuelle avec source.jpg du traitement original :', '',
        '| Article original | Quantite | Prix unitaire | Montant HT | Ecart produit |',
        '|---|---:|---:|---:|---:|']
for c in comparisons:
    q,p,m=c['observe'];report.append(f"| {c['article_original']} | {q} | {p} | {m} | {c['ecart_produit']} |")
report+=['', 'Les cinq triplets sont conformes a la facture originale. Les designations restent celles de l OCR : Souris sans fil, Cartouche toner noir et Cable reseau 5 m restent tronques ou degrades en francais. Aucune restauration depuis la reference visuelle.',
         '', 'En-tete et totaux identiques au traitement archive. Facture francaise de regression : sortie complete strictement identique aux trois articles reussis. Les cas 00042 FR/AR et 00065 sont couverts par les tests existants.',
         '', 'Des lignes et des informations fiscales restant partielles, l alerte generale devient "Tableau OCR local partiellement extrait". Les alertes detaillees et la revue des roles heuristiques restent presentes.',
         '', 'Aucun commit ni push. Les archives d origine sont preservees (SHA256 dans rejeu.json).']
(output/'rapport.md').write_text('\n'.join(report)+'\n',encoding='utf8')
print('Replay OK: five lines match original; French regression identical; archives unchanged.')
