"""Serveur local Document AI. Lancement : python lancer_web.py"""
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from rapport_pdf import export_pdf

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
        run=files[0].parent;data=load(files[0]);controls=load(run/'controles.json')
        # Display the uploaded name, not the canonical storage basename.
        data['fichier_source']=load(p/'statut.json')['nom_fichier']
        files[0].write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
        write_status(p,etat='export',message='Création du rapport PDF…')
        pdf_error=None
        try:export_pdf(data,controls,run/'rapport.pdf')
        except Exception as exc:
            pdf_error='Le PDF n’a pas pu être créé. Le JSON reste disponible.'
            (p/'export_erreur.log').write_text(str(exc),encoding='utf-8')
        write_status(p,etat='termine',message='Analyse disponible. Vérifiez les valeurs et les alertes.',
            run_path=str(run.relative_to(p)),document_id=data['document_id'],
            resultat_statut=data.get('statut','a_verifier'),pdf_disponible=(run/'rapport.pdf').exists(),
            avertissement_pdf=pdf_error,nombre_articles=len(data.get('lignes',[])),
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
    run=result_folder(folder(job_id))
    return dict(document=load(run/'document.json'),controles=load(run/'controles.json'))


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
