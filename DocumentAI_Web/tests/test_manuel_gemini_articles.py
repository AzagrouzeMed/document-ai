"""Simulation du script manuel ; aucune vraie cle ni requete."""
import io
import json
import socket
import sys
import tempfile
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import importlib.util
spec=importlib.util.spec_from_file_location('essai_manuel_isole',ROOT/'essai_gemini_articles.py')
manual=importlib.util.module_from_spec(spec);spec.loader.exec_module(manual)


class ManuelArticlesTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name);self.prompt=self.base/'consigne.txt'
        self.prompt.write_text('Consigne exacte fournie par utilisateur. Ne rien inventer.',encoding='utf-8')
        self.args=Namespace(modele='models/modele-ai-studio-exact',texte_seul=False,
            image=self.base/'tableau.png',consigne=self.prompt,temperature=0,sortie=self.base/'resultat.json')
        guard=patch.object(socket.socket,'connect',side_effect=AssertionError('Reseau interdit'))
        guard.start();self.addCleanup(guard.stop)

    def test_image_seule_et_consigne_exacte_apres_pretest(self):
        data={'lignes':[dict(reference='INF-04',designation='Cable',quantite=17,
                            prix_unitaire=57.28,montant=973.76)],'incertitudes':[]}
        image={'inlineData':{'mimeType':'image/png','data':'image-fictive'}}
        with patch.object(manual,'generer',side_effect=['OK',json.dumps(data)]) as generate, \
             patch.object(manual,'charger_image',return_value=image) as load:
            result=manual.executer(self.args,'cle-fictive-ne-pas-archiver')
        self.assertEqual(result,data)
        self.assertEqual(generate.call_count,2)
        self.assertEqual(generate.call_args_list[0].args[2],[{'text':'Réponds OK'}])
        call=generate.call_args_list[1]
        self.assertEqual(call.args[0],'modele-ai-studio-exact')
        self.assertEqual(call.args[2],[image,{'text':self.prompt.read_text(encoding='utf-8')}])
        self.assertEqual(call.args[3],manual.schema_tableau())
        load.assert_called_once_with(self.args.image)
        self.assertEqual(json.loads(self.args.sortie.read_text(encoding='utf-8')),data)
        diagnostic=self.args.sortie.with_suffix('.diagnostic.json').read_text(encoding='utf-8')
        self.assertNotIn('cle-fictive-ne-pas-archiver',diagnostic)
        self.assertNotIn('inlineData',diagnostic)

    def test_echec_pretest_aucune_image_lue_ou_envoyee(self):
        with patch.object(manual,'generer',side_effect=RuntimeError('HTTP 404')) as generate, \
             patch.object(manual,'charger_image') as load:
            with self.assertRaisesRegex(RuntimeError,'404'):manual.executer(self.args,'cle-fictive')
        generate.assert_called_once();load.assert_not_called()
        self.assertFalse(self.args.sortie.exists())

    def test_mode_texte_seul(self):
        self.args.texte_seul=True
        with patch.object(manual,'generer',return_value='OK') as generate,patch.object(manual,'charger_image') as load:
            self.assertIsNone(manual.executer(self.args,'cle-fictive'))
        generate.assert_called_once();load.assert_not_called()

    def test_http_google_message_et_cle_masquee(self):
        error=HTTPError('https://exemple.invalid',404,'Not Found',{},io.BytesIO(json.dumps({
            'error':{'status':'NOT_FOUND','message':'Modele introuvable cle-fictive'}}).encode()))
        with patch.object(manual,'urlopen',side_effect=error):
            with self.assertRaises(RuntimeError) as result:
                manual.generer('modele-fictif','cle-fictive',[{'text':'Réponds OK'}])
        self.assertIn('HTTP 404',str(result.exception));self.assertIn('NOT_FOUND',str(result.exception))
        self.assertIn('Modele introuvable',str(result.exception));self.assertNotIn('cle-fictive',str(result.exception))


if __name__=='__main__':unittest.main()
