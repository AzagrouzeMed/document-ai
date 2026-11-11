"""Rejeu OCR FR/AR et comparaison visuelle de DEMO-2025-00042."""
import copy
import json
import socket
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from moteur.extraction_ocr_prioritaire import direct, recuperer_lignes_bilingues
from moteur.validation_donnees import valider_calculs


class LignesBilinguesTests(unittest.TestCase):
    def setUp(self):
        guard=patch.object(socket.socket,'connect',side_effect=AssertionError('Reseau interdit'))
        guard.start();self.addCleanup(guard.stop)
        self.fixture=json.loads((Path(__file__).parent/'fixtures/demo_2025_00042_ocr.json').read_text(encoding='utf-8'))

    def test_archive_complete_original_et_entete(self):
        raw=self.fixture['ar'];before=copy.deepcopy(raw)
        data,proofs,alerts=direct(raw,1)
        self.assertEqual(len(data['lignes']),2)
        expected=[('Souris sans fil',5,214.03,1070.15),('Clavier USB',8,204.46,1635.68)]
        for i,(name,q,p,m) in enumerate(expected):
            line=data['lignes'][i]
            self.assertTrue(line['designation'].startswith(name+' /'))
            self.assertEqual([line[k] for k in ['quantite','prix_unitaire_ht','montant_ht']],[q,p,m])
            self.assertIsNone(line['prix_unitaire_ttc']);self.assertIsNone(line['montant_ttc'])
            for key in ['designation','quantite','prix_unitaire_ht','montant_ht']:
                self.assertTrue(proofs[f'lignes.{i}.{key}'][0]['corroboration_ocr'])
        for key,value in self.fixture['entete_avant'].items():self.assertEqual(data[key],value,key)
        self.assertTrue(all(v['etat']=='coherent' for v in valider_calculs(data) if v['etat']!='non_verifiable'))
        self.assertEqual(raw,before)

    def test_fr_colonne_prix_absente_pas_de_devination(self):
        data,_,alerts=direct(self.fixture['fr'],1)
        self.assertEqual(data['lignes'],[])
        self.assertTrue(any('colonnes' in a for a in alerts))

    def test_montant_ambigu_reste_vide(self):
        raw=copy.deepcopy(self.fixture['ar'])
        raw['rec_texts'].append('1 080,00');raw['rec_boxes'].append([1300,793,1390,821])
        data,_,alerts=direct(raw,1)
        self.assertEqual(len(data['lignes']),2)
        self.assertIsNone(data['lignes'][0]['montant_ht'])
        self.assertEqual(data['lignes'][1]['montant_ht'],1635.68)
        self.assertTrue(any('lignes.0.montant_ht' in a and 'ambigue' in a for a in alerts))

    def test_reference_separee_et_lignes_repetees(self):
        raw=copy.deepcopy(self.fixture['ar'])
        raw['rec_texts'][34]=raw['rec_texts'][29]
        data,_,_=direct(raw,1)
        self.assertEqual(len(data['lignes']),2)
        self.assertEqual(data['lignes'][0]['designation'],data['lignes'][1]['designation'])
        self.assertFalse(any(l['designation'].startswith('INF-') for l in data['lignes']))

    def test_pas_de_reconstruction_depuis_totaux(self):
        raw=copy.deepcopy(self.fixture['ar'])
        ids=[i for i in range(len(raw['rec_texts'])) if not 28<=i<=37]
        raw['rec_texts']=[raw['rec_texts'][i] for i in ids]
        raw['rec_boxes']=[raw['rec_boxes'][i] for i in ids]
        data,_,_=direct(raw,1)
        self.assertEqual(data['lignes'],[])
        self.assertEqual(data['total_ht'],2705.83)

    def test_lignes_existantes_conservees(self):
        prepared={'lignes':[{'designation':'Deja extrait'}],'preuves':[]}
        before=copy.deepcopy(prepared)
        self.assertEqual(recuperer_lignes_bilingues(prepared,self.fixture['ar']),[])
        self.assertEqual(prepared,before)


if __name__=='__main__':unittest.main()
