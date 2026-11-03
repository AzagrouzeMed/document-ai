"""Offline synthetic invoice corpus; 300 independent invoices by default.
Run from project root: python scripts/generate_dataset.py
The PDF is a CLEAN reference. The JPEG is the input image (clean/scan/photo).
"""
import argparse
import csv
import hashlib
import io
import json
import math
import random
from collections import Counter
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from PIL import Image, ImageEnhance, ImageFilter
from playwright.sync_api import sync_playwright
from pydantic import BaseModel, Field, model_validator
from labels_source import FR, AR

ROOT = Path(__file__).resolve().parents[1]
LANGS = ['fr', 'ar', 'fr-ar']
PALETTES = [('#126f78','#eef6f7'),('#283f71','#f0f3fa'),('#73503d','#f7f2ef'),('#35634e','#eff6f0'),('#764269','#f7f0f6'),('#434c5b','#f1f3f5')]
CITIES = [('Marrakech','مراكش'),('Casablanca','الدار البيضاء'),('Rabat','الرباط'),('Tanger','طنجة'),('Agadir','أكادير'),('Fès','فاس'),('Oujda','وجدة'),('Meknès','مكناس')]
BRANDS = [('ATLAS','أطلس'),('OASIS','واحة'),('NOUR','نور'),('RIHAB','رحاب'),('AMANA','أمانة'),('NAKHIL','نخيل'),('SAFA','صفاء'),('MANAR','منار'),('BAHJA','بهجة'),('RIF','الريف'),('SAHARA','الصحراء'),('ANDALOUS','الأندلس')]
CATALOG = [
 ('BUR-01','Ramette papier A4','رزمة ورق A4',2500,8500),
 ('BUR-02','Classeur à levier','ملف برافعة',1200,4500),
 ('BUR-03','Boîte de stylos','علبة أقلام',1500,9500),
 ('BUR-04','Enveloppes commerciales','أظرفة تجارية',1800,14000),
 ('INF-01','Clavier USB','لوحة مفاتيح',6500,35000),
 ('INF-02','Souris sans fil','فأرة لاسلكية',4500,24000),
 ('INF-03','Cartouche toner noir','خرطوشة حبر أسود',18000,69000),
 ('INF-04','Câble réseau 5 m','كابل شبكة 5 أمتار',2000,9000),
 ('ENT-01','Produit nettoyant 5 L','منظف 5 لترات',3500,16000),
 ('ENT-02','Lot de sacs poubelle','حزمة أكياس نفايات',1000,6500),
 ('MOB-01','Chaise de bureau','كرسي مكتب',35000,120000),
 ('MOB-02','Étagère métallique','رف معدني',65000,200000),
 ('SER-01','Maintenance informatique','صيانة الحاسوب',15000,90000),
 ('SER-02','Installation du matériel','تركيب المعدات',12000,75000),
 ('SER-03','Frais de livraison','مصاريف التسليم',2000,12000),
 ('SER-04','Impression de brochures','طباعة مطويات',7000,45000),
]


def dec(v):
    return Decimal(str(v)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


class Line(BaseModel):
    reference: str
    designation: str
    quantite: int = Field(gt=0)
    prix_unitaire_ht: Decimal = Field(gt=0)
    montant_ht: Decimal

    @model_validator(mode='after')
    def check(self):
        if dec(self.quantite*self.prix_unitaire_ht) != self.montant_ht:
            raise ValueError('Incorrect line amount')
        return self


class Invoice(BaseModel):
    fournisseur: str
    adresse_fournisseur: str
    telephone: str
    email: str
    ice: str = Field(pattern=r'^\d{15}$')
    numero_facture: str
    date: str
    date_echeance: str
    date_livraison: str
    bon_commande: str
    devise: str
    client: dict
    mode_paiement: str
    delai_paiement_jours: int
    rib: str = Field(pattern=r'^\d{24}$')
    lignes: list[Line]
    taux_tva: int
    total_ht: Decimal
    montant_tva: Decimal
    total_ttc: Decimal

    @model_validator(mode='after')
    def check(self):
        if sum(x.montant_ht for x in self.lignes) != self.total_ht:
            raise ValueError('HT inconsistent')
        if dec(self.total_ht*self.taux_tva/100) != self.montant_tva:
            raise ValueError('TVA inconsistent')
        if self.total_ht+self.montant_tva != self.total_ttc:
            raise ValueError('TTC inconsistent')
        if date.fromisoformat(self.date_echeance) < date.fromisoformat(self.date):
            raise ValueError('Due date precedes invoice date')
        return self


def text(fr, ar, lang):
    return ar if lang == 'ar' else fr if lang == 'fr' else f'{fr} / {ar}'


def fake_digits(rng, count):
    return '9'+''.join(str(rng.randrange(10)) for _ in range(count-1))


def build_record(rng, idx, lang, template):
    frbrand, arbrand = rng.choice(BRANDS)
    cityfr, cityar = rng.choice(CITIES)
    clientfr, clientar = rng.choice(BRANDS)
    clientcityfr, clientcityar = rng.choice(CITIES)
    city = text(cityfr, cityar, lang)
    brand = text(frbrand, arbrand, lang)
    lines = []
    for ref, fr, ar, low, high in rng.sample(CATALOG, rng.randint(1,6)):
        price = Decimal(rng.randrange(low, high+1))/100
        qty = rng.randint(1,18)
        lines.append(dict(reference=ref,designation=text(fr,ar,lang),quantite=qty,prix_unitaire_ht=float(price),montant_ht=float(dec(price*qty))))
    ht = sum(dec(x['montant_ht']) for x in lines)
    # Synthetic test cases, not a statement of applicable tax rules.
    rate = rng.choices([20,10,0],weights=[8,1,1])[0]
    vat = dec(ht*rate/100)
    day = date(2025,1,1)+timedelta(days=rng.randrange(650))
    days = rng.choice([0,15,30,45,60])
    payment = rng.choice([('Virement bancaire','تحويل بنكي'),('Chèque','شيك'),('Espèces','نقدا')])
    number = f'DEMO-{day.year}-{idx:05d}'
    f = dict(fournisseur=text(frbrand+' Fournitures SARL',arbrand+' للتوريدات',lang),adresse_fournisseur=text(f'{rng.randint(1,180)}, rue Exemple, {cityfr}',f'{rng.randint(1,180)}، شارع المثال، {cityar}',lang),telephone='+212 5 00 00 00 00',email=frbrand.lower()+'.demo@example.com',ice=fake_digits(rng,15),numero_facture=number,date=day.isoformat(),date_echeance=(day+timedelta(days=days)).isoformat(),date_livraison=(day-timedelta(days=rng.randint(0,3))).isoformat(),bon_commande=f'BC-DEMO-{rng.randrange(10000,99999)}',devise='MAD',client=dict(nom=text(clientfr+' Services SARL',clientar+' للخدمات',lang),adresse=text(f'{rng.randint(1,90)}, avenue Modèle, {clientcityfr}',f'{rng.randint(1,90)}، شارع النموذج، {clientcityar}',lang),ice=fake_digits(rng,15)),mode_paiement=text(*payment,lang),delai_paiement_jours=days,rib='0'*24,lignes=lines,taux_tva=rate,total_ht=float(ht),montant_tva=float(vat),total_ttc=float(ht+vat))
    Invoice.model_validate(f)
    accent,pale=rng.choice(PALETTES)
    # 12 explicit layout profiles. The third header family is reserved to test.
    t = AR.copy() if lang=='ar' else FR.copy() if lang=='fr' else {k:text(FR[k],AR[k],lang) for k in FR}
    signature = f'M15 65Q{rng.randint(25,40)} 8 52 22Q65 37 35 68Q58 34 71 43Q80 57 88 37Q105 18 106 48Q124 31 127 49Q145 60 156 43Q174 29 181 47L204 36'
    ctx = dict(lang=lang,f=f,t=t,brand=brand,city=city,tagline=text('Fournitures et services','توريدات وخدمات',lang),accent=accent,pale=pale,font=rng.choice(['Arial',"'DejaVu Sans'",'Tahoma']),header_mode=template//4+1,party_mode=template%4+1,light_meta=bool(template%2),reverse_summary=bool(template%3==1),swap_marks=bool(template%2),logo_radius=rng.choice([4,15,32]),logo_type=rng.randrange(4),ink=rng.choice(['#315d9b','#3b5483','#553e86']),stamp_round=rng.choice([True,False]),stamp_angle=rng.randint(-10,7),has_stamp=rng.random()<.8,has_signature=rng.random()<.75,signature_path=signature,tax_label=text(f'TVA {rate} %',f'الضريبة {rate} %',lang),day_label=text('jours','يوما',lang))
    return f,ctx


def transformed_box(box, angle, w, h):
    theta=math.radians(angle);c=math.cos(theta);s=math.sin(theta)
    points=[]
    for x,y in [(box[0],box[1]),(box[2],box[1]),(box[2],box[3]),(box[0],box[3])]:
        dx,dy=x-w/2,y-h/2
        points.append((c*dx+s*dy+w/2,-s*dx+c*dy+h/2))
    xs,ys=zip(*points)
    raw=[min(xs),min(ys),max(xs),max(ys)]
    # Content has wide margins. Never silently clamp a clipped field.
    if not (0<=raw[0]<raw[2]<=w and 0<=raw[1]<raw[3]<=h):
        raise ValueError(f'Rotated region clipped: {raw}')
    return [round(v,2) for v in raw]


def degrade(image, kind, rng):
    info=dict(type=kind,rotation_deg=0.0,synthetic=True)
    if kind=='clean':return image,info
    angle=rng.uniform(-1.6,1.6) if kind=='scan' else rng.uniform(-2.2,2.2)
    image=image.rotate(angle,resample=Image.Resampling.BICUBIC,expand=False,fillcolor='white')
    if kind=='scan':
        image=ImageEnhance.Contrast(image).enhance(rng.uniform(.78,.96))
        image=image.filter(ImageFilter.GaussianBlur(rng.uniform(.25,.65)))
    else:
        # Simulated phone capture: rotation, smooth shadow, lower resolution and blur.
        width,height=image.size
        shadow=Image.linear_gradient('L').resize((width,height)).rotate(rng.choice([0,180]))
        shadow=shadow.point(lambda v:int(180+v*75/255))
        image=Image.composite(image,Image.new('RGB',image.size,(25,25,25)),shadow)
        reduced=(int(width*.72),int(height*.72))
        image=image.resize(reduced,Image.Resampling.LANCZOS).resize((width,height),Image.Resampling.BICUBIC)
        image=image.filter(ImageFilter.GaussianBlur(rng.uniform(.35,.8)))
    info['rotation_deg']=round(angle,8)
    return image,info


def questions(fields, zones, lang):
    prompts=[('total_ttc','Quel est le montant total TTC ?','ما هو المجموع شامل الضريبة؟'),('ice',"Quel est l’ICE du fournisseur ?",'ما هو المعرف الموحد للمورد؟'),('date','Quelle est la date de la facture ?','ما هو تاريخ الفاتورة؟'),('fournisseur','Quel est le fournisseur ?','من هو المورد؟'),('numero_facture','Quel est le numéro de la facture ?','ما هو رقم الفاتورة؟')]
    result=[]
    for key,fr,ar in prompts:
        zone=next(z for z in zones if z.get('champ')==key)
        value=f'{fields[key]:.2f} MAD' if key=='total_ttc' else fields[key]
        result.append(dict(question=text(fr,ar,lang),reponse=value,champ=key,page=1,bbox=zone['bbox'],repondable=True))
    return result


def render(page,env,root,split,idx,lang,template,quality,seed):
    rng=random.Random(seed+idx*1009)
    f,ctx=build_record(rng,idx,lang,template)
    doc_id=f'invoice_{idx:05d}'
    splitroot=root/split
    for d in ['images','annotations','pdf']:(splitroot/d).mkdir(parents=True,exist_ok=True)
    html=env.get_template('invoice.html').render(**ctx)
    page.set_content(html)
    page.evaluate('document.fonts.ready')
    # Validate each text cell and inter-section boundaries before saving.
    bad=page.locator('td, th, .card, .metadata > span, .grand').evaluate_all('els=>els.filter(e=>e.scrollWidth>e.clientWidth+2).map(e=>e.textContent)')
    if bad:raise ValueError(f'{doc_id}: horizontal overflow {bad}')
    note=page.locator('.note').bounding_box();footer=page.locator('.foot').bounding_box()
    if note['y']+note['height']+4>footer['y']:raise ValueError(f'{doc_id}: footer overlap')
    zones=page.locator('[data-field], [data-region]').evaluate_all('''els=>els.map(e=>{const r=e.getBoundingClientRect();return {champ:e.dataset.field||null,type:e.dataset.region||'champ',texte_visible:(e.innerText||e.textContent||'').trim(),page:1,bbox:[r.left*2,r.top*2,r.right*2,r.bottom*2]}})''')
    img=Image.open(io.BytesIO(page.screenshot(type='jpeg',quality=96))).convert('RGB')
    img,aug=degrade(img,quality,rng)
    w,h=img.size
    for zone in zones:
        zone['bbox']=transformed_box(zone['bbox'],aug['rotation_deg'],w,h)
    imagepath=splitroot/'images'/f'{doc_id}.jpg'
    pdfpath=splitroot/'pdf'/f'{doc_id}.pdf'
    img.save(imagepath,quality=80 if quality=='clean' else 70 if quality=='scan' else 65,subsampling=2,optimize=True)
    page.pdf(path=str(pdfpath),width='794px',height='1123px',print_background=True)
    a=dict(schema_version='1.0',document_id=doc_id,group_id=doc_id,split=split,langue=lang,synthetique=True,template_id=f'layout_{template:02d}',seed=seed+idx*1009,image=f'images/{doc_id}.jpg',pdf_reference=f'pdf/{doc_id}.pdf',pdf_reference_est_propre=True,texte_reference=page.locator('body').inner_text(),image_dimensions=dict(width=w,height=h),bbox_format='xyxy',bbox_unite='pixels_image',augmentation=aug,marques=dict(logo=True,cachet=ctx['has_stamp'],signature=ctx['has_signature']),champs=f,zones=zones,questions_reponses=questions(f,zones,lang),questions_sans_preuve=[dict(question=text('Quel est le bénéfice annuel du fournisseur ?','ما هو الربح السنوي للمورد؟',lang),repondable=False,reponse_attendue='preuve_insuffisante')],validation_humaine=dict(statut='a_verifier',notes=''),sha256_image=hashlib.sha256(imagepath.read_bytes()).hexdigest())
    (splitroot/'annotations'/f'{doc_id}.json').write_text(json.dumps(a,ensure_ascii=False,indent=2),encoding='utf-8')
    return dict(document_id=doc_id,split=split,langue=lang,template_id=a['template_id'],qualite=quality,n_lignes=len(f['lignes']),cachet=ctx['has_stamp'],signature=ctx['has_signature'],image=f'{split}/{a["image"]}',annotation=f'{split}/annotations/{doc_id}.json',pdf=f'{split}/{a["pdf_reference"]}')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--documents',type=int,default=300)
    parser.add_argument('--test',type=int,default=100)
    parser.add_argument('--seed',type=int,default=20261020)
    parser.add_argument('--output',type=Path,default=ROOT/'data'/'dataset_v1')
    args=parser.parse_args()
    if not 0<args.test<args.documents:parser.error('Require 0 < test < documents')
    root=args.output.resolve()
    if root.exists() and any(root.iterdir()):parser.error('Output folder is not empty. Choose a new --output; existing data is preserved.')
    root.mkdir(parents=True,exist_ok=True)
    env=Environment(loader=FileSystemLoader(ROOT/'templates'),autoescape=select_autoescape(['html']))
    env.filters['money']=lambda v:f'{v:,.2f}'.replace(',',' ').replace('.',',')
    rows=[];n_dev=args.documents-args.test
    # Stratified language counts. Globally balanced for the default 300 invoices.
    jobs=[]
    for split,n,offset in [('dev',n_dev,0),('test',args.test,n_dev%3)]:
        group=[]
        for i in range(n):
            lang=LANGS[(i+offset)%3]
            template=(i//3)%8 if split=='dev' else 8+(i//3)%4
            group.append((split,lang,template))
        random.Random(args.seed+(0 if split=='dev' else 1)).shuffle(group)
        jobs+=group
    qualities=(['clean','scan','photo']*((args.documents+2)//3))[:args.documents]
    random.Random(args.seed+7).shuffle(qualities)
    config=dict(documents=args.documents,test=args.test,seed=args.seed,output=str(root),generator_version='1.0',status='generating')
    (root/'generation_config.json').write_text(json.dumps(config,indent=2),encoding='utf-8')
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        page=browser.new_page(viewport=dict(width=794,height=1123),device_scale_factor=2)
        page.emulate_media(media='print')
        try:
            for idx,((split,lang,template),quality) in enumerate(zip(jobs,qualities),1):
                row=render(page,env,root,split,idx,lang,template,quality,args.seed)
                rows.append(row)
                if idx%10==0 or idx==args.documents:print(f'[{idx}/{args.documents}] {split} {lang} {quality}',flush=True)
        finally:browser.close()
    with (root/'manifest.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    summary=dict(documents=len(rows),splits=dict(Counter(r['split'] for r in rows)),langues=dict(Counter(r['langue'] for r in rows)),qualites=dict(Counter(r['qualite'] for r in rows)),templates=dict(Counter(r['template_id'] for r in rows)),validation_humaine='NON EFFECTUEE - a realiser avant evaluation finale',nature='entierement synthetique; ne prouve pas les performances sur factures reelles')
    (root/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    config['status']='complete';(root/'generation_config.json').write_text(json.dumps(config,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False,indent=2))
    print('Next: python scripts/validate_dataset.py --dataset "'+str(root)+'"')


if __name__=='__main__':main()
