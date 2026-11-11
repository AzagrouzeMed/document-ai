'use strict';
const $=id=>document.getElementById(id);
let token='',selectedFile=null,currentId=null,pollTimer=null,viewVersion=0,currentDocument=null;
const states={attente:'En attente',analyse:'Analyse en cours',export:'Création du PDF',termine:'À vérifier',echec:'Échec'};
const labels={fournisseur:'Fournisseur',ice_fournisseur:'ICE fournisseur',client:'Client',ice_client:'ICE client',numero_document:'Numéro du document',date:'Date',date_echeance:'Échéance',adresse_fournisseur:'Adresse fournisseur',adresse_client:'Adresse de facturation',adresse_livraison:'Adresse de livraison',taux_tva:'Taux TVA (%)',devise:'Devise',total_ht:'Total HT',montant_tva:'TVA',total_ttc:'Total TTC',total_document:'Total (base non précisée)'};
const amountKeys=['total_ht','montant_tva','total_ttc'];
const lineLabels={designation:'Désignation',taux_tva:'TVA (%)',quantite:'Quantité',unite:'Unité',prix_unitaire:'Prix unitaire (base non précisée)',montant:'Montant (base non précisée)',hauteur_cm:'Hauteur (cm)',largeur_cm:'Largeur (cm)',prix_unitaire_ht:'Prix unitaire HT',montant_ht:'Montant HT',prix_unitaire_ttc:'Prix unitaire TTC',montant_ttc:'Montant TTC',page:'Page'};
function node(tag,text,className){const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(className)e.className=className;return e;}
function show(view){for(const name of ['upload','progress','result'])$(name+'-view').classList.toggle('hidden',name!==view);}
function format(value){if(value===null||value===undefined||value==='')return '—';if(typeof value==='number')return new Intl.NumberFormat('fr-FR',{maximumFractionDigits:2}).format(value);return String(value);}
async function api(url,options={}){const r=await fetch(url,options);if(!r.ok){let msg;try{msg=(await r.json()).detail;}catch{msg='Le serveur est indisponible.';}throw new Error(typeof msg==='string'?msg:'La demande a été refusée.');}return r.json();}
function setFile(file){selectedFile=file;$('drop-title').textContent=file?file.name:'Déposez votre document ici';$('file-info').textContent=file?`${(file.size/1024/1024).toFixed(2)} Mo · prêt à être importé`:'ou choisissez un fichier sur votre ordinateur';$('submit').disabled=!file;$('upload-error').classList.add('hidden');}
function reset(){viewVersion++;clearTimeout(pollTimer);currentId=null;currentDocument=null;localStorage.removeItem('documentai_active');$('file').value='';setFile(null);$('breadcrumb').textContent='Nouveau document';show('upload');refreshHistory();}
async function refreshHistory(){try{const items=await api('/api/traitements');const history=$('history');history.replaceChildren();if(!items.length){history.append(node('p','Aucun document pour le moment.','muted'));return;}for(const job of items){const b=node('button',undefined,'history-item'+(job.id===currentId?' active':''));b.type='button';b.append(node('strong',job.nom_fichier));const meta=node('small');meta.append(node('span',states[job.etat]||job.etat),node('span',new Date(job.created_at).toLocaleDateString('fr-FR',{day:'2-digit',month:'short'})));b.append(meta);b.onclick=()=>openJob(job.id);history.append(b);}}catch(e){$('history').replaceChildren(node('p','Historique indisponible.','muted'));}}
function progress(job){show('progress');$('progress-name').textContent=job.nom_fichier||'Document importé';$('progress-message').textContent=job.message||'Traitement en cours…';$('progress-error').classList.add('hidden');$('back-after-error').classList.add('hidden');document.querySelector('.spinner').classList.remove('hidden');for(const id of ['step-upload','step-analysis','step-export'])$(id).classList.remove('on');$('step-upload').classList.add('on');if(['analyse','export'].includes(job.etat))$('step-analysis').classList.add('on');if(job.etat==='export')$('step-export').classList.add('on');if(job.etat==='echec'){document.querySelector('.spinner').classList.add('hidden');$('progress-error').textContent=job.message;$('progress-error').classList.remove('hidden');$('back-after-error').classList.remove('hidden');}}
async function openJob(id){const version=++viewVersion;clearTimeout(pollTimer);currentId=id;localStorage.setItem('documentai_active',id);$('breadcrumb').textContent='Traitement du document';show('progress');$('progress-name').textContent='Chargement du résultat…';refreshHistory();poll(id,version);}
async function poll(id,version,errors=0){try{const job=await api(`/api/traitements/${id}`);if(version!==viewVersion)return;if(job.etat==='termine'){const result=await api(`/api/traitements/${id}/resultat`);if(version!==viewVersion)return;renderResult(id,job,result);refreshHistory();return;}progress(job);if(job.etat==='echec'){refreshHistory();return;}pollTimer=setTimeout(()=>poll(id,version),2000);}catch(e){if(version!==viewVersion)return;if(errors<4){$('progress-message').textContent='Connexion interrompue. Nouvelle tentative…';pollTimer=setTimeout(()=>poll(id,version,errors+1),3000);}else{progress({etat:'echec',message:e.message});}}}
function setTab(name){for(const b of document.querySelectorAll('[data-tab]'))b.setAttribute('aria-selected',String(b.dataset.tab===name));for(const tab of ['fields','articles','alerts','json'])$('tab-'+tab).classList.toggle('hidden',tab!==name);}
function renderResult(id,job,result){const d=result.document,c=result.controles;currentDocument=d;show('result');$('breadcrumb').textContent='Résultat';$('result-title').textContent=job.nom_fichier;$('document-id').textContent=d.document_id;$('review-status').textContent=d.statut==='partiel'?'Résultat partiel':'À vérifier';$('metric-type').textContent=d.type_document==='inconnu'?'Non identifié':d.type_document==='devis'?'Devis':'Facture';$('metric-pages').textContent=(d.pages_traitees||[]).length;$('metric-lines').textContent=(d.lignes||[]).length;$('metric-total').textContent=(d.total_ttc??d.total_document)==null?'Non extrait':format(d.total_ttc??d.total_document)+' '+(d.devise||'');if($('metric-total-label'))$('metric-total-label').textContent=d.total_ttc==null?'Total (base non précisée)':'Total TTC';
const base=`/api/traitements/${id}`;for(const [el,kind] of [['download-json','json'],['download-pdf','pdf'],['download-controls','controles'],['download-proofs','preuves'],['download-text','texte']])$(el).href=base+'/telecharger/'+kind;
$('download-pdf').classList.toggle('hidden',!job.pdf_disponible);
const fields=$('fields-list');fields.replaceChildren();for(const [key,label] of Object.entries(labels)){const row=node('div');row.append(node('dt',label));const value=node('dd');if(amountKeys.includes(key)){const input=node('input');input.type='text';input.inputMode='decimal';input.id='amount-'+key;input.name=key;input.setAttribute('aria-label',label);input.value=d[key]==null?'':String(d[key]).replace('.',',');input.addEventListener('input',()=>{$('amounts-status').textContent='Modifications non validées';});value.append(input);}else{value.textContent=format(d[key]);value.className=d[key]==null?'missing':'';}value.dir='auto';row.append(value);fields.append(row);}
$('amounts-status').textContent=result.montants_validation?'Montants validés · '+new Date(result.montants_validation.validated_at).toLocaleString('fr-FR'): 'Montants à vérifier';
const preview=$('source-preview');preview.replaceChildren();$('open-source').href=base+'/source';if(job.nom_fichier.toLowerCase().endsWith('.pdf')){const iframe=document.createElement('iframe');iframe.src=base+'/source';iframe.title='Aperçu du document original';preview.append(iframe);}else{const img=document.createElement('img');img.src=base+'/source';img.alt='Document original importé';preview.append(img);}
const lines=d.lignes||[],keys=Object.keys(lineLabels).filter(k=>['designation','quantite','page'].includes(k)||lines.some(l=>l[k]!=null));const table=$('articles-table');table.replaceChildren();if(lines.length){const thead=node('thead'),tr=node('tr');keys.forEach(k=>tr.append(node('th',lineLabels[k])));thead.append(tr);table.append(thead);const body=node('tbody');for(const line of lines){const row=node('tr');for(const key of keys){const cell=node('td',format(line[key]),line[key]==null?'missing':'');cell.dir='auto';row.append(cell);}body.append(row);}table.append(body);}else{const caption=node('caption','Aucune ligne structurée extraite. Consultez les alertes et le texte OCR.');table.append(caption);}
$('article-count').textContent=lines.length;const alerts=[...(c.alertes||[])];if(job.avertissement_pdf)alerts.push(job.avertissement_pdf);if(job.sauvegarde && job.sauvegarde.etat==='echec')alerts.push(job.sauvegarde.message);$('alert-count').textContent=alerts.length;const list=$('alerts-list');list.replaceChildren();for(const a of alerts.length?alerts:['Aucune alerte enregistrée. Vérifiez les valeurs avec le document original.'])list.append(node('li',a));$('json-output').textContent=JSON.stringify(d,null,2);setTab('fields');}
$('amounts-form').addEventListener('submit',async e=>{
 e.preventDefault();const id=currentId,version=viewVersion;const button=$('validate-amounts');button.disabled=true;
 const inputs=amountKeys.map(key=>$('amount-'+key));
 const amounts=Object.fromEntries(amountKeys.map((key,index)=>[key,inputs[index].value]));
 inputs.forEach(input=>input.disabled=true);
 try{
  const saved=await api(`/api/traitements/${id}/valider-montants`,{method:'POST',headers:{'Content-Type':'application/json','X-DocumentAI-Token':token},body:JSON.stringify(amounts)});
  if(version!==viewVersion)return;
  Object.assign(currentDocument,saved.montants);
  $('json-output').textContent=JSON.stringify(currentDocument,null,2);
  $('metric-total').textContent=format(currentDocument.total_ttc)+' '+(currentDocument.devise||'');
  $('metric-total-label').textContent='Total TTC';
  $('amounts-status').textContent='Montants validés · '+new Date(saved.validated_at).toLocaleString('fr-FR');
 }catch(error){if(version===viewVersion)$('amounts-status').textContent=error.message;}
 finally{button.disabled=false;inputs.forEach(input=>input.disabled=false);}
});
$('file').addEventListener('change',e=>setFile(e.target.files[0]||null));
for(const event of ['dragenter','dragover'])$('dropzone').addEventListener(event,e=>{e.preventDefault();$('dropzone').classList.add('dragging');});
for(const event of ['dragleave','drop'])$('dropzone').addEventListener(event,e=>{e.preventDefault();$('dropzone').classList.remove('dragging');if(event==='drop')setFile(e.dataTransfer.files[0]||null);});
$('upload-form').addEventListener('submit',async e=>{e.preventDefault();if(!selectedFile)return;const error=$('upload-error');error.classList.add('hidden');if(selectedFile.size>25*1024*1024){error.textContent='Le fichier dépasse 25 Mo.';error.classList.remove('hidden');return;}$('submit').disabled=true;$('submit').textContent='Importation…';try{const data=new FormData();data.append('file',selectedFile);data.append('lang',$('lang').value);data.append('sans_llm',String($('sans-llm').checked));const job=await api('/api/traitements',{method:'POST',headers:{'X-DocumentAI-Token':token},body:data});await openJob(job.id);}catch(e){error.textContent=e.message;error.classList.remove('hidden');}finally{$('submit').disabled=!selectedFile;$('submit').textContent='Analyser le document →';}});
$('new-document').onclick=reset;$('back-after-error').onclick=reset;$('refresh-history').onclick=refreshHistory;for(const b of document.querySelectorAll('[data-tab]'))b.onclick=()=>setTab(b.dataset.tab);
$('copy-json').onclick=async()=>{try{await navigator.clipboard.writeText(JSON.stringify(currentDocument,null,2));$('copy-json').textContent='Copié';setTimeout(()=>$('copy-json').textContent='Copier le JSON',1800);}catch{$('copy-json').textContent='Sélectionnez le texte pour le copier';}};
(async()=>{try{const cfg=await api('/api/config');token=cfg.token;await refreshHistory();const id=localStorage.getItem('documentai_active');if(id&&/^[a-f0-9]{32}$/.test(id))await openJob(id);}catch(e){$('upload-error').textContent=e.message;$('upload-error').classList.remove('hidden');}})();

// Consultation SQLite : independante du traitement et sans lancement d'extraction.
let invoiceListVersion=0,invoiceDetailVersion=0,editedInvoice=null,invoiceSaving=false,invoiceQuestionVersion=0,invoiceSearchVersion=0;
const invoiceListLabels={id:'Identifiant',numero_document:'Numéro',fournisseur:'Fournisseur',client:'Client',date_facture:'Date',devise:'Devise',statut:'Statut'};
const invoiceDetailLabels={...invoiceListLabels,type_document:'Type de document',document_id:'Document',ice_fournisseur:'ICE fournisseur',ice_client:'ICE client',date_echeance:'Échéance',adresse_fournisseur:'Adresse fournisseur',adresse_client:'Adresse de facturation',adresse_livraison:'Adresse de livraison',total_ht:'Total HT',montant_tva:'TVA',total_ttc:'Total TTC',total_document:'Total (base non précisée)',taux_tva:'Taux TVA (%)'};
function invoiceValue(value){return value===null||value===undefined||value===''?'—':String(value);}
function invoiceCell(tag,value){const cell=node(tag,invoiceValue(value));cell.dir='auto';if(tag==='th')cell.scope='col';return cell;}
function invoiceTableHead(table,columns){const head=node('thead'),row=node('tr');for(const label of columns)row.append(invoiceCell('th',label));head.append(row);table.append(head);}
async function refreshInvoices(){
 const version=++invoiceListVersion;++invoiceDetailVersion;clearInvoiceEditor();
 const button=$('refresh-invoices');button.disabled=true;
 $('invoices-table').replaceChildren();$('invoice-detail').classList.add('hidden');
 $('invoice-detail').setAttribute('aria-busy','false');
 $('invoice-fields').replaceChildren();$('invoice-articles').replaceChildren();
 $('invoices-status').textContent='Chargement des factures…';
 try{
  const invoices=await api('/api/factures');if(version!==invoiceListVersion)return;
  $('invoices-status').textContent=invoices.length?`${invoices.length} facture(s) enregistrée(s). Cliquez sur un numéro pour consulter le détail.`:'Aucune facture enregistrée.';
  if(!invoices.length)return;
  const table=$('invoices-table');invoiceTableHead(table,Object.values(invoiceListLabels));const body=node('tbody');
  for(const invoice of invoices){const row=node('tr');for(const key of Object.keys(invoiceListLabels)){
   const cell=invoiceCell('td',key==='numero_document'?null:invoice[key]);
   if(key==='numero_document'){const open=node('button',invoiceValue(invoice[key]),'text-button');open.type='button';open.dir='auto';open.setAttribute('aria-label',`Consulter la facture ${invoiceValue(invoice.numero_document)} (identifiant ${invoice.id})`);open.onclick=()=>openInvoice(invoice.id);cell.replaceChildren(open);}
   row.append(cell);
  }body.append(row);}table.append(body);
 }catch{if(version===invoiceListVersion)$('invoices-status').textContent='Impossible de lire les factures enregistrées. Réessayez avec « Actualiser ».';}
 finally{if(version===invoiceListVersion)button.disabled=false;}
}
async function openInvoice(id){
 clearInvoiceEditor();
 const version=++invoiceDetailVersion,detail=$('invoice-detail');detail.classList.remove('hidden');detail.setAttribute('aria-busy','true');
 $('invoice-fields').replaceChildren();$('invoice-articles').replaceChildren();$('invoice-detail-status').textContent='Chargement de la facture…';
 try{
  const invoice=await api(`/api/factures/${encodeURIComponent(id)}`);if(version!==invoiceDetailVersion)return;
  editedInvoice=invoice;$('edit-invoice').classList.remove('hidden');$('invoice-questions').classList.remove('hidden');$('invoice-search').classList.remove('hidden');
  await loadInvoiceHistory(id,version);if(version!==invoiceDetailVersion)return;
  for(const [key,label] of Object.entries(invoiceDetailLabels)){const row=node('div'),value=node('dd',invoiceValue(invoice[key]));value.dir='auto';row.append(node('dt',label),value);$('invoice-fields').append(row);}
  const table=$('invoice-articles'),lines=invoice.lignes||[];
  if(lines.length){const columns={numero_ligne:'Ligne',...lineLabels};invoiceTableHead(table,Object.values(columns));const body=node('tbody');for(const line of lines){const row=node('tr');for(const key of Object.keys(columns))row.append(invoiceCell('td',line[key]));body.append(row);}table.append(body);}
  else table.append(node('caption','Aucun article enregistré.'));
  $('invoice-detail-status').textContent='Données enregistrées dans SQLite. Les valeurs restent à vérifier.';
 }catch(error){if(version===invoiceDetailVersion)$('invoice-detail-status').textContent=error.message==='Facture introuvable.'?'Facture introuvable. Actualisez la liste.':'Impossible de lire cette facture. Réessayez plus tard.';}
 finally{if(version===invoiceDetailVersion)detail.setAttribute('aria-busy','false');}
}
function clearInvoiceEditor(){
 ++invoiceSearchVersion;$('invoice-search').classList.add('hidden');$('invoice-search-query').value='';$('invoice-search-results').replaceChildren();$('invoice-search-status').textContent='';$('invoice-search-status').setAttribute('aria-busy','false');
 ++invoiceQuestionVersion;$('invoice-questions').classList.add('hidden');$('invoice-answer').textContent='';$('invoice-answer').setAttribute('aria-busy','false');
 editedInvoice=null;$('edit-invoice').classList.add('hidden');$('invoice-edit-form').classList.add('hidden');
 $('invoice-edit-status').textContent='';$('invoice-history').replaceChildren();$('invoice-history-status').textContent='';
}
async function loadInvoiceHistory(id,version){
 $('invoice-history-status').textContent='Chargement de l’historique…';
 try{
  const entries=await api(`/api/factures/${encodeURIComponent(id)}/historique`);if(version!==invoiceDetailVersion)return;
  const table=$('invoice-history');table.replaceChildren();
  $('invoice-history-status').textContent=entries.length?'Les corrections ne valent pas validation de la facture.':'Aucune correction enregistrée.';
  if(!entries.length)return;
  invoiceTableHead(table,['Champ','Ancienne valeur','Nouvelle valeur','Date']);const body=node('tbody');
  for(const entry of entries){const row=node('tr');const date=new Date(entry.date_action);const when=Number.isNaN(date.getTime())?invoiceValue(entry.date_action):date.toLocaleString('fr-FR');
   for(const value of [invoiceDetailLabels[entry.champ]||entry.champ,entry.ancienne_valeur,entry.nouvelle_valeur,when])row.append(invoiceCell('td',value));body.append(row);
  }table.append(body);
 }catch{if(version===invoiceDetailVersion)$('invoice-history-status').textContent='Impossible de lire l’historique. Réouvrez la facture pour réessayer.';}
}
function setInvoiceEditValue(){
 if(!editedInvoice)return;
 const champ=$('invoice-edit-field').value;$('invoice-edit-value').value=editedInvoice[champ]??'';
 $('invoice-edit-value').maxLength=champ.startsWith('adresse_')?4000:500;
}
$('edit-invoice').onclick=()=>{
 if(!editedInvoice||invoiceSaving)return;
 const select=$('invoice-edit-field');select.replaceChildren();
 for(const champ of editedInvoice.champs_modifiables||[]){if(!Object.hasOwn(invoiceDetailLabels,champ))continue;const option=node('option',invoiceDetailLabels[champ]);option.value=champ;select.append(option);}
 select.value=select.children[0]?.value||'';setInvoiceEditValue();
 $('invoice-edit-status').textContent='';$('invoice-edit-form').classList.remove('hidden');
};
$('invoice-edit-field').onchange=setInvoiceEditValue;
$('cancel-invoice-edit').onclick=()=>{if(!invoiceSaving){$('invoice-edit-form').classList.add('hidden');$('invoice-edit-status').textContent='';}};
$('invoice-edit-form').onsubmit=async event=>{
 event.preventDefault();if(!editedInvoice||invoiceSaving)return;
 const id=editedInvoice.id,version=invoiceDetailVersion;
 const champ=$('invoice-edit-field').value,nouvelle_valeur=$('invoice-edit-value').value.trim()||null;
 invoiceSaving=true;for(const name of ['save-invoice-header','cancel-invoice-edit','invoice-edit-field','invoice-edit-value'])$(name).disabled=true;
 $('invoice-edit-status').textContent='Enregistrement en cours…';
 try{
  const saved=await api(`/api/factures/${encodeURIComponent(id)}/corrections`,{method:'POST',headers:{'Content-Type':'application/json','X-DocumentAI-Token':token},body:JSON.stringify({champ,nouvelle_valeur})});
  if(version!==invoiceDetailVersion)return;
  const reloadVersion=invoiceDetailVersion+1;await refreshInvoices();if(invoiceDetailVersion!==reloadVersion)return;
  await openInvoice(id);if(invoiceDetailVersion!==reloadVersion+1)return;
  $('invoice-edit-status').textContent=saved.message;
 }catch(error){if(version===invoiceDetailVersion)$('invoice-edit-status').textContent=error.message==='Failed to fetch'?'Connexion interrompue. Vérifiez la facture et son historique avant de réessayer.':error.message;}
 finally{invoiceSaving=false;for(const name of ['save-invoice-header','cancel-invoice-edit','invoice-edit-field','invoice-edit-value'])$(name).disabled=false;}
};
async function askInvoiceQuestion(code){
 if(!editedInvoice)return;
 const id=editedInvoice.id,detailVersion=invoiceDetailVersion,questionVersion=++invoiceQuestionVersion;
 const answer=$('invoice-answer');answer.textContent='Consultation de la facture…';answer.setAttribute('aria-busy','true');
 try{
  const result=await api(`/api/factures/${encodeURIComponent(id)}/questions/${encodeURIComponent(code)}`);
  if(detailVersion!==invoiceDetailVersion||questionVersion!==invoiceQuestionVersion)return;
  answer.textContent=result.reponse;
 }catch(error){
  if(detailVersion!==invoiceDetailVersion||questionVersion!==invoiceQuestionVersion)return;
  answer.textContent=error.message==='Facture introuvable.'?'Facture introuvable. Actualisez la liste.':'Impossible de consulter cette information. Réessayez plus tard.';
 }finally{if(detailVersion===invoiceDetailVersion&&questionVersion===invoiceQuestionVersion)answer.setAttribute('aria-busy','false');}
}
$('invoice-search-form').onsubmit=async event=>{
 event.preventDefault();if(!editedInvoice)return;
 const id=editedInvoice.id,detailVersion=invoiceDetailVersion,searchVersion=++invoiceSearchVersion;
 const status=$('invoice-search-status'),results=$('invoice-search-results');results.replaceChildren();
 const q=$('invoice-search-query').value.trim();
 if(!q){status.textContent='Saisissez des mots à rechercher.';status.setAttribute('aria-busy','false');return;}
 status.textContent='Recherche dans les textes enregistrés…';status.setAttribute('aria-busy','true');
 try{
  const found=await api(`/api/factures/${encodeURIComponent(id)}/recherche?q=${encodeURIComponent(q)}`);
  if(detailVersion!==invoiceDetailVersion||searchVersion!==invoiceSearchVersion)return;
  status.textContent=found.message;
  for(const passage of found.passages){const item=node('li');
   const langue=passage.langue==='fr'?'française (fr)':'arabe (ar)';
   item.append(node('p',`Page ${passage.numero_page} · Lecture ${langue} · Source : ${passage.source}`,'muted'));
   const text=node('p',passage.passage,'ocr-passage');text.dir='auto';item.append(text);results.append(item);
  }
 }catch(error){
  if(detailVersion!==invoiceDetailVersion||searchVersion!==invoiceSearchVersion)return;
  status.textContent=error.message==='Facture introuvable.'?'Facture introuvable. Actualisez la liste.':error.message==='Failed to fetch'?'Recherche indisponible. Réessayez plus tard.':error.message;
 }finally{if(detailVersion===invoiceDetailVersion&&searchVersion===invoiceSearchVersion)status.setAttribute('aria-busy','false');}
};
for(const code of ['fournisseur','client','date_facture','total_ttc'])$('ask-invoice-'+code).onclick=()=>askInvoiceQuestion(code);
$('refresh-invoices').onclick=refreshInvoices;
refreshInvoices();
