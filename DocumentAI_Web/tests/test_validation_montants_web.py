"""Web demo tests using synthetic data: no OCR, no Gemini."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app as web
from fastapi.testclient import TestClient


class AmountValidationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data=Path(self.tmp.name)
        self.job='a'*32
        self.run=self.data/self.job/'resultats'/'DOC-demo'
        self.run.mkdir(parents=True)
        self.document=dict(document_id='DOC-demo',statut='a_verifier',total_ht=None,
                           montant_tva=None,total_ttc=999,lignes=[dict(designation='Demo')])
        for path,value in [(self.run/'document.json',self.document),
                           (self.run/'controles.json',dict(alertes=['Extraction initiale'])),
                           (self.data/self.job/'statut.json',dict(id=self.job,etat='termine',run_path='resultats/DOC-demo'))]:
            path.write_text(json.dumps(value),encoding='utf-8')
        self.original=(self.run/'document.json').read_bytes()
        self.status=(self.data/self.job/'statut.json').read_bytes()
        self.controls=(self.run/'controles.json').read_bytes()
        self.addCleanup(patch.stopall)
        patch.object(web,'DATA',self.data).start()
        self.worker=patch.object(web,'worker',side_effect=AssertionError('No extraction allowed')).start()
        self.client=TestClient(web.app)
        self.addCleanup(self.client.close)
        self.headers={'X-DocumentAI-Token':web.TOKEN}
        self.url=f'/api/traitements/{self.job}'

    def submit(self,ht='100,00',tva='20',ttc='120'):
        return self.client.post(self.url+'/valider-montants',headers=self.headers,
                                json=dict(total_ht=ht,montant_tva=tva,total_ttc=ttc))

    def test_incoherent_then_correction_and_reload(self):
        bad=self.submit(ttc='130')
        self.assertEqual(bad.status_code,422)
        self.assertEqual(bad.json()['controle']['ecart'],10)
        self.assertFalse((self.run/'montants_valides.json').exists())
        good=self.submit(ht='100,50',tva='20,10',ttc='120,60')
        self.assertEqual(good.status_code,200)
        saved=good.json()
        self.assertEqual(saved['controle']['etat'],'coherent')
        self.assertTrue(saved['validated_at'])
        self.assertEqual(json.loads((self.run/'montants_valides.json').read_text(encoding='utf-8')),saved)
        # Fresh client performs the same GET as a page reload.
        with TestClient(web.app) as fresh:
            result=fresh.get(self.url+'/resultat').json()
        self.assertEqual(result['document']['total_ttc'],120.6)
        self.assertEqual(result['montants_validation'],saved)
        self.assertEqual(result['document']['statut'],'a_verifier')
        self.assertEqual(result['document']['lignes'],self.document['lignes'])
        self.assertEqual((self.run/'document.json').read_bytes(),self.original)
        self.assertEqual((self.data/self.job/'statut.json').read_bytes(),self.status)
        self.assertEqual((self.run/'controles.json').read_bytes(),self.controls)
        self.worker.assert_not_called()
        # A rejected subsequent edit cannot replace the saved version.
        self.assertEqual(self.submit(ttc='150').status_code,422)
        self.assertEqual(self.client.get(self.url+'/resultat').json()['montants_validation'],saved)

    def test_empty_is_null_and_incomplete(self):
        for value in ['', '  ', None]:
            with self.subTest(value=value):
                response=self.submit(ht=value)
                self.assertEqual(response.status_code,422)
                self.assertEqual(response.json()['detail'],'Contr\u00f4le incomplet')
                self.assertIsNone(response.json()['montants']['total_ht'])
                self.assertFalse((self.run/'montants_valides.json').exists())
        self.assertEqual(self.submit(ht='0',tva='0',ttc='0').status_code,200)

    def test_invalid_inputs_and_token(self):
        for value in ['NaN','Infinity','abc','1,2.3',True,[],{},1e308]:
            with self.subTest(value=value):
                self.assertEqual(self.submit(ht=value).status_code,422)
        self.assertEqual(self.client.post(self.url+'/valider-montants',json={}).status_code,403)
        self.assertEqual(self.client.post(self.url+'/valider-montants',headers=self.headers,
                                         json=dict(total_ht=100,montant_tva=20,total_ttc=120,statut='valide')).status_code,422)


if __name__=='__main__':unittest.main()
