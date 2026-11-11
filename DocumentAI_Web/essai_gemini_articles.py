"""Essai manuel isole : texte seul ou image deja recadree, jamais OCR.

Aucun appel a l'import. Le code API du modele et la consigne AI Studio sont
fournis explicitement. La cle est saisie au clavier sans echo.
"""
import argparse
import base64
import getpass
import json
import math
import re
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from moteur.extraction_gemini import API
from moteur.validation_donnees import valider_calculs


def schema_tableau():
    properties={k:{'type':['string','null']} for k in ['reference','designation']}
    properties.update({k:{'type':['number','null']} for k in ['quantite','prix_unitaire','montant']})
    return {'type':'object','properties':{
        'lignes':{'type':'array','items':{'type':'object','properties':properties,
                   'required':list(properties),'additionalProperties':False}},
        'incertitudes':{'type':'array','items':{'type':'string'}}},
        'required':['lignes','incertitudes'],'additionalProperties':False}


def generer(modele,cle,parts,schema=None,temperature=0):
    payload={'contents':[{'role':'user','parts':parts}],
             'generationConfig':{'temperature':temperature,'maxOutputTokens':8192}}
    if schema is not None:
        payload['generationConfig'].update(responseMimeType='application/json',responseJsonSchema=schema)
    request=Request(API+'/models/'+modele+':generateContent',
        data=json.dumps(payload,ensure_ascii=False).encode('utf-8'),
        headers={'Content-Type':'application/json','x-goog-api-key':cle},method='POST')
    try:
        with urlopen(request,timeout=180) as response:data=json.loads(response.read())
    except HTTPError as exc:
        message='Message Google non disponible au format JSON.'
        try:
            error=json.loads(exc.read()).get('error',{})
            message=str(error.get('status',''))+' : '+str(error.get('message',''))
        except (ValueError,AttributeError):pass
        raise RuntimeError(f'Gemini HTTP {exc.code} : '+message.replace(cle,'[CLE MASQUEE]')) from None
    except (URLError,TimeoutError):
        raise RuntimeError('Gemini inaccessible ou delai depasse.') from None
    candidates=data.get('candidates',[])
    if not candidates or candidates[0].get('finishReason')!='STOP':
        raise ValueError('Reponse Gemini absente, bloquee ou interrompue.')
    content=''.join(p.get('text','') for p in candidates[0].get('content',{}).get('parts',[]) if not p.get('thought'))
    return content.replace(cle,'[CLE MASQUEE]')


def charger_image(path):
    from PIL import Image
    with Image.open(path) as image:
        mime={'PNG':'image/png','JPEG':'image/jpeg','WEBP':'image/webp'}.get(image.format)
        if mime is None or getattr(image,'n_frames',1)!=1:
            raise ValueError('Fournir une seule image PNG, JPEG ou WebP deja recadree.')
        image.verify()
    return {'inlineData':{'mimeType':mime,'data':base64.b64encode(path.read_bytes()).decode('ascii')}}


def verifier_sortie(data):
    if not isinstance(data,dict) or set(data)!={'lignes','incertitudes'}:
        raise ValueError('Objet JSON de tableau invalide.')
    if not isinstance(data['lignes'],list) or len(data['lignes'])>300:
        raise ValueError('Liste des lignes invalide ou trop longue.')
    fields={'reference','designation','quantite','prix_unitaire','montant'}
    for line in data['lignes']:
        if not isinstance(line,dict) or set(line)!=fields:raise ValueError('Schema de ligne invalide.')
        for key,value in line.items():
            if value is None:continue
            if key in ['reference','designation']:
                if not isinstance(value,str):raise ValueError('Texte invalide : '+key)
            elif type(value) not in (int,float) or not math.isfinite(value):
                raise ValueError('Nombre invalide : '+key)
    if not isinstance(data['incertitudes'],list) or not all(isinstance(v,str) for v in data['incertitudes']):
        raise ValueError('Incertitudes invalides.')
    return data


def executer(args,cle):
    modele=args.modele.strip().removeprefix('models/')
    if not re.fullmatch(r'[A-Za-z0-9._-]+',modele):raise ValueError('Identifiant API invalide.')
    print('Modele : '+modele+' ; API : '+API)
    # Toujours avant la lecture et l'envoi de l'image ; aucun nouvel essai automatique.
    text=generer(modele,cle,[{'text':'Réponds OK'}],temperature=args.temperature)
    if text.strip()!='OK':raise ValueError('Le test texte ne retourne pas exactement OK ; image non envoyee.')
    print('Test texte OK.')
    if args.texte_seul:return None
    consigne=args.consigne.read_text(encoding='utf-8-sig')
    if not consigne.strip():raise ValueError('La consigne AI Studio est vide.')
    image=charger_image(args.image)
    content=generer(modele,cle,[image,{'text':consigne}],schema_tableau(),args.temperature)
    data=verifier_sortie(json.loads(content))
    args.sortie.parent.mkdir(parents=True,exist_ok=True)
    args.sortie.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    diagnostic=dict(modele=modele,api=API,source_articles='gemini_image_recadree_seule',
                    consigne=consigne,temperature=args.temperature,
                    schema=schema_tableau(),verifications=valider_calculs(data))
    args.sortie.with_suffix('.diagnostic.json').write_text(
        json.dumps(diagnostic,ensure_ascii=False,indent=2),encoding='utf-8')
    print('JSON : '+str(args.sortie))
    return data


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--modele',required=True,help='Code API exact du modele AI Studio, sans choix automatique.')
    mode=parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--texte-seul',action='store_true')
    mode.add_argument('--image',type=Path,help='Image du tableau deja recadree manuellement.')
    parser.add_argument('--consigne',type=Path,help='Fichier UTF-8 contenant la consigne exacte AI Studio.')
    parser.add_argument('--temperature',type=float,default=0)
    parser.add_argument('--sortie',type=Path,default=Path(__file__).resolve().parent/'results/essai_tableau.json')
    args=parser.parse_args()
    if args.image and args.consigne is None:parser.error('--consigne est obligatoire avec --image')
    cle=''
    try:
        cle=getpass.getpass('Cle API Gemini (saisie masquee, non archivee) : ').strip()
        if not cle:raise ValueError('Cle absente.')
        executer(args,cle)
    except (ValueError,OSError,RuntimeError) as exc:
        # Masque aussi la cle dans les erreurs autres que HTTP.
        print('Essai interrompu : '+str(exc).replace(cle,'[CLE MASQUEE]') if cle else 'Essai interrompu : cle absente.')
        return 1
    except KeyboardInterrupt:
        print('Essai annule.');return 1
    return 0


if __name__=='__main__':raise SystemExit(main())
