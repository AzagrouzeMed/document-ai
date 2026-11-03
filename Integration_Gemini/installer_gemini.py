"""Copie une mise a jour dans le projet existant, avec sauvegarde des fichiers."""
import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path

FILES=['app.py','traitement_document.py','rapport_pdf.py','static/index.html','static/app.js',
       'moteur/extraction_gemini.py','verifier_gemini.py','demarrer_gemini.py']

def install(project):
    source=Path(__file__).resolve().parent
    target=Path(project).resolve()/'DocumentAI_Web'
    if not (target/'lancer_web.py').is_file():raise ValueError('Projet existant introuvable : '+str(target))
    for name in ['moteur/extraction_ocr_prioritaire.py','moteur/corroboration_ocr.py','moteur/extraction_vision.py']:
        if not (target/name).is_file():raise ValueError('La version OCR prioritaire doit deja etre installee : '+name)
    for name in FILES:
        if not (source/name).is_file():raise ValueError('Paquet incomplet : '+name)
        if (source/name).resolve()==(target/name).resolve():raise ValueError('Extraire le ZIP dans un dossier separe de DocumentAI_Web.')
    backup=target.parent/('sauvegarde_avant_gemini_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
    backup.mkdir()
    existed=[];created=[]
    for name in FILES:
        dst=target/name
        if dst.exists():
            b=backup/name;b.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(dst,b);existed.append(name)
        else:created.append(name)
    (backup/'restauration.json').write_text(json.dumps(dict(fichiers_sauvegardes=existed,nouveaux_fichiers=created),indent=2),encoding='utf-8')
    for name in FILES:
        dst=target/name;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source/name,dst)
    print('Integration installee. Sauvegarde : '+str(backup))
    print('Le dossier data est conserve. Lancer DocumentAI_Web/demarrer_gemini.py.')
    return backup

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--projet',default=r'C:\Users\User\document-ai');a=p.parse_args()
    try:install(a.projet)
    except Exception as exc:raise SystemExit('Installation arretee : '+str(exc))
