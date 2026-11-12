"""Crée une facture fictive dans DOCUMENTAI_DATA_DIR, obligatoirement défini."""
import os
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import stockage as s

if __name__=='__main__':
    dossier=os.environ.get('DOCUMENTAI_DATA_DIR')
    if not dossier:raise SystemExit('Définissez DOCUMENTAI_DATA_DIR vers un dossier temporaire.')
    base=Path(dossier)/'document_ai.db'
    if base.exists():raise SystemExit('Choisissez un nouveau dossier temporaire vide.')
    s.initialiser_base(base)
    f=s.enregistrer_facture({'numero_document':'DEMO-RAG','statut':'a_verifier','lignes':[]},base,traitement_id='demo-rag')
    s.enregistrer_pages_document(f,[dict(numero_page=2,langue='fr',texte_ocr='Conditions de paiement : règlement par virement à 30 jours après réception de la facture.',source='ocr/page_2_fr.json')],base,traitement_id='demo-rag')
    print('Facture DEMO-RAG créée, texte fictif en page 2.')
