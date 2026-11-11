"""Rattachement des parties : archive originale et zones inversees/ambigues."""
import copy
import json
import socket
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from moteur.extraction_ocr_prioritaire import direct, rattacher_client_ice
from moteur.extraction_vision import validate_output


class ClientICETests(unittest.TestCase):
    def setUp(self):
        guard=patch.object(socket.socket,'connect',side_effect=AssertionError('Reseau interdit'))
        guard.start();self.addCleanup(guard.stop)
        self.raw=json.loads((Path(__file__).parent/'fixtures/demo_2025_00072_adresse_ocr.json').read_text(encoding='utf-8'))

    def assert_parties(self,data):
        self.assertEqual(data['client'],self.raw['rec_texts'][6])
        self.assertTrue(data['client'].startswith('MANAR Services SARL'))
        self.assertEqual(data['ice_fournisseur'],'911988929928273')
        self.assertEqual(data['ice_client'],'944593181599141')

    def test_archive_complete_et_regressions(self):
        before=copy.deepcopy(self.raw);data,proofs,_=direct(self.raw,1)
        self.assert_parties(data)
        self.assertEqual(data['fournisseur'],self.raw['rec_texts'][7])
        self.assertEqual(data['adresse_fournisseur'],self.raw['rec_texts'][9])
        self.assertEqual([data[k] for k in ['numero_document','date','date_echeance']],
                         ['DEMO-2025-00072','2025-12-19','2026-01-18'])
        self.assertEqual([data[k] for k in ['total_ht','montant_tva','total_ttc']],[1719.99,344,2063.99])
        for key,ids in [('client',[5,7]),('ice_client',[5,11]),('ice_fournisseur',[6,15])]:
            self.assertEqual(proofs[key][0]['blocs_ocr'],ids)
            self.assertTrue(proofs[key][0]['corroboration_ocr'])
        self.assertEqual(self.raw,before)

    def extract_parties(self,r):
        # Isolation du rattachement : ne modifie pas les anciennes regles du nom fournisseur.
        prepared=dict(lignes=[],preuves=[])
        alerts=rattacher_client_ice(prepared,r)
        data,proofs,notices=validate_output(prepared,r,1)
        return data,proofs,alerts+notices

    def test_zones_inversees(self):
        r=copy.deepcopy(self.raw)
        r['rec_boxes']=[[1600-b[2],b[1],1600-b[0],b[3]] for b in r['rec_boxes']]
        data,_,_=direct(r,1)
        self.assert_parties(data)

    def test_ice_non_attribuable(self):
        r=copy.deepcopy(self.raw)
        # Place l'ICE fournisseur entre les deux zones, a cheval sur leur limite.
        r['rec_boxes'][14]=[500,488,800,526]
        data,_,alerts=self.extract_parties(r)
        self.assertIsNone(data['ice_fournisseur'])
        self.assertEqual(data['ice_client'],'944593181599141')
        self.assertTrue(any('bloc 15' in a and 'non attribue' in a for a in alerts))

    def test_zero_initial_conserve(self):
        r=copy.deepcopy(self.raw);r['rec_texts'][14]='ICE: 001234567890123'
        data,_,_=self.extract_parties(r)
        self.assertEqual(data['ice_fournisseur'],'001234567890123')
        self.assertIsInstance(data['ice_fournisseur'],str)
        self.assertEqual(len(data['ice_fournisseur']),15)

    def test_ice_concurrents_dans_meme_zone(self):
        r=copy.deepcopy(self.raw)
        r['rec_texts'].append('ICE: 000000000000001');r['rec_boxes'].append([845,535,1105,570])
        data,_,alerts=self.extract_parties(r)
        self.assertIsNone(data['ice_fournisseur'])
        self.assertEqual(data['ice_client'],'944593181599141')
        self.assertTrue(any('ice_fournisseur' in a and 'ambigu' in a for a in alerts))

    def test_nom_client_ambigu(self):
        r=copy.deepcopy(self.raw)
        r['rec_texts'].append('Autre Entreprise SARL');r['rec_boxes'].append([114,365,638,399])
        data,_,alerts=self.extract_parties(r)
        self.assertIsNone(data['client'])
        self.assertTrue(any('Client OCR non attribue' in a for a in alerts))


if __name__=='__main__':unittest.main()
