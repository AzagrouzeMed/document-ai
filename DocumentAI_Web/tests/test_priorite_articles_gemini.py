"""Priorite Gemini et secours simules jusqu'au JSON final : aucun reseau/OCR."""
import copy
import json
import socket
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from moteur import extraction_gemini as gemini
import traitement_document as pipeline


def proposal(lines):
    data={k:None for k in gemini.TEXT_FIELDS+gemini.NUM_FIELDS}
    data.update(type_document='facture',fournisseur='Entete Gemini',client='Client Gemini',
                numero_document='TEST-65',date='2025-07-14',total_ht=4224.98,
                montant_tva=845,total_ttc=5069.98,devise='MAD',lignes=lines,incertitudes=[])
    return data


def line(name,q,p,m):
    d={k:None for k in gemini.LINE_TEXT+gemini.LINE_NUM}
    d.update(designation=name,quantite=q,prix_unitaire_ht=p,montant_ht=m)
    return d


def response(data):
    return {'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':json.dumps(data)}]}}]}


class PrioriteArticlesTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.base=Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch.object(socket.socket,'connect',side_effect=AssertionError('Reseau interdit')))
        self.stack.enter_context(patch.object(gemini,'urlopen',side_effect=AssertionError('API interdite')))
        self.stack.enter_context(patch.object(gemini,'configuration',return_value=('cle-fictive','modele-fictif')))
        self.stack.enter_context(patch.object(gemini,'page_png',return_value=b'image-fictive'))
        self.source=self.base/'source.jpg';self.source.write_bytes(b'fictif')
        self.ocr=self.base/'ocr.json';self.ocr.write_text(json.dumps(dict(
            page_index=0,input_path='fictif.jpg',rec_texts=['Facture'],rec_boxes=[[0,0,100,20]])),encoding='utf-8')
        self.pages={0:{'fr':self.ocr}}
        self.local=dict(fournisseur='Entete local different',client='Client local',
                        lignes=[dict(designation='Article local',quantite=2,prix_unitaire=3,montant=6)])

    def run_case(self,remote):
        with patch.object(pipeline,'ocr_document',return_value=self.pages), \
             patch.object(gemini,'request_json',return_value=remote), \
             patch('moteur.extraction_ocr_prioritaire.direct',return_value=(copy.deepcopy(self.local),
                 {'fournisseur':{'origine':'local'},'lignes.0.designation':{'origine':'local'}},[])) as local:
            folder=pipeline.traiter_document(input_path=self.source,output=self.base/'sorties')
        read=lambda name:json.loads((folder/name).read_text(encoding='utf-8'))
        return read('document.json'),read('controles.json'),read('execution.json'),read('preuves.json'),local

    def test_cinq_lignes_gemini_conservees_meme_ecart(self):
        lines=[line('Cable reseau',17,57.28,974),line('Stylos',2,58.74,117.48),
               line('Clavier',13,74.60,969.80),line('Classeur',16,33.19,531.04),
               line('Classeur',2,816.45,1632.90)]
        data,controls,execution,proofs,local=self.run_case(response(proposal(lines)))
        local.assert_not_called()
        self.assertEqual(len(data['lignes']),5)
        for expected,actual in zip(lines,data['lignes']):
            for key,value in expected.items():self.assertEqual(actual[key],value)
        self.assertEqual(data['fournisseur'],'Entete Gemini')
        self.assertEqual(controls['sources_articles'],[dict(page=1,source='gemini',raison_basculement=None)])
        self.assertEqual(execution['routes'][0]['source_articles'],'gemini')
        self.assertEqual(controls['verifications'][0]['etat'],'a_verifier')
        self.assertEqual(data['lignes'][0]['montant_ht'],974)
        self.assertTrue(any('Quantite x prix' in a for a in controls['alertes']))
        self.assertEqual(proofs['page_1']['lignes.0.designation']['origine'],'gemini_image_et_ocr')

    def test_ligne_partielle_ou_zero_reste_gemini(self):
        for lines in [[line('Lecture partielle',None,None,None)],[line(None,0,0,0)]]:
            with self.subTest(lines=lines):
                data,controls,_,_,local=self.run_case(response(proposal(lines)))
                local.assert_not_called()
                self.assertEqual(controls['sources_articles'][0]['source'],'gemini')
                self.assertEqual(data['lignes'][0]['designation'],lines[0]['designation'])

    def test_aucune_ligne_utilisable_secours_articles_seuls(self):
        for lines in [[],[line(None,None,None,None)],'liste invalide']:
            with self.subTest(lines=lines):
                data,controls,execution,proofs,local=self.run_case(response(proposal(lines)))
                local.assert_called_once()
                self.assertEqual(data['fournisseur'],'Entete Gemini')
                self.assertEqual(data['numero_document'],'TEST-65')
                self.assertEqual(data['total_ht'],4224.98)
                self.assertEqual(data['lignes'][0]['designation'],'Article local')
                self.assertEqual(controls['sources_articles'][0],dict(page=1,source='secours_local',
                    raison_basculement='aucune_ligne_gemini_utilisable'))
                self.assertEqual(execution['routes'][0]['source_articles'],'secours_local')
                self.assertEqual(proofs['page_1']['lignes.0.designation']['origine'],'local')
                self.assertEqual(proofs['page_1']['fournisseur']['origine'],'gemini_image_et_ocr')
                self.assertEqual(data['statut'],'partiel')

    def test_erreur_api_et_json_inexploitable(self):
        for error in [RuntimeError('Gemini HTTP 404 : modele introuvable'),ValueError('JSON invalide')]:
            with self.subTest(error=str(error)), \
                 patch.object(pipeline,'ocr_document',return_value=self.pages), \
                 patch.object(gemini,'extract',side_effect=error), \
                 patch('moteur.extraction_ocr_prioritaire.direct',return_value=(copy.deepcopy(self.local),{},[])):
                run=pipeline.traiter_document(input_path=self.source,output=self.base/'erreurs')
                controls=json.loads((run/'controles.json').read_text(encoding='utf-8'))
                data=json.loads((run/'document.json').read_text(encoding='utf-8'))
                self.assertEqual(data['fournisseur'],'Entete local different')
                self.assertEqual(controls['sources_articles'][0]['raison_basculement'],
                                 'appel_ou_reponse_gemini_inexploitable')
                self.assertTrue(any(str(error) in a for a in controls['alertes']))


if __name__=='__main__':unittest.main()
