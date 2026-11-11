// Small DOM substitute: reject HTML interpretation and exercise rendered data.
'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
class Element{
 constructor(tag){this.tag=tag;this.children=[];this.textContent='';this.attributes={};this.classes=new Set();this.classList={add:c=>this.classes.add(c),remove:c=>this.classes.delete(c)};}
 append(...items){this.children.push(...items);}
 replaceChildren(...items){this.children=items;this.textContent='';}
 setAttribute(key,value){this.attributes[key]=value;}
 set innerHTML(value){throw Error('HTML interpretation forbidden');}
}
const source=fs.readFileSync(path.join(__dirname,'../static/app.js'),'utf8');
const elements=new Map();
const $=id=>{if(!elements.has(id))elements.set(id,new Element('div'));return elements.get(id);};
const calls=[];
let respond=async()=>[];
const context=vm.createContext({document:{createElement:tag=>new Element(tag)},$,encodeURIComponent,token:'test',
 api:async (url,options)=>{calls.push(url);return respond(url,options);}});
const helper=source.match(/^function node\(.*$/m)[0];
const labels=source.match(/^const lineLabels=.*$/m)[0];
const section=source.slice(source.indexOf('// Consultation SQLite :'));
vm.runInContext(helper+'\n'+labels+'\n'+section,context);
const run=expression=>vm.runInContext(expression,context);
const text=e=>[e.textContent,...e.children.map(text)].join(' ');
const all=e=>[e,...e.children.flatMap(all)];
const flush=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
 await flush();
 assert.match($('invoices-status').textContent,/Aucune facture/);
 assert.equal(run('invoiceValue(null)'), '\u2014');
 assert.equal(run('invoiceValue(0)'), '0');
 const malicious='<img src=x onerror=alert(1)>';
 const invoice={id:1,numero_document:'F-001',fournisseur:'\u00c9t\u00e9',client:'\u0634\u0631\u0643\u0629',statut:'partiel',lignes:[{designation:malicious,quantite:null}]};
 respond=async url=>url.endsWith('/historique')?[]:url==='/api/factures'?[invoice]:invoice;
 await run('refreshInvoices()');
 assert.match(text($('invoices-table')),/F-001/);
 assert.ok(text($('invoices-table')).includes(invoice.client));
 const button=all($('invoices-table')).find(e=>e.tag==='button');
 await button.onclick();
 assert.ok(text($('invoice-articles')).includes(malicious));
 assert.equal(all($('invoice-articles')).some(e=>e.tag==='img'),false);
 assert.ok(text($('invoice-fields')).includes('\u2014'));
 assert.ok(all($('invoice-fields')).filter(e=>e.tag==='dd').every(e=>e.dir==='auto'));
 assert.equal($('invoice-detail').attributes['aria-busy'],'false');
 respond=async()=>{throw Error('Facture introuvable.');};
 await run('openInvoice(99)');
 assert.match($('invoice-detail-status').textContent,/Facture introuvable/);
 assert.equal($('invoice-fields').children.length,0);
 await run('refreshInvoices()');
 assert.match($('invoices-status').textContent,/Impossible de lire/);
 assert.equal($('refresh-invoices').disabled,false);
 let resolveOld;
 respond=url=>url.endsWith('/1')?new Promise(resolve=>resolveOld=resolve):Promise.resolve(url.endsWith('/historique')?[]:{...invoice,numero_document:'F-002',lignes:[]});
 const old=run('openInvoice(1)');
 await run('openInvoice(2)');
 resolveOld(invoice);await old;
 assert.ok(text($('invoice-fields')).includes('F-002'));
 assert.equal(text($('invoice-fields')).includes('F-001'),false);
 assert.match(text($('invoice-articles')),/Aucun article/);
 assert.ok(calls.every(url=>/^\/api\/factures(?:\/\d+(?:\/historique)?)?$/.test(url)));
 let current={...invoice,champs_modifiables:['fournisseur','client','date_facture']};
 const history=[];let submitted;
 respond=async (url,options)=>{
  if(options?.method==='POST'){
   submitted=JSON.parse(options.body);
   assert.equal(options.headers['X-DocumentAI-Token'],'test');
   history.push({champ:submitted.champ,ancienne_valeur:current[submitted.champ],nouvelle_valeur:submitted.nouvelle_valeur,date_action:'2026-11-09T10:11:12+00:00'});
   current={...current,[submitted.champ]:submitted.nouvelle_valeur};
   return {message:'Correction enregistree. La facture reste a verifier.'};
  }
  return url.endsWith('/historique')?history:url==='/api/factures'?[current]:current;
 };
 await run('openInvoice(1)');
 $('edit-invoice').onclick();
 assert.equal($('invoice-edit-field').children.length,3);
 assert.equal($('invoice-edit-value').value,invoice.fournisseur);
 $('invoice-edit-value').value=malicious;
 await $('invoice-edit-form').onsubmit({preventDefault(){}});
 assert.equal(submitted.champ,'fournisseur');
 assert.equal(submitted.nouvelle_valeur,malicious);
 assert.ok(text($('invoice-fields')).includes(malicious));
 assert.ok(text($('invoice-history')).includes(malicious));
 assert.ok(text($('invoice-history')).includes(invoice.fournisseur));
 assert.equal(all($('invoice-history')).some(e=>e.tag==='img'),false);
 assert.match($('invoice-edit-status').textContent,/Correction enregistree/);
 assert.equal($('save-invoice-header').disabled,false);
 $('edit-invoice').onclick();
 $('invoice-edit-field').value='client';$('invoice-edit-field').onchange();
 assert.equal($('invoice-edit-value').value,invoice.client);
 $('invoice-edit-value').value='   ';
 await $('invoice-edit-form').onsubmit({preventDefault(){}});
 assert.equal(submitted.nouvelle_valeur,null);
 assert.ok(text($('invoice-history')).includes('\u2014'));
 $('edit-invoice').onclick();
 respond=async()=>{throw Error('Date invalide : utilisez AAAA-MM-JJ.');};
 $('invoice-edit-field').value='date_facture';$('invoice-edit-field').onchange();
 $('invoice-edit-value').value='2026-02-30';
 await $('invoice-edit-form').onsubmit({preventDefault(){}});
 assert.match($('invoice-edit-status').textContent,/Date invalide/);
 assert.equal($('invoice-edit-value').value,'2026-02-30');
 assert.equal($('save-invoice-header').disabled,false);
 $('cancel-invoice-edit').onclick();
 assert.ok($('invoice-edit-form').classes.has('hidden'));
 respond=async url=>url.endsWith('/historique')?Promise.reject(Error('unavailable')):current;
 await run('openInvoice(1)');
 assert.match($('invoice-history-status').textContent,/Impossible de lire/);
 assert.ok(text($('invoice-fields')).includes('F-001'));
 const questionCalls=[];
 respond=async (url,options)=>{
  if(url.includes('/questions/')){assert.equal(options,undefined);questionCalls.push(url);return {reponse:'Facture F-001. '+invoice.client+' '+malicious};}
  return url.endsWith('/historique')?[]:current;
 };
 await run('openInvoice(1)');
 for(const code of ['fournisseur','client','date_facture','total_ttc'])await $('ask-invoice-'+code).onclick();
 assert.equal(questionCalls.length,4);
 assert.ok(questionCalls.every(url=>url.startsWith('/api/factures/1/questions/')));
 assert.ok($('invoice-answer').textContent.includes(invoice.client));
 assert.ok($('invoice-answer').textContent.includes(malicious));
 assert.equal($('invoice-answer').children.length,0);
 assert.equal($('invoice-answer').attributes['aria-busy'],'false');
 let resolveAnswer;
 respond=url=>url.includes('/questions/')?new Promise(resolve=>resolveAnswer=resolve):Promise.resolve(url.endsWith('/historique')?[]:{...current,id:2});
 const pending=run("askInvoiceQuestion('client')");
 await run('openInvoice(2)');resolveAnswer({reponse:'Old invoice answer'});await pending;
 assert.equal($('invoice-answer').textContent,'');
 assert.equal($('invoice-answer').attributes['aria-busy'],'false');
 let resolveFirst;
 respond=url=>url.endsWith('/fournisseur')?new Promise(resolve=>resolveFirst=resolve):Promise.resolve({reponse:'Latest question answer'});
 const first=run("askInvoiceQuestion('fournisseur')");await run("askInvoiceQuestion('client')");
 resolveFirst({reponse:'Old question answer'});await first;
 assert.equal($('invoice-answer').textContent,'Latest question answer');
 respond=async()=>{throw Error('Facture introuvable.');};await run("askInvoiceQuestion('client')");
 assert.match($('invoice-answer').textContent,/Facture introuvable/);
 respond=async()=>{throw Error('private internal details');};await run("askInvoiceQuestion('client')");
 assert.match($('invoice-answer').textContent,/Impossible de consulter/);
 assert.equal($('invoice-answer').textContent.includes('private'),false);
 assert.equal($('invoice-answer').attributes['aria-busy'],'false');
 let searchResponse={etat:'resultats',message:'1 passage trouve.',passages:[{numero_page:2,langue:'ar',source:'ocr/page_2_ar.json',passage:invoice.client+' '+malicious}]};
 let requestedSearch;
 respond=async (url,options)=>{
  if(url.includes('/recherche?')){assert.equal(options,undefined);requestedSearch=url;return searchResponse;}
  return url.endsWith('/historique')?[]:current;
 };
 await run('openInvoice(1)');
 $('invoice-search-query').value=invoice.client+' "cafe" + 100%';
 const submitSearch=()=>$('invoice-search-form').onsubmit({preventDefault(){}});
 await submitSearch();
 assert.ok(requestedSearch.startsWith('/api/factures/1/recherche?q='));
 assert.ok(requestedSearch.includes(encodeURIComponent(invoice.client+' "cafe" + 100%')));
 assert.ok(text($('invoice-search-results')).includes('Page 2'));
 assert.ok(text($('invoice-search-results')).includes('arabe (ar)'));
 assert.ok(text($('invoice-search-results')).includes('ocr/page_2_ar.json'));
 assert.ok(text($('invoice-search-results')).includes(malicious));
 assert.equal(all($('invoice-search-results')).some(e=>e.tag==='img'),false);
 assert.equal(all($('invoice-search-results')).find(e=>e.className==='ocr-passage').dir,'auto');
 assert.equal($('invoice-search-status').attributes['aria-busy'],'false');
 for(const message of ['Aucun texte OCR enregistre','Aucun passage trouve','Recherche indisponible : FTS5 absent']){
  searchResponse={message,passages:[]};await submitSearch();
  assert.equal($('invoice-search-status').textContent,message);
  assert.equal($('invoice-search-results').children.length,0);
 }
 let resolveSearch;
 respond=url=>url.includes('/recherche?')?new Promise(resolve=>resolveSearch=resolve):Promise.resolve(url.endsWith('/historique')?[]:{...current,id:2});
 const oldSearch=submitSearch();await run('openInvoice(2)');
 resolveSearch({message:'Old search',passages:[]});await oldSearch;
 assert.equal($('invoice-search-status').textContent,'');
 assert.equal($('invoice-search-results').children.length,0);
 $('invoice-search-query').value='   ';requestedSearch=null;
 await submitSearch();assert.match($('invoice-search-status').textContent,/Saisissez/);
 assert.equal(requestedSearch,null);
 respond=async()=>{throw Error('Facture introuvable.');};$('invoice-search-query').value='cafe';await submitSearch();
 assert.match($('invoice-search-status').textContent,/Facture introuvable/);
 assert.equal($('invoice-search-status').attributes['aria-busy'],'false');
 const html=fs.readFileSync(path.join(__dirname,'../static/index.html'),'utf8');
 assert.ok(/id="invoice-answer" dir="auto"/.test(html));
 assert.ok(/id="invoice-search-query" type="search" dir="auto"/.test(html));
 console.log('OK: consultation, corrections, structured questions, OCR search, safe text, errors, stale responses.');
})().catch(error=>{console.error(error);process.exitCode=1;});
