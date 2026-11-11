"""Tests synthetiques, sans OCR ni API."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from moteur.validation_donnees import valider_calculs
from moteur.extraction_gemini import checked_output
import traitement_document as pipeline


class ValidationTests(unittest.TestCase):
    def total(self,ht,tva,ttc):
        return valider_calculs(dict(total_ht=ht,montant_tva=tva,total_ttc=ttc))[-1]

    def test_totaux_et_limites(self):
        for ttc,etat in [(120,'coherent'),(120.03,'coherent'),(119.97,'coherent'),
                         (120.030001,'a_verifier'),(119.969999,'a_verifier')]:
            with self.subTest(ttc=ttc):self.assertEqual(self.total(100,20,ttc)['etat'],etat)
        self.assertEqual(self.total(0.1,0.2,0.3)['ecart'],0)

    def test_zero_et_negatifs(self):
        for ht,tva,ttc in [(0,0,0),(-100,-20,-120)]:
            self.assertEqual(self.total(ht,tva,ttc)['etat'],'coherent')
        for q,p,m in [(0,25,0),(-2,25,-50),(2,-25,-50)]:
            row=dict(quantite=q,prix_unitaire_ht=p,montant_ht=m)
            self.assertEqual(valider_calculs({'lignes':[row]})[0]['etat'],'coherent')

    def test_non_exploitables(self):
        for value in [None,'100',True,False,float('nan'),float('inf'),-float('inf'),[],{}]:
            for key in ['total_ht','montant_tva','total_ttc']:
                with self.subTest(value=value,key=key):
                    d=dict(total_ht=100,montant_tva=20,total_ttc=120);d[key]=value
                    r=valider_calculs(d)[-1]
                    self.assertEqual(r['etat'],'non_verifiable')
                    self.assertIsNone(r['valeur_attendue']);self.assertIsNone(r['ecart'])
                    json.dumps(r,allow_nan=False)
            row=dict(quantite=2,prix_unitaire=5,montant=value)
            self.assertEqual(valider_calculs({'lignes':[row]})[2]['etat'],'non_verifiable')
        self.assertEqual(valider_calculs({})[-1]['etat'],'non_verifiable')

    def test_bases_distinctes_et_limites_lignes(self):
        row=dict(quantite=2,prix_unitaire_ht=10,montant_ttc=20)
        self.assertTrue(all(r['etat']=='non_verifiable' for r in valider_calculs({'lignes':[row]})[:3]))
        row.update(montant_ht=20.03,prix_unitaire_ttc=12,montant_ttc=24.031,prix_unitaire=5,montant=10)
        r=valider_calculs({'lignes':[row]})
        self.assertEqual([v['etat'] for v in r[:3]],['coherent','a_verifier','coherent'])
        self.assertEqual(r[0],dict(code='LIGNE_QUANTITE_PRIX_HT',champ='lignes.0.montant_ht',
            etat='coherent',valeurs=dict(quantite=2,prix_unitaire_ht=10,montant_ht=20.03),
            valeur_attendue=20,ecart=0.03,tolerance=0.03))
        self.assertEqual(r[2]['code'],'LIGNE_QUANTITE_PRIX_GENERIQUE')

    def test_aucune_mutation_ni_somme_articles(self):
        d=dict(total_ht=100,montant_tva=20,total_ttc=120,
               lignes=[dict(quantite=2,prix_unitaire=5,montant=10)])
        before=copy.deepcopy(d);r=valider_calculs(d)
        self.assertEqual(d,before);self.assertEqual(len(r),4)
        self.assertEqual(r[-1]['etat'],'coherent')

    def test_gemini_reutilise_les_regles(self):
        for montant,alerte in [(20.03,False),(20.031,True)]:
            d,_,a=checked_output({'lignes':[dict(quantite=2,prix_unitaire_ht=10,montant_ht=montant)]},{},1)
            self.assertEqual(any('Quantite x prix' in s for s in a),alerte)
            self.assertEqual(d['lignes'][0]['montant_ht'],montant)
        _,_,a=checked_output(dict(total_ht=100,montant_tva=20,total_ttc=120.04),{},1)
        self.assertIn('HT + TVA different du TTC imprime.',a)

    def test_integration_controles_json(self):
        with tempfile.TemporaryDirectory() as temporary:
            base=Path(temporary);cache=base/'ocr.json'
            cache.write_text(json.dumps(dict(page_index=0,input_path='fictif.pdf',
                rec_texts=['Texte fictif'],rec_boxes=[[0,0,10,10]])),encoding='utf-8')
            d=dict(total_ht=100,montant_tva=20,total_ttc=120.04,
                   lignes=[dict(quantite=2,prix_unitaire_ht=10,montant_ht=20)])
            with patch.object(pipeline,'process_pages',return_value=(d,{},['Alerte conservee'],[],False)):
                run=pipeline.traiter_document(ocr_cache=[cache],output=base/'sortie',sans_llm=True)
            c=json.loads((run/'controles.json').read_text(encoding='utf-8'))
            document=json.loads((run/'document.json').read_text(encoding='utf-8'))
            self.assertEqual(c['verifications'],valider_calculs(document))
            self.assertEqual(c['verifications'][-1]['etat'],'a_verifier')
            self.assertEqual(c['statut'],'a_verifier');self.assertTrue(c['revue_humaine_requise'])
            self.assertIn('Alerte conservee',c['alertes'])
            self.assertEqual(c['nombre_articles'],1);self.assertEqual(c['nombre_pages'],1)
            self.assertEqual(document['total_ttc'],120.04)


if __name__=='__main__':unittest.main()
