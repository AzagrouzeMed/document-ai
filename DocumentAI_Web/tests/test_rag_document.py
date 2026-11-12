"""RAG : SQLite temporaire et transport Gemini simulé, réseau interdit."""
import json
import unittest
from unittest.mock import patch
from DocumentAI_Web.tests.test_recherche_document import RechercheDocumentTests, web
from moteur import extraction_gemini as gemini


class RagTests(unittest.TestCase):
    sauvegarder=RechercheDocumentTests.sauvegarder

    def setUp(self):
        RechercheDocumentTests.setUp(self)
        self.sauvegarder([dict(numero_page=2,langue='fr',texte_ocr='Conditions de paiement : règlement à 30 jours.',source='ocr/page_2_fr.json')])
        self.config=patch.object(gemini,'configuration',return_value=('fictif','modele-fictif'))
        self.config.start();self.addCleanup(self.config.stop)
        self.transport=patch.object(gemini,'urlopen',side_effect=AssertionError('API interdite'))
        self.transport.start();self.addCleanup(self.transport.stop)

    def demander(self,question='Quelles sont les conditions de paiement ?',identifiant=None):
        return self.client.post(f'/api/factures/{identifiant or self.identifiant}/question-document',
            json={'question':question},headers={'x-documentai-token':web.TOKEN})

    def reponse(self,refs=None):
        return {'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':json.dumps({
            'reponse':'Règlement à 30 jours.','references':['E1'] if refs is None else refs})}]}}]}

    def test_paiement_page_et_aucune_ecriture(self):
        avant=self.base.read_bytes()
        with patch.object(gemini,'request_json',return_value=self.reponse()) as appel:
            r=self.demander().json()
        self.assertEqual(r['etat'],'reponse');self.assertEqual(r['references'],['E1'])
        self.assertEqual(r['passages'][0]['numero_page'],2)
        transmis=json.loads(appel.call_args.args[1]['contents'][0]['parts'][0]['text'])
        self.assertEqual(set(transmis),{'question','extraits'})
        self.assertIn('30 jours',transmis['extraits'][0]['texte'])
        self.assertEqual(avant,self.base.read_bytes())

    def test_absences_sans_gemini(self):
        autre=self.s.enregistrer_facture({'numero_document':'VIDE','lignes':[]},self.base,traitement_id='vide')
        with patch.object(gemini,'request_json',side_effect=AssertionError('Appel interdit')):
            self.assertEqual(self.demander(identifiant=autre).json()['etat'],'aucun_texte')
            self.assertEqual(self.demander('xylophone').json()['etat'],'aucun_resultat')

    def test_reference_inventee_et_panne(self):
        for brut in [self.reponse(['page_999']),self.reponse(['E99']),RuntimeError('HTTP 503')]:
            with self.subTest(brut=brut),patch.object(gemini,'request_json',**({'side_effect':brut} if isinstance(brut,Exception) else {'return_value':brut})):
                r=self.demander().json()
                self.assertEqual(r['etat'],'generation_indisponible')
                self.assertIsNone(r['reponse']);self.assertTrue(r['passages'])

    def test_isolation_factures(self):
        autre=self.s.enregistrer_facture({'numero_document':'AUTRE','lignes':[]},self.base,traitement_id='autre')
        self.sauvegarder([dict(numero_page=4,langue='fr',texte_ocr='Conditions de paiement : comptant SECRET_AUTRE.',source='ocr/page_4_fr.json')],identifiant=autre,traitement='autre')
        for identifiant,attendu,interdit in [(self.identifiant,'30 jours','SECRET_AUTRE'),(autre,'SECRET_AUTRE','30 jours')]:
            with patch.object(gemini,'request_json',return_value=self.reponse()) as appel:
                self.demander(identifiant=identifiant)
            texte=appel.call_args.args[1]['contents'][0]['parts'][0]['text']
            self.assertIn(attendu,texte);self.assertNotIn(interdit,texte)

    def test_information_manquante_et_validation(self):
        with patch.object(gemini,'request_json',return_value=self.reponse([])):
            self.assertIn('pas disponible',self.demander().json()['reponse'])
        for question in ['', 'a'*201]:
            self.assertEqual(self.demander(question).status_code,422)
        self.assertEqual(self.demander(identifiant=999999).status_code,404)
