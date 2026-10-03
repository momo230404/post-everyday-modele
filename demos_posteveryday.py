#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Post Everyday — cinq comptes de démonstration, plannings remplis.

Un compte de démo doit se regarder comme un compte qui tourne depuis deux mois :
des publications déjà parties derrière soi, un mois calé devant, et une petite
réserve. Les contenus ne sont pas écrits à la main — ils sortent du générateur
maison (`moteur.composer`), avec le profil du client fictif : c'est ce que le
vrai outil produirait, pas une maquette.

    python3 demos_posteveryday.py            # crée ou recrée les cinq comptes
"""
import json
import os
import sqlite3
import sys
from datetime import datetime, time as heure_du_jour, timedelta

ICI = "/docker/posteveryday"
sys.path.insert(0, ICI)
os.chdir(ICI)

import app as A            # noqa: E402  (creer_tables, ecrire_config)
import moteur as M         # noqa: E402
from werkzeug.security import generate_password_hash   # noqa: E402

REGISTRE = os.path.join(ICI, "espaces.json")
CONFIG = os.path.join(ICI, "config.json")
MDP_DEMO = "Demo2026!"

# Le catalogue d'angles de l'outil a un accent d'artisan : « envoyez-moi une
# photo », « un outil dans mon camion », « le froid révèle les défauts ». Vrai
# pour le BTP et la menuiserie, faux pour une agence ou un cabinet de
# recrutement — et une démo qui sonne faux ne vend rien. D'où un jeu d'angles
# transversaux pour les métiers de service.
ANGLES_TRANSVERSAUX = ["erreur", "mythe", "prix", "coulisses", "faq", "checklist",
                       "temoignage", "comparatif", "metier", "objection"]
# L'imprimeur travaille en atelier : pas de camion, pas de dépannage du dimanche.
ANGLES_ATELIER = ANGLES_TRANSVERSAUX + ["avantapres", "chiffre"]

# ── Les cinq entreprises ────────────────────────────────────────────────────
DEMOS = [
    {
        "id": "demo-imprimerie", "nom": "Démo · Imprimerie Marchand",
        "identifiant": "demo-imprimerie", "angles": ANGLES_ATELIER,
        "profil": {
            "metier": "imprimeur",
            "secteur": "impression grand format et signalétique",
            "ville": "Lyon et le Rhône",
            "cible": "des commerçants, des agences et des collectivités",
            "offre": "des affiches, enseignes et vitrophanies imprimées et posées en 48 h",
            "difference": "un atelier intégré : on imprime nous-mêmes, donc on tient les délais",
            "ton": "direct", "prenom": "Julien",
        },
        "abonnes": {"youtube": 780, "instagram": 3120, "tiktok": 1450,
                    "linkedin": 1980, "facebook": 2440},
    },
    {
        "id": "demo-menuiserie", "nom": "Démo · Menuiserie Delaunay",
        "identifiant": "demo-menuiserie",
        "profil": {
            "metier": "menuisier",
            "secteur": "menuiserie bois sur mesure",
            "ville": "Nantes et la Loire-Atlantique",
            "cible": "des propriétaires qui rénovent leur maison",
            "offre": "des cuisines, escaliers et dressings dessinés, fabriqués et posés",
            "difference": "on dessine, on fabrique à l'atelier et on pose : un seul interlocuteur",
            "ton": "chaleureux", "prenom": "Marc",
        },
        "abonnes": {"youtube": 1240, "instagram": 5860, "tiktok": 4300,
                    "linkedin": 640, "facebook": 3110},
    },
    {
        "id": "demo-marketing", "nom": "Démo · Studio Nova (agence marketing)",
        "identifiant": "demo-marketing", "angles": ANGLES_TRANSVERSAUX,
        "profil": {
            "metier": "consultant en marketing",
            "secteur": "acquisition et contenu pour les PME",
            "ville": "Bordeaux et la Gironde",
            "cible": "des dirigeants de PME qui veulent des demandes entrantes",
            "offre": "un système d'acquisition complet : contenu, publicité et suivi des demandes",
            "difference": "on est jugés sur les rendez-vous obtenus, pas sur les impressions",
            "ton": "expert", "prenom": "Sarah",
        },
        "abonnes": {"youtube": 2100, "instagram": 4470, "tiktok": 2680,
                    "linkedin": 8900, "facebook": 1520},
    },
    {
        "id": "demo-btp", "nom": "Démo · Rocher & Fils (BTP)",
        "identifiant": "demo-btp",
        "profil": {
            "metier": "entrepreneur du bâtiment",
            "secteur": "gros œuvre et rénovation",
            "ville": "Marseille et les Bouches-du-Rhône",
            "cible": "des particuliers et des syndics de copropriété",
            "offre": "des chantiers suivis du devis à la réception, avec un conducteur de travaux dédié",
            "difference": "des délais tenus et un compte rendu de chantier chaque semaine",
            "ton": "direct", "prenom": "Karim",
        },
        "abonnes": {"youtube": 940, "instagram": 3980, "tiktok": 5240,
                    "linkedin": 1120, "facebook": 4650},
    },
    {
        "id": "demo-recrutement", "nom": "Démo · Cap Talents (recrutement)",
        "identifiant": "demo-recrutement", "angles": ANGLES_TRANSVERSAUX,
        "profil": {
            "metier": "recruteur",
            "secteur": "recrutement et chasse de profils",
            "ville": "Lille et les Hauts-de-France",
            "cible": "des PME industrielles qui peinent à recruter",
            "offre": "des recrutements garantis, du sourcing à l'intégration",
            "difference": "trois profils qualifiés présentés en dix jours, ou on ne facture pas",
            "ton": "chaleureux", "prenom": "Élodie",
        },
        "abonnes": {"youtube": 520, "instagram": 2240, "tiktok": 1870,
                    "linkedin": 12400, "facebook": 980},
    },
]

SEMAINES_PASSEES = 4      # ce qui est déjà parti
SEMAINES_A_VENIR = 3      # ce qui est calé devant
BROUILLONS = 8            # la réserve, pour que « y poser un post » ait de quoi


# ── Le calendrier ───────────────────────────────────────────────────────────
def creneaux(reseau, lundi):
    """Les rendez-vous de la semaine pour ce réseau, tirés de la cadence
    conseillée par l'outil lui-même : mêmes jours, mêmes heures que ce que
    l'agenda propose en pointillé. Un compte de démo doit être l'exemple du
    conseil, pas une exception."""
    cad = M.CADENCE[reseau]
    jours, heures, total = cad["jours"], cad["heures"], cad["total"]
    vus, out, i = set(), [], 0
    while len(out) < total and i < 60:
        j = jours[i % len(jours)]
        h = heures[(i // len(jours)) % len(heures)]
        quand = datetime.combine(lundi + timedelta(days=j),
                                 heure_du_jour.fromisoformat(h))
        if quand not in vus:
            vus.add(quand)
            out.append(quand)
        i += 1
    return sorted(out)


def modes_semaine(reseau):
    """La répartition des formats conseillée, ramenée au nombre de posts."""
    cad = M.CADENCE[reseau]
    liste = []
    for mode, (n, _) in sorted(cad["formats"].items(), key=lambda kv: -kv[1][0]):
        liste += [mode] * n
    total = cad["total"]
    while len(liste) < total:
        liste.append(cad["roi"])
    return liste[:total]


def lundi_de(d):
    return (d - timedelta(days=d.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0)


# ── Remplissage d'un compte ─────────────────────────────────────────────────
def remplir(demo):
    chemin = os.path.join(ICI, "pe_%s.db" % demo["id"])
    if os.path.exists(chemin):
        os.rename(chemin, chemin + ".remplace_%d" % int(datetime.now().timestamp()))
    c = sqlite3.connect(chemin, timeout=20)
    c.row_factory = sqlite3.Row
    A.creer_tables(c)

    profil = dict(demo["profil"])
    with c:
        for cle, val in profil.items():
            c.execute("INSERT INTO profil(cle,valeur) VALUES(?,?) "
                      "ON CONFLICT(cle) DO UPDATE SET valeur=excluded.valeur", (cle, val))

    maintenant = datetime.now()
    depart = lundi_de(maintenant) - timedelta(weeks=SEMAINES_PASSEES)
    angles = demo.get("angles") or [a["id"] for a in M.ANGLES]
    # Chaque compte démarre sur un autre angle : deux démos ouvertes côte à
    # côte ne doivent pas raconter la même histoire.
    curseur = DEMOS.index(demo) * 3
    n_publies = n_prevus = 0

    for k in range(SEMAINES_PASSEES + SEMAINES_A_VENIR):
        lundi = depart + timedelta(weeks=k)
        for reseau in M.ORDRE_RESEAUX:
            for quand, mode in zip(creneaux(reseau, lundi), modes_semaine(reseau)):
                angle = angles[curseur % len(angles)]
                curseur += 1
                vedette = (mode == "parle" and reseau in ("youtube", "linkedin"))
                r = M.composer(profil, reseau, angle, mode, vedette)
                passe = quand < maintenant
                with c:
                    c.execute(
                        "INSERT INTO posts(reseau,angle,mode,titre,legende,contenu,"
                        "etat,prevu_le,publie_le,cree_le) VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (reseau, angle, mode, r.get("titre"), r.get("legende"),
                         json.dumps(r, ensure_ascii=False),
                         "publie" if passe else "prevu",
                         quand.isoformat(timespec="seconds"),
                         quand.isoformat(timespec="seconds") if passe else None,
                         (quand - timedelta(days=3)).isoformat(timespec="seconds")))
                if passe:
                    n_publies += 1
                else:
                    n_prevus += 1

    # La réserve : des posts prêts, pas encore datés. C'est ce qu'on pose sur
    # un créneau libre pendant la démonstration.
    for i in range(BROUILLONS):
        reseau = M.ORDRE_RESEAUX[i % len(M.ORDRE_RESEAUX)]
        mode = modes_semaine(reseau)[i % len(modes_semaine(reseau))]
        angle = angles[curseur % len(angles)]
        curseur += 1
        r = M.composer(profil, reseau, angle, mode, False)
        with c:
            c.execute(
                "INSERT INTO posts(reseau,angle,mode,titre,legende,contenu,etat,cree_le)"
                " VALUES(?,?,?,?,?,?,'brouillon',?)",
                (reseau, angle, mode, r.get("titre"), r.get("legende"),
                 json.dumps(r, ensure_ascii=False),
                 (maintenant - timedelta(days=i)).isoformat(timespec="seconds")))

    # Quatre relevés d'abonnés par réseau : une courbe qui monte se lit mieux
    # qu'un chiffre isolé.
    with c:
        for reseau, fin in demo["abonnes"].items():
            for m in range(4):
                jour = (maintenant - timedelta(days=30 * (3 - m))).strftime("%Y-%m-%d")
                nombre = int(fin * (0.72 + 0.28 * m / 3.0))
                c.execute("INSERT INTO abonnes(plateforme,nombre,source,releve_le) "
                          "VALUES(?,?,'saisie',?)", (reseau, nombre, jour))
    c.close()
    return n_publies, n_prevus


# ── Registre des comptes et identifiants ───────────────────────────────────
def main():
    liste = json.load(open(REGISTRE, encoding="utf-8"))
    connus = {e["id"]: e for e in liste}
    cfg = json.load(open(CONFIG, encoding="utf-8"))
    comptes = cfg.get("comptes") or []

    # Le mot de passe de l'administrateur, s'il est fourni au lancement :
    #   PE_ADMIN=moi PE_ADMIN_MDP='…' python demos_posteveryday.py
    #
    # ⚠️ Il était écrit EN CLAIR ici, avec l'identifiant « mohamed » en dur.
    # Un mot de passe personnel n'a rien à faire dans un dépôt — à plus forte
    # raison dans un dépôt que l'on partage. Il vient maintenant de
    # l'environnement, et rien ne se passe si on ne le donne pas.
    admin = os.environ.get("PE_ADMIN", "").strip()
    admin_mdp = os.environ.get("PE_ADMIN_MDP", "")
    if admin and admin_mdp:
        for cpt in comptes:
            if cpt.get("identifiant") == admin:
                cpt["mdp_hash"] = generate_password_hash(admin_mdp)
                print("mot de passe de « %s » (admin) mis à jour" % admin)

    for demo in DEMOS:
        pub, prev = remplir(demo)
        e = {"id": demo["id"], "nom": demo["nom"],
             "fichier": "pe_%s.db" % demo["id"],
             "cree_le": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
        if demo["id"] in connus:
            connus[demo["id"]].update(e)
        else:
            liste.append(e)
            connus[demo["id"]] = e
        # Un identifiant par démo : enfermé dans son espace, il montre au
        # client exactement ce qu'il verrait chez lui.
        cpt = next((x for x in comptes if x.get("identifiant") == demo["identifiant"]), None)
        if cpt is None:
            cpt = {"identifiant": demo["identifiant"]}
            comptes.append(cpt)
        cpt.update({"nom": demo["nom"], "role": "membre", "espace": demo["id"],
                    "mdp_hash": generate_password_hash(MDP_DEMO)})
        print("%-18s %3d publiés · %3d calés · %d en réserve"
              % (demo["id"], pub, prev, BROUILLONS))

    horodatage = int(datetime.now().timestamp())
    import shutil
    shutil.copy2(REGISTRE, REGISTRE + ".bak_demos_%d" % horodatage)
    shutil.copy2(CONFIG, CONFIG + ".bak_demos_%d" % horodatage)
    json.dump(liste, open(REGISTRE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    cfg["comptes"] = comptes
    A.ecrire_config(cfg)
    print("espaces.json et config.json écrits (sauvegardes .bak_demos_%d)" % horodatage)


if __name__ == "__main__":
    main()
