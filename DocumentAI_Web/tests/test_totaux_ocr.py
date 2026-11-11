"""Rejeu de blocs archives et cas geometriques synthetiques, sans OCR/API."""
import copy
import json
import socket
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from moteur.extraction_ocr_prioritaire import direct, completer_totaux
from moteur.validation_donnees import valider_calculs


class TotauxOCRTests(unittest.TestCase):
    def setUp(self):
        self.raw=json.loads((Path(__file__).parent/'fixtures/demo_2025_00072_totaux_ocr.json').read_text(encoding='utf-8'))
        self.network=patch.object(socket.socket,'connect',side_effect=AssertionError('Reseau interdit'))
        self.network.start();self.addCleanup(self.network.stop)

    def test_rejeu_blocs_archives(self):
        before=copy.deepcopy(self.raw)
        data,proofs,alerts=direct(self.raw,1)
        self.assertEqual([data[k] for k in ['total_ht','montant_tva','total_ttc','devise']],
                         [1719.99,344.0,2063.99,'MAD'])
        self.assertEqual(valider_calculs(data)[-1]['etat'],'coherent')
        self.assertEqual(valider_calculs(data)[-1]['ecart'],0)
        for key in ['total_ht','montant_tva','total_ttc']:
            self.assertTrue(proofs[key][0]['corroboration_ocr'])
        self.assertEqual(before,self.raw)

    def test_plusieurs_montants_plausibles(self):
        self.raw['rec_texts'].append('1 800,00')
        self.raw['rec_boxes'].append([1190,955,1350,991])
        data,_,alerts=direct(self.raw,1)
        self.assertIsNone(data['total_ht'])
        self.assertEqual(data['total_ttc'],2063.99)
        self.assertTrue(any('ambigus' in a and 'total_ht' in a for a in alerts))
        self.assertEqual(valider_calculs(data)[-1]['etat'],'non_verifiable')

    def test_ne_remplace_pas_valeur_renseignee_meme_zero(self):
        for value in [0,123.45]:
            prepared=dict(total_ht=value,preuves=[])
            completer_totaux(prepared,self.raw)
            self.assertEqual(prepared['total_ht'],value)
            self.assertFalse(any(p['champ']=='total_ht' for p in prepared['preuves']))

    def test_libelles_fr_et_ar_et_montants_a_gauche(self):
        for labels in [('Total HT','TVA 20 %','Total TTC'),
                       ('المجموع دون الضريبة','الضريبة على القيمة المضافة','المجموع شامل الضريبة')]:
            r=dict(rec_texts=[],rec_boxes=[])
            for i,(label,amount) in enumerate(zip(labels,['1 719,99','344,00','2 063,99'])):
                y=100+i*60
                r['rec_texts'].extend([label,amount]);r['rec_boxes'].extend([[300,y,600,y+30],[80,y,250,y+30]])
            prepared={'preuves':[]};completer_totaux(prepared,r)
            self.assertEqual([prepared[k] for k in ['total_ht','montant_tva','total_ttc']],[1719.99,344,2063.99])

    def test_aucune_deduction_et_libelle_degrade_refuse(self):
        r=dict(rec_texts=['otal T /','1 719,99','Total TTC / شامل الضريبة','2 063,99'],
               rec_boxes=[[300,100,600,130],[650,100,800,130],[300,160,600,190],[650,160,800,190]])
        prepared={'preuves':[]};completer_totaux(prepared,r)
        self.assertIsNone(prepared.get('total_ht'));self.assertIsNone(prepared.get('montant_tva'))
        self.assertEqual(prepared['total_ttc'],2063.99)

    def test_cellule_partagee_ambigue(self):
        r=dict(rec_texts=['Total HT','Total TTC','10,00'],
               rec_boxes=[[0,100,100,140],[0,120,100,160],[150,115,220,145]])
        prepared={'preuves':[]};alerts=completer_totaux(prepared,r)
        self.assertIsNone(prepared.get('total_ht'));self.assertIsNone(prepared.get('total_ttc'))
        self.assertTrue(alerts)


if __name__=='__main__':unittest.main()
