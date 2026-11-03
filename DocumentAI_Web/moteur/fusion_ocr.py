"""Position-based OCR fusion. No annotations, vocabulary or language model.

Arabic OCR supplies geometry and Arabic text. A mutually aligned French OCR
block can supply its Latin segment. This policy is a DEV heuristic, not a
calibrated probability of correctness. Raw alternatives remain in provenance.
"""
import copy
import re
import unicodedata
from decimal import Decimal, InvalidOperation


def is_arabic(c):
    return 'ARABIC' in unicodedata.name(c, '') and c.isalpha()


def latin_key(text):
    return ''.join(c for c in unicodedata.normalize('NFKD', text).casefold()
                   if c.isalnum() and not unicodedata.combining(c))


def split_scripts(text):
    # Do not include an isolated Arabic character embedded in a corrupted Latin word.
    if '/' in text:
        left, right = text.split('/', 1)
        if any(is_arabic(c) for c in right):
            return left.strip(), right.strip()
    for match in re.finditer(r'[^\W\d_]+', text, re.UNICODE):
        word = match.group()
        for i in range(len(word)-1):
            if is_arabic(word[i]) and is_arabic(word[i+1]):
                start = match.start()+i
                return text[:start].strip(' /'), text[start:].strip(' /')
    return text.strip(' /'), ''


def overlap(a, b):
    x1,y1,x2,y2 = a['bbox']; u1,v1,u2,v2 = b['bbox']
    intersection = max(0,min(x2,u2)-max(x1,u1))*max(0,min(y2,v2)-max(y1,v1))
    union = (x2-x1)*(y2-y1)+(u2-u1)*(v2-v1)-intersection
    return intersection/union if union>0 else 0.0


def best_matches(a, b):
    result = {}
    for block in a:
        candidates = sorted(((overlap(block,other),other['id']) for other in b), reverse=True)
        if not candidates or candidates[0][0]<.55:continue
        if len(candidates)>1 and candidates[0][0]-candidates[1][0]<.10:continue
        result[block['id']] = candidates[0][1]
    return result


def numeric_value(text):
    value=''.join(str(unicodedata.digit(c)) if c.isdigit() else c for c in text)
    value=re.sub(r'\s','',value).replace(',','.')
    if not re.fullmatch(r'\d+(?:\.\d+)?',value):return None
    try:return Decimal(value)
    except InvalidOperation:return None


def fuse_blocks(arabic, french, eligible_ids):
    forward = best_matches(arabic, french)
    backward = best_matches(french, arabic)
    french_by_id = {b['id']:b for b in french}
    fused = copy.deepcopy(arabic)
    decisions = []; warnings = []
    for block in fused:
        block['origines'] = [dict(lecture='ar',bloc=block['id'],texte=block['texte'],bbox=block['bbox'],confiance=block['confiance'])]
        other_id = forward.get(block['id'])
        if block['id'] not in eligible_ids:
            if other_id is not None and backward.get(other_id)==block['id']:
                other=french_by_id[other_id]
                a,b=numeric_value(block['texte']),numeric_value(other['texte'])
                if a is not None and b is not None and a!=b:
                    block['origines'].append(dict(lecture='fr',bloc=other_id,texte=other['texte'],bbox=other['bbox'],confiance=other['confiance']))
                    warnings.append(f"Lectures numeriques divergentes au bloc {block['id']} : valeur arabe conservee, revue requise")
            continue
        if other_id is None or backward.get(other_id)!=block['id']:
            warnings.append(f"Fusion non effectuee : alignement absent ou ambigu, bloc {block['id']}")
            continue
        other = french_by_id[other_id]
        block['origines'].append(dict(lecture='fr',bloc=other_id,texte=other['texte'],bbox=other['bbox'],confiance=other['confiance']))
        latin_ar, arabic_text = split_scripts(block['texte'])
        latin_fr = other['texte'].split('/',1)[0].strip()
        if not arabic_text or not latin_fr:continue
        # Never take a French fragment containing Arabic letters or only punctuation.
        usable = (not any(is_arabic(c) for c in latin_fr)
                  and any('LATIN' in unicodedata.name(c,'') for c in latin_fr))
        contaminated = any(is_arabic(c) for c in latin_ar)
        length_ok = len(latin_key(latin_fr)) >= len(latin_key(latin_ar))*.9
        same = latin_key(latin_fr)==latin_key(latin_ar)
        # Equal letters do not justify replacing good word boundaries with merged words.
        # Prefer a richer spacing ONLY when letters are equal; never invent a boundary.
        spaces_ar=len(latin_ar.split());spaces_fr=len(latin_fr.split())
        # Keep the established FR tie-break for equal letters and equal spacing.
        # Change that tie-break only when it would remove an existing word boundary.
        same_prefer_fr = same and spaces_fr>=spaces_ar
        # An aligned multi-word FR fragment can fill a manifestly truncated Latin
        # segment, even when a confidence averaged over both scripts is below .85.
        # Only existing OCR characters are used; no product dictionary or completion.
        truncated=(len(latin_key(latin_ar))<4 and len(latin_key(latin_fr))>=8
                   or len(latin_key(latin_fr))>=2*max(1,len(latin_key(latin_ar))))
        fuller_fragment=(truncated and len(latin_fr.split())>=2 and
                         (other.get('confiance') or 0)>=.70)
        use_fr = usable and (contaminated or same_prefer_fr or fuller_fragment or
                             (not same and
                              (other.get('confiance') or 0)>=.85 and length_ok))
        latin = latin_fr if use_fr else latin_ar
        if usable and not same:
            warnings.append(f"Lectures latines divergentes au bloc {block['id']} : verifier le texte fusionne")
        block['texte'] = latin+' / '+arabic_text
        # Confidence of a synthesized text is undefined; retain component scores only.
        block['confiance'] = None
        decisions.append(dict(bloc=block['id'],texte_fusionne=block['texte'],
                              latin_retenu='fr' if use_fr else 'ar',
                              politique='espacement_preserve; fragment_FR_plus_complet_si_aligne_et_troncature; sinon_regle_085',
                              origines=block['origines']))
    return fused, dict(decisions=decisions,alertes=warnings,
                      politique='appariement_mutuel_IoU_055_marge_010; geometrie_ar; fragments_linguistiques',
                      utilise_annotations_reference=False)
