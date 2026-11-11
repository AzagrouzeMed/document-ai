"""References et dates locales : archives et concurrence, aucun OCR/API."""
import copy
import json
import socket
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from moteur.extraction_ocr_prioritaire import direct, completer_references_dates
from moteur.extraction_vision import validate_output


class ReferencesDatesTests(unittest.TestCase):
    def setUp(self):
        guard=patch.object(socket.socket,'connect',side_effect=AssertionError('Reseau interdit'))
        guard.start();self.addCleanup(guard.stop)

    def test_archive_complete_et_regressions(self):
        r=json.loads((Path(__file__).parent/'fixtures/demo_2025_00072_adresse_ocr.json').read_text(encoding='utf-8'))
        before=copy.deepcopy(r);data,proofs,_=direct(r,1)
        self.assertEqual([data[k] for k in ['numero_document','date','date_echeance']],
                         ['DEMO-2025-00072','2025-12-19','2026-01-18'])
        self.assertEqual(data['adresse_fournisseur'],r['rec_texts'][9])
        self.assertEqual(data['fournisseur'],r['rec_texts'][7])
        self.assertEqual([data[k] for k in ['total_ht','montant_tva','total_ttc']],[1719.99,344,2063.99])
        for key,ids in [('numero_document',[1,3]),('date',[16,20]),('date_echeance',[17,21])]:
            self.assertEqual(proofs[key][0]['blocs_ocr'],ids)
            self.assertTrue(proofs[key][0]['corroboration_ocr'])
        self.assertEqual(r,before)

    def fixture(self,arabic=False):
        titles=['Facture','Date de facture','Échéance','Bon de commande','Livraison']
        if arabic:titles=['فاتورة','تاريخ الفاتورة','تاريخ الاستحقاق','طلب الشراء','التسليم']
        texts=[titles[0],'INV-987',titles[3],'PO-555',titles[1],'11/03/2025',
               titles[2],'12/04/2025',titles[4],'15/03/2025','Conditions : 30 jours']
        boxes=[[0,0,200,30],[0,35,200,65],[400,0,600,30],[400,35,600,65],
               [0,100,200,130],[0,135,200,165],[400,100,600,130],[400,135,600,165],
               [0,250,200,280],[0,285,200,315],[0,400,250,430]]
        return dict(rec_texts=texts,rec_boxes=boxes)

    def extract(self,r):
        prepared={'lignes':[],'preuves':[]}
        alerts=completer_references_dates(prepared,r)
        data,proofs,notices=validate_output(prepared,r,1)
        return data,proofs,alerts+notices

    def test_references_dates_concurrentes_fr_ar(self):
        for arabic in [False,True]:
            data,_,_=self.extract(self.fixture(arabic))
            self.assertEqual([data[k] for k in ['numero_document','date','date_echeance']],
                             ['INV-987','2025-03-11','2025-04-12'])

    def test_plusieurs_candidats_null_et_alerte(self):
        for key,text,box in [('numero_document','INV-988',[0,55,200,80]),
                             ('date','13/03/2025',[0,155,200,180]),
                             ('date_echeance','14/04/2025',[400,155,600,180])]:
            r=self.fixture();r['rec_texts'].append(text);r['rec_boxes'].append(box)
            data,_,alerts=self.extract(r)
            self.assertIsNone(data[key])
            self.assertTrue(any(key in a and 'plusieurs' in a for a in alerts))

    def test_echeance_non_calculee_et_date_invalide(self):
        r=self.fixture();r['rec_texts'][7]='illisible';r['rec_texts'][5]='31/02/2025'
        data,_,alerts=self.extract(r)
        self.assertIsNone(data['date']);self.assertIsNone(data['date_echeance'])
        self.assertTrue(any('date_echeance' in a for a in alerts))
        self.assertEqual(data['numero_document'],'INV-987')

    def test_champs_dans_meme_bloc(self):
        r=dict(rec_texts=['Numéro de facture : AB-456','Date de facture : 2025-03-11',
                          'Échéance : 2025-04-12','Bon de commande : PO-555',
                          'Livraison : 2025-03-15'],
               rec_boxes=[[0,i*100,400,i*100+30] for i in range(5)])
        data,_,_=self.extract(r)
        self.assertEqual([data[k] for k in ['numero_document','date','date_echeance']],
                         ['AB-456','2025-03-11','2025-04-12'])


if __name__=='__main__':unittest.main()
