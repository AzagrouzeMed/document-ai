import sqlite3
import re
import unicodedata
from pathlib import Path
import json
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone
from contextlib import closing


# La base sera conservée dans DocumentAI_Web/data.
DOSSIER_DATA = Path(__file__).resolve().parent / "data"
CHEMIN_BASE = DOSSIER_DATA / "document_ai.db"


def ouvrir_connexion(chemin_base=None, *, lecture_seule=False):
    """Ouvre la base et active les liens entre les tables."""
    chemin = Path(chemin_base) if chemin_base is not None else CHEMIN_BASE
    if lecture_seule:
        connexion = sqlite3.connect(chemin.resolve().as_uri() + '?mode=ro', uri=True)
        connexion.execute('PRAGMA query_only = ON')
    else:
        chemin.parent.mkdir(parents=True, exist_ok=True)
        connexion = sqlite3.connect(chemin)
    connexion.row_factory = sqlite3.Row
    connexion.execute("PRAGMA foreign_keys = ON")

    return connexion


def initialiser_base(chemin_base=None):
    """Cree les tables et applique les migrations sans effacer les donnees."""
    connexion = ouvrir_connexion(chemin_base)

    try:
        with connexion:
            connexion.execute("BEGIN IMMEDIATE")
            connexion.execute("""
                CREATE TABLE IF NOT EXISTS factures (
                    id INTEGER PRIMARY KEY,
                    numero_document TEXT,
                    fournisseur TEXT,
                    client TEXT,
                    date_facture TEXT,
                    devise TEXT,
                    total_ht_centimes INTEGER,
                    montant_tva_centimes INTEGER,
                    total_ttc_centimes INTEGER,
                    statut TEXT,
                    chemin_original TEXT,
                    json_brut TEXT NOT NULL,
                    cree_le TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            connexion.execute("""
                CREATE TABLE IF NOT EXISTS lignes_facture (
                    id INTEGER PRIMARY KEY,
                    facture_id INTEGER NOT NULL,
                    numero_ligne INTEGER NOT NULL,
                    designation TEXT,
                    quantite TEXT,
                    unite TEXT,
                    prix_unitaire_ht TEXT,
                    montant_ht TEXT,
                    prix_unitaire_ttc TEXT,
                    montant_ttc TEXT,
                    prix_unitaire TEXT,
                    montant TEXT,
                    taux_tva TEXT,
                    page INTEGER,
                    FOREIGN KEY (facture_id)
                        REFERENCES factures(id),
                    UNIQUE (facture_id, numero_ligne)
                )
            """)
            connexion.execute("""
                CREATE TABLE IF NOT EXISTS validations (
                    id INTEGER PRIMARY KEY,
                    facture_id INTEGER NOT NULL,
                    champ TEXT NOT NULL,
                    ancienne_valeur TEXT,
                    nouvelle_valeur TEXT,
                    action TEXT NOT NULL
                        CHECK (action IN ('correction', 'confirmation')),
                    auteur TEXT,
                    date_action TEXT NOT NULL
                        DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (facture_id)
                        REFERENCES factures(id)
                )
            """)
            _migrer(connexion)
            _initialiser_pages_document(connexion)
    finally:
        connexion.close()



def fts5_disponible():
    """Sonde uniquement une base memoire, jamais la base de factures."""
    with closing(sqlite3.connect(':memory:')) as connexion:
        try:
            connexion.execute('CREATE VIRTUAL TABLE sonde USING fts5(texte)')
            return True
        except sqlite3.OperationalError as erreur:
            if 'no such module' in str(erreur).lower():
                return False
            raise


def _initialiser_pages_document(connexion):
    connexion.execute("""
        CREATE TABLE IF NOT EXISTS pages_document (
            id INTEGER PRIMARY KEY,
            facture_id INTEGER NOT NULL REFERENCES factures(id),
            numero_page INTEGER NOT NULL CHECK(numero_page > 0),
            langue TEXT NOT NULL CHECK(langue IN ('fr','ar')),
            texte_ocr TEXT NOT NULL,
            source TEXT NOT NULL,
            UNIQUE(facture_id, numero_page, langue)
        )
    """)
    noms=('pages_document_ai','pages_document_ad','pages_document_au')
    if not fts5_disponible():
        # Les textes restent sauvegardables si un autre SQLite n'a pas FTS5.
        # Le prochain demarrage avec FTS5 reconstruira l'index depuis les textes.
        for nom in noms:
            connexion.execute(f'DROP TRIGGER IF EXISTS {nom}')
        return
    index_existant=connexion.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='pages_document_fts'").fetchone()
    triggers={r[0] for r in connexion.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
    connexion.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS pages_document_fts USING fts5(
            texte_ocr, content='pages_document', content_rowid='id',
            tokenize='unicode61 remove_diacritics 2')
    """)
    connexion.execute("""CREATE TRIGGER IF NOT EXISTS pages_document_ai AFTER INSERT ON pages_document BEGIN
        INSERT INTO pages_document_fts(rowid,texte_ocr) VALUES(new.id,new.texte_ocr); END""")
    connexion.execute("""CREATE TRIGGER IF NOT EXISTS pages_document_ad AFTER DELETE ON pages_document BEGIN
        INSERT INTO pages_document_fts(pages_document_fts,rowid,texte_ocr) VALUES('delete',old.id,old.texte_ocr); END""")
    connexion.execute("""CREATE TRIGGER IF NOT EXISTS pages_document_au AFTER UPDATE ON pages_document BEGIN
        INSERT INTO pages_document_fts(pages_document_fts,rowid,texte_ocr) VALUES('delete',old.id,old.texte_ocr);
        INSERT INTO pages_document_fts(rowid,texte_ocr) VALUES(new.id,new.texte_ocr); END""")
    if not index_existant or not set(noms).issubset(triggers):
        connexion.execute("INSERT INTO pages_document_fts(pages_document_fts) VALUES('rebuild')")


def enregistrer_pages_document(facture_id,pages,chemin_base=None,*,traitement_id):
    """Ajout atomique, sans remplacer une lecture deja archivee ni deviner son lien."""
    with closing(ouvrir_connexion(chemin_base)) as connexion:
        with connexion:
            connexion.execute('BEGIN IMMEDIATE')
            facture=connexion.execute('SELECT traitement_id FROM factures WHERE id=?',(facture_id,)).fetchone()
            if facture is None or not traitement_id or facture['traitement_id']!=traitement_id:
                raise ValueError('Lien entre traitement et facture absent ou incorrect.')
            for page in pages:
                numero=page.get('numero_page')
                langue=page.get('langue')
                texte=page.get('texte_ocr')
                source=page.get('source')
                if type(numero) is not int or numero<1 or langue not in ('fr','ar') or not isinstance(texte,str):
                    raise ValueError('Lecture OCR invalide.')
                # Source relative deterministe ; aucun chemin absolu dans les reponses.
                if source!=f'ocr/page_{numero}_{langue}.json':
                    raise ValueError('Source OCR invalide.')
                connexion.execute("""INSERT INTO pages_document
                    (facture_id,numero_page,langue,texte_ocr,source) VALUES(?,?,?,?,?)
                    ON CONFLICT(facture_id,numero_page,langue) DO NOTHING""",
                    (facture_id,numero,langue,texte,source))
            return connexion.execute('SELECT count(*) FROM pages_document WHERE facture_id=?',(facture_id,)).fetchone()[0]


def rechercher_pages_document(facture_id,recherche,chemin_base=None,*,question=False):
    """Mots entiers, tous requis. Ponctuation ignoree, operateurs FTS non interpretes."""
    if not isinstance(recherche,str) or not recherche.strip() or len(recherche)>200:
        raise ValueError('Saisissez de 1 à 200 caractères.')
    mots=re.findall(r'[^\W_]+',recherche,flags=re.UNICODE)
    if not mots or len(mots)>20:
        raise ValueError('Saisissez de 1 à 20 mots contenant des lettres ou des chiffres.')
    if question:
        vides=set('quelles quelle quels quel sont est les le la des de du un une et sur dans document facture que qui comment à au aux'.split())
        mots=[m for m in mots if m.lower() not in vides] or mots
    expression=(' OR ' if question else ' AND ').join('"'+mot+'"' for mot in mots)
    with closing(ouvrir_connexion(chemin_base,lecture_seule=True)) as connexion:
        connexion.execute('BEGIN')
        existe=connexion.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pages_document'").fetchone()
        if not existe or not connexion.execute(
                "SELECT 1 FROM pages_document WHERE facture_id=? AND length(trim(texte_ocr))>0 LIMIT 1",(facture_id,)).fetchone():
            return dict(etat='aucun_texte',message='Aucun texte OCR enregistré',passages=[])
        index=connexion.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pages_document_fts'").fetchone()
        if not index or not fts5_disponible():
            return dict(etat='fts5_indisponible',message='Recherche indisponible : FTS5 ou son index est absent. Les textes OCR sont conservés.',passages=[])
        lignes=connexion.execute("""
            SELECT p.numero_page,p.langue,p.source,
                   snippet(pages_document_fts,0,'','',' … ',32) AS passage
            FROM pages_document_fts JOIN pages_document p ON p.id=pages_document_fts.rowid
            WHERE pages_document_fts MATCH ? AND p.facture_id=?
            ORDER BY bm25(pages_document_fts),p.numero_page,p.langue LIMIT 20
        """,(expression,facture_id)).fetchall()
        passages=[dict(ligne) for ligne in lignes]
        return dict(etat='resultats' if passages else 'aucun_resultat',
                    message=f'{len(passages)} passage(s) trouvé(s).' if passages else 'Aucun passage trouvé',passages=passages)


# Noms SQL exclusivement issus de ces listes internes, jamais de la saisie.
CHAMPS_TEXTE = (
    'numero_document', 'fournisseur', 'client', 'date_facture', 'devise',
    'ice_fournisseur', 'ice_client', 'date_echeance', 'adresse_fournisseur',
    'adresse_client', 'adresse_livraison', 'type_document', 'document_id',
    'chemin_original', 'statut',
)
CHAMPS_NOMBRES = ('total_ht', 'montant_tva', 'total_ttc', 'total_document', 'taux_tva')
CENTIMES = ('total_ht', 'montant_tva', 'total_ttc')
NOMBRES_ARTICLE = (
    'quantite', 'prix_unitaire_ht', 'montant_ht', 'prix_unitaire_ttc',
    'montant_ttc', 'prix_unitaire', 'montant', 'taux_tva', 'hauteur_cm', 'largeur_cm',
)
CHAMPS_MODIFIABLES = frozenset(CHAMPS_TEXTE + CHAMPS_NOMBRES)
ALIASES = {'date_facture': 'date', 'chemin_original': 'fichier_source'}


def texte_ou_null(valeur):
    if valeur is not None and not isinstance(valeur, str):
        raise ValueError('Une chaine de caracteres ou None est attendue.')
    return valeur


def nombre_vers_texte(valeur):
    """Decimal exact en TEXT SQLite ; aucune conversion via float."""
    if valeur is None:
        return None
    if isinstance(valeur, bool) or not isinstance(valeur, (str, int, float, Decimal)):
        raise ValueError('Un nombre est attendu, pas un booleen ou un objet.')
    try:
        nombre = Decimal(str(valeur))
    except InvalidOperation as erreur:
        raise ValueError(f'Nombre invalide : {valeur!r}') from erreur
    if not nombre.is_finite():
        raise ValueError('Le nombre doit etre fini.')
    return format(nombre, 'f')


def montant_vers_centimes(valeur):
    """MAD/EUR seulement : conversion exacte et controle de la limite SQLite."""
    texte = nombre_vers_texte(valeur)
    if texte is None:
        return None
    # Decoupage textuel pour ne pas subir la precision du contexte Decimal.
    entier, _, fraction = texte.partition('.')
    if any(c != '0' for c in fraction[2:]):
        raise ValueError('Le total contient plus de deux decimales significatives.')
    signe = -1 if entier.startswith('-') else 1
    resultat = signe * (abs(int(entier)) * 100 + int((fraction + '00')[:2]))
    if not -(2**63) <= resultat < 2**63:
        raise ValueError('Total en centimes hors de la capacite INTEGER SQLite.')
    return resultat


def _centimes(valeur, devise):
    code = devise.strip().upper() if devise is not None else None
    return montant_vers_centimes(valeur) if code in ('MAD', 'EUR') else None


def _valeurs_document(document):
    valeurs = {champ: texte_ou_null(document.get(ALIASES.get(champ, champ)))
               for champ in CHAMPS_TEXTE}
    if valeurs['numero_document'] is None:
        valeurs['numero_document'] = texte_ou_null(document.get('numero_facture'))
    for champ in CHAMPS_NOMBRES:
        valeurs[champ] = nombre_vers_texte(document.get(champ))
    for champ in CENTIMES:
        valeurs[champ + '_centimes'] = _centimes(valeurs[champ], valeurs['devise'])
    # Le total generique suit la meme precision, sans lui attribuer HT ou TTC.
    _centimes(valeurs['total_document'], valeurs['devise'])
    return valeurs


def _migrer(connexion):
    """Ajouts seulement. Recupere les nouveaux champs depuis le JSON historique.

    Les colonnes deja presentes ne sont jamais reecrites, donc les corrections
    humaines et les centimes historiques restent intacts.
    """
    # SQLite ne permet pas de retirer NOT NULL directement. On conserve donc
    # l'ancienne colonne et ses valeurs, puis on expose un statut nullable.
    # Aucun remplacement de table ni effacement de donnees.
    colonnes = {r['name']: r for r in connexion.execute('PRAGMA table_info(factures)')}
    if colonnes['statut']['notnull']:
        connexion.execute('ALTER TABLE factures RENAME COLUMN statut TO statut_legacy')
        connexion.execute('ALTER TABLE factures ADD COLUMN statut TEXT')
        connexion.execute('UPDATE factures SET statut = statut_legacy')
    for table, champs in (
        ('factures', CHAMPS_TEXTE + CHAMPS_NOMBRES),
        ('lignes_facture', ('hauteur_cm', 'largeur_cm')),
    ):
        existants = {r['name'] for r in connexion.execute(f'PRAGMA table_info({table})')}
        nouveaux = [champ for champ in champs if champ not in existants]
        for champ in nouveaux:
            connexion.execute(f'ALTER TABLE {table} ADD COLUMN {champ} TEXT')
        if table == 'factures' and nouveaux:
            for ligne in connexion.execute('SELECT * FROM factures').fetchall():
                document = json.loads(ligne['json_brut'], parse_float=Decimal)
                for champ in nouveaux:
                    valeur = document.get(ALIASES.get(champ, champ))
                    if valeur is None and champ in CENTIMES and ligne[champ + '_centimes'] is not None:
                        # Les anciennes colonnes utilisaient toutes les devises en centimes.
                        valeur = format(Decimal(ligne[champ + '_centimes']).scaleb(-2), 'f')
                    valeur = (nombre_vers_texte(valeur) if champ in CHAMPS_NOMBRES
                              else texte_ou_null(valeur))
                    connexion.execute(f'UPDATE factures SET {champ} = ? WHERE id = ?',
                                      (valeur, ligne['id']))
        if table == 'lignes_facture' and nouveaux:
            for facture in connexion.execute('SELECT id, json_brut FROM factures').fetchall():
                document = json.loads(facture['json_brut'], parse_float=Decimal)
                for numero, article in enumerate(document.get('lignes', []), 1):
                    for champ in nouveaux:
                        connexion.execute(
                            f'UPDATE lignes_facture SET {champ} = ? WHERE facture_id = ? AND numero_ligne = ?',
                            (nombre_vers_texte(article.get(champ)), facture['id'], numero))

    # L'identifiant du traitement web est distinct du numero de facture.
    # Les factures historiques sans lien web gardent NULL.
    existants = {r['name'] for r in connexion.execute('PRAGMA table_info(factures)')}
    if 'traitement_id' not in existants:
        connexion.execute('ALTER TABLE factures ADD COLUMN traitement_id TEXT')
    connexion.execute(
        'CREATE UNIQUE INDEX IF NOT EXISTS factures_traitement_unique ON factures(traitement_id)')


def _inserer(connexion, table, valeurs):
    champs = ', '.join(valeurs)
    parametres = ', '.join('?' for _ in valeurs)
    return connexion.execute(f'INSERT INTO {table} ({champs}) VALUES ({parametres})',
                             tuple(valeurs.values())).lastrowid


def _article(connexion, facture_id, numero, article):
    if not isinstance(article, dict):
        raise ValueError('Chaque article doit etre un objet.')
    if isinstance(numero, bool) or not isinstance(numero, int) or numero < 1:
        raise ValueError('Le numero de ligne doit etre un entier positif.')
    valeurs = dict(facture_id=facture_id, numero_ligne=numero)
    for champ in ('designation', 'unite'):
        valeurs[champ] = texte_ou_null(article.get(champ))
    for champ in NOMBRES_ARTICLE:
        valeurs[champ] = nombre_vers_texte(article.get(champ))
    page = article.get('page')
    if page is not None and (isinstance(page, bool) or not isinstance(page, int) or page < 1):
        raise ValueError('La page doit etre un entier positif ou None.')
    valeurs['page'] = page
    return _inserer(connexion, 'lignes_facture', valeurs)


def _decimal_json(valeur):
    # JSON standard ne connait pas Decimal : representation exacte en chaine.
    if isinstance(valeur, Decimal):
        return nombre_vers_texte(valeur)
    raise TypeError('Objet non serialisable en JSON : ' + type(valeur).__name__)


def _refuser_constante(valeur):
    raise ValueError('Nombre non fini : ' + valeur)


def enregistrer_facture(document, chemin_base=None, *, traitement_id=None, chemin_original=None):
    """Objet dict ou texte JSON original -> identifiant entier.

    Passer le texte JSON pour preserver aussi espaces, ordre et notation des nombres.
    Un dict est serialise sans etre modifie ; utiliser des chaines pour ses decimaux
    de precision superieure a celle des float Python.
    Avec traitement_id, une nouvelle tentative retourne le meme identifiant,
    sans modifier la facture deja enregistree. chemin_original permet de garder
    le chemin reel du fichier sans modifier le JSON brut.
    """
    if isinstance(document, str):
        brut = document
        document = json.loads(brut, parse_float=Decimal,
                              parse_constant=_refuser_constante)
    else:
        brut = json.dumps(document, ensure_ascii=False, allow_nan=False, default=_decimal_json)
    if not isinstance(document, dict):
        raise ValueError('La facture doit etre un objet JSON.')
    articles = document.get('lignes', [])
    if not isinstance(articles, list):
        raise ValueError("'lignes' doit etre une liste.")
    valeurs = _valeurs_document(document)
    valeurs['json_brut'] = brut
    traitement_id = texte_ou_null(traitement_id)
    if traitement_id is not None:
        if not traitement_id.strip():
            raise ValueError('Identifiant de traitement vide.')
        valeurs['traitement_id'] = traitement_id
    if chemin_original is not None:
        valeurs['chemin_original'] = texte_ou_null(chemin_original)
    with closing(ouvrir_connexion(chemin_base)) as connexion:
        with connexion:
            # Verrou avant la recherche : deux sauvegardes du meme traitement
            # ne peuvent pas toutes deux conclure qu'il reste a inserer.
            connexion.execute('BEGIN IMMEDIATE')
            if traitement_id is not None:
                existante = connexion.execute(
                    'SELECT id FROM factures WHERE traitement_id = ?', (traitement_id,)).fetchone()
                if existante is not None:
                    return existante['id']
            facture_id = _inserer(connexion, 'factures', valeurs)
            for numero, article in enumerate(articles, 1):
                _article(connexion, facture_id, numero, article)
        return facture_id


def lire_facture(facture_id, chemin_base=None, *, lecture_seule=False):
    """Retourne un dict incluant lignes, ou None. Les decimaux restent du texte."""
    with closing(ouvrir_connexion(chemin_base, lecture_seule=lecture_seule)) as connexion:
        # Un meme instant de lecture pour la facture et ses lignes.
        connexion.execute('BEGIN')
        ligne = connexion.execute('SELECT * FROM factures WHERE id = ?', (facture_id,)).fetchone()
        if ligne is None:
            return None
        resultat = dict(ligne)
        resultat['lignes'] = _lire_articles(connexion, facture_id)
        return resultat


def _lire_articles(connexion, facture_id):
    return [dict(r) for r in connexion.execute(
        'SELECT * FROM lignes_facture WHERE facture_id = ? ORDER BY numero_ligne', (facture_id,))]


def lire_articles(facture_id, chemin_base=None):
    with closing(ouvrir_connexion(chemin_base)) as connexion:
        return _lire_articles(connexion, facture_id)


def lister_factures(chemin_base=None, *, lecture_seule=False):
    """Liste des fiches enregistrees, par identifiant croissant, sans les articles."""
    with closing(ouvrir_connexion(chemin_base, lecture_seule=lecture_seule)) as connexion:
        return [dict(r) for r in connexion.execute('SELECT * FROM factures ORDER BY id')]


def ajouter_article(facture_id, numero_ligne, designation, quantite,
                    prix_unitaire, montant, chemin_base=None):
    with closing(ouvrir_connexion(chemin_base)) as connexion:
        with connexion:
            return _article(connexion, facture_id, numero_ligne, dict(
                designation=designation, quantite=quantite,
                prix_unitaire=prix_unitaire, montant=montant))


def corriger_champ(facture_id, champ, nouvelle_valeur, auteur=None, chemin_base=None):
    """Corrige un champ autorise et retourne l'identifiant de l'historique.

    Les noms sont ceux des colonnes (date_facture). Aucun calcul ni confirmation.
    """
    if not isinstance(champ, str) or champ not in CHAMPS_MODIFIABLES:
        raise ValueError('Champ non modifiable : ' + str(champ))
    auteur = texte_ou_null(auteur)
    valeur = (nombre_vers_texte(nouvelle_valeur) if champ in CHAMPS_NOMBRES
              else texte_ou_null(nouvelle_valeur))
    if champ == 'statut' and valeur not in (None, 'a_verifier', 'partiel'):
        raise ValueError('Une correction ne peut pas confirmer une facture.')
    with closing(ouvrir_connexion(chemin_base)) as connexion:
        with connexion:
            connexion.execute('BEGIN IMMEDIATE')
            ligne = connexion.execute('SELECT * FROM factures WHERE id = ?', (facture_id,)).fetchone()
            if ligne is None:
                raise ValueError('Facture inexistante.')
            changements = {champ: valeur}
            if champ in CENTIMES:
                changements[champ + '_centimes'] = _centimes(valeur, ligne['devise'])
            if champ == 'total_document':
                _centimes(valeur, ligne['devise'])
            if champ == 'devise':
                _centimes(ligne['total_document'], valeur)
                for total in CENTIMES:
                    changements[total + '_centimes'] = _centimes(ligne[total], valeur)
            affectations = ', '.join(nom + ' = ?' for nom in changements)
            connexion.execute(f'UPDATE factures SET {affectations} WHERE id = ?',
                              (*changements.values(), facture_id))
            return _inserer(connexion, 'validations', dict(
                facture_id=facture_id, champ=champ, ancienne_valeur=ligne[champ],
                nouvelle_valeur=valeur, action='correction', auteur=auteur,
                date_action=datetime.now(timezone.utc).isoformat()))


def lire_validations(facture_id, chemin_base=None, *, lecture_seule=False):
    with closing(ouvrir_connexion(chemin_base, lecture_seule=lecture_seule)) as connexion:
        return [dict(r) for r in connexion.execute(
            'SELECT * FROM validations WHERE facture_id = ? ORDER BY id', (facture_id,))]


_ARABE_DIACRITIQUES = re.compile(r'[\u064B-\u065F\u0670\u0640]')
_ARABE_VARIANTES = str.maketrans({'أ': 'ا', 'إ': 'ا', 'آ': 'ا', 'ى': 'ي', 'ة': 'ه', 'ؤ': 'و', 'ئ': 'ي'})


def normaliser_texte_recherche(texte):
    """Normalise un texte FR/AR pour le filtrage structure et lexical."""
    if not texte:
        return ''
    valeur = unicodedata.normalize('NFKD', str(texte))
    valeur = ''.join(c for c in valeur if not unicodedata.combining(c))
    valeur = _ARABE_DIACRITIQUES.sub('', valeur).translate(_ARABE_VARIANTES)
    valeur = valeur.casefold()
    return re.sub(r'\s+', ' ', valeur).strip()


def lister_factures_avec_lignes(chemin_base=None, *, lecture_seule=True, inclure_pages=True):
    """Retourne les factures enregistrees avec leurs articles et pages OCR."""
    chemin = Path(chemin_base) if chemin_base is not None else CHEMIN_BASE
    if lecture_seule and not chemin.exists():
        return []
    with closing(ouvrir_connexion(chemin, lecture_seule=lecture_seule)) as connexion:
        connexion.execute('BEGIN')
        factures = [dict(r) for r in connexion.execute('SELECT * FROM factures ORDER BY id')]
        lignes_par_facture = {}
        for r in connexion.execute('SELECT * FROM lignes_facture ORDER BY facture_id, numero_ligne'):
            ligne = dict(r)
            lignes_par_facture.setdefault(ligne['facture_id'], []).append(ligne)
        pages_par_facture = {}
        if inclure_pages:
            existe = connexion.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='pages_document'"
            ).fetchone()
            if existe:
                for r in connexion.execute(
                    'SELECT id, facture_id, numero_page, langue, texte_ocr, source '
                    'FROM pages_document ORDER BY facture_id, numero_page, langue'
                ):
                    page = dict(r)
                    pages_par_facture.setdefault(page['facture_id'], []).append(page)
        for facture in factures:
            fid = facture['id']
            facture['lignes'] = lignes_par_facture.get(fid, [])
            if inclure_pages:
                facture['pages'] = pages_par_facture.get(fid, [])
        return factures


def _decimal_ou_none(valeur):
    if valeur is None or isinstance(valeur, bool):
        return None
    if isinstance(valeur, str) and not valeur.strip():
        return None
    try:
        nombre = Decimal(str(valeur).strip())
    except (InvalidOperation, ValueError):
        return None
    return nombre if nombre.is_finite() else None


def calculer_agregats_factures(factures, lignes=None):
    """Calcule des totaux exacts en Decimal regroupes par devise."""
    groupes = {}
    for facture in factures:
        devise = (facture.get('devise') or 'NON_PRECISEE').strip().upper() or 'NON_PRECISEE'
        g = groupes.setdefault(devise, {
            'nombre_factures': 0,
            'total_ht': Decimal('0'), 'has_ht': False,
            'montant_tva': Decimal('0'), 'has_tva': False,
            'total_ttc': Decimal('0'), 'has_ttc': False,
            'total_document': Decimal('0'), 'has_doc': False,
            'ttc_valeurs': [],
        })
        g['nombre_factures'] += 1
        for champ, flag in (('total_ht', 'has_ht'), ('montant_tva', 'has_tva'),
                            ('total_ttc', 'has_ttc'), ('total_document', 'has_doc')):
            val = _decimal_ou_none(facture.get(champ))
            if val is not None:
                g[champ] += val
                g[flag] = True
                if champ == 'total_ttc':
                    g['ttc_valeurs'].append(val)

    par_devise = {}
    for devise, g in groupes.items():
        ttcs = g['ttc_valeurs']
        moyenne = (sum(ttcs) / Decimal(len(ttcs))).quantize(Decimal('0.01')) if ttcs else None
        par_devise[devise] = {
            'nombre_factures': g['nombre_factures'],
            'total_ht': format(g['total_ht'], 'f') if g['has_ht'] else None,
            'montant_tva': format(g['montant_tva'], 'f') if g['has_tva'] else None,
            'total_ttc': format(g['total_ttc'], 'f') if g['has_ttc'] else None,
            'total_document': format(g['total_document'], 'f') if g['has_doc'] else None,
            'moyenne_ttc': format(moyenne, 'f') if moyenne is not None else None,
            'min_ttc': format(min(ttcs), 'f') if ttcs else None,
            'max_ttc': format(max(ttcs), 'f') if ttcs else None,
        }

    resume = {
        'nombre_factures': len(factures),
        'par_devise': par_devise,
    }
    if lignes is not None:
        q_sum, has_q = Decimal('0'), False
        ht_sum, has_lht = Decimal('0'), False
        ttc_sum, has_lttc = Decimal('0'), False
        gen_sum, has_lgen = Decimal('0'), False
        for ligne in lignes:
            for champ, acc in (('quantite', 'q'), ('montant_ht', 'ht'),
                               ('montant_ttc', 'ttc'), ('montant', 'gen')):
                val = _decimal_ou_none(ligne.get(champ))
                if val is not None:
                    if acc == 'q':
                        q_sum += val; has_q = True
                    elif acc == 'ht':
                        ht_sum += val; has_lht = True
                    elif acc == 'ttc':
                        ttc_sum += val; has_lttc = True
                    else:
                        gen_sum += val; has_lgen = True
        resume['lignes'] = {
            'nombre_lignes': len(lignes),
            'somme_quantites': format(q_sum, 'f') if has_q else None,
            'somme_montants_ht': format(ht_sum, 'f') if has_lht else None,
            'somme_montants_ttc': format(ttc_sum, 'f') if has_lttc else None,
            'somme_montants': format(gen_sum, 'f') if has_lgen else None,
        }
    return resume


def rechercher_factures_structurees(
    chemin_base=None,
    *,
    fournisseur=None,
    client=None,
    ice=None,
    numero_document=None,
    date_debut=None,
    date_fin=None,
    devise=None,
    statut=None,
    article=None,
    facture_id=None,
):
    """Filtre les factures et lignes dans SQLite (lecture seule) avec calculs Decimal."""
    toutes = lister_factures_avec_lignes(chemin_base, lecture_seule=True, inclure_pages=False)
    n_fourn = normaliser_texte_recherche(fournisseur)
    n_client = normaliser_texte_recherche(client)
    n_num = normaliser_texte_recherche(numero_document)
    n_art = normaliser_texte_recherche(article)
    ice_clean = re.sub(r'\s+', '', str(ice)) if ice else ''
    devise_clean = devise.strip().upper() if devise else ''
    statut_clean = statut.strip().lower() if statut else ''

    factures_trouvees = []
    lignes_trouvees = []
    for f in toutes:
        if facture_id is not None and f.get('id') != facture_id:
            continue
        if n_fourn and n_fourn not in normaliser_texte_recherche(f.get('fournisseur')):
            continue
        if n_client and n_client not in normaliser_texte_recherche(f.get('client')):
            continue
        if ice_clean:
            ices = (
                re.sub(r'\s+', '', f.get('ice_fournisseur') or ''),
                re.sub(r'\s+', '', f.get('ice_client') or ''),
            )
            if not any(ice_clean in val for val in ices if val):
                continue
        if n_num and n_num not in normaliser_texte_recherche(f.get('numero_document')):
            continue
        date_f = f.get('date_facture') or ''
        if date_debut and (not date_f or date_f < date_debut):
            continue
        if date_fin and (not date_f or date_f > date_fin):
            continue
        if devise_clean and (f.get('devise') or '').strip().upper() != devise_clean:
            continue
        if statut_clean and (f.get('statut') or '').strip().lower() != statut_clean:
            continue

        lignes_f = f.get('lignes', [])
        if n_art:
            lignes_match = [
                l for l in lignes_f
                if n_art in normaliser_texte_recherche(l.get('designation'))
            ]
            if not lignes_match:
                continue
        else:
            lignes_match = list(lignes_f)

        factures_trouvees.append(f)
        for l in lignes_match:
            lignes_trouvees.append({
                **l,
                'numero_document': f.get('numero_document'),
                'fournisseur': f.get('fournisseur'),
                'client': f.get('client'),
                'date_facture': f.get('date_facture'),
                'devise': f.get('devise'),
            })

    return {
        'factures': factures_trouvees,
        'lignes': lignes_trouvees,
        'calculs': calculer_agregats_factures(factures_trouvees, lignes_trouvees),
    }


def executer_sql_lecture_seule(requete, parametres=(), chemin_base=None, *, limite=100):
    """Execute uniquement une requete SELECT SQLite en mode lecture seule."""
    if not isinstance(requete, str) or not requete.strip():
        raise ValueError('Requete SQL vide.')
    sql = requete.strip().rstrip(';').strip()
    if ';' in sql:
        raise ValueError('Une seule requete SELECT est autorisee.')
    sql_min = re.sub(r'--[^\n]*|/\*.*?\*/', ' ', sql, flags=re.S).strip().lower()
    if not (sql_min.startswith('select ') or sql_min.startswith('with ')):
        raise ValueError('Seules les requetes SELECT sont autorisees.')
    mots_interdits = re.compile(
        r'\b(insert|update|delete|drop|alter|create|replace|pragma|attach|detach|vacuum|reindex|json_brut|chemin_original)\b'
    )
    if mots_interdits.search(sql_min):
        raise ValueError('Mot-cle ou colonne interdite dans la requete SQL.')
    chemin = Path(chemin_base) if chemin_base is not None else CHEMIN_BASE
    if not chemin.exists():
        return []
    with closing(ouvrir_connexion(chemin, lecture_seule=True)) as connexion:
        connexion.execute('BEGIN')
        curseur = connexion.execute(sql, tuple(parametres))
        return [dict(r) for r in curseur.fetchmany(limite)]


if __name__ == '__main__':
    initialiser_base()
    print(f'Base prete : {CHEMIN_BASE}')
