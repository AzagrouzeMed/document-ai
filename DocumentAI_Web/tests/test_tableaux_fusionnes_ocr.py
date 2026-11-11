"""Conserve les articles lisibles sans inventer le role des cellules numeriques."""
import copy
import json
import socket
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from moteur.extraction_ocr_prioritaire import direct
from moteur.validation_donnees import valider_calculs


class TableauFusionneTests(unittest.TestCase):
    def setUp(self):
        guard=patch.object(socket.socket,'connect',side_effect=AssertionError('Reseau interdit'))
        guard.start();self.addCleanup(guard.stop)
        self.fixture=json.loads((Path(__file__).parent/'fixtures/tableaux_00042_00065_regression.json').read_text(encoding='utf-8'))

    def test_65_cinq_designations_nombres_non_attribues(self):
        case=self.fixture['00065']['ar'];raw=case['raw'];before=copy.deepcopy(raw)
        data,proofs,alerts=direct(raw,1)
        self.assertEqual(len(data['lignes']),5)
        for index,block_id in enumerate([32,37,42,47,52]):
            line=data['lignes'][index]
            self.assertEqual(line['designation'],raw['rec_texts'][block_id-1])
            for key in ['quantite','prix_unitaire_ht','prix_unitaire_ttc','prix_unitaire',
                        'montant_ht','montant_ttc','montant']:
                self.assertIsNone(line[key])
            self.assertEqual(proofs[f'lignes.{index}.designation'][0]['blocs_ocr'],[block_id])
            self.assertTrue(any(f'lignes.{index} :' in a and 'fusionne' in a for a in alerts))
        for key,value in case['avant'].items():
            if key!='lignes':self.assertEqual(data[key],value,key)
        self.assertEqual(raw,before)
        self.assertEqual(valider_calculs(data)[-1]['etat'],'coherent')
        self.assertTrue(all(v['etat']=='non_verifiable' for v in valider_calculs(data)[:-1]))

    def test_42_fr_ar_et_regression_trois_articles_inchanges(self):
        for name,lang,count in [('00042','fr',0),('00042','ar',2),('regression','fr',3)]:
            with self.subTest(name=name,lang=lang):
                case=self.fixture[name][lang];data,_,_=direct(case['raw'],1)
                self.assertEqual(data,case['avant'])
                self.assertEqual(len(data['lignes']),count)

    def test_65_fr_libelles_perdus_pas_de_role_devine(self):
        case=self.fixture['00065']['fr'];data,_,alerts=direct(case['raw'],1)
        self.assertEqual(data,case['avant'])
        self.assertTrue(any('colonnes' in a for a in alerts))

    def test_totaux_non_utilises_pour_articles(self):
        raw=copy.deepcopy(self.fixture['00065']['ar']['raw'])
        for index in [52,55,59]:raw['rec_texts'][index]='999 999,99'
        data,_,_=direct(raw,1)
        self.assertEqual(len(data['lignes']),5)
        self.assertTrue(all(l['quantite'] is None and l['montant_ht'] is None for l in data['lignes']))

    def test_reference_ambigue_ligne_refusee(self):
        raw=copy.deepcopy(self.fixture['00065']['ar']['raw'])
        raw['rec_texts'].append('AUTRE-123');raw['rec_boxes'].append([72,817,146,853])
        data,_,alerts=direct(raw,1)
        self.assertEqual(len(data['lignes']),4)
        self.assertTrue(any('reference ou rangee' in a for a in alerts))


if __name__=='__main__':unittest.main()
