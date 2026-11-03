"""Verifie la configuration et liste les modeles sans transmettre de facture."""
import os
import sys
from moteur.extraction_gemini import configuration, request_json

if __name__=='__main__':
    # The listing endpoint requires a key, but no selected model.
    selected=os.environ.get('GEMINI_MODEL','')
    if not selected:os.environ['GEMINI_MODEL']='listing'
    try:
        data=request_json('/models?pageSize=1000')
        models=[m for m in data.get('models',[]) if 'generateContent' in m.get('supportedGenerationMethods',[])]
        print('Connexion API OK. Aucun document envoye.')
        print('Codes API Flash disponibles (choisir celui utilise dans AI Studio) :')
        for m in models:
            name=m['name'].removeprefix('models/')
            if 'flash' in name.lower() and not any(x in name.lower() for x in ['image','audio','tts']):print('  '+name)
        if selected:
            names={m['name'].removeprefix('models/') for m in models}
            if selected.removeprefix('models/') not in names:raise ValueError('Le modele configure ne figure pas dans la liste accessible. Verifier GEMINI_MODEL.')
            print('Modele configure : '+selected)
        else:print('Configurer ensuite GEMINI_MODEL avec un code de la liste.')
    except Exception as exc:
        print('Configuration non valide : '+str(exc));sys.exit(1)
