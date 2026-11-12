"""Serveur local Document AI. Lancement : python lancer_web.py"""
import json
import math
import os
import re
import secrets
import sqlite3
import subprocess
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from rapport_pdf import export_pdf
import stockage
import rag_document
from moteur.validation_donnees import valider_calculs

ROOT=Path(__file__).resolve().parent
DATA=Path(os.environ.get('DOCUMENTAI_DATA_DIR',str(ROOT/'data'))).resolve()
MAX_BYTES=25*1024*1024
TOKEN=secrets.token_urlsafe(32)
POOL=ThreadPoolExecutor(max_workers=1,thread_name_prefix='documentai')
SLOTS=threading.BoundedSemaphore(5)
ALLOWED={'.pdf','.png','.jpg','.jpeg','.webp','.bmp','.tif','.tiff'}
ARTIFACTS={'json':'document.json','pdf':'rapport.pdf','controles':'controles.json',
           'preuves':'preuves.json','texte':'texte_ocr.txt'}
@asynccontextmanager
async def lifespan(_app):
    DATA.mkdir(parents=True,exist_ok=True)
    for p in DATA.iterdir():
        if p.is_dir() and (p/'statut.json').exists():
            try:
                if load(p/'statut.json').get('etat') in ['attente','analyse','export']:
                    write_status(p,etat='echec',message='Ce traitement a été interrompu par un arrêt du serveur. Relancez-le avec le document original.')
            except (OSError,ValueError):pass
    yield

app=FastAPI(title='Document AI local',docs_url=None,redoc_url=None,lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware,allowed_hosts=['*'] if os.environ.get('DOCUMENTAI_PREVIEW')=='1' else ['127.0.0.1','localhost','testserver'])
app.mount('/static',StaticFiles(directory=ROOT/'static'),name='static')


def now():return datetime.now(timezone.utc).isoformat()


def folder(job_id):
    if not re.fullmatch(r'[a-f0-9]{32}',job_id):raise HTTPException(404,'Traitement introuvable.')
    p=DATA/job_id
    if not (p/'statut.json').is_file():raise HTTPException(404,'Traitement introuvable.')
    return p


def load(path):return json.loads(path.read_text(encoding='utf-8'))


def write_status(p,**changes):
    path=p/'statut.json';value=load(path) if path.exists() else {}
    value.update(changes,updated_at=now());temp=p/'statut.tmp'
    temp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(path)
    return value


def public_status(p):
    value=load(p/'statut.json');value.pop('run_path',None)
    return value


def result_folder(p):
    value=load(p/'statut.json')
    if value.get('etat')!='termine':raise HTTPException(409,'Le résultat est encore indisponible.')
    run=p/value['run_path']
    if not run.resolve().is_relative_to(p.resolve()):raise HTTPException(404,'Résultat invalide.')
    return run


def build_command(p,source,lang,sans_llm):
    cmd=[sys.executable,str(ROOT/'traitement_document.py'),'--input',str(source),
         '--output',str(p/'resultats'),'--lang',lang]
    if sans_llm:cmd.append('--sans-llm')
    return cmd


def lire_archives_ocr(run):
    """Lit exclusivement les archives FR/AR du resultat courant, sans OCR."""
    pages=[]
    for chemin in sorted((run/'ocr').glob('page_*.json')):
        match=re.fullmatch(r'page_([1-9][0-9]*)_(fr|ar)\.json',chemin.name)
        if not match:continue
        if not chemin.resolve().is_relative_to(run.resolve()):
            raise ValueError('Archive OCR hors du resultat courant.')
        numero=int(match[1]);langue=match[2]
        brut=json.loads(chemin.read_text(encoding='utf-8-sig'))
        if not isinstance(brut,dict):raise ValueError('Archive OCR invalide.')
        brut=brut.get('res',brut)
        if not isinstance(brut,dict):raise ValueError('Lecture OCR invalide.')
        textes=brut.get('rec_texts')
        if not isinstance(textes,list) or any(not isinstance(texte,str) for texte in textes):
            raise ValueError('Texte OCR invalide.')
        if 'page_index' in brut and (type(brut['page_index']) is not int or brut['page_index']!=numero-1):
            raise ValueError('Numero de page OCR incoherent.')
        pages.append(dict(numero_page=numero,langue=langue,texte_ocr='\n'.join(textes),source=f'ocr/{chemin.name}'))
    return pages


def sauvegarder_textes_ocr(p,facture_id,run):
    try:
        pages=lire_archives_ocr(run)
        if not pages:return dict(etat='absent',message='Aucun texte OCR enregistré')
        nombre=stockage.enregistrer_pages_document(facture_id,pages,DATA/'document_ai.db',traitement_id=p.name)
        return dict(etat='enregistre',nombre_lectures=nombre,message='Lectures OCR enregistrées.')
    except Exception as exc:
        # L'archivage OCR ne doit pas annuler la facture ni masquer l'extraction.
        try:(p/'stockage_ocr_erreur.log').write_text(f'{type(exc).__name__}: {exc}',encoding='utf-8')
        except OSError:pass
        return dict(etat='echec',message="La facture est enregistrée, mais ses textes OCR n'ont pas pu être sauvegardés. Les archives sont conservées.")


def sauvegarder_resultat(p,source,json_brut,*,run=None):
    """Sauvegarde independante du PDF ; retourne un diagnostic pour statut.json."""
    try:
        chemin_base=DATA/'document_ai.db'
        # Initialisation reexecutable et connexion locale a chaque operation.
        stockage.initialiser_base(chemin_base)
        facture_id=stockage.enregistrer_facture(json_brut,chemin_base,
            traitement_id=p.name,chemin_original=str(source.resolve()))
        if run is None:
            document_id=json.loads(json_brut).get('document_id')
            if isinstance(document_id,str) and re.fullmatch(r'DOC-[A-Za-z0-9_-]+',document_id):
                run=p/'resultats'/document_id
        textes_ocr=sauvegarder_textes_ocr(p,facture_id,run) if run is not None else dict(etat='absent',message='Aucun texte OCR enregistré')
        return dict(etat='enregistre',facture_id=facture_id,textes_ocr=textes_ocr,
            message='Extraction enregistrée. Les valeurs restent à vérifier.',diagnostic=None)
    except Exception as exc:
        diagnostic=f'{type(exc).__name__}: {exc}'
        # Un journal indisponible ne doit pas masquer le resultat d'extraction.
        try:(p/'stockage_erreur.log').write_text(diagnostic,encoding='utf-8')
        except OSError:pass
        return dict(etat='echec',facture_id=None,
            message="L'extraction est disponible, mais l'enregistrement a échoué. Les fichiers sont conservés.",
            diagnostic=diagnostic)


def worker(p,source,lang,sans_llm):
    try:
        write_status(p,etat='analyse',message='Lecture des pages et extraction en cours…')
        env=os.environ.copy();env['PYTHONUTF8']='1';env['PYTHONUNBUFFERED']='1'
        with (p/'traitement.log').open('w',encoding='utf-8') as stream:
            process=subprocess.run(build_command(p,source,lang,sans_llm),stdout=stream,
                stderr=subprocess.STDOUT,env=env,timeout=3600)
        files=list((p/'resultats').glob('DOC-*/document.json'))
        if process.returncode or len(files)!=1:
            raise RuntimeError('Le traitement a échoué. Le diagnostic est conservé dans le dossier de ce traitement.')
        run=files[0].parent;json_brut=files[0].read_bytes().decode('utf-8')
        data=json.loads(json_brut);controls=load(run/'controles.json')
        if not isinstance(data,dict) or not isinstance(data.get('document_id'),str) or not data['document_id']:
            raise ValueError('Le JSON final ne contient pas un document exploitable.')
        # Display the uploaded name, not the canonical storage basename.
        data['fichier_source']=load(p/'statut.json')['nom_fichier']
        # Le nom affiche reste en memoire ; le JSON final n'est plus reecrit.
        sauvegarde=sauvegarder_resultat(p,source,json_brut,run=run)
        write_status(p,etat='export',message='Création du rapport PDF…')
        pdf_error=None
        try:export_pdf(data,controls,run/'rapport.pdf')
        except Exception as exc:
            pdf_error='Le PDF n’a pas pu être créé. Le JSON reste disponible.'
            (p/'export_erreur.log').write_text(str(exc),encoding='utf-8')
        write_status(p,etat='termine',message='Analyse disponible. Vérifiez les valeurs et les alertes.',
            run_path=str(run.relative_to(p)),document_id=data['document_id'],
            resultat_statut=data.get('statut','a_verifier'),pdf_disponible=(run/'rapport.pdf').exists(),
            avertissement_pdf=pdf_error,sauvegarde=sauvegarde,nombre_articles=len(data.get('lignes',[])),
            nombre_pages=len(data.get('pages_traitees',[])))
    except subprocess.TimeoutExpired:
        write_status(p,etat='echec',message='Temps maximal de traitement dépassé. Vous pouvez réessayer avec moins de pages.')
    except Exception as exc:
        write_status(p,etat='echec',message=str(exc))
    finally:SLOTS.release()


@app.middleware('http')
async def protect(request:Request,call_next):
    if request.method=='POST' and request.headers.get('x-documentai-token')!=TOKEN:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=403,content={'detail':'Rechargez la page avant de lancer le traitement.'})
    response=await call_next(request)
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['X-Frame-Options']='SAMEORIGIN'
    response.headers['Cache-Control']='no-store'
    return response


@app.get('/')
def home():return FileResponse(ROOT/'static/index.html')


@app.get('/api/config')
def config():return dict(token=TOKEN,taille_max_mo=25,pages_max=20,fournisseur='gemini',gemini_configure=bool(os.environ.get('GEMINI_API_KEY') and os.environ.get('GEMINI_MODEL')),modele=os.environ.get('GEMINI_MODEL',''))


@app.get('/api/traitements')
def history():
    if not DATA.is_dir():return []
    rows=[]
    for p in DATA.iterdir():
        if p.is_dir() and (p/'statut.json').is_file():
            try:rows.append(public_status(p))
            except (OSError,ValueError):continue
    return sorted(rows,key=lambda v:v.get('created_at',''),reverse=True)[:50]


# Projection explicite : aucune metadonnee technique ni JSON brut dans ces GET.
CHAMPS_LISTE_FACTURES=('id','numero_document','fournisseur','client','date_facture','devise','statut')
CHAMPS_DETAIL_FACTURE=CHAMPS_LISTE_FACTURES + (
    'type_document','document_id','ice_fournisseur','ice_client','date_echeance',
    'adresse_fournisseur','adresse_client','adresse_livraison',*stockage.CHAMPS_NOMBRES)
CHAMPS_ARTICLE=('numero_ligne','designation','unite',*stockage.NOMBRES_ARTICLE,'page')
# Sous-ensemble metier explicite ; les montants gardent leur formulaire existant.
CHAMPS_ENTETE=tuple(champ for champ in (
    'fournisseur','client','ice_fournisseur','ice_client','numero_document',
    'date_facture','date_echeance','adresse_fournisseur','adresse_client','adresse_livraison')
    if champ in stockage.CHAMPS_MODIFIABLES)



def lire_factures_enregistrees(facture_id=None):
    try:
        base=DATA/'document_ai.db'
        if not base.exists():
            return [] if facture_id is None else None
        # Ne pas initialiser/migrer la base pendant une consultation.
        if facture_id is None:
            return stockage.lister_factures(base,lecture_seule=True)
        return stockage.lire_facture(facture_id,base,lecture_seule=True)
    except (sqlite3.Error,OSError):
        raise HTTPException(503,'Lecture des factures enregistrées indisponible. Réessayez plus tard.') from None


@app.get('/api/factures')
def factures_enregistrees():
    return [{champ:f.get(champ) for champ in CHAMPS_LISTE_FACTURES}
            for f in lire_factures_enregistrees()]


@app.get('/api/factures/{facture_id}')
def facture_enregistree(facture_id:int):
    facture=lire_factures_enregistrees(facture_id)
    if facture is None:
        raise HTTPException(404,'Facture introuvable.')
    detail={champ:facture.get(champ) for champ in CHAMPS_DETAIL_FACTURE}
    detail['champs_modifiables']=list(CHAMPS_ENTETE)
    detail['lignes']=[{champ:ligne.get(champ) for champ in CHAMPS_ARTICLE}
                      for ligne in facture['lignes']]
    return detail


def exiger_facture_enregistree(facture_id):
    facture=lire_factures_enregistrees(facture_id)
    if facture is None:
        raise HTTPException(404,'Facture introuvable.')
    return facture


# Codes fixes : choix d'un champ autorise, sans SQL ni generation de texte libre.
QUESTIONS_FACTURE={
    'fournisseur':('fournisseur','Fournisseur'),
    'client':('client','Client'),
    'date_facture':('date_facture','Date de facture'),
    'total_ttc':('total_ttc','Total TTC'),
}


@app.get('/api/factures/{facture_id}/questions/{code_question}')
def question_facture(facture_id:int,code_question:str):
    if code_question not in QUESTIONS_FACTURE:
        raise HTTPException(422,'Question non autorisée. Choisissez une des quatre questions proposées.')
    facture=exiger_facture_enregistree(facture_id)
    champ,libelle=QUESTIONS_FACTURE[code_question]
    valeur=facture.get(champ)
    def texte(value):
        return 'Information non disponible' if value is None or isinstance(value,str) and not value.strip() else str(value)
    numero=facture.get('numero_document')
    statut=facture.get('statut')
    statut_affiche='à vérifier' if statut=='a_verifier' else texte(statut)
    information=texte(valeur)
    if champ=='total_ttc' and information!='Information non disponible' and facture.get('devise'):
        information+=' '+facture['devise']
    reponse=f'Facture {texte(numero)}. Statut : {statut_affiche}. {libelle} : {information}.'
    if statut=='partiel':
        reponse+=' Résultat partiel : cette facture reste à vérifier avec le document original.'
    elif statut=='a_verifier':
        reponse+=' Cette facture reste à vérifier avec le document original.'
    elif statut is None or isinstance(statut,str) and not statut.strip():
        reponse+=' Statut non renseigné : vérifiez les données avec le document original.'
    return dict(facture_id=facture_id,question=code_question,numero_document=numero,
                statut=statut,valeur=valeur,reponse=reponse)


@app.get('/api/factures/{facture_id}/recherche')
def rechercher_document_facture(facture_id:int,q:str=''):
    exiger_facture_enregistree(facture_id)
    try:
        return stockage.rechercher_pages_document(facture_id,q,DATA/'document_ai.db')
    except ValueError as exc:
        raise HTTPException(422,str(exc)) from None
    except (sqlite3.Error,OSError):
        raise HTTPException(503,'Recherche documentaire indisponible. Réessayez plus tard.') from None


@app.post('/api/factures/{facture_id}/question-document')
def question_document(facture_id:int,payload:dict):
    exiger_facture_enregistree(facture_id)
    if set(payload)!={'question'} or not isinstance(payload['question'],str):
        raise HTTPException(422,'Fournissez uniquement une question texte.')
    question=payload['question'].strip()
    try:
        recherche=stockage.rechercher_pages_document(facture_id,question,DATA/'document_ai.db',question=True)
    except ValueError as exc:
        raise HTTPException(422,str(exc)) from None
    except (sqlite3.Error,OSError):
        raise HTTPException(503,'Recherche documentaire indisponible.') from None
    passages=[dict(p,id=f'E{i+1}') for i,p in enumerate(recherche['passages'][:5])]
    resultat=dict(etat=recherche['etat'],message=recherche['message'],reponse=None,passages=passages,references=[])
    if not passages:return resultat
    try:
        genere=rag_document.generer(question,passages)
        resultat.update(genere,etat='reponse',message='Réponse fondée sur les extraits OCR ; vérifiez le document original.')
    except Exception:
        resultat.update(etat='generation_indisponible',message='Réponse générée indisponible. Consultez les extraits trouvés.')
    return resultat


@app.get('/api/factures/{facture_id}/historique')
def historique_facture(facture_id:int):
    exiger_facture_enregistree(facture_id)
    try:
        historique=stockage.lire_validations(facture_id,DATA/'document_ai.db',lecture_seule=True)
    except (sqlite3.Error,OSError):
        raise HTTPException(503,'Historique indisponible. Réessayez plus tard.') from None
    # Ne pas publier d'anciennes corrections de chemins ou de metadonnees internes.
    champs=('id','champ','ancienne_valeur','nouvelle_valeur','date_action','action')
    return [{champ:ligne.get(champ) for champ in champs} for ligne in historique
            if ligne['champ'] in CHAMPS_DETAIL_FACTURE]


def valider_correction_entete(payload):
    if not isinstance(payload,dict) or set(payload)!={'champ','nouvelle_valeur'}:
        raise HTTPException(422,'Fournissez uniquement champ et nouvelle_valeur.')
    champ=payload['champ']
    if not isinstance(champ,str) or champ not in CHAMPS_ENTETE:
        raise HTTPException(422,"Ce champ ne peut pas être corrigé dans l'en-tête.")
    valeur=payload['nouvelle_valeur']
    if valeur is not None:
        if not isinstance(valeur,str):
            raise HTTPException(422,'La valeur doit être un texte ou null.')
        valeur=valeur.strip() or None
    if valeur is not None:
        limite=4000 if champ.startswith('adresse_') else 500
        if len(valeur)>limite or any(ord(c)<32 and c not in '\n\t' for c in valeur):
            raise HTTPException(422,'Texte trop long ou contenant des caractères de contrôle interdits.')
        if champ in ('date_facture','date_echeance'):
            try:
                if not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}',valeur):raise ValueError()
                date.fromisoformat(valeur)
            except ValueError:
                raise HTTPException(422,'Date invalide : utilisez AAAA-MM-JJ.') from None
    return champ,valeur


@app.post('/api/factures/{facture_id}/corrections')
async def corriger_entete_facture(facture_id:int,request:Request):
    try:payload=await request.json()
    except ValueError:raise HTTPException(422,'Saisie JSON invalide.') from None
    champ,valeur=valider_correction_entete(payload)
    exiger_facture_enregistree(facture_id)
    try:
        validation_id=stockage.corriger_champ(facture_id,champ,valeur,chemin_base=DATA/'document_ai.db')
    except ValueError:
        raise HTTPException(422,'Correction refusée. Vérifiez le champ et sa valeur.') from None
    except (sqlite3.Error,OSError):
        raise HTTPException(503,"Enregistrement impossible. La correction n'a pas été sauvegardée.") from None
    return dict(validation_id=validation_id,champ=champ,nouvelle_valeur=valeur,
                message='Correction enregistrée. La facture reste à vérifier.')


def validate_file(path,suffix):
    if suffix=='.pdf':
        from pypdf import PdfReader
        if path.read_bytes()[:5]!=b'%PDF-':raise ValueError('Le fichier ne semble pas être un PDF valide.')
        reader=PdfReader(str(path))
        if reader.is_encrypted:raise ValueError('Les PDF protégés par mot de passe ne sont pas pris en charge.')
        count=len(reader.pages)
        if count<1 or count>20:raise ValueError('Le document doit contenir entre 1 et 20 pages.')
    else:
        from PIL import Image
        with Image.open(path) as im:
            if im.width*im.height>40_000_000:raise ValueError('Image trop grande : maximum 40 millions de pixels.')
            im.verify()


@app.post('/api/traitements',status_code=202)
async def create(file:UploadFile=File(...),lang:str=Form('auto'),sans_llm:bool=Form(False)):
    if lang not in ['auto','fr','ar']:raise HTTPException(422,'Langue invalide.')
    if not sans_llm and not (os.environ.get('GEMINI_API_KEY') and os.environ.get('GEMINI_MODEL')):
        raise HTTPException(503,'Configurer GEMINI_API_KEY et GEMINI_MODEL dans le terminal, puis relancer le serveur.')
    name=Path((file.filename or '').replace('\\','/')).name
    suffix=Path(name).suffix.lower()
    if suffix not in ALLOWED:raise HTTPException(415,'Choisissez un PDF, PNG, JPEG, WebP, BMP ou TIFF.')
    if not SLOTS.acquire(blocking=False):raise HTTPException(429,'La file est pleine. Attendez la fin d’un traitement.')
    job_id=uuid.uuid4().hex;p=DATA/job_id;p.mkdir(parents=True,exist_ok=False)
    source=p/('source'+suffix);size=0
    try:
        with source.open('wb') as stream:
            while chunk:=await file.read(1024*1024):
                size+=len(chunk)
                if size>MAX_BYTES:raise HTTPException(413,'Fichier trop volumineux : maximum 25 Mo.')
                stream.write(chunk)
        if not size:raise HTTPException(422,'Le fichier est vide.')
        try:validate_file(source,suffix)
        except Exception as exc:raise HTTPException(422,str(exc)) from exc
        write_status(p,id=job_id,nom_fichier=name,source=source.name,created_at=now(),
            taille_octets=size,etat='attente',message='Document en attente de traitement.',langue=lang,sans_llm=sans_llm)
        POOL.submit(worker,p,source,lang,sans_llm)
    except Exception:
        SLOTS.release()
        import shutil
        shutil.rmtree(p,ignore_errors=True)
        raise
    finally:await file.close()
    return dict(id=job_id)


@app.get('/api/traitements/{job_id}')
def status(job_id:str):return public_status(folder(job_id))


@app.get('/api/traitements/{job_id}/resultat')
def result(job_id:str):
    p=folder(job_id);run=result_folder(p)
    document=load(run/'document.json')
    nom_fichier=load(p/'statut.json').get('nom_fichier')
    if nom_fichier is not None:document['fichier_source']=nom_fichier
    saved=load(run/'montants_valides.json') if (run/'montants_valides.json').is_file() else None
    if saved:
        document.update(saved['montants'])
    return dict(document=document,controles=load(run/'controles.json'),montants_validation=saved)


MONTANTS=('total_ht','montant_tva','total_ttc')


def parse_montant(value):
    if value is None or isinstance(value,str) and not value.strip():return None
    if isinstance(value,str):
        value=value.strip()
        if not re.fullmatch(r'[+-]?[0-9]+(?:[.,][0-9]+)?',value):
            raise HTTPException(422,'Montant invalide : utilisez un nombre avec une virgule ou un point décimal.')
        value=value.replace(',','.')
    elif type(value) not in (int,float):
        raise HTTPException(422,'Montant invalide.')
    try:value=float(value)
    except (ValueError,OverflowError):raise HTTPException(422,'Montant invalide.')
    if not math.isfinite(value):raise HTTPException(422,'Montant invalide.')
    return value


@app.post('/api/traitements/{job_id}/valider-montants')
async def validate_amounts(job_id:str,request:Request):
    run=result_folder(folder(job_id))
    try:payload=await request.json()
    except ValueError:raise HTTPException(422,'Saisie JSON invalide.')
    if not isinstance(payload,dict) or set(payload)!=set(MONTANTS):
        raise HTTPException(422,'Fournissez uniquement total_ht, montant_tva et total_ttc.')
    montants={key:parse_montant(payload[key]) for key in MONTANTS}
    controle=valider_calculs(montants)[-1]
    if controle['etat']!='coherent':
        from fastapi.responses import JSONResponse
        message='Contrôle incomplet' if controle['etat']=='non_verifiable' else f"Montants incohérents : écart de {controle['ecart']}."
        return JSONResponse(status_code=422,content=dict(detail=message,controle=controle,montants=montants))
    saved=dict(montants=montants,controle=controle,validated_at=now(),statut='montants_valides')
    # A unique temporary file keeps concurrent submissions atomic.
    temp=run/('montants-'+uuid.uuid4().hex+'.tmp')
    try:
        temp.write_text(json.dumps(saved,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
        temp.replace(run/'montants_valides.json')
    finally:temp.unlink(missing_ok=True)
    return saved


@app.get('/api/traitements/{job_id}/source')
def source(job_id:str):
    p=folder(job_id);value=load(p/'statut.json');name=value['source']
    return FileResponse(p/name,filename=value['nom_fichier'],content_disposition_type='inline')


@app.get('/api/traitements/{job_id}/telecharger/{kind}')
def download(job_id:str,kind:str):
    if kind not in ARTIFACTS:raise HTTPException(404,'Export inconnu.')
    run=result_folder(folder(job_id));path=run/ARTIFACTS[kind]
    if not path.is_file():raise HTTPException(404,'Ce fichier est indisponible.')
    return FileResponse(path,filename=f'{load(run/"document.json")["document_id"]}_{path.name}')
