"""Formas del francés -> posibles palabras base (reglas + participios irregulares).

El lematizador (simplemma) falla con muchos participios, femeninos y adverbios: «passée», «vécu»,
«sélectionnée», «maximale», «conformément»… Aquí se generan los candidatos con reglas; el diccionario
se queda solo con los que existen. `candidates(form)` devuelve [(base, motivo)] del más al menos probable.
"""

# Participios pasados irregulares -> infinitivo
IRREGULAR_PARTICIPLES = {
    "eu": "avoir", "été": "être", "fait": "faire", "dit": "dire", "vu": "voir", "pu": "pouvoir", "su": "savoir",
    "dû": "devoir", "voulu": "vouloir", "venu": "venir", "tenu": "tenir", "vécu": "vivre", "lu": "lire",
    "mis": "mettre", "pris": "prendre", "appris": "apprendre", "compris": "comprendre", "surpris": "surprendre",
    "écrit": "écrire", "décrit": "décrire", "inscrit": "inscrire", "ouvert": "ouvrir", "couvert": "couvrir",
    "découvert": "découvrir", "offert": "offrir", "souffert": "souffrir", "mort": "mourir", "né": "naître",
    "connu": "connaître", "reconnu": "reconnaître", "paru": "paraître", "disparu": "disparaître",
    "apparu": "apparaître", "cru": "croire", "bu": "boire", "reçu": "recevoir", "aperçu": "apercevoir",
    "conçu": "concevoir", "plu": "plaire", "tu": "taire", "vaincu": "vaincre", "convaincu": "convaincre",
    "suivi": "suivre", "conduit": "conduire", "construit": "construire", "détruit": "détruire", "produit": "produire",
    "traduit": "traduire", "instruit": "instruire", "cuit": "cuire", "atteint": "atteindre", "peint": "peindre",
    "craint": "craindre", "éteint": "éteindre", "joint": "joindre", "contraint": "contraindre", "plaint": "plaindre",
    "absous": "absoudre", "résolu": "résoudre", "dissous": "dissoudre", "inclus": "inclure", "exclu": "exclure",
    "conclu": "conclure", "interdit": "interdire", "contredit": "contredire", "satisfait": "satisfaire",
    "défait": "défaire", "refait": "refaire", "parti": "partir", "sorti": "sortir", "senti": "sentir",
    "servi": "servir", "dormi": "dormir", "menti": "mentir", "couru": "courir", "acquis": "acquérir",
    "requis": "requérir", "promis": "promettre", "permis": "permettre", "admis": "admettre", "soumis": "soumettre",
    "remis": "remettre", "commis": "commettre", "transmis": "transmettre", "mû": "mouvoir", "crû": "croître",
    "assis": "asseoir", "ri": "rire", "suffi": "suffire", "nui": "nuire", "lui": "luire",
}

_ADJ_FEM = (("ive", "if"), ("ives", "if"), ("euse", "eux"), ("euses", "eux"), ("ère", "er"), ("ères", "er"),
            ("elle", "el"), ("elles", "el"), ("enne", "en"), ("ennes", "en"), ("onne", "on"), ("onnes", "on"),
            ("ienne", "ien"), ("iennes", "ien"), ("trice", "teur"), ("trices", "teur"), ("ette", "et"),
            ("ettes", "et"), ("ante", "ant"), ("antes", "ant"), ("ente", "ent"), ("entes", "ent"),
            ("ale", "al"), ("ales", "al"), ("aux", "al"), ("ague", "ag"), ("ouce", "ou"), ("fausse", "faux"),
            ("grosse", "gros"), ("belle", "beau"), ("belles", "beau"), ("nouvelle", "nouveau"),
            ("nouvelles", "nouveau"), ("vieille", "vieux"), ("vieilles", "vieux"), ("folle", "fou"),
            ("molle", "mou"), ("blanche", "blanc"), ("blanches", "blanc"), ("longue", "long"),
            ("longues", "long"), ("sèche", "sec"), ("fraîche", "frais"), ("douce", "doux"), ("douces", "doux"))


def _unique(items):
    seen, out = set(), []
    for base, why in items:
        if base and base not in seen:
            seen.add(base)
            out.append((base, why))
    return out


def candidates(form: str) -> list:
    """[(base, motivo)] para una forma en minúsculas."""
    w = form.lower().replace("’", "'")
    out = []
    if w in IRREGULAR_PARTICIPLES:
        out.append((IRREGULAR_PARTICIPLES[w], "participio"))

    # --- plurales y femeninos de participios irregulares: «mises», «prises», «vécues»
    for suf in ("es", "e", "s"):
        if w.endswith(suf) and w[: -len(suf)] in IRREGULAR_PARTICIPLES:
            out.append((IRREGULAR_PARTICIPLES[w[: -len(suf)]], "participio"))

    # --- adverbios en -ment
    if w.endswith("ément") and len(w) > 6:
        out.append((w[:-5] + "e", "adverbio"))                    # conformément -> conforme
    if w.endswith("amment"):
        out.append((w[:-6] + "ant", "adverbio"))                  # constamment -> constant
    if w.endswith("emment"):
        out.append((w[:-6] + "ent", "adverbio"))                  # évidemment -> évident
    if w.endswith("ment") and len(w) > 6:
        stem = w[:-4]
        out.append((stem, "adverbio"))                            # temporairement -> temporaire
        if stem.endswith("e"):
            out.append((stem[:-1], "adverbio"))                   # ultérieurement -> ultérieur
        if stem.endswith("ell"):
            out.append((stem[:-1], "adverbio"))                   # naturellement -> naturel
        if stem.endswith("ve"):
            out.append((stem[:-2] + "f", "adverbio"))             # activement -> actif (-ive)
        out.append((stem + "e", "adverbio"))                      # vraiment -> vrai(e) / poliment -> poli(e)

    # --- participios regulares: -é/-ée/-és/-ées -> -er ; -i/-ie/-is/-ies -> -ir ; -u/-ue/-us/-ues -> -re/-ir
    for suf in ("ées", "ée", "és", "é"):
        if w.endswith(suf):
            stem = w[: -len(suf)]
            out.append((stem + "er", "participio"))
            break
    for suf in ("ies", "ie", "is", "i"):
        if w.endswith(suf) and len(w) > len(suf) + 1:
            stem = w[: -len(suf)]
            out.append((stem + "ir", "participio"))
            out.append((stem + "ire", "participio"))
            break
    for suf in ("ues", "ue", "us", "u"):
        if w.endswith(suf) and len(w) > len(suf) + 1:
            stem = w[: -len(suf)]
            out.append((stem + "re", "participio"))
            out.append((stem + "ir", "participio"))
            out.append((stem + "oir", "participio"))
            break
    for suf in ("tes", "te"):                                     # ouverte -> ouvert, écrite -> écrit
        if w.endswith(suf):
            out.append((w[: -len(suf)] + "t", "femenino"))

    # --- adjetivos: femenino/plural irregulares (primero las formas enteras: belles -> beau)
    for suf, repl in _ADJ_FEM:
        if w == suf:
            out.append((repl, "femenino"))
    for suf, repl in _ADJ_FEM:
        if w.endswith(suf) and len(w) > len(suf):
            out.append((w[: -len(suf)] + repl, "femenino"))
    # --- plurales y femeninos sencillos
    if w.endswith("eaux"):
        out.append((w[:-1], "plural"))                            # bateaux -> bateau
    if w.endswith("aux"):
        out.append((w[:-3] + "al", "plural"))                     # chevaux -> cheval
    for suf in ("s", "x", "es", "e"):                             # secondes -> seconde ; certaines -> certain
        if w.endswith(suf) and len(w) > len(suf) + 1:
            out.append((w[: -len(suf)], "plural/femenino"))

    # --- prefijo «ré-/re-» de repetición: réessayer -> essayer
    for pre in ("ré", "re"):
        if w.startswith(pre) and len(w) > len(pre) + 3:
            rest = w[len(pre):]
            out.append((rest, "prefijo re-"))
            if rest.startswith("e") and pre == "ré":
                out.append((rest[1:] if rest[1:2] != "e" else rest, "prefijo re-"))
            break
    return _unique(out)


# Confusiones típicas del OCR (una letra por otra): envovées -> envoyées
OCR_CONFUSIONS = (("v", "y"), ("y", "v"), ("l", "i"), ("i", "l"), ("0", "o"), ("1", "l"), ("c", "e"), ("e", "c"),
                  ("u", "n"), ("n", "u"), ("a", "o"), ("o", "a"), ("rn", "m"), ("m", "rn"), ("é", "e"), ("è", "e"))


def ocr_variants(form: str) -> list:
    """Variantes de la palabra cambiando UNA confusión típica del OCR."""
    w = form.lower()
    out = []
    for a, b in OCR_CONFUSIONS:
        start = 0
        while True:
            i = w.find(a, start)
            if i < 0:
                break
            out.append(w[:i] + b + w[i + len(a):])
            start = i + 1
    return list(dict.fromkeys(out))
