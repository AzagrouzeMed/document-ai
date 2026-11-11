"""Offline replay: role anchors, missing cells, ambiguity and French regression."""
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

class ColonnesAligneesTests(unittest.TestCase):
    def setUp(self):
        self.fixture=json.loads((Path(__file__).parent/'fixtures/demo_2026_00035_ocr.json').read_text(encoding='utf8'))
        guard=patch.object(socket.socket,'connect',side_effect=AssertionError('Reseau interdit'))
        guard.start();self.addCleanup(guard.stop)

    def test_cinq_lignes_original_preuves_et_entete_inchanges(self):
        raw=self.fixture['ar'];before=copy.deepcopy(raw)
        data,proofs,alerts=direct(raw,1)
        self.assertEqual(len(data['lignes']),5)
        for i,(_,q,p,m) in enumerate(self.fixture['original']):
            line=data['lignes'][i]
            self.assertEqual([line[k] for k in ['quantite','prix_unitaire','montant_ht']],[q,p,m])
            self.assertLessEqual(abs(q*p-m),.03)
            self.assertIsNone(line['prix_unitaire_ht'])
            self.assertIsNone(line['prix_unitaire_ttc'])
            for key,block in [('quantite',31+5*i),('prix_unitaire',32+5*i),('montant_ht',33+5*i)]:
                proof=proofs[f'lignes.{i}.{key}'][0]
                self.assertEqual(proof['blocs_ocr'],[28,block])
                self.assertTrue(proof['corroboration_ocr'])
        for key,value in self.fixture['avant'].items():
            if key in data and key not in ['lignes','type_document']:self.assertEqual(data[key],value,key)
        self.assertEqual(raw,before)
        self.assertFalse(any('Tableau OCR local non extrait' in a for a in alerts))
        self.assertTrue(any('partiellement extrait' in a for a in alerts))

    def test_sans_ancrage_quantite_produit_commutatif_ne_suffit_pas(self):
        raw=copy.deepcopy(self.fixture['ar']);raw['rec_texts'][27]=raw['rec_texts'][27].replace('Q/','')
        data,_,alerts=direct(raw,1)
        self.assertEqual(len(data['lignes']),5)
        self.assertTrue(all(l['quantite'] is None and l['prix_unitaire'] is None and l['montant_ht'] is None for l in data['lignes']))
        self.assertTrue(any('ambigus' in a for a in alerts))

    def test_produit_ambigu_ou_incoherent_null(self):
        for q,p,m in [('1','10','10'),('7','238,01','999,99')]:
            with self.subTest(q=q,p=p,m=m):
                raw=copy.deepcopy(self.fixture['ar'])
                raw['rec_texts'][30:33]=[q,p,m]
                data,_,alerts=direct(raw,1)
                self.assertTrue(all(data['lignes'][0][k] is None for k in ['quantite','prix_unitaire','montant_ht']))
                self.assertEqual(data['lignes'][1]['quantite'],15)
                self.assertTrue(any('lignes.0' in a and 'ambigus' in a for a in alerts))

    def test_cellule_absente_ou_double_pas_de_calcul(self):
        for duplicate in [False,True]:
            raw=copy.deepcopy(self.fixture['ar'])
            if duplicate:
                raw['rec_texts'].append(raw['rec_texts'][31]);raw['rec_boxes'].append(raw['rec_boxes'][31])
            else:
                del raw['rec_texts'][31];del raw['rec_boxes'][31]
            data,_,_=direct(raw,1)
            self.assertEqual(len(data['lignes']),5)
            self.assertIsNone(data['lignes'][0]['prix_unitaire'])
            self.assertIsNone(data['lignes'][0]['montant_ht'])
            self.assertEqual(data['lignes'][1]['prix_unitaire'],516.31)

    def test_colonnes_non_recurrentes_refusees(self):
        raw=copy.deepcopy(self.fixture['ar'])
        for i in [31,36,41,46,51]:raw['rec_boxes'][i][0]+=100*(i//5)
        data,_,_=direct(raw,1)
        self.assertTrue(all(l['quantite'] is None for l in data['lignes']))

    def test_totaux_ne_modifient_pas_les_articles(self):
        raw=copy.deepcopy(self.fixture['ar'])
        for i in [54,57,61]:
            if i<len(raw['rec_texts']):raw['rec_texts'][i]='999999,99'
        self.assertEqual(direct(raw,1)[0]['lignes'],direct(self.fixture['ar'],1)[0]['lignes'])

    def test_facture_francaise_reussie_inchangee(self):
        f=json.loads((Path(__file__).parent/'fixtures/tableaux_00042_00065_regression.json').read_text(encoding='utf8'))
        case=f['regression']['fr']
        self.assertEqual(direct(case['raw'],1)[0],case['avant'])

if __name__=='__main__':unittest.main()
