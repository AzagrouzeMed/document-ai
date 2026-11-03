"""Rapprochement de propositions visuelles avec le texte ET les roles OCR.
Les rapprochements sont des controles heuristiques, jamais une precision mesuree.
"""
import re
from copy import deepcopy
from datetime import datetime
from statistics import median
from moteur.extraction_souple import norm,number

def prepare(payload,raw):
    if not isinstance(payload,dict):raise ValueError('Reponse JSON non objet.')
    d=deepcopy(payload);d['preuves']=[];notices=[]
    # Normalisation des nombres proposes avant les controles geometriques.
    def normalize_numeric(obj, keys):
        for key in keys:
            value = obj.get(key)
            if isinstance(value, str):
                texte = value.rstrip('%').strip() if key == 'taux_tva' else value
                obj[key] = number(texte)
                if obj[key] is None:
                    notices.append('Nombre ambigu rejete : ' + key)

    normalize_numeric(d, ['total_ht','montant_tva','total_ttc','taux_tva'])
    if isinstance(d.get('lignes'), list):
        for ligne in d['lignes']:
            if isinstance(ligne, dict):
                if 'tva' in ligne:
                    alias=ligne.pop('tva')
                    if ligne.get('taux_tva') is None:ligne['taux_tva']=alias
                    elif number(str(alias).rstrip('%')) != number(str(ligne['taux_tva']).rstrip('%')):
                        ligne['taux_tva']=None;notices.append('Taux TVA contradictoires, revue requise.')
                normalize_numeric(ligne, [
                    'quantite','prix_unitaire_ht','montant_ht',
                    'prix_unitaire','montant','prix_unitaire_ttc','montant_ttc','taux_tva','hauteur_cm','largeur_cm'
                ])

    r=raw.get('res',raw)
    bs=[dict(id=i+1,texte=t,bbox=b) for i,(t,b) in enumerate(zip(r.get('rec_texts',[]),r.get('rec_boxes',[])))]
    if not bs:return d,['OCR vide : propositions non corroborees.']
    cx=lambda b:(b['bbox'][0]+b['bbox'][2])/2
    cy=lambda b:(b['bbox'][1]+b['bbox'][3])/2
    h=median(max(1,b['bbox'][3]-b['bbox'][1]) for b in bs)
    def proof(key,blocks):
        if blocks:d['preuves'].append(dict(champ=key,texte_visible=' '.join(b['texte'] for b in blocks),blocs_ocr=[b['id'] for b in blocks]))
    def labels(names):return [b for b in bs if norm(b['texte']) in names]
    def right(b):
        return sorted([v for v in bs if v['bbox'][0]>=b['bbox'][2] and abs(cy(v)-cy(b))<h*.65],key=lambda v:v['bbox'][0])
    placeholders={'votreclient','nomduclient','client','prenomnom','nomprenom','nomsociete','nomdelasociete','nomentreprise','votresociete','adresse'}
    def field(key,names,parser=None):
        hits=[]
        for block in bs:
            parts=re.fullmatch(r'\s*(.+?)\s*[:：]\s*(.+)\s*',block['texte'])
            if parts and norm(parts[1]) in names:
                v=parser(parts[2]) if parser else parts[2].strip()
                if v is not None:hits.append((v,[block]))
        for label in labels(names):
            cells=right(label)
            if len(cells)==1:
                v=parser(cells[0]['texte']) if parser else cells[0]['texte'].strip()
                if v is not None:hits.append((v,[label,cells[0]]))
        if len(hits)==1:
            v,blocks=hits[0]
            if d.get(key) is not None and d[key]!=v:notices.append('Valeur harmonisee depuis un libelle OCR : '+key)
            d[key]=v;proof(key,blocks)
    def dt(s):
        for fmt in ['%d/%m/%Y','%Y-%m-%d','%d.%m.%Y','%d-%m-%Y']:
            try:return datetime.strptime(s.strip(),fmt).date().isoformat()
            except ValueError:pass
        short=re.fullmatch(r'(\d{1,2})/(\d{1,2})/(\d{2})',s.strip())
        if short:
            try:
                value=datetime(2000+int(short[3]),int(short[2]),int(short[1])).date().isoformat()
                notices.append('Annee sur deux chiffres interpretee dans 2000-2099, revue requise : '+s)
                return value
            except ValueError:pass
        return None
    field('numero_document',['facturen','factureno','numerofacture','numerodocument','devisn','ndefacture','numerodelafacture'])
    # N° followed by the identifier in the same OCR block, without a colon.
    ids=[]
    for block in bs:
        match=re.fullmatch(r'\s*(?:N[°º]|N[oO]\.?|Facture\s*N[°º])\s*[:：]?\s*([A-Za-z0-9][A-Za-z0-9./_-]*)\s*',block['texte'],re.I)
        if match:ids.append((match[1],block))
    if len({v for v,b in ids})==1:
        d['numero_document']=ids[0][0];proof('numero_document',[b for v,b in ids])
    field('ice_fournisseur',['icefournisseur'])
    field('ice_client',['iceclient'])
    field('date',['date','datedefacture','datefacture','datedefacturation'],dt)
    field('date_echeance',['echeance','dateecheance','datedecheance'],dt)
    field('total_ht',['totalht','montanttotalht','soustotalht'],number)
    field('total_ttc',['total','totalttc','totalapayer','totalapayereur','netapayer','totalttceur'],number)
    # TVA labels can include the rate: the amount must be a separate aligned cell.
    vat=[b for b in bs if re.fullmatch(r'(?:montant)?tva\d*(?:0)?',norm(b['texte']))]
    if len(vat)==1:
        label=vat[0];cells=right(label)
        amounts=[b for b in cells if '%' not in b['texte'] and number(b['texte']) is not None]
        if len(amounts)==1:
            d['montant_tva']=number(amounts[0]['texte']);proof('montant_tva',[label,amounts[0]])
        rate_hits=[]
        for block in [label]+cells:
            rates=re.findall(r'(\d+(?:[.,]\d+)?)\s*%',block['texte'])
            if len(rates)==1:rate_hits.append((float(rates[0].replace(',','.')),block))
        if len(rate_hits)==1:d['taux_tva']=rate_hits[0][0];proof('taux_tva',[rate_hits[0][1]])
    for k in ['date','date_echeance']:
        if isinstance(d.get(k),str):d[k]=dt(d[k])
    currencies=[]
    for code,pat in [('EUR',r'€|\bEUR\b'),('MAD',r'\bMAD\b|\bDhs?\b'),('USD',r'\bUSD\b')]:
        matches=[b for b in bs if re.search(pat,b['texte'],re.I)]
        if matches:currencies.append((code,matches))
    if len(currencies)==1:d['devise']=currencies[0][0];proof('devise',currencies[0][1])
    billing=labels(['facturea','facturera','client','destinataire','alattentionde'])
    shipping=labels(['envoyea','livrea','expediera','expediea','adresselivraison','adressedelivraison'])
    tableheads=labels(['designation','description','libelle'])
    top_table=min((b['bbox'][1] for b in tableheads),default=max(cy(b) for b in bs))
    anchors=billing+shipping
    def zone(label):
        # Other column titles can sit lower than CLIENT: bound the zone by
        # their horizontal starts, provided they precede the table.
        peers=[b['bbox'][0] for b in bs if
               (abs(cy(b)-cy(label))<h or
                (norm(b['texte']) in ['date','modedepaiement','echeance']
                 and label['bbox'][1]-h<=b['bbox'][1]<top_table))
               and b['bbox'][0]>label['bbox'][0]+h]
        end=min(peers,default=max(b['bbox'][2] for b in bs)+h)
        return sorted([b for b in bs if b['bbox'][1]>=label['bbox'][3]-h*.25 and cy(b)>cy(label)+h*.4 and cy(b)<top_table and label['bbox'][0]-h<=b['bbox'][0]<end-h*.3],key=lambda b:(b['bbox'][1],b['bbox'][0]))
    if len(billing)==1:
        region=zone(billing[0])
        if region:
            name=region[0]
            if norm(name['texte']) not in placeholders:
                d['client']=name['texte'];proof('client',[billing[0],name])
            else:d['client']=None;notices.append('Identite client de modele non renseignee.')
            addr=[b for b in region[1:] if not re.search(r'@|IBAN|SIRET|TVA|\bICE\b|Code\s*client|^\s*\+?\d[\d .-]{8,}$|T[eé]l[eé]?\s*[:.]|Fax\s*:',b['texte'],re.I)]
            if addr:d['adresse_client']=', '.join(re.sub(r'^Adresse\s*:\s*','',b['texte'],flags=re.I) for b in addr);proof('adresse_client',addr)
    if len(shipping)==1:
        region=zone(shipping[0])
        if region:
            # Name repeated as first shipping line: address starts after it.
            if norm(region[0]['texte']) in placeholders or (d.get('client') and norm(region[0]['texte'])==norm(d['client'])):region=region[1:]
            region=[b for b in region if not re.search(r'T[eé]l[eé]?\s*[:.]|Fax\s*:|@',b['texte'],re.I)]
            if region:d['adresse_livraison']=', '.join(re.sub(r'^Adresse\s*:\s*','',b['texte'],flags=re.I) for b in region);proof('adresse_livraison',region)
    if d.get('fournisseur') and norm(d['fournisseur']) in placeholders:
        d['fournisseur']=None;notices.append('Identite fournisseur de modele non renseignee.')
    cutoff=min((b['bbox'][1] for b in anchors),default=top_table)
    supplier=[b for b in bs if d.get('fournisseur') and norm(b['texte'])==norm(d['fournisseur']) and cy(b)<cutoff]
    if supplier:
        name=min(supplier,key=cy);d['fournisseur']=name['texte'];proof('fournisseur',[name])
        address=sorted([b for b in bs if name['bbox'][3]<=b['bbox'][1]<cutoff and abs(b['bbox'][0]-name['bbox'][0])<h and not re.search(r'@',b['texte'])],key=cy)
        if address:d['adresse_fournisseur']=', '.join(b['texte'] for b in address);proof('adresse_fournisseur',address)
    # Dates below their own label in a bounded column (not all text below CLIENT).
    for key,names in [('date',['date','datedefacturation']),('date_echeance',['echeance'])]:
        hits=[]
        for label in labels(names):
            for b in bs:
                if (b['id']!=label['id']
                    and 0 < cy(b)-cy(label) <= 3*h
                    and b['bbox'][1]>=label['bbox'][3]-h*.6
                    and b['bbox'][0]>=label['bbox'][0]-h*.5
                    and b['bbox'][0]<=label['bbox'][2]+h
                    and dt(b['texte'])):
                    hits.append((dt(b['texte']),[label,b]))
        if len(hits)==1:d[key]=hits[0][0];proof(key,hits[0][1])
    # Unlabelled date accepted only if unique in the invoice-reference header.
    if d.get('date') is None and ids:
        candidates=[(dt(b['texte']),b) for b in bs if cy(b)<cutoff and dt(b['texte'])]
        if len(candidates)==1:
            d['date']=candidates[0][0];proof('date',[candidates[0][1]])
    # Beneficiary explicitly named in payment instructions, corroborated by a
    # separate name block in the supplier header. No supplier name is hard-coded.
    beneficiaries=[]
    for b in bs:
        match=re.fullmatch(r"\s*Paiement\s+[aà]\s+l['’]ordre\s+de\s+(.+?)\s*",b['texte'],re.I)
        if match:
            names=[v for v in bs if cy(v)<top_table and norm(v['texte'])==norm(match[1])]
            if len(names)==1:beneficiaries.append((names[0]['texte'],[b,names[0]]))
    if len(beneficiaries)==1:
        d['fournisseur']=beneficiaries[0][0];proof('fournisseur',beneficiaries[0][1])
        name=beneficiaries[0][1][1]
        addresses=[b for b in bs if cy(name)<cy(b)<top_table
                   and abs(b['bbox'][0]-name['bbox'][0])<h
                   and re.match(r'^\d+\s+[A-Za-zÀ-ÿ]',b['texte'])
                   and not re.search(r'@|ICE|https?://',b['texte'],re.I)]
        if len(addresses)==1:
            d['adresse_fournisseur']=addresses[0]['texte'];proof('adresse_fournisseur',addresses)
    # ICE must be explicitly labelled; repeated supplier IDs are allowed.
    client_region=zone(billing[0]) if len(billing)==1 else []
    client_ids={b['id'] for b in client_region}
    for key,is_client in [('ice_client',True),('ice_fournisseur',False)]:
        hits=[]
        for b in bs:
            matches=re.findall(r'\bICE\s*[:：]?\s*(\d{15})(?!\d)',b['texte'],re.I)
            in_client=b['id'] in client_ids
            if in_client!=is_client:continue
            for value in matches:hits.append((value,b))
        if hits and len({v for v,b in hits})==1:
            d[key]=hits[0][0];proof(key,[b for v,b in hits])
    # A full legal name may occur in the stamp, not in the split logo.
    proposed=d.get('fournisseur')
    supplier_matches=[b for b in bs if proposed and norm(b['texte'])==norm(proposed)
                      and b['id'] not in client_ids and b['bbox'][1]<top_table or
                      (proposed and norm(b['texte'])==norm(proposed)
                       and re.search(r'\b(?:SARL|SARLAU|SA|SAS|EURL)\b',b['texte'],re.I))]
    if supplier_matches:proof('fournisseur',supplier_matches)
    # Corroborate a proposed supplier address only in the supplier header.
    proposed=d.get('adresse_fournisseur')
    if proposed:
        candidates=[b for b in bs if cy(b)<cutoff and norm(b['texte'])==norm(proposed)]
        if len(candidates)==1:proof('adresse_fournisseur',candidates)

    # Source row and column both constrain numeric article evidence.
    aliases={'quantite':['qte','quantite'],'prix_unitaire_ht':['prixunitht','prixunitaireht','prixht','puht'],'montant_ht':['montantht','totalht'],
             'prix_unitaire':['prix','prixunitaire','pu'],'montant':['montant','total'],
             'prix_unitaire_ttc':['prixttc','prixunitairettc','puttc'],'montant_ttc':['totalttc','montantttc'],
             'taux_tva':['tvapourcent','tva','tauxtva']}
    if len(tableheads)==1:
        title=tableheads[0];band=[b for b in bs if abs(cy(b)-cy(title))<h]
        cols={key:next((b for b in band if norm(re.sub(r'\([^)]*\)','',b['texte'])) in names),None) for key,names in aliases.items()}
        heads=[title]+[b for b in cols.values() if b]
        used_rows=set()
        for i,line in enumerate(d.get('lignes',[])):
            if not isinstance(line,dict):continue
            # Explicit TTC headings override an incorrect HT interpretation;
            # amounts themselves are recovered only from their source cells below.
            for ttc,ht,neutral in [('prix_unitaire_ttc','prix_unitaire_ht','prix_unitaire'),
                                   ('montant_ttc','montant_ht','montant')]:
                if cols.get(ttc):
                    line[ht]=None;line[neutral]=None
            if 'tva' in line:
                line.setdefault('taux_tva',number(str(line.pop('tva')).rstrip('%')))
            names=[b for b in bs if line.get('designation') and norm(b['texte'])==norm(line['designation']) and cy(b)>cy(title)]
            names=sorted([b for b in names if b['id'] not in used_rows],key=cy)
            if not names:continue
            desc=names[0];used_rows.add(desc['id']);proof(f'lignes.{i}.designation',[desc])
            row=[b for b in bs if abs(cy(b)-cy(desc))<h*.8 and b['id']!=desc['id']]
            for key,head in cols.items():
                value=line.get(key)
                if head is None:continue
                matches=[]
                for b in row:
                    nearest=min(heads,key=lambda v:abs(cx(v)-cx(b)))
                    if nearest['id']!=head['id']:continue
                    parsed=number(b['texte'].rstrip('%').strip())
                    if key=='quantite':
                        m=re.fullmatch(r'\s*(\d+(?:[.,]\d+)?)\s*([a-zA-Z]+)?\s*',b['texte'])
                        parsed=number(m[1]) if m else None
                        if m and m[2] and line.get('unite')==m[2]:proof(f'lignes.{i}.unite',[b])
                    if parsed is not None and (value is None or abs(parsed-float(value))<.0001
                                               or key in ['prix_unitaire_ttc','montant_ttc']):matches.append(b)
                if len(matches)==1:
                    text=matches[0]['texte'].rstrip('%').strip()
                    if key=='quantite':text=re.match(r'\s*\d+(?:[.,]\d+)?',text)[0]
                    line[key]=number(text);proof(f'lignes.{i}.{key}',matches)
    supported={p['champ'] for p in d['preuves']}
    rates={l.get('taux_tva') for i,l in enumerate(d.get('lignes',[]))
           if isinstance(l,dict) and l.get('taux_tva') is not None and f'lignes.{i}.taux_tva' in supported}
    if len(rates)>1:
        d['taux_tva']=None;notices.append('Plusieurs taux de TVA par ligne; pas de taux global unique.')
    if d.get('type_document') not in ['facture','devis']:
        kinds=[k for k in ['facture','devis'] if labels([k])]
        if len(kinds)==1:d['type_document']=kinds[0]
    # Preserve the exact source spelling while exposing a deterministic separator repair.
    for evidence in d['preuves']:
        for id_ in evidence['blocs_ocr']:
            original=bs[id_-1]['texte']
            stripped=re.sub(r'(EUR|MAD|USD|€|\$|Dhs?)','',original,flags=re.I).replace(' ','').strip()
            if re.fullmatch(r'\d{1,3}(?:\.\d{3})+\.\d{2}|\d{1,3}(?:,\d{3})+,\d{2}',stripped):
                notices.append('Separateurs OCR normalises, revue requise : '+original)
    # All other proposals stay without proof and will be rejected by the strict validator.
    d['incertitudes']=[s for s in d.get('incertitudes',[]) if isinstance(s,str)]+notices
    return d,notices
