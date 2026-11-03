"""Demarre le serveur avec une cle saisie sans echo et sans fichier de secret."""
import getpass
import os
import subprocess
import sys
from pathlib import Path
from moteur.extraction_gemini import request_json

if __name__=='__main__':
    try:
        if not os.environ.get('GEMINI_API_KEY'):
            os.environ['GEMINI_API_KEY']=getpass.getpass('Cle API Gemini (saisie masquee) : ').strip()
        chosen=os.environ.get('GEMINI_MODEL','').removeprefix('models/')
        os.environ['GEMINI_MODEL']=chosen or 'listing'
        data=request_json('/models?pageSize=1000')
        all_models=[m['name'].removeprefix('models/') for m in data.get('models',[]) if 'generateContent' in m.get('supportedGenerationMethods',[])]
        if not chosen:
            models=[m for m in all_models if 'flash' in m.lower() and not any(x in m.lower() for x in ['image','audio','tts'])]
            if not models:raise ValueError('Aucun modele Flash accessible. Verifier les droits du projet AI Studio.')
            print('Selectionner de preference le modele deja teste dans AI Studio :')
            for i,name in enumerate(models,1):print(f'{i}. {name}')
            answer=input('Numero du modele, ou code API exact : ').strip()
            chosen=models[int(answer)-1] if answer.isdigit() and 1<=int(answer)<=len(models) else answer
        if chosen not in all_models:raise ValueError('Code de modele non accessible pour cette cle.')
        os.environ['GEMINI_MODEL']=chosen
        print('Modele : '+chosen)
        print('Les documents et leur texte OCR seront envoyes a Google en mode Gemini. Quotas/frais possibles.')
        root=Path(__file__).resolve().parent
        subprocess.run([sys.executable,str(root/'lancer_web.py')],cwd=root.parent,env=os.environ.copy(),check=True)
    except KeyboardInterrupt:print('\nArret demande.')
    except Exception as exc:
        print('Demarrage impossible : '+str(exc));sys.exit(1)
