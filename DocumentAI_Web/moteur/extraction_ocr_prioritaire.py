"""OCR geometrique d'abord, complement visuel controle ensuite.
Les roles restent heuristiques; aucune certification ni nouvelle precision mesuree.
"""
import json
import re
from copy import deepcopy
from datetime import datetime
from statistics import median
from moteur.extraction_souple import norm, number
from moteur.corroboration_ocr import prepare
from moteur.extraction_vision import validate_output, Line


def _blocks(raw):
    r=raw.get('res',raw)
    return [dict(id=i+1,texte=str(t),bbox=b) for i,(t,b) in
            enumerate(zip(r.get('rec_texts',[]),r.get('rec_boxes',[])))]



def completer_totaux(prepared,raw):
    """Complete uniquement les totaux vides, depuis un libelle et une cellule alignee.

    Les composantes bilingues sont separees par /. Aucun choix par proximite
    entre plusieurs montants plausibles, aucune deduction arithmetique.
    Les preuves sont ensuite controlees par validate_output comme les autres champs.
    """
    aliases={
        'total_ht':{'totalht','montanttotalht','soustotalht',
                    'دونالضريبة','المجموعدونالضريبة','الإجماليدونالضريبة'},
        'montant_tva':{'tva','montanttva','الضريبة','مبلغالضريبة','الضريبةعلىالقيمةالمضافة'},
        'total_ttc':{'totalttc','montanttotalttc','شاملالضريبة',
                     'المجموعشاملالضريبة','الإجماليشاملالضريبة'},
    }
    aliases={k:{norm(v) for v in values} for k,values in aliases.items()}
    bs=_blocks(raw);alerts=[];labels=[]
    for block in bs:
        roles=set()
        for part in block['texte'].split('/'):
            text=norm(part)
            for key,names in aliases.items():
                # Un taux est un element du libelle TVA, jamais un montant candidat.
                candidate=''.join(c for c in text if not c.isdecimal()) if key=='montant_tva' else text
                if candidate in names:roles.add(key)
        if roles:labels.append((block,roles))
    matches={key:[] for key in aliases}
    for label,roles in labels:
        height=max(1,label['bbox'][3]-label['bbox'][1])
        center=(label['bbox'][1]+label['bbox'][3])/2
        candidates=[]
        for cell in bs:
            if cell['id']==label['id'] or '%' in cell['texte']:continue
            value=number(cell['texte'])
            if value is None:continue
            cy=(cell['bbox'][1]+cell['bbox'][3])/2
            left=cell['bbox'][2]<=label['bbox'][0]
            right=cell['bbox'][0]>=label['bbox'][2]
            gap=label['bbox'][0]-cell['bbox'][2] if left else cell['bbox'][0]-label['bbox'][2]
            if (left or right) and abs(cy-center)<=height*.65 and 0<=gap<=height*12:
                candidates.append((value,cell))
        for key in roles:
            if prepared.get(key) is not None:continue
            if len(roles)!=1 or len(candidates)>1:
                alerts.append('Totaux OCR ambigus : '+key+'; libelle contradictoire ou plusieurs montants alignes, valeur laissee vide.')
                matches[key].append(None)
            elif candidates:matches[key].append((label,*candidates[0]))
    # Une meme cellule ne doit pas servir deux champs ou deux libelles concurrents.
    cells=[hit[2]['id'] for hits in matches.values() for hit in hits if hit is not None]
    for key,hits in matches.items():
        if prepared.get(key) is not None or not hits:continue
        if len(hits)!=1 or hits[0] is None or cells.count(hits[0][2]['id'])!=1:
            if not any(key in a for a in alerts):
                alerts.append('Totaux OCR ambigus : '+key+'; plusieurs associations possibles, valeur laissee vide.')
            continue
        label,value,cell=hits[0]
        prepared[key]=value
        prepared.setdefault('preuves',[]).append(dict(champ=key,
            texte_visible=label['texte']+' '+cell['texte'],blocs_ocr=[label['id'],cell['id']]))
    return alerts



def corriger_adresse_fournisseur(prepared,raw):
    """Adresse locale bornee autour du fournisseur, sans emprunter la zone client."""
    bs=_blocks(raw)
    prepared['adresse_fournisseur']=None
    prepared['preuves']=[p for p in prepared.get('preuves',[]) if p['champ']!='adresse_fournisseur']
    def refuse():
        return ['Adresse fournisseur non extraite : zone ou adresse OCR absente, ambigue ou non exploitable.']
    if not bs or not prepared.get('fournisseur'):return refuse()
    h=median(max(1,b['bbox'][3]-b['bbox'][1]) for b in bs)
    cy=lambda b:(b['bbox'][1]+b['bbox'][3])/2
    roles={
        'fournisseur':{'fournisseur','vendeur','emetteur','المورد','المصدر'},
        'autre':{'client','facturea','facturera','factura','destinataire','alattentionde',
                 'livrea','envoyea','الزبون','العميل','المشتري','عنوانالتسليم'},
    }
    roles={key:{norm(v) for v in values} for key,values in roles.items()}
    def role(block,key):
        return any(norm(part.split(':',1)[0].strip()) in roles[key] for part in block['texte'].split('/'))
    # Les titres peuvent etre bilingues ou fusionnes avec d'autres colonnes.
    def limite(block):
        text=norm(block['texte'])
        return any(word in text for word in ['designation','description','البيان','سعرالوحدة',
                                             'datedefacture','تاريخالفاتورة','bondecommande'])
    bottom=min((b['bbox'][1] for b in bs if limite(b)),default=max(b['bbox'][3] for b in bs)+h)
    names=[b for b in bs if norm(b['texte'])==norm(prepared['fournisseur']) and cy(b)<bottom]
    if len(names)!=1:return refuse()
    name=names[0]
    supplier_labels=[b for b in bs if role(b,'fournisseur') and cy(b)<bottom]
    linked=[b for b in supplier_labels if 0<cy(name)-cy(b)<=3*h
            and min(abs(b['bbox'][0]-name['bbox'][0]),abs(b['bbox'][2]-name['bbox'][2]))<=2*h]
    if supplier_labels and len(linked)!=1:return refuse()
    anchor=linked[0] if linked else name
    others=[b for b in bs if role(b,'autre') and cy(b)<bottom]
    left=float('-inf');right=float('inf')
    for other in others:
        dx=other['bbox'][0]-anchor['bbox'][0]
        if abs(dx)<=2*h:
            if cy(other)>cy(name):bottom=min(bottom,other['bbox'][1])
            elif cy(other)<=cy(name) and not linked:return refuse()
        elif dx<0:left=max(left,(other['bbox'][0]+anchor['bbox'][0])/2)
        else:right=min(right,(other['bbox'][0]+anchor['bbox'][0])/2)
    bottom=min(bottom,name['bbox'][3]+6*h)
    excluded=r'@|https?://|\b(?:ICE|IF|RC|RIB|IBAN|SIRET|TVA)\b|T[eé]l|Fax|هاتف|الهاتف|الضريبي|^\s*\+?\d[\d .()-]{7,}$'
    street=r'rue|avenue|boulevard|route|impasse|chemin|lotissement|adresse|شارع|زنقة|حي|طريق|عنوان'
    candidates=[]
    for block in bs:
        x0,y0,x1,y1=block['bbox']
        aligned=min(abs(x0-name['bbox'][0]),abs(x1-name['bbox'][2]))<=2*h
        if (block['id']==name['id'] or not aligned or not left<(x0+x1)/2<right
            or y0<name['bbox'][3]-h*.35 or cy(block)<=cy(name) or y1>bottom):continue
        text=block['texte'].strip()
        if re.search(excluded,text,re.I) or not re.search(street,text,re.I):continue
        # Un simple titre Adresse/عنوان ne constitue pas une adresse.
        content=re.sub(r'(?i)adresse|عنوان|[\s/:：]','',text)
        if len(content)<4:continue
        candidates.append(block)
    if len(candidates)!=1:return refuse()
    address=candidates[0]
    prepared['adresse_fournisseur']=address['texte']
    prepared['preuves'].append(dict(champ='adresse_fournisseur',texte_visible=address['texte'],
                                    blocs_ocr=[address['id']]))
    return []



def completer_references_dates(prepared,raw):
    """Associe des valeurs lues aux libelles, sans calcul ni reference codee en dur."""
    aliases={
        'numero_document':{'facture','numerofacture','numerodefacture','facturen','factureno',
                           'referencefacture','فاتورة','رقمالفاتورة'},
        'date':{'datedefacture','datefacture','datedefacturation','datedemission',
                'dateemission','تاريخالفاتورة','تاريخالإصدار'},
        'date_echeance':{'echeance','dateecheance','datedecheance','تاريخالاستحقاق'},
        'commande':{'bondecommande','numerodecommande','numerocommande','referencecommande',
                    'commande','طلبالشراء','رقمالطلب','رقمطلبالشراء'},
        'livraison':{'livraison','datedelivraison','datelivraison','التسليم','تاريخالتسليم'},
    }
    aliases={key:{norm(v) for v in values} for key,values in aliases.items()}
    def roles(block):
        found=set()
        for part in block['texte'].split('/'):
            label=re.split(r'[:：]',part,1)[0].strip()
            for key,names in aliases.items():
                if norm(label) in names:found.add(key)
            # Un libelle arabe explicite peut survivre a un prefixe OCR degrade.
            for key,pattern in [('date_echeance',r'(?<!\w)تاريخ\s+الاستحقاق(?!\w)'),
                                ('date',r'(?<!\w)تاريخ\s+(?:الفاتورة|الإصدار)(?!\w)')]:
                if re.search(pattern,label):found.add(key)
        return found
    def parse_date(text):
        text=text.strip()
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}|\d{2}[/.-]\d{2}[/.-]\d{4}',text):return None
        for fmt in ['%Y-%m-%d','%d/%m/%Y','%d.%m.%Y','%d-%m-%Y']:
            try:return datetime.strptime(text,fmt).date().isoformat()
            except ValueError:pass
        return None
    def parse_reference(text):
        text=text.strip()
        if (re.fullmatch(r'[\w][\w./-]*',text) and any(c.isdecimal() for c in text)
            and not re.fullmatch(r'\d{4}-\d{2}-\d{2}|\d{2}[/.-]\d{2}[/.-]\d{4}',text)):
            return text
        return None
    bs=_blocks(raw);labels=[(b,roles(b)) for b in bs if roles(b)]
    label_ids={b['id'] for b,_ in labels}
    cy=lambda b:(b['bbox'][1]+b['bbox'][3])/2
    associations={key:[] for key in ['numero_document','date','date_echeance']}
    for label,kinds in labels:
        for key in kinds & associations.keys():
            parser=parse_reference if key=='numero_document' else parse_date
            hits=[]
            # Valeur et libelle dans le meme bloc, avec separateur explicite.
            for part in re.split(r'[:：]',label['texte'])[1:]:
                value=parser(part)
                if value is not None:hits.append((value,label))
            height=max(1,label['bbox'][3]-label['bbox'][1])
            for cell in bs:
                if cell['id'] in label_ids:continue
                value=parser(cell['texte'])
                if value is None:continue
                x0,y0,x1,y1=cell['bbox'];l0,t0,l1,b0=label['bbox']
                # Sous le libelle dans sa colonne, ou a cote sur la meme ligne.
                below=(0<cy(cell)-cy(label)<=2*height and y0>=b0-height*.5
                       and (l0-height*.5<=(x0+x1)/2<=l1+height*.5))
                beside=(abs(cy(cell)-cy(label))<=height*.65
                        and ((0<=x0-l1<=4*height) or (0<=l0-x1<=4*height)))
                if below or beside:hits.append((value,cell))
            associations[key].append((label,kinds,hits))
    # Une cellule partagee entre deux champs ne permet pas de determiner son role.
    owners={}
    for key,groups in associations.items():
        for _,_,hits in groups:
            for _,cell in hits:owners.setdefault(cell['id'],set()).add(key)
    alerts=[]
    for key,groups in associations.items():
        if not groups:continue
        hits=[(label,value,cell) for label,_,values in groups for value,cell in values]
        ambiguous=(any(len(kinds)!=1 for _,kinds,_ in groups) or len(hits)>1
                   or any(len(owners[cell['id']])>1 for _,_,cell in hits))
        # Remplace seulement ces trois champs et leurs preuves, pas les autres donnees.
        prepared[key]=None
        prepared['preuves']=[p for p in prepared.get('preuves',[]) if p['champ']!=key]
        if ambiguous or not hits:
            reason='plusieurs candidats ou roles plausibles' if ambiguous else 'aucune valeur exploitable alignee'
            alerts.append('Reference/date OCR non extraite : '+key+'; '+reason+'.')
            continue
        label,value,cell=hits[0]
        # Exclut une cellule plus clairement rattachee a commande/livraison.
        competing=False
        for other,kinds in labels:
            if not kinds & {'commande','livraison'}:continue
            height=max(1,other['bbox'][3]-other['bbox'][1])
            x0,y0,x1,y1=cell['bbox'];l0,t0,l1,b0=other['bbox']
            if (0<cy(cell)-cy(other)<=2*height and y0>=b0-height*.5
                and l0-height*.5<=(x0+x1)/2<=l1+height*.5):competing=True
        if competing:
            alerts.append('Reference/date OCR non extraite : '+key+'; association commande ou livraison concurrente.')
            continue
        prepared[key]=value
        sources=[label] if label['id']==cell['id'] else [label,cell]
        prepared['preuves'].append(dict(champ=key,texte_visible=' '.join(b['texte'] for b in sources),
                                        blocs_ocr=[b['id'] for b in sources]))
    return alerts



def rattacher_client_ice(prepared,raw):
    """Client et ICE exclusivement rattaches aux zones explicites, sans cote fixe."""
    bs=_blocks(raw);alerts=[]
    keys=['client','ice_client','ice_fournisseur']
    for key in keys:prepared[key]=None
    prepared['preuves']=[p for p in prepared.get('preuves',[]) if p['champ'] not in keys]
    aliases={
        'client':{'client','facturea','factura','facturera','destinataire','alattentionde',
                  'الزبون','العميل','المشتري'},
        'fournisseur':{'fournisseur','vendeur','emetteur','المورد','المصدر'},
        'livraison':{'livrea','envoyea','adresselivraison','عنوانالتسليم'},
    }
    aliases={key:{norm(v) for v in values} for key,values in aliases.items()}
    def roles(block):
        parts={norm(re.split(r'[:：]',part,1)[0]) for part in block['texte'].split('/')}
        return {key for key,values in aliases.items() if parts & values}
    anchors=[(b,roles(b)) for b in bs if roles(b)]
    def boundary(block):
        text=norm(block['texte'])
        return any(word in text for word in ['designation','description','البيان',
                   'datedefacture','تاريخالفاتورة','datedemission','bondecommande'])
    end=min((b['bbox'][1] for b in bs if boundary(b)),default=float('inf'))
    zones=[]
    for anchor,kinds in anchors:
        if len(kinds)!=1 or anchor['bbox'][1]>=end:continue
        height=max(1,anchor['bbox'][3]-anchor['bbox'][1])
        center=(anchor['bbox'][0]+anchor['bbox'][2])/2
        left=float('-inf');right=float('inf');bottom=min(end,anchor['bbox'][3]+7*height)
        for other,_ in anchors:
            if other['id']==anchor['id'] or other['bbox'][1]>=end:continue
            peer=(other['bbox'][0]+other['bbox'][2])/2
            if abs(peer-center)<=2*height:
                if other['bbox'][1]>anchor['bbox'][1]:bottom=min(bottom,other['bbox'][1])
            elif abs(other['bbox'][1]-anchor['bbox'][1])<=2*height:
                edge=(peer+center)/2
                if peer<center:left=max(left,edge)
                else:right=min(right,edge)
        zones.append(dict(role=next(iter(kinds)),anchor=anchor,height=height,
                          left=left,right=right,top=anchor['bbox'][3]-height*.35,bottom=bottom))
    def contains(zone,block):
        x0,y0,x1,y1=block['bbox']
        anchor=zone['anchor']['bbox']
        aligned=min(abs(x0-anchor[0]),abs(x1-anchor[2]))<=2*zone['height']
        return (zone['left']<(x0+x1)/2<zone['right'] and aligned and y0>=zone['top']
                and y1<=zone['bottom'] and block['id']!=zone['anchor']['id'])
    def save(key,value,zone,blocks):
        prepared[key]=value
        source=[zone['anchor']]+blocks
        prepared['preuves'].append(dict(champ=key,texte_visible=' '.join(b['texte'] for b in source),
                                        blocs_ocr=[b['id'] for b in source]))
    client_zones=[z for z in zones if z['role']=='client']
    if len(client_zones)==1:
        zone=client_zones[0];names=[]
        for block in bs:
            if not contains(zone,block):continue
            if block['bbox'][1]-zone['anchor']['bbox'][3]>2*zone['height']:continue
            text=block['texte'].strip()
            if (not text or any(c.isdecimal() for c in text) or roles(block)
                or re.search(r'@|https?://|[:：]|adresse|rue|avenue|boulevard|contact|t[eé]l|fax|شارع|عنوان|هاتف',text,re.I)
                or norm(text) in {'client','votreclient','nomduclient','prenomnom'}):continue
            if len([z for z in zones if contains(z,block)])==1:names.append(block)
        if len(names)==1:save('client',names[0]['texte'],zone,names)
        else:alerts.append('Client OCR non attribue : nom absent ou plusieurs candidats dans la zone client.')
    else:alerts.append('Client OCR non attribue : zone client absente ou ambigue.')
    hits={'client':[],'fournisseur':[]}
    # Le libelle ICE est obligatoire ; ni IF, ni telephone, ni SIRET ne le remplacent.
    for block in bs:
        values=re.findall(r'(?<!\w)ICE\s*[:：]?\s*([0-9]{15})(?![0-9])',block['texte'],re.I)
        if not values:continue
        owners=[z for z in zones if contains(z,block)]
        if len(owners)!=1 or owners[0]['role'] not in hits:
            alerts.append(f'ICE OCR non attribue : bloc {block["id"]}; zone absente ou ambigue.')
            continue
        for value in values:hits[owners[0]['role']].append((value,owners[0],block))
    for role,values in hits.items():
        key='ice_'+role
        role_zones=[z for z in zones if z['role']==role]
        if len(role_zones)==1 and len({v for v,_,_ in values})==1:
            save(key,values[0][0],values[0][1],[b for _,_,b in values])
        else:alerts.append('ICE OCR non attribue : '+key+'; zone ou identifiant absent ou ambigu.')
    return alerts



def recuperer_lignes_bilingues(prepared,raw):
    """Tableau a colonnes distinctes : lecture des cellules, aucune reconstruction."""
    if prepared.get('lignes'):return []
    bs=_blocks(raw)
    aliases={
        'reference':{'ref','reference','المرجع'},
        'designation':{'designation','description','libelle','البيان','الوصف'},
        'quantite':{'qte','quantite','الكمية'},
        'prix_unitaire_ht':{'puht','prixunitaireht','prixunitht','prixht','ثمنالوحدةدونالضريبة'},
        'prix_unitaire_ttc':{'puttc','prixunitairettc','prixttc','ثمنالوحدةشاملالضريبة'},
        'prix_unitaire':{'pu','prix','prixunitaire','ثمنالوحدة'},
        'montant_ht':{'montantht','totalht','المبلغدونالضريبة'},
        'montant_ttc':{'montantttc','totalttc','المبلغشاملالضريبة'},
        'montant':{'montant','total','المبلغ'},
    }
    aliases={k:{norm(v) for v in values} for k,values in aliases.items()}
    def roles(block):
        parts={norm(part) for part in block['texte'].split('/')}
        found={k for k,values in aliases.items() if parts & values}
        for neutral,explicit in [('prix_unitaire',{'prix_unitaire_ht','prix_unitaire_ttc'}),
                                 ('montant',{'montant_ht','montant_ttc'})]:
            if found & explicit:found.discard(neutral)
        return found
    cy=lambda b:(b['bbox'][1]+b['bbox'][3])/2
    cx=lambda b:(b['bbox'][0]+b['bbox'][2])/2
    titles=[b for b in bs if roles(b)=={'designation'}]
    if len(titles)!=1:return ['Tableau OCR local non extrait : titre de designation absent, fusionne ou ambigu.']
    title=titles[0];height=max(1,title['bbox'][3]-title['bbox'][1])
    band=[b for b in bs if abs(cy(b)-cy(title))<=height]
    columns={};ambiguous=False
    for block in band:
        found=roles(block)
        if len(found)>1:ambiguous=True;continue
        if len(found)==1:
            key=next(iter(found))
            if key in columns:ambiguous=True
            columns[key]=block
    prices=[k for k in columns if k.startswith('prix_unitaire')]
    amounts=[k for k in columns if k in ['montant','montant_ht','montant_ttc']]
    if ambiguous or 'quantite' not in columns or len(prices)!=1 or len(amounts)!=1:
        return ['Tableau OCR local non extrait : colonnes quantite, prix ou montant absentes ou ambigues.']
    heads=list(columns.values())
    if len({cx(b) for b in heads})!=len(heads):
        return ['Tableau OCR local non extrait : colonnes superposees.']
    def column(block):
        distances=[(abs(cx(block)-cx(head)),key) for key,head in columns.items()]
        distances.sort()
        if len(distances)>1 and abs(distances[0][0]-distances[1][0])<1e-6:return None
        return distances[0][1]
    def end(block):
        parts=[norm(part) for part in block['texte'].split('/')]
        return any(p.startswith(('totalht','totalttc','soustotal','montanttva','reglement',
                                 'المجموع','معلوماتالأداء')) for p in parts)
    bottom=min((b['bbox'][1] for b in bs if cy(b)>cy(title)+height and end(b)),
               default=max(b['bbox'][3] for b in bs)+height)
    body=[b for b in bs if cy(title)+height<cy(b)<bottom]
    quantities=[b for b in body if column(b)=='quantite' and number(b['texte']) is not None]
    lines=[];proofs=[];alerts=[]
    for quantity in sorted(quantities,key=cy):
        row=[b for b in body if abs(cy(b)-cy(quantity))<height*.8]
        if sum(b in quantities for b in row)!=1:
            alerts.append('Ligne OCR ambigue : plusieurs quantites alignees.');continue
        descriptions=[b for b in row if column(b)=='designation' and number(b['texte']) is None]
        if len(descriptions)!=1:
            alerts.append('Ligne OCR non extraite : designation absente ou sur plusieurs blocs.');continue
        index=len(lines);line=dict(designation=descriptions[0]['texte'],quantite=number(quantity['texte']))
        source={'designation':descriptions[0],'quantite':quantity}
        for key in prices+amounts:
            cells=[b for b in row if column(b)==key and number(b['texte']) is not None]
            line[key]=number(cells[0]['texte']) if len(cells)==1 else None
            if len(cells)==1:source[key]=cells[0]
            else:alerts.append(f'Cellule OCR absente ou ambigue : lignes.{index}.{key}.')
        lines.append(line)
        for key,block in source.items():
            proofs.append(dict(champ=f'lignes.{index}.{key}',texte_visible=block['texte'],blocs_ocr=[block['id']]))
    prepared['lignes']=lines
    prepared.setdefault('preuves',[]).extend(proofs)
    if not lines:alerts.append('Tableau OCR local : aucune ligne non ambigue reconnue.')
    return alerts



def recuperer_designations_entete_fusionne(prepared,raw):
    """Conserve les lignes identifiables quand les roles numeriques sont indissociables.

    Les colonnes doivent se repeter sur plusieurs lignes. Un Q initial aligne
    ancre la quantite; le produit ne sert jamais a choisir entre quantite et prix.
    Sans cet indice spatial, les roles restent vides. Aucune base fiscale du
    prix unitaire n'est deduite de celle du montant.
    """
    if prepared.get('lignes'):return []
    bs=_blocks(raw)
    def parts(block):return {norm(p) for p in block['texte'].split('/')}
    titles=[b for b in bs if parts(b) & {'designation','description','libelle','البيان','الوصف'}]
    refs=[b for b in bs if parts(b) & {'ref','reference','المرجع'}]
    if len(titles)!=1 or len(refs)!=1:return []
    title=titles[0];ref=refs[0]
    height=max(1,title['bbox'][3]-title['bbox'][1])
    cy=lambda b:(b['bbox'][1]+b['bbox'][3])/2
    cx=lambda b:(b['bbox'][0]+b['bbox'][2])/2
    fused=[]
    for block in bs:
        text=norm(block['texte'])
        quantity=any(v in text for v in ['quantite','qte','الكمية'])
        unit=any(v in text for v in ['prixunitaire','puht','puttc','الوحدة'])
        amount=any(v in text for v in ['montant','المبلغ'])
        if (quantity and unit and amount and abs(cy(block)-cy(title))<=2*height
            and block['bbox'][0]>title['bbox'][2]):fused.append(block)
    if len(fused)!=1 or abs(cy(ref)-cy(title))>height or cx(ref)>=cx(title):return []
    header=fused[0]
    def ending(block):
        text=norm(block['texte'])
        return any(v in text for v in ['totalht','totalttc','reglement','معلوماتالأداء','دونالضريبة','شاملالضريبة'])
    bottom=min((b['bbox'][1] for b in bs if cy(b)>cy(title)+height and ending(b)),
               default=max(b['bbox'][3] for b in bs)+height)
    body=[b for b in bs if cy(title)+height*.6<cy(b)<bottom]
    edge=(cx(ref)+cx(title))/2
    descriptions=[b for b in body if edge<cx(b)<header['bbox'][0]
                  and number(b['texte']) is None and b['texte'].strip()]
    # Infer persistent numeric columns from cell geometry, never from magnitudes.
    numeric=sorted([b for b in body if cx(b)>=header['bbox'][0]
                    and number(b['texte']) is not None],key=lambda b:b['bbox'][0])
    clusters=[]
    for cell in numeric:
        matches=[g for g in clusters if abs(cell['bbox'][0]-median(b['bbox'][0] for b in g))<height*.6]
        if len(matches)==1:matches[0].append(cell)
        elif not matches:clusters.append([cell])
    stable=(len(clusters)==3 and all(len(g)>=3 for g in clusters)
            and all(median(b['bbox'][0] for b in clusters[i+1])-median(b['bbox'][0] for b in clusters[i])>height
                    for i in range(2)))
    # Q is a spatial anchor only at the left edge of the fused header.
    # Arabic text order inside a mixed RTL/LTR string supplies no x coordinates.
    text=header['texte'].strip()
    quantity_anchor=(stable and re.match(r'^Q(?:te|uantite)?\s*/',text,re.I)
                     and '\u0627\u0644\u0643\u0645\u064a\u0629' in norm(text)
                     and abs(median(b['bbox'][0] for b in clusters[0])-header['bbox'][0])<height*.6)
    unit_hint='\u0627\u0644\u0648\u062d\u062f\u0629' in norm(text) or 'prixunitaire' in norm(text)
    amount_hint='\u0627\u0644\u0645\u0628\u0644\u063a' in norm(text) or 'montant' in norm(text)
    amount_ht='\u0627\u0644\u0645\u0628\u0644\u063a\u062f\u0648\u0646\u0627\u0644\u0636\u0631\u064a\u0628\u0629' in norm(text) or 'montantht' in norm(text)
    lines=[];alerts=[]
    for description in sorted(descriptions,key=cy):
        references=[b for b in body if cx(b)<edge and abs(cy(b)-cy(description))<=height
                    and re.fullmatch(r'[\w][\w./-]*',b['texte'].strip())
                    and any(c.isdecimal() for c in b['texte'])]
        neighbors=[b for b in body if cx(b)>=header['bbox'][0]
                   and abs(cy(b)-cy(description))<=height and number(b['texte']) is not None]
        if len(references)!=1 or len(neighbors)<2:
            alerts.append('Ligne OCR partielle non retenue : reference ou rangee numerique absente/ambigue.')
            continue
        index=len(lines)
        lines.append(dict(designation=description['texte'],quantite=None))
        prepared.setdefault('preuves',[]).append(dict(champ=f'lignes.{index}.designation',
            texte_visible=description['texte'],blocs_ocr=[description['id']]))
        line=lines[-1]
        # Nearest description row groups cells despite baseline/skew differences.
        row=[b for b in neighbors if sum(abs(cy(b)-cy(d))<=height for d in descriptions)==1]
        cells=[[b for b in row if b in group] for group in clusters] if stable else []
        if quantity_anchor and unit_hint and amount_hint and all(len(g)==1 for g in cells):
            q=number(cells[0][0]['texte'])
            other=[number(g[0]['texte']) for g in cells[1:]]
            candidates=[(i,j) for i,j in [(0,1),(1,0)] if abs(q*other[i]-other[j])<=.03]
            if len(candidates)==1:
                price,amount=candidates[0]
                amount_key='montant_ht' if amount_ht else 'montant'
                sources={'quantite':cells[0][0], 'prix_unitaire':cells[price+1][0],
                         amount_key:cells[amount+1][0]}
                for key,cell in sources.items():
                    line[key]=number(cell['texte'])
                    prepared['preuves'].append(dict(champ=f'lignes.{index}.{key}',
                        texte_visible=header['texte']+' '+cell['texte'],
                        blocs_ocr=[header['id'],cell['id']]))
                alerts.append(f'Ligne OCR lignes.{index} : roles heuristiques depuis Q et colonnes alignees; '
                              'produit coherent; base HT/TTC du prix unitaire non lisible, prix conserve generique.')
                continue
        alerts.append(f'Ligne OCR partielle lignes.{index} : en-tete numerique fusionne (bloc {header["id"]}); '
                      'roles absents ou ambigus, quantite, prix et montant laisses vides; '
                      'le produit seul ne distingue pas quantite et prix.')
    prepared['lignes']=lines
    return alerts


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
    extra.extend(recuperer_lignes_bilingues(prepared,raw))
    extra.extend(recuperer_designations_entete_fusionne(prepared,raw))
    extra.extend(rattacher_client_ice(prepared,raw))
    extra.extend(completer_references_dates(prepared,raw))
    extra.extend(corriger_adresse_fournisseur(prepared,raw))
    extra.extend(completer_totaux(prepared,raw))
    data,proofs,alerts=validate_output(prepared,raw,page)
    alerts=[a for a in alerts if not a.startswith('Extraction multimodale experimentale')]
    if data.get('lignes'):
        extra=[a.replace('Tableau OCR local non extrait', 'Tableau OCR local partiellement extrait') for a in extra]
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
