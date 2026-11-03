"""Extraction de secours par libelles et positions. Aucun nom de fournisseur code en dur."""
import re
import unicodedata
from datetime import datetime
from statistics import median

FIELDS=['fournisseur','ice_fournisseur','client','ice_client','numero_document','date','devise','total_ht','montant_tva','total_ttc']
def norm(s):
    return ''.join(c for c in unicodedata.normalize('NFKD',s).lower() if c.isalnum() and not unicodedata.combining(c))
def number(s):
    s=re.sub(r'(EUR|MAD|USD|€|\$|Dhs?)','',s,flags=re.I).strip().replace('\u00a0','').replace(' ','')
    if not re.fullmatch(r'\d+(?:[.,]\d+)*',s):return None
    if re.fullmatch(r'\d{1,3}(?:[.,]\d{3})+[.,]\d{2}',s):
        return float(re.sub(r'[.,]','',s[:-3])+'.'+s[-2:])
    if ',' in s and '.' in s:
        decimal=',' if s.rfind(',')>s.rfind('.') else '.'
        s=s.replace('.' if decimal==',' else ',','').replace(decimal,'.')
    elif ',' in s or '.' in s:
        sep=',' if ',' in s else '.'
        if len(s.split(sep))>2 or len(s.split(sep)[-1])==3:return None
        s=s.replace(sep,'.')
    return float(s)

def extract(raw,page):
    r=raw.get('res',raw)
    bs=[dict(id=i+1,texte=t,bbox=b,page=page) for i,(t,b) in enumerate(zip(r['rec_texts'],r['rec_boxes']))]
    cx=lambda b:(b['bbox'][0]+b['bbox'][2])/2
    cy=lambda b:(b['bbox'][1]+b['bbox'][3])/2
    height=median(max(1,b['bbox'][3]-b['bbox'][1]) for b in bs)
    data={k:None for k in FIELDS};data['lignes']=[];proof={};alerts=[]
    def put(k,v,blocks):
        data[k]=v;proof[k]={'page':page,'blocs':blocks}
    def right(label):
        items=[b for b in bs if b['bbox'][0]>=label['bbox'][2] and abs(cy(b)-cy(label))<height*.65]
        return sorted(items,key=lambda b:b['bbox'][0])
    for b in bs:
        n=norm(b['texte']);cells=right(b)
        if n in ['facturen','factureno','numerofacture','devisn','numerodevis'] and len(cells)==1:
            put('numero_document',cells[0]['texte'],[b,cells[0]])
        if n in ['date','datefacture','datedefacture','datedevis'] and len(cells)==1:
            try:put('date',datetime.strptime(cells[0]['texte'].strip(),'%d/%m/%Y').date().isoformat(),[b,cells[0]])
            except ValueError:pass
        if n.startswith(('totalapayer','netapayer','totalttc')):
            vals=[(v,number(v['texte'])) for v in cells];vals=[(v,x) for v,x in vals if x is not None]
            if len(vals)==1:put('total_ttc',vals[0][1],[b,vals[0][0]])
    text=' '.join(b['texte'] for b in bs)
    currencies=[c for c,pattern in [('EUR',r'€|\bEUR\b'),('MAD',r'\bMAD\b|\bDhs?\b'),('USD',r'\bUSD\b|\$')] if re.search(pattern,text,re.I)]
    if len(currencies)==1:put('devise',currencies[0],[b for b in bs if re.search(r'€|EUR|MAD|Dhs?|USD|\$',b['texte'],re.I)])
    billing=[b for b in bs if norm(b['texte']) in ['facturea','client','destinataire']]
    if len(billing)==1:
        label=billing[0]
        below=sorted([b for b in bs if b['bbox'][1]>=label['bbox'][3] and b['bbox'][1]-label['bbox'][3]<height*2 and abs(b['bbox'][0]-label['bbox'][0])<height*2],key=lambda b:b['bbox'][1])
        if below:
            if norm(below[0]['texte']) in ['votreclient','nomduclient']:alerts.append('Client : texte de modele, identite non renseignee.')
            else:put('client',below[0]['texte'],[label,below[0]])
        # Repeated business name corroborated in the upper header (e.g. logo + address block).
        upper=[b for b in bs if b['bbox'][3]<label['bbox'][1] and len(norm(b['texte']))>3 and not re.search(r'\d|@',b['texte']) and norm(b['texte']) not in ['facture','devis']]
        groups={}
        for b in upper:groups.setdefault(norm(b['texte']),[]).append(b)
        repeated=[v for v in groups.values() if len(v)>=2]
        if len(repeated)==1:put('fournisseur',repeated[0][0]['texte'],repeated[0])
    titles=[b for b in bs if norm(b['texte']) in ['description','designation','libelle']]
    if len(titles)!=1:alerts.append('Tableau non reconnu par le secours geometrique.');return data,proof,alerts
    title=titles[0];band=[b for b in bs if abs(cy(b)-cy(title))<height]
    aliases=[['quantite','qte'],['prix','pr ix','prixunitaire','pu','prixht','prixunitaireht'],['montant','montantht','total','totalht']]
    cols=[]
    for names in aliases:
        found=[b for b in band if norm(re.sub(r'\([^)]*\)','',b['texte'])) in names]
        if len(found)!=1:return data,proof,alerts+['Colonnes financieres ambigues.']
        cols.append(found[0])
    if not cx(title)<min(cx(b) for b in cols):return data,proof,alerts+['Disposition du tableau non prise en charge.']
    ending=[b['bbox'][1] for b in bs if cy(b)>cy(title)+height and norm(b['texte']).startswith(('montanttotal','totalapayer','totalht','totalttc','soustotal','netapayer'))]
    if not ending:return data,proof,alerts+['Fin de tableau non identifiee.']
    body=[b for b in bs if b['bbox'][1]>max(x['bbox'][3] for x in band) and cy(b)<min(ending)]
    anchors=[cx(title)]+[cx(b) for b in cols]
    cells=[[] for _ in anchors]
    for b in body:cells[min(range(4),key=lambda i:abs(cx(b)-anchors[i]))].append(b)
    if not cells[1] or len(cells[1])!=len(cells[2]) or len(cells[1])!=len(cells[3]):return data,proof,alerts+['Alignement des cellules incomplet.']
    for col in cells:col.sort(key=cy)
    for q,p,m in zip(*cells[1:]):
        if max(cy(q),cy(p),cy(m))-min(cy(q),cy(p),cy(m))>height:continue
        match=re.fullmatch(r'\s*(\d+(?:[.,]\d+)?)\s*([A-Za-zÀ-ÿ]+)?\s*',q['texte'])
        desc=[b for b in cells[0] if abs(cy(b)-cy(q))<height]
        if not match or not desc:continue
        qty=number(match[1]);price=number(p['texte']);amount=number(m['texte'])
        if None in (qty,price,amount):continue
        explicit_ht=all('ht' in norm(b['texte']) for b in [cols[1],cols[2]])
        line={'designation':' '.join(b['texte'] for b in desc),'quantite':qty,'unite':match[2],
              'prix_unitaire_ht':price if explicit_ht else None,'montant_ht':amount if explicit_ht else None,
              'prix_unitaire':price,'montant':amount,'base_fiscale':'HT' if explicit_ht else 'non_precisee','page':page}
        data['lignes'].append(line);proof['ligne_'+str(len(data['lignes']))]={'page':page,'blocs':desc+[q,p,m]}
        if abs(qty*price-amount)>.02:alerts.append('Incoherence quantite x prix, ligne '+str(len(data['lignes'])))
    alerts.append('Extraction de secours par positions; revue humaine requise.')
    if data['lignes'] and not explicit_ht:alerts.append('Prix et montants lus sans mention HT : base fiscale non precisee; HT et TVA laisses vides.')
    return data,proof,alerts
