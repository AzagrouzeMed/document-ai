"""OCR geometrique d'abord, complement visuel controle ensuite.
Les roles restent heuristiques; aucune certification ni nouvelle precision mesuree.
"""
import json
import re
from copy import deepcopy
from statistics import median
from moteur.extraction_souple import norm, number
from moteur.corroboration_ocr import prepare
from moteur.extraction_vision import validate_output, Line


def _blocks(raw):
    r=raw.get('res',raw)
    return [dict(id=i+1,texte=str(t),bbox=b) for i,(t,b) in
            enumerate(zip(r.get('rec_texts',[]),r.get('rec_boxes',[])))]


def direct(raw,page):
    """Propose des lignes depuis les cellules, sans texte de reponse attendu."""
    bs=_blocks(raw);seed={'lignes':[]};extra=[]
    if not bs:return validate_output(seed,raw,page)
    h=median(max(1,b['bbox'][3]-b['bbox'][1]) for b in bs)
    cx=lambda b:(b['bbox'][0]+b['bbox'][2])/2
    cy=lambda b:(b['bbox'][1]+b['bbox'][3])/2
    title=[b for b in bs if norm(b['texte']) in ['designation','description','libelle']]
    top=min((b['bbox'][1] for b in title),default=max(cy(b) for b in bs))
    clients=[b for b in bs if norm(b['texte']) in ['client','facturea','facturera','destinataire','alattentionde']]
    cutoff=min((b['bbox'][1] for b in clients),default=top)
    # Un seul nom legal distinct dans l'en-tete fournisseur ou un cachet.
    legal=[b for b in bs if re.search(r'\b(?:SARL(?:AU)?|SAS|EURL|SA)\b',b['texte'],re.I)
           and (cy(b)<cutoff or b['bbox'][1]>top) and not re.search(r'ICE|\bRC\b|@',b['texte'],re.I)]
    if len({norm(b['texte']) for b in legal})==1:seed['fournisseur']=legal[0]['texte']
    if seed.get('fournisseur'):
        address=[b for b in bs if cy(b)<cutoff and
                 re.search(r'\b(?:rue|avenue|boulevard|route|adresse)\b',b['texte'],re.I)
                 and not re.search(r'ICE|@|\bTel\b',b['texte'],re.I)]
        if len(address)==1 and norm(address[0]['texte'])!='adresse':seed['adresse_fournisseur']=address[0]['texte']
    if len(title)==1:
        t=title[0]
        band=[b for b in bs if abs(cy(b)-cy(t))<h]
        numeric_headers=[b for b in band if norm(b['texte']) in
            ['qte','quantite','prix','prixunitaire','pu','prixht','prixunitht','prixunitaireht','puht',
             'prixttc','prixunitairettc','puttc','montant','total','montantht','totalht','montantttc','totalttc','tva','tvapourcent','tauxtva']]
        heads=[t]+numeric_headers
        if numeric_headers:
            totals=[b['bbox'][1] for b in bs if cy(b)>cy(t)+h and re.match(r'^(?:total|soustotal|montanttva|netapayer)',norm(b['texte']))]
            bottom=min(totals,default=max(b['bbox'][3] for b in bs)+h)
            for b in sorted(bs,key=lambda x:(cy(x),x['bbox'][0])):
                if not cy(t)+h*.7<cy(b)<bottom:continue
                if min(heads,key=lambda x:abs(cx(x)-cx(b)))['id']!=t['id']:continue
                if number(b['texte']) is not None:continue
                row=[v for v in bs if abs(cy(v)-cy(b))<h*.8 and v['id']!=b['id']]
                numeric=[v for v in row if number(v['texte']) is not None or re.fullmatch(r'\d+(?:[.,]\d+)?\s*[a-zA-Z]+',v['texte'].strip())]
                if not numeric:continue
                # Multi-line descriptions and ambiguous row geometry are deferred.
                if any(v['id']!=b['id'] and min(heads,key=lambda x:abs(cx(x)-cx(v)))['id']==t['id'] for v in row):
                    extra.append('Description sur plusieurs blocs ou ligne ambigue; complement necessaire.');continue
                item={'designation':b['texte']}
                for v in numeric:
                    if norm(min(heads,key=lambda x:abs(cx(x)-cx(v)))['texte']) in ['qte','quantite']:
                        m=re.fullmatch(r'\s*(\d+(?:[.,]\d+)?)\s*([a-zA-Z]+)?\s*',v['texte'])
                        if m and m[2]:item['unite']=m[2]
                seed['lignes'].append(item)
    prepared,_=prepare(seed,raw)
    # Explicit supplier label, or a single legal-name proposal, is needed.
    for key,names in [('fournisseur',['fournisseur','vendeur','emetteur'])]:
        hits=[]
        for label in bs:
            m=re.fullmatch(r'\s*(Fournisseur|Vendeur|Emetteur)\s*:\s*(.+)',label['texte'],re.I)
            if m:hits.append((m[2],[label]));continue
            if norm(label['texte']) not in names:continue
            below=[b for b in bs if 0<cy(b)-cy(label)<2.5*h and abs(b['bbox'][0]-label['bbox'][0])<h]
            if below:
                b=min(below,key=cy)
                if not re.search(r'ICE|@|adresse',b['texte'],re.I):hits.append((b['texte'],[label,b]))
        if len(hits)==1:
            prepared[key]=hits[0][0]
            prepared['preuves'].append(dict(champ=key,texte_visible=' '.join(b['texte'] for b in hits[0][1]),blocs_ocr=[b['id'] for b in hits[0][1]]))
    data,proofs,alerts=validate_output(prepared,raw,page)
    alerts=[a for a in alerts if not a.startswith('Extraction multimodale experimentale')]
    return data,proofs,list(dict.fromkeys(alerts+extra))


def unresolved(data):
    # Optional absent ICE, delivery or due dates do not force model calls.
    missing=[k for k in ['fournisseur','client','numero_document','date','devise','total_ttc'] if data.get(k) is None]
    if not data.get('lignes'):missing.append('lignes')
    for i,l in enumerate(data.get('lignes',[])):
        for k in ['designation','quantite']:
            if l.get(k) is None:missing.append(f'lignes.{i}.{k}')
        if not any(l.get(k) is not None for k in ['prix_unitaire_ht','prix_unitaire_ttc','prix_unitaire']):missing.append(f'lignes.{i}.prix')
        if not any(l.get(k) is not None for k in ['montant_ht','montant_ttc','montant']):missing.append(f'lignes.{i}.montant')
    return missing


def merge(base,proofs,proposal,extra_proofs):
    """Never overwrite confirmed values; never align repeated rows by index alone."""
    data=deepcopy(base);sources=deepcopy(proofs);alerts=[]
    def fill(target,other,key,path,sourcepath):
        v=other.get(key)
        if v is None:return
        if target.get(key) is None:
            target[key]=v
            if sourcepath in extra_proofs:sources[path]=deepcopy(extra_proofs[sourcepath])
        elif target[key]!=v:alerts.append('Conflit OCR/Qwen : '+path+'; valeur OCR conservee pour revue.')
    for key in base:
        if key in ['lignes','type_document']:continue
        fill(data,proposal,key,key,key)
    if data.get('type_document')=='inconnu':data['type_document']=proposal.get('type_document','inconnu')
    if not data.get('lignes'):
        data['lignes']=deepcopy(proposal.get('lignes',[]))
        sources.update({k:deepcopy(v) for k,v in extra_proofs.items() if k.startswith('lignes.')})
    else:
        for i,line in enumerate(data['lignes']):
            name=norm(line.get('designation') or '')
            candidates=[(j,l) for j,l in enumerate(proposal.get('lignes',[])) if norm(l.get('designation') or '')==name]
            multiplicity=sum(norm(l.get('designation') or '')==name for l in data['lignes'])
            if len(candidates)!=1 or multiplicity!=1:continue
            j,other=candidates[0]
            for key in Line.model_fields:
                # No parallel HT/TTC guess may change the known fiscal basis.
                if key.startswith('prix_unitaire') and any(line.get(k) is not None for k in ['prix_unitaire_ht','prix_unitaire_ttc','prix_unitaire']):continue
                if key in ['montant_ht','montant_ttc','montant'] and any(line.get(k) is not None for k in ['montant_ht','montant_ttc','montant']):continue
                fill(line,other,key,f'lignes.{i}.{key}',f'lignes.{j}.{key}')
        if len(proposal.get('lignes',[]))!=len(data['lignes']):alerts.append('Nombre de lignes OCR/Qwen different; lignes OCR conservees, revue du tableau requise.')
    return data,sources,alerts


def extract(source,index,raw,folder,sans_llm=False,vision=None):
    folder.mkdir(parents=True,exist_ok=True)
    data,proofs,alerts=direct(raw,index+1)
    def save(name,obj):(folder/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding='utf-8')
    save('extraction_ocr_directe.json',dict(donnees=data,preuves=proofs,alertes=alerts))
    missing=unresolved(data);called=False;failed=False
    if missing and source is not None and not sans_llm:
        called=True
        try:
            if vision is None:
                from moteur.extraction_vision import extract as vision
            proposed,ep,ea=vision(source,index,raw,folder/'complement_vision',unresolved_fields=missing)
            data,proofs,conflicts=merge(data,proofs,proposed,ep)
            alerts+=ea+conflicts
        except Exception as exc:
            failed=True;alerts.append('Complement Qwen indisponible; extraction OCR conservee : '+str(exc))
    remaining=unresolved(data)
    if remaining:alerts.append('Champs ou lignes non resolus : '+', '.join(remaining))
    alerts.append('Extraction OCR prioritaire heuristique; revue des roles et des valeurs requise.')
    route=dict(moteur='ocr_prioritaire_qwen_complement' if called else 'ocr_prioritaire',
               qwen_appele=called,qwen_echec=failed,champs_non_resolus=remaining)
    save('decision_pipeline.json',route)
    return data,proofs,list(dict.fromkeys(alerts)),route,bool(remaining or failed)
