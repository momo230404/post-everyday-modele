# -*- coding: utf-8 -*-
"""Post Everyday — le moteur d'écriture.

Il produit des scripts *réellement écrits*, pas des gabarits à trous. Le profil
métier est saisi une seule fois ; ensuite chaque angle sait s'en servir pour
composer des phrases qui se tiennent en français.

Trois familles de sortie, une par mode de publication :
  · parle    → un script à dire face caméra, découpé pour le prompteur
  · carrousel→ des planches courtes, une idée par planche
  · anime    → des répliques calées pour l'animation Remotion

Le tirage est déterministe pour une graine donnée : deux clics sur « une autre
idée » donnent deux angles différents, mais revenir sur une idée déjà générée
redonne exactement le même texte.
"""

import hashlib
import random
import re
import unicodedata

# ─────────────────────────────────────────────────────────────────────────────
# Les réseaux, et ce que chacun tolère
# ─────────────────────────────────────────────────────────────────────────────

RESEAUX = {
    "youtube": {
        "nom": "YouTube",
        "agent": "Victor",
        "complet": "Victor « Rush » Delcourt",
        "role": "Agent YouTube",
        "c1": "#ff3b3b", "c2": "#ffc447",
        "replique": "Ici on ne fait pas du jetable. Une vidéo bien faite te ramène des clients pendant deux ans.",
        "bulles": ["Miniature en test.", "Le chapitrage est prêt.", "Ça se regarde jusqu'au bout."],
        "duree": (180, 600),
        "duree_courte": (35, 60),
        "legende": 900,
        "hashtags": 3,
        "format": "16:9 pour la chaîne, 9:16 pour les Shorts",
        "conseil": "Les 15 premières secondes décident de tout. Annonce ce qu'on va apprendre, pas qui tu es.",
    },
    "instagram": {
        "nom": "Instagram",
        "agent": "Léna",
        "complet": "Léna « Grid » Fabre",
        "role": "Agent Instagram",
        "c1": "#ff3df0", "c2": "#ffc447",
        "replique": "Trois secondes. C'est ce que tu as avant que le pouce reparte. Après on peut parler.",
        "bulles": ["Le carrousel monte bien.", "Reel à 12 000 vues.", "On garde la même palette."],
        "duree": (20, 45),
        "duree_courte": (20, 45),
        "legende": 2200,
        "hashtags": 8,
        "format": "9:16 pour les Reels, 4:5 pour les carrousels",
        "conseil": "La première planche du carrousel fait tout le travail. Les suivantes ne font que tenir la promesse.",
    },
    "tiktok": {
        "nom": "TikTok",
        "agent": "Malik",
        "complet": "Malik « Loop » Ferrand",
        "role": "Agent TikTok",
        "c1": "#38e8ff", "c2": "#ff3df0",
        "replique": "Pas d'intro, pas de bonjour. Tu commences par la phrase qui dérange, sinon tu es mort.",
        "bulles": ["Rétention à 68 %.", "Ça reboucle bien.", "Deuxième montage en test."],
        "duree": (15, 40),
        "duree_courte": (15, 40),
        "legende": 2200,
        "hashtags": 5,
        "format": "9:16, plein cadre",
        "conseil": "Écris la dernière phrase pour qu'elle donne envie de revoir la première. La boucle double les vues.",
    },
    "linkedin": {
        "nom": "LinkedIn",
        "agent": "Claire",
        "complet": "Claire « Deck » Nogaret",
        "role": "Agent LinkedIn",
        "c1": "#38a8ff", "c2": "#5cf08d",
        "replique": "Ton client ici, c'est un décideur pressé. Une idée, un exemple chiffré, et tu t'arrêtes.",
        "bulles": ["Le post décolle.", "Quatre demandes en MP.", "On garde le ton sobre."],
        "duree": (45, 90),
        "duree_courte": (45, 90),
        "legende": 3000,
        "hashtags": 3,
        "format": "Texte, carrousel PDF, ou vidéo 1:1",
        "conseil": "Les trois premières lignes sont visibles avant le « voir plus ». Tout se joue là.",
    },
    "facebook": {
        "nom": "Facebook",
        "agent": "Serge",
        "complet": "Serge « Proximité » Bouvier",
        "role": "Agent Facebook",
        "c1": "#5cf08d", "c2": "#38a8ff",
        "replique": "Ici les gens te connaissent, ou connaissent quelqu'un qui te connaît. Parle comme au comptoir.",
        "bulles": ["Douze partages.", "Le groupe local a repris le post.", "Trois appels ce matin."],
        "duree": (40, 90),
        "duree_courte": (40, 90),
        "legende": 2000,
        "hashtags": 2,
        "format": "1:1 ou 4:5, vidéo ou photo",
        "conseil": "Le partage vaut plus que le like. Écris quelque chose qu'on a envie d'envoyer à un proche.",
    },
}

ORDRE_RESEAUX = ["youtube", "instagram", "tiktok", "linkedin", "facebook"]

# Le sixième personnage : il ne publie pas, il arbitre.
PATRON = {
    "id": "cm",
    "nom": "Community manager",
    "agent": "Nadia",
    "complet": "Nadia « Chiffre » Belkacem",
    "role": "Community manager",
    "sous_titre": "Elle dirige les cinq agents",
    "c1": "#ffc447", "c2": "#a86bff",
    "replique": "Je ne publie pas moi-même : je décide de ce qui part, où, et quel jour. Les cinq agents en dessous exécutent.",
    "bulles": ["Le plan de la semaine est prêt.", "Deux réseaux en retard.", "Le vendredi, on soigne."],
}

# ─────────────────────────────────────────────────────────────────────────────
# Les angles : de quoi peut-on parler tous les jours sans se répéter
# ─────────────────────────────────────────────────────────────────────────────
# Chaque angle sait composer trois choses à partir du profil : un titre, une
# accroche, et un corps. Les tournures sont écrites pour rester naturelles
# quel que soit le métier inséré.

ANGLES = [
    {
        "id": "erreur",
        "famille": "pédagogie",
        "titre": "L'erreur qui coûte le plus cher dans mon métier",
        "promesse": "Ce qu'on paie sans même le savoir",
        "hook": "Il y a une erreur que je vois presque tous les jours, et elle coûte plus cher que tout le reste.",
        "corps": [
            "Neuf fois sur dix, on me contacte trop tard. Le problème est là depuis des mois, et ce qui aurait coûté trois fois rien devient un vrai budget.",
            "Le réflexe, c'est de comparer les prix. Le bon réflexe, c'est de comparer ce qui est réellement compris dans le prix.",
            "Ce que je conseille : posez une seule question avant de signer — « qu'est-ce qui n'est pas inclus ? ». La réponse vous apprend tout.",
        ],
        "chute": "Si vous avez un doute, décrivez-moi votre situation : je vous dis franchement si ça vaut le coup.",
    },
    {
        "id": "mythe",
        "famille": "pédagogie",
        "titre": "Non, ce n'est pas vrai — et voilà pourquoi",
        "promesse": "On démonte une idée reçue",
        "hook": "On me répète ça toutes les semaines. C'est faux, et ça fait perdre de l'argent aux gens.",
        "corps": [
            "L'idée vient d'une époque où c'était vrai. Le métier a changé, les matériaux aussi, et la règle ne tient plus.",
            "Concrètement, sur les derniers chantiers que j'ai faits, c'est même l'inverse qui s'est vérifié.",
            "Ce qui compte vraiment, ce n'est pas ce critère-là, c'est la façon dont le travail est préparé en amont.",
        ],
        "chute": "Si on vous a dit le contraire, demandez sur quoi la personne se base. Vous verrez.",
    },
    {
        "id": "prix",
        "famille": "confiance",
        "titre": "Combien ça coûte vraiment, et pourquoi",
        "promesse": "Le prix expliqué, sans détour",
        "hook": "Personne n'ose donner de prix. Moi je vais le faire, et je vais surtout expliquer ce qu'il y a dedans.",
        "corps": [
            "Un devis, ce n'est pas un chiffre : c'est du temps, du matériel, une garantie et un déplacement. Quand on enlève une ligne, on la paiera plus tard.",
            "Les écarts entre deux devis viennent presque toujours de là. Pas de la marge, mais de ce qu'on a décidé de ne pas faire.",
            "Mon conseil : faites-vous détailler le devis ligne par ligne. Un professionnel sérieux le fait volontiers.",
        ],
        "chute": "Envoyez-moi votre devis si vous voulez un avis. Je vous dis ce qui manque, même si ce n'est pas moi qui le fais.",
    },
    {
        "id": "coulisses",
        "famille": "preuve",
        "titre": "Une journée de {metier}, sans filtre",
        "promesse": "Ce que vous ne voyez jamais",
        "hook": "On ne voit jamais cette partie du travail. Pourtant c'est elle qui fait la différence sur le résultat.",
        "corps": [
            "La préparation prend souvent plus de temps que l'intervention elle-même. C'est normal, et c'est même bon signe.",
            "Ce que j'aime le moins ? Le rangement. Ce qui compte le plus ? Le rangement. On ne laisse jamais un chantier sale.",
            "Chaque étape est photographiée. Ce n'est pas pour les réseaux : c'est pour que le client sache exactement ce qui a été fait.",
        ],
        "chute": "Si vous voulez voir ce que ça donne chez vous, dites-moi ce que vous avez comme situation.",
    },
    {
        "id": "avantapres",
        "famille": "preuve",
        "titre": "Avant / après : un cas réel",
        "promesse": "Le résultat, et le chemin pour y arriver",
        "hook": "Regardez l'état de départ. Franchement, on aurait pu croire que c'était fichu.",
        "corps": [
            "Le diagnostic a pris vingt minutes. C'est ce qui a permis de ne pas tout refaire et de diviser la facture.",
            "On a traité la cause, pas le symptôme. C'est la seule façon d'éviter que ça revienne dans six mois.",
            "Résultat livré dans les délais annoncés, avec les photos de chaque étape.",
        ],
        "chute": "Vous avez une situation qui ressemble à ça ? Envoyez-moi une photo, je vous dis ce que j'en pense.",
    },
    {
        "id": "faq",
        "famille": "confiance",
        "titre": "La question qu'on me pose le plus souvent",
        "promesse": "Une réponse claire, une bonne fois",
        "hook": "Cette question revient tellement souvent que je préfère y répondre ici une fois pour toutes.",
        "corps": [
            "La réponse courte tient en une phrase. La réponse honnête demande de savoir dans quelle situation vous êtes.",
            "Dans le cas le plus courant, oui, c'est possible, et ça se règle rapidement.",
            "Dans les cas particuliers, il faut regarder avant de promettre quoi que ce soit. Se tromper là-dessus coûte cher.",
        ],
        "chute": "Posez-moi votre question en commentaire, je réponds à toutes.",
    },
    {
        "id": "checklist",
        "famille": "utile",
        "titre": "{n} choses à vérifier avant de choisir",
        "promesse": "La liste à garder sous la main",
        "hook": "Avant de signer quoi que ce soit, vérifiez ces points. Ça vous évitera les mauvaises surprises.",
        "corps": [
            "L'assurance et la garantie. Demandez l'attestation, elle doit être en cours de validité.",
            "Le devis détaillé. Un prix global sans détail, c'est un prix qui bougera.",
            "Les délais écrits. Un délai annoncé à l'oral n'engage personne.",
            "Les avis récents, pas les plus anciens. Une entreprise change.",
        ],
        "chute": "Gardez cette liste. Elle vous servira même si ce n'est pas avec moi que vous travaillez.",
    },
    {
        "id": "temoignage",
        "famille": "preuve",
        "titre": "Ce qu'un client m'a dit à la fin",
        "promesse": "Un retour client, mot pour mot",
        "hook": "À la fin de l'intervention, il m'a dit une phrase que je n'oublie pas.",
        "corps": [
            "Il s'attendait à des travaux lourds. Le diagnostic a montré qu'on pouvait faire beaucoup plus simple.",
            "Ce qui l'a rassuré, ce n'est pas le prix : c'est d'avoir eu une explication claire avant de décider.",
            "C'est comme ça que je travaille. On explique, on chiffre, et vous décidez sans pression.",
        ],
        "chute": "Si vous voulez ce genre d'échange avant de vous engager, écrivez-moi.",
    },
    {
        "id": "comparatif",
        "famille": "utile",
        "titre": "{a} ou {b} : lequel choisir",
        "promesse": "Le vrai critère de décision",
        "hook": "On me demande souvent lequel des deux est le mieux. La réponse dépend d'un seul critère, et ce n'est pas le prix.",
        "corps": [
            "Le premier est plus rapide à mettre en œuvre, donc moins cher au départ.",
            "Le second dure plus longtemps, mais il demande une préparation qui se paie.",
            "Le bon choix dépend de la durée pendant laquelle vous comptez garder le bien. Au-delà de sept ans, la question ne se pose plus.",
        ],
        "chute": "Dites-moi votre situation, je vous oriente sans vous vendre le plus cher.",
    },
    {
        "id": "saison",
        "famille": "actualité",
        "titre": "Ce qu'il faut faire {saison}",
        "promesse": "Le bon geste au bon moment",
        "hook": "C'est la période où j'ai le plus d'appels pour des choses qu'on aurait pu éviter.",
        "corps": [
            "Le froid et l'humidité révèlent les défauts qui dormaient depuis l'été. Ce n'est pas un hasard.",
            "Un contrôle maintenant coûte le prix d'un plein d'essence. La réparation en urgence, c'est dix fois plus.",
            "Si vous devez ne faire qu'une chose : vérifiez les points d'étanchéité. C'est de là que tout part.",
        ],
        "chute": "Un doute ? Envoyez-moi une photo, je regarde et je vous dis.",
    },
    {
        "id": "metier",
        "famille": "identité",
        "titre": "Pourquoi je fais ce métier",
        "promesse": "Ce qui me tient encore debout à 6 h du matin",
        "hook": "On me demande souvent pourquoi j'ai choisi ce métier. La vraie raison n'est pas celle qu'on croit.",
        "corps": [
            "Ce n'est pas la technique. La technique s'apprend, et elle finit par être acquise.",
            "C'est le moment où le client comprend ce qui se passe chez lui. Il arrête d'avoir peur, il décide.",
            "Un client qui comprend, c'est un client qui ne se fait plus avoir. C'est ça que je vends, en fait.",
        ],
        "chute": "Si vous voulez comprendre ce qui se passe chez vous, c'est gratuit. Écrivez-moi.",
    },
    {
        "id": "urgence",
        "famille": "utile",
        "titre": "Que faire en attendant, en cas d'urgence",
        "promesse": "Les gestes qui limitent la casse",
        "hook": "Si ça vous arrive un dimanche soir, voilà exactement quoi faire en attendant.",
        "corps": [
            "D'abord, coupez. On limite les dégâts avant de chercher à comprendre.",
            "Ensuite, photographiez. Ça servira pour l'assurance, et ça m'aidera à évaluer à distance.",
            "Surtout, ne bricolez pas de réparation provisoire agressive : c'est souvent ce qui transforme une petite panne en gros chantier.",
        ],
        "chute": "Gardez mon numéro. Sur ce genre de cas, un appel de deux minutes évite parfois des semaines d'ennuis.",
    },
    {
        "id": "chiffre",
        "famille": "pédagogie",
        "titre": "Un chiffre qui va vous surprendre",
        "promesse": "Ce que révèlent mes derniers chantiers",
        "hook": "J'ai regardé mes interventions des douze derniers mois. Un chiffre m'a sauté aux yeux.",
        "corps": [
            "La majorité des cas que je traite auraient pu être évités par un contrôle annuel.",
            "Ce n'est pas de la négligence : personne ne sait qu'il faut le faire, parce que personne ne le dit.",
            "Alors je le dis. Un contrôle par an, et vous divisez vos frais par trois sur dix ans.",
        ],
        "chute": "Vous ne savez pas quand ça a été contrôlé chez vous pour la dernière fois ? C'est déjà une réponse.",
    },
    {
        "id": "objection",
        "famille": "confiance",
        "titre": "« C'est trop cher » — parlons-en",
        "promesse": "La réponse honnête à l'objection prix",
        "hook": "On me dit souvent que c'est cher. Je comprends, et je vais vous expliquer d'où vient le prix.",
        "corps": [
            "Je peux baisser le prix. Mais alors il faut enlever quelque chose, et je vous dirai quoi.",
            "Ce qu'on enlève en premier, c'est presque toujours ce qui garantit que ça tienne dans le temps.",
            "Payer deux fois moins cher pour refaire dans trois ans, ce n'est pas une économie. C'est un report.",
        ],
        "chute": "Si le budget est serré, dites-le moi franchement. On trouvera la version qui tient.",
    },
    {
        "id": "materiel",
        "famille": "identité",
        "titre": "L'outil que je ne prête à personne",
        "promesse": "Ce qu'il y a dans le camion",
        "hook": "Il y a un outil dans mon camion que je ne prête jamais. Pas par avarice — vous allez comprendre.",
        "corps": [
            "Il est réglé au millimètre. Une fois déréglé, il faut le renvoyer, et je suis à l'arrêt une semaine.",
            "C'est lui qui fait la différence entre un travail correct et un travail durable.",
            "Le matériel ne fait pas tout, mais un mauvais outil garantit un mauvais résultat.",
        ],
        "chute": "Demandez toujours avec quoi on travaille chez vous. La réponse en dit long.",
    },
]

# Pour varier la formulation des titres sans jamais tomber à plat.
SAISONS = ["en hiver", "au printemps", "en été", "à l'automne", "avant l'hiver", "après les grosses pluies"]
NOMBRES = ["3", "4", "5", "6", "7"]


# ─────────────────────────────────────────────────────────────────────────────
# Le profil : saisi une fois, utilisé partout
# ─────────────────────────────────────────────────────────────────────────────

CHAMPS_PROFIL = [
    ("metier", "Ton métier", "chauffeur VTC", True),
    ("secteur", "Ton secteur", "transport de personnes", True),
    ("ville", "Ta zone", "Paris et Île-de-France", True),
    ("cible", "Tes clients", "des dirigeants et des voyageurs d'affaires", True),
    ("offre", "Ce que tu vends", "des courses réservées à l'avance, à prix fixe", True),
    ("difference", "Ce qui te distingue", "un tarif annoncé d'avance et zéro frais d'attente", False),
    ("ton", "Ton style", "direct", False),
    ("prenom", "Ton prénom à l'écran", "", False),
]

TONS = {
    "direct": "Phrases courtes. On va au fait. Pas d'enrobage.",
    "pedagogue": "On explique, on prend le temps, on donne un exemple.",
    "chaleureux": "On parle comme à un voisin. Simple, humain.",
    "expert": "On appuie sur la technique et sur les chiffres.",
}


# Un metier d'essai, credible et generique : il fait tourner tout l'outil des
# la premiere seconde. `essai: True` le distingue d'un vrai profil, pour que
# l'interface puisse le dire au lieu de le faire passer pour une reponse.
PROFIL_ESSAI = {
    "metier": "artisan",
    "secteur": "renovation de l'habitat",
    "ville": "Paris et sa region",
    "cible": "des proprietaires qui renovent leur maison",
    "offre": "des chantiers suivis du devis a la reception",
    "difference": "un interlocuteur unique et des delais tenus",
    "ton": "direct",
    "prenom": "",
    "essai": True,
}


def profil_defaut():
    """Le metier d'essai, pas un formulaire vide : on doit pouvoir juger
    l'outil avant de remplir sa fiche."""
    p = {c: "" for c, _, _, _ in CHAMPS_PROFIL}
    p.update(PROFIL_ESSAI)
    return p


def profil_complet(p):
    """Un profil est utilisable dès que les champs obligatoires sont là."""
    return all((p or {}).get(c) for c, _, _, obligatoire in CHAMPS_PROFIL if obligatoire)


def _domaine(p):
    return p.get("secteur") or p.get("metier") or "ton métier"


def _article_metier(p):
    """« un chauffeur », « une esthéticienne » : on évite le faux accord."""
    m = (p.get("metier") or "professionnel").strip()
    return m


# ─────────────────────────────────────────────────────────────────────────────
# Composition
# ─────────────────────────────────────────────────────────────────────────────

def _graine(*morceaux):
    brut = "|".join(str(m) for m in morceaux)
    return int(hashlib.sha256(brut.encode("utf-8")).hexdigest()[:12], 16)


def _remplir(gabarit, p, rnd):
    """Substitue les repères du gabarit par le profil, sans laisser de trou."""
    val = {
        "metier": _article_metier(p),
        "domaine": _domaine(p),
        "cible": p.get("cible") or "mes clients",
        "ville": p.get("ville") or "ma zone",
        "offre": p.get("offre") or "ce que je propose",
        "difference": p.get("difference") or "",
        "saison": rnd.choice(SAISONS),
        "n": rnd.choice(NOMBRES),
        "faux": "ce qu'on vous a raconté sur " + _domaine(p) + " n'est pas vrai",
        "a": "la solution rapide",
        "b": "la solution durable",
    }
    out = gabarit
    for cle, v in val.items():
        out = out.replace("{" + cle + "}", v)
    return re.sub(r"\{[a-z_]+\}", "", out).strip()


def _accroche_reseau(reseau, angle, p, rnd):
    """Chaque réseau a sa façon d'entrer dans le sujet."""
    base = angle["hook"]
    if reseau == "tiktok":
        return base
    if reseau == "linkedin":
        return base.replace("Il y a une erreur", "Il y a une erreur")  # ton déjà sobre
    if reseau == "youtube":
        return base + " Restez jusqu'à la fin, la dernière partie est celle qui compte."
    if reseau == "facebook":
        return base
    return base


VIDES = {"avec", "sans", "dans", "pour", "chez", "leur", "leurs", "elle", "elles",
         "cette", "votre", "notre", "plus", "tout", "tous", "toute", "toutes",
         "mais", "donc", "aussi", "meme", "entre", "vers", "sous", "personnes"}


def _hashtags(reseau, p):
    mots = []
    for source in (p.get("metier"), p.get("secteur"), p.get("ville")):
        for mot in re.split(r"[\s,/']+", (source or "")):
            mot = _sans_accent(mot).lower()
            mot = re.sub(r"[^a-z0-9]", "", mot)
            if len(mot) >= 4 and mot not in mots and mot not in VIDES:
                mots.append(mot)
    generiques = ["conseil", "artisan", "avantapres", "astuce", "pro", "france", "metier", "qualite"]
    for g in generiques:
        if g not in mots:
            mots.append(g)
    n = RESEAUX[reseau]["hashtags"]
    return ["#" + m for m in mots[:n]]


def _sans_accent(t):
    return "".join(c for c in unicodedata.normalize("NFD", t or "")
                   if unicodedata.category(c) != "Mn")


def _mots(t):
    return len([m for m in re.split(r"\s+", t.strip()) if m])


def _secondes(texte):
    """À l'oral, on tient environ 150 mots par minute en parlant clairement."""
    return round(_mots(texte) / 150.0 * 60)


# ─────────────────────────────────────────────────────────────────────────────
# Les trois modes de sortie
# ─────────────────────────────────────────────────────────────────────────────

def script_parle(reseau, angle, p, rnd, vedette=False):
    """Un script à dire face caméra, découpé en blocs pour le prompteur.

    En mode vedette (le post du vendredi), on garde tous les points, on ajoute
    une preuve chiffrée et une clôture qui donne envie de revenir.
    """
    r = RESEAUX[reseau]
    cible_bas, cible_haut = r["duree"]
    if vedette:
        # On s'autorise la moitie en plus : c'est le rendez-vous de la semaine.
        cible_bas, cible_haut = cible_bas, int(cible_haut * 1.5)

    blocs = []
    hook = _remplir(_accroche_reseau(reseau, angle, p, rnd), p, rnd)
    blocs.append({"role": "Accroche", "texte": hook,
                  "note": "Regard caméra, pas de bonjour, pas de présentation."})

    promesse = _remplir(angle["promesse"], p, rnd)
    intro = "Je suis %s%s, et aujourd'hui je vous montre %s." % (
        p.get("prenom") or "", (", " + _article_metier(p)) if p.get("prenom") else _article_metier(p),
        promesse[0].lower() + promesse[1:] if promesse else "ce qu'il faut savoir")
    intro = re.sub(r"^Je suis , ", "Je suis ", intro)
    blocs.append({"role": "Contexte", "texte": _remplir(intro, p, rnd),
                  "note": "Une phrase, pas plus. On enchaîne."})

    corps = list(angle["corps"])
    rnd.shuffle(corps)
    garder = len(corps) if vedette else (3 if cible_haut > 60 else 2)
    for i, c in enumerate(corps[:garder], 1):
        blocs.append({"role": "Point %d" % i, "texte": _remplir(c, p, rnd),
                      "note": "Marque un temps d'arrêt avant de passer au suivant."})

    if p.get("difference"):
        blocs.append({"role": "Preuve",
                      "texte": _remplir("Chez moi, %s. C'est écrit, pas promis." % p["difference"], p, rnd),
                      "note": "C'est le moment où tu te distingues. Dis-le calmement."})

    if vedette:
        blocs.append({
            "role": "Preuve chiffrée",
            "texte": _remplir("Sur les douze derniers mois, la moitié des demandes que je reçois "
                              "auraient pu être réglées deux fois moins cher si on m'avait appelé "
                              "plus tôt. Ce n'est pas une formule commerciale, c'est ce que je "
                              "constate chantier après chantier.", p, rnd),
            "note": "Le moment où tu passes de « je raconte » à « je prouve ». Ralentis le débit."})
        blocs.append({
            "role": "Rendez-vous",
            "texte": _remplir("Je publie ce genre de retour tous les vendredis. Si ça vous est "
                              "utile, abonnez-vous, vous aurez le prochain.", p, rnd),
            "note": "C'est ce bloc qui construit ton audience. Ne le saute jamais."})

    zone = p.get("ville") or ""
    cta = _remplir(angle["chute"], p, rnd)
    if zone:
        cta += " J'interviens sur %s." % zone
    blocs.append({"role": "Appel", "texte": cta,
                  "note": "Une seule action demandée. Jamais deux."})

    # On rentre dans la fenetre du reseau : tant qu'on deborde, on retire le
    # point le moins porteur (jamais l'accroche ni l'appel).
    def duree(bs):
        return sum(_secondes(b["texte"]) for b in bs)

    while duree(blocs) > cible_haut:
        retirables = [i for i, b in enumerate(blocs) if b["role"].startswith("Point")]
        if vedette and len(retirables) <= 2:
            break
        if not retirables:
            break
        blocs.pop(retirables[-1])

    total = duree(blocs)
    return {
        "blocs": blocs,
        "duree_estimee": total,
        "cible": [cible_bas, cible_haut],
        "texte": "\n\n".join(b["texte"] for b in blocs),
    }


def carrousel(reseau, angle, p, rnd):
    """Des planches courtes : une idée par planche, jamais deux."""
    planches = []
    titre = _remplir(angle["titre"], p, rnd)
    planches.append({"n": 1, "type": "couverture", "titre": titre,
                     "texte": _remplir(angle["promesse"], p, rnd),
                     "note": "Gros texte, peu de mots. C'est cette planche qui fait le clic."})

    corps = list(angle["corps"])
    rnd.shuffle(corps)
    for i, c in enumerate(corps, 2):
        phrase = _remplir(c, p, rnd)
        # On coupe à la première phrase : une planche ne supporte pas un paragraphe.
        court = re.split(r"(?<=[.!?])\s+", phrase)[0]
        reste = phrase[len(court):].strip()
        planches.append({"n": i, "type": "point",
                         "titre": "%d." % (i - 1), "texte": court,
                         "detail": reste, "note": "Une idée, une planche."})

    if p.get("difference"):
        planches.append({"n": len(planches) + 1, "type": "preuve",
                         "titre": "Chez moi", "texte": p["difference"].capitalize() + ".",
                         "note": "Ta différence, en une ligne."})

    planches.append({"n": len(planches) + 1, "type": "appel",
                     "titre": "On en parle ?",
                     "texte": _remplir(angle["chute"], p, rnd),
                     "note": "Dis exactement quoi faire : commenter, écrire, appeler."})
    return {"planches": planches, "titre": titre}


def anime(reseau, angle, p, rnd):
    """Des répliques courtes, calées pour l'animation Remotion.

    Le moteur d'animation affiche les mots au fil de la voix : il lui faut des
    phrases brèves et une intention par phrase.
    """
    src = script_parle(reseau, angle, p, rnd)
    repliques = []
    for b in src["blocs"]:
        for phrase in re.split(r"(?<=[.!?])\s+", b["texte"]):
            phrase = phrase.strip()
            if not phrase:
                continue
            repliques.append({
                "texte": phrase,
                "role": b["role"],
                "secondes": max(1.4, _secondes(phrase)),
                "accent": b["role"] in ("Accroche", "Appel", "Preuve"),
            })
    return {
        "repliques": repliques,
        "duree_estimee": round(sum(r["secondes"] for r in repliques)),
        "titre": _remplir(angle["titre"], p, rnd),
    }


def legende(reseau, angle, p, rnd, script):
    """La légende du post, calibrée pour le réseau."""
    r = RESEAUX[reseau]
    titre = _remplir(angle["titre"], p, rnd)
    ouverture = _remplir(angle["hook"], p, rnd)

    if reseau == "linkedin":
        corps = "\n\n".join(_remplir(c, p, rnd) for c in angle["corps"][:3])
        texte = "%s\n\n%s\n\n%s" % (ouverture, corps, _remplir(angle["chute"], p, rnd))
    elif reseau == "youtube":
        points = "\n".join("• " + _remplir(c, p, rnd).split(".")[0] + "."
                           for c in angle["corps"][:3])
        texte = "%s\n\n%s\n\nAu programme :\n%s\n\n%s" % (
            titre, ouverture, points, _remplir(angle["chute"], p, rnd))
    else:
        texte = "%s\n\n%s" % (ouverture, _remplir(angle["chute"], p, rnd))

    tags = _hashtags(reseau, p)
    texte = texte.strip()
    if tags:
        texte += "\n\n" + " ".join(tags)
    if len(texte) > r["legende"]:
        texte = texte[: r["legende"] - 1].rsplit(" ", 1)[0] + "…"
    return texte


# ─────────────────────────────────────────────────────────────────────────────
# Point d'entrée
# ─────────────────────────────────────────────────────────────────────────────

def idees(p, reseau, combien=6, decalage=0):
    """Une liste d'angles proposés pour ce réseau, stable d'un appel à l'autre."""
    rnd = random.Random(_graine(p.get("metier"), p.get("secteur"), reseau, "idees"))
    ordre = list(range(len(ANGLES)))
    rnd.shuffle(ordre)
    out = []
    for k in range(combien):
        i = ordre[(decalage + k) % len(ordre)]
        a = ANGLES[i]
        r2 = random.Random(_graine(p.get("metier"), reseau, a["id"]))
        out.append({
            "angle": a["id"],
            "famille": a["famille"],
            "titre": _remplir(a["titre"], p, r2),
            "promesse": _remplir(a["promesse"], p, r2),
        })
    return out


def composer(p, reseau, angle_id, mode, vedette=False):
    """Produit le contenu complet pour un angle, un réseau et un mode donnés."""
    a = next((x for x in ANGLES if x["id"] == angle_id), None)
    if a is None:
        raise ValueError("angle inconnu : %s" % angle_id)
    if reseau not in RESEAUX:
        raise ValueError("réseau inconnu : %s" % reseau)

    rnd = random.Random(_graine(p.get("metier"), p.get("secteur"), reseau, angle_id, mode))
    r = RESEAUX[reseau]

    sortie = {
        "reseau": reseau,
        "reseau_nom": r["nom"],
        "angle": angle_id,
        "mode": mode,
        "titre": _remplir(a["titre"], p, rnd),
        "conseil": r["conseil"],
        "format": r["format"],
        "vedette": bool(vedette),
    }

    if mode == "parle":
        sortie["script"] = script_parle(reseau, a, p, rnd, vedette)
    elif mode == "carrousel":
        sortie["carrousel"] = carrousel(reseau, a, p, rnd)
    elif mode == "anime":
        sortie["anime"] = anime(reseau, a, p, rnd)
    else:
        raise ValueError("mode inconnu : %s" % mode)

    sortie["legende"] = legende(reseau, a, p, rnd,
                                sortie.get("script") or sortie.get("anime") or {})
    return sortie


# Le vendredi, les gens preparent leur week-end et lisent plus longuement.
# On y place la piece maitresse : un angle qui installe la confiance, en
# format parle, avec une preuve chiffree et une cloture plus travaillee.
ANGLES_VEDETTE = ["avantapres", "temoignage", "prix", "objection", "coulisses", "metier"]


# ─────────────────────────────────────────────────────────────────────────────
# La cadence : combien par semaine, et a quelle heure
# ─────────────────────────────────────────────────────────────────────────────
# « parle » = toi face camera. « carrousel » = des planches qui expliquent.
# « anime » = ton avatar, ou du texte anime, quand tu n'es pas devant l'objectif.
JOURS_NOM = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")

CADENCE = {
    "youtube": {
        "total": 3,
        "phrase": "Une vidéo qui installe ton autorité, plus deux formats courts "
                  "pour rester visible entre deux.",
        "roi": "parle",
        "formats": {
            "parle": (1, "Ta pièce maîtresse. Une vidéo bien faite se regarde encore "
                          "dans deux ans et travaille pour toi tout ce temps."),
            "anime": (2, "Des Shorts. Ils vont chercher ceux qui ne te connaissent pas "
                          "et les ramènent vers la vidéo longue."),
            "carrousel": (0, "YouTube n'a pas de carrousel. Recycle-le en miniature "
                              "ou en post Communauté."),
        },
        "jours": (1, 3),          # mardi, jeudi
        "heures": ("18:00", "19:30"),
        "note": "Le soir en semaine : les gens regardent une vidéo longue quand ils "
                "ont fini leur journée, pas à midi entre deux chantiers.",
    },
    "instagram": {
        "total": 5,
        "phrase": "Le carrousel fait le travail de fond, les vidéos courtes vont "
                  "chercher du monde.",
        "roi": "carrousel",
        "formats": {
            "carrousel": (3, "Le format qu'on enregistre et qu'on renvoie à un proche. "
                              "C'est lui qui te fait connaître pour de bon."),
            "anime": (2, "Des Reels. La portée y est encore généreuse : c'est ta "
                          "meilleure chance d'être vu par des inconnus."),
            "parle": (1, "Un face caméra de temps en temps : on achète à une tête, "
                          "pas à un logo."),
        },
        "jours": (0, 2, 4),
        "heures": ("12:30", "18:30"),
        "note": "Midi et début de soirée. Évite le week-end : tes clients sont "
                "en famille, pas en train de chercher un artisan.",
    },
    "tiktok": {
        "total": 5,
        "phrase": "C'est le réseau du volume : ici on publie souvent, court, "
                  "et on accepte que tout ne marche pas.",
        "roi": "anime",
        "formats": {
            "anime": (3, "Court, rythmé, sous-titré. Une vidéo sur cinq décolle : "
                          "c'est normal, et c'est pour ça qu'il en faut cinq."),
            "parle": (2, "Ton visage et ta voix, sans montage. C'est ce qui crée "
                          "l'attachement — le reste ne fait que la portée."),
            "carrousel": (1, "Le carrousel photo marche aussi ici, moins fort qu'ailleurs."),
        },
        "jours": (0, 1, 2, 3, 4),
        "heures": ("12:00", "19:00", "21:00"),
        "note": "Le soir tard fonctionne bien. Publie à heure régulière : "
                "l'algorithme te teste sur ton audience habituelle en premier.",
    },
    "linkedin": {
        "total": 3,
        "phrase": "Peu, mais soigné. On y vient pour apprendre, pas pour se distraire.",
        "roi": "carrousel",
        "formats": {
            "carrousel": (2, "Le document PDF est le format le plus poussé par LinkedIn. "
                              "C'est là que tu montres que tu sais de quoi tu parles."),
            "parle": (1, "Une vidéo courte, sous-titrée : elle se regarde sans le son, "
                          "au bureau."),
            "anime": (0, "Peu adapté : le ton y est plus sobre qu'ailleurs."),
        },
        "jours": (1, 3),
        "heures": ("08:00", "12:00"),
        "note": "Le matin en semaine, avant la réunion. Mardi et jeudi sont les "
                "deux meilleurs jours ; le week-end, personne.",
    },
    "facebook": {
        "total": 3,
        "phrase": "Le réseau de la proximité : c'est là que le bouche-à-oreille "
                  "de ta ville se fait.",
        "roi": "parle",
        "formats": {
            "parle": (2, "Parle comme à un voisin. Le local marche très fort ici, "
                          "surtout dans les groupes de ta commune."),
            "carrousel": (1, "Un avant/après en plusieurs images : c'est le contenu "
                              "le plus partagé sur Facebook."),
            "anime": (0, "Possible, mais le face caméra rend mieux auprès de "
                          "cette audience."),
        },
        "jours": (2, 5),
        "heures": ("12:30", "19:30"),
        "note": "Midi et après le dîner. Le samedi matin fonctionne aussi : "
                "c'est le moment où l'on pense à ses travaux.",
    },
}

# Ce qu'il faut avoir compris avant de produire quoi que ce soit.
LES_TROIS = (
    {"mode": "parle", "titre": "Le script", "sous": "Une vidéo, quelle qu'elle soit",
     "quoi": "Un texte écrit pour être dit : accroche, points, conclusion. Il sert "
             "au face caméra comme au témoignage d'un client, à un avant/après ou "
             "à un format court. Le prompteur le déroule pendant le tournage.",
     "quand": "Dès qu'il y a une vidéo à tourner. Le type de vidéo se choisit "
              "juste après.",
     "cout": "20 minutes et un téléphone."},
    {"mode": "carrousel", "titre": "Le carrousel", "sous": "Tu expliques",
     "quoi": "Des planches qu'on fait glisser, une idée par planche, et la dernière "
             "qui invite à te contacter.",
     "quand": "Quand tu veux enseigner quelque chose. C'est le format qu'on "
              "enregistre et qu'on renvoie à un proche.",
     "cout": "Aucun tournage. Tu relis, tu publies."},
    {"mode": "anime", "titre": "L'avatar", "sous": "Sans toi devant l'objectif",
     "quoi": "Une photo de ton visage qui dit ton texte, lèvres synchronisées. "
             "Ou, sans photo, un texte animé lu par une voix.",
     "quand": "Quand tu n'as pas le temps de tourner, ou pas envie. Tu gardes "
              "le rythme les semaines chargées.",
     "cout": "Une photo, deux minutes de fabrication."},
)


def cadence(reseau):
    """Le rythme conseille sur ce reseau, pret a afficher."""
    c = CADENCE.get(reseau) or {}
    if not c:
        return {}
    formats = []
    for mode in ("parle", "carrousel", "anime"):
        n, pourquoi = c["formats"].get(mode, (0, ""))
        formats.append({"mode": mode, "par_semaine": n, "pourquoi": pourquoi,
                        "roi": mode == c.get("roi")})
    return {"total": c["total"], "phrase": c["phrase"], "formats": formats,
            "jours": [JOURS_NOM[j] for j in c["jours"]], "heures": list(c["heures"]),
            "note": c["note"]}


def creneaux(reseau, depuis=None, combien=6):
    """Les prochains rendez-vous conseilles sur ce reseau : (date, heure)."""
    import datetime as _dt
    c = CADENCE.get(reseau)
    if not c:
        return []
    debut = depuis or _dt.datetime.now()
    sortie = []
    for j in range(21):
        jour = (debut + _dt.timedelta(days=j)).date()
        if jour.weekday() not in c["jours"]:
            continue
        for h in c["heures"]:
            quand = _dt.datetime.combine(jour, _dt.time.fromisoformat(h))
            # Un creneau deja passe n'est pas un rendez-vous : on l'ignore.
            if quand <= debut:
                continue
            sortie.append(quand.isoformat(timespec="minutes"))
            if len(sortie) >= combien:
                return sortie
    return sortie


def plan_semaine(p, jours=7, par_jour=1, depart=0, debut=None):
    """Le plan de la direction commerciale : qui poste quoi, quel jour.

    On alterne les réseaux et les familles d'angles pour ne pas servir deux
    fois la même chose à la même audience dans la semaine.
    """
    import datetime as _dt
    debut = debut or _dt.date.today()
    rnd = random.Random(_graine(p.get("metier"), "plan", depart))
    ordre = list(range(len(ANGLES)))
    rnd.shuffle(ordre)
    modes = ["parle", "carrousel", "anime"]
    plan = []
    k = 0
    for j in range(jours):
        jour_date = debut + _dt.timedelta(days=j)
        vendredi = jour_date.weekday() == 4
        for c in range(par_jour):
            reseau = ORDRE_RESEAUX[(j * par_jour + c + depart) % len(ORDRE_RESEAUX)]
            if vendredi and c == 0:
                # La piece maitresse : un angle de confiance, dit face camera.
                rv = random.Random(_graine(p.get("metier"), "vedette", jour_date.isoformat()))
                aid = rv.choice(ANGLES_VEDETTE)
                a = next(x for x in ANGLES if x["id"] == aid)
                mode = "parle"
            else:
                a = ANGLES[ordre[(k + depart) % len(ordre)]]
                mode = modes[(j + c) % len(modes)]
            r2 = random.Random(_graine(p.get("metier"), reseau, a["id"]))
            plan.append({
                "jour": j,
                "date": jour_date.isoformat(),
                "reseau": reseau,
                "reseau_nom": RESEAUX[reseau]["nom"],
                "agent": RESEAUX[reseau]["agent"],
                "angle": a["id"],
                "mode": mode,
                "vedette": bool(vendredi and c == 0),
                "titre": _remplir(a["titre"], p, r2),
            })
            k += 1
    return plan
