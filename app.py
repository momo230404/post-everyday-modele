# -*- coding: utf-8 -*-
"""Post Everyday — le serveur.

Un service par métier : le profil est saisi une fois, les six agents s'en
servent ensuite pour proposer, écrire, monter et publier.

Ce fichier ne contient que la plomberie : l'écriture est dans moteur.py,
l'interface dans index.html.
"""

import json
import hmac
import logging
import fcntl
import tempfile
import os
import re
import secrets
import sqlite3
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta

from flask import (Flask, has_request_context, jsonify, redirect,
                   render_template_string, request, send_from_directory, session, abort)

import depots
import moteur as M

ICI = os.path.dirname(os.path.abspath(__file__))
# Ou vivent les donnees. A cote du code par defaut, donc rien ne change en
# local. Sur un hebergeur, PE_DONNEES pointe sur le disque persistant : sans
# lui, les bases, les medias, les comptes et les jetons OAuth repartiraient
# de zero a chaque deploiement.
DONNEES = os.environ.get("PE_DONNEES") or ICI
os.makedirs(DONNEES, exist_ok=True)
BASE_DONNEES = os.path.join(DONNEES, "pe.db")
MEDIAS = os.path.join(DONNEES, "medias")
CONFIG = os.path.join(DONNEES, "config.json")
BASE_PUB = os.environ.get("PE_BASE", "http://127.0.0.1:5010")
# ⚠️ PE_BASE est l'adresse PUBLIQUE du service : c'est elle que les cinq
# reseaux utilisent pour vous renvoyer apres une connexion. En production,
# elle DOIT etre renseignee (voir le service systemd), sinon les retours
# OAuth echouent. En local, 127.0.0.1 est le bon defaut.

# L'API du studio tourne deja sur cette machine : elle sait transcrire au mot
# pres (Whisper) et lancer un rendu Remotion. On ne reecrit pas ce travail.
STUDIO_API = "http://127.0.0.1:5005"

os.makedirs(MEDIAS, exist_ok=True)
app = Flask(__name__, static_folder=None)

IFRAME_ORIGINS = os.environ.get(
    "PE_IFRAME_ORIGINS",
    "https://www.gopresta.fr https://gopresta.fr https://automatisation-drab.vercel.app",
)

@app.after_request
def autoriser_iframe(response):
    # Autoriser l'intégration en iframe depuis GO PRESTA (et soi-même)
    response.headers["Content-Security-Policy"] = (
        f"frame-ancestors 'self' {IFRAME_ORIGINS}"
    )
    # Supprimer l'ancien header s'il est posé par défaut
    if "X-Frame-Options" in response.headers:
        del response.headers["X-Frame-Options"]
    return response


def _cle_session():
    """Rangee dans config.json : sans elle, chaque redemarrage deconnecterait
    tout le monde."""
    c = config()
    if not c.get("secret"):
        c["secret"] = secrets.token_hex(32)
        ecrire_config(c)
    return c["secret"]


# ─────────────────────────────────────────────────────────────────────────────
# Base
# ─────────────────────────────────────────────────────────────────────────────

REGISTRE = os.path.join(DONNEES, "espaces.json")


def espaces():
    """La liste des comptes clients. Le premier est celui d'origine."""
    try:
        with open(REGISTRE, encoding="utf-8") as f:
            liste = json.load(f)
    except Exception:
        liste = []
    if not liste:
        # L'outil tournait deja pour un seul client : ses donnees deviennent
        # le premier compte, sans rien deplacer.
        liste = [{"id": "principal", "nom": "Compte principal",
                  "fichier": "pe.db", "cree_le": maintenant()}]
        ecrire_espaces(liste)
    return liste


def ecrire_espaces(liste):
    with open(REGISTRE, "w", encoding="utf-8") as f:
        json.dump(liste, f, ensure_ascii=False, indent=2)


# ══════════════════════════════════════════════════════════════════════════
#  CONNEXION
#  La plateforme n'en avait aucune : n'importe qui connaissant l'adresse
#  entrait, changeait de compte client et publiait en leur nom. Les comptes
#  vivent dans config.json (« comptes »), le mot de passe n'y est jamais en
#  clair — seulement son empreinte scrypt.
# ══════════════════════════════════════════════════════════════════════════
from werkzeug.security import check_password_hash, generate_password_hash

# Ces adresses DOIVENT répondre sans connexion : les plateformes (Meta,
# TikTok, Google…) les vérifient avant d'examiner une application, et les
# retours OAuth arrivent forcément avant que l'utilisateur soit reconnu.
LIBRES = ("/connexion", "/deconnexion", "/guide", "/tuto", "/confidentialite",
          "/conditions", "/api/sante", "/api/reseaux/", "/medias/", "/favicon",
          # Le retour de LinkedIn arrive comme les autres : avant toute
          # reconnaissance. ⚠️ On n'ouvre QUE le retour — « /reglages » pose
          # les identifiants de l'application, il reste derrière la porte.
          "/api/linkedin-page/retour",
          # Les plateformes viennent lire un fichier a la racine du domaine
          # pour prouver qu'il nous appartient. Redirige vers /connexion, et
          # la verification echoue sans jamais dire pourquoi.
          "/verifications")


def comptes():
    return (config().get("comptes") or [])


def compte_courant():
    ident = session.get("qui")
    for c in comptes():
        if c.get("identifiant") == ident:
            return c
    return None


def est_admin():
    c = compte_courant()
    return bool(c and c.get("role") == "admin")


@app.before_request
def _porte():
    """Rien ne passe sans connexion, hors les adresses publiques."""
    chemin = request.path
    if any(chemin == l or chemin.startswith(l) for l in LIBRES):
        return None
    if chemin == "/installation":
        return None
    if not comptes():
        return render_template_string(PAGE_NON_CONFIGUREE), 503
    # ⚠️ Les fichiers de preuve de domaine sont A LA RACINE : aucun préfixe de
    # LIBRES ne peut les couvrir. On les laisse passer un par un, et seulement
    # s'ils existent vraiment — on n'ouvre pas la racine du site.
    if chemin.count("/") == 1 and (chemin.endswith(".txt") or chemin.endswith(".html")):
        f = _verif_nom(chemin)
        if f and os.path.exists(os.path.join(VERIFS, f)):
            return None
    if session.get("qui"):
        # Un compte non-admin est enfermé dans SON espace client.
        c = compte_courant()
        if c and c.get("espace") and not est_admin():
            session["espace_impose"] = c["espace"]
        return None
    if chemin.startswith("/api/"):
        return jsonify({"erreur": "Connexion requise"}), 401
    return redirect("/connexion")


@app.route("/connexion", methods=["GET", "POST"])
def connexion():
    erreur = ""
    if request.method == "POST":
        qui = (request.form.get("identifiant") or "").strip().lower()
        mdp = request.form.get("motdepasse") or ""
        for c in comptes():
            if c.get("identifiant") == qui and check_password_hash(c.get("mdp_hash", ""), mdp):
                session.permanent = True
                session["qui"] = qui
                if c.get("espace"):
                    resp = redirect("/")
                    resp.set_cookie("pe_espace", c["espace"], max_age=31536000, samesite="Lax")
                    return resp
                return redirect("/")
        erreur = "Identifiant ou mot de passe incorrect."
        time.sleep(1)          # on ralentit les essais en rafale
    return render_template_string(PAGE_CONNEXION, erreur=erreur)


class _MasquerJetonInstallation(logging.Filter):
    def filter(self, record):
        message = record.getMessage()
        record.msg = re.sub(r"(/installation\?)[^\s]*", r"\1[masqué]", message)
        record.args = ()
        return True


# Le serveur de développement journalise l'adresse complète de la requête.
logging.getLogger("werkzeug").addFilter(_MasquerJetonInstallation())


def _jeton_installation_valide():
    if comptes():
        return False
    try:
        with open(os.path.join(DONNEES, "jeton_installation.txt"), encoding="utf-8") as fichier:
            attendu = fichier.read().strip()
    except OSError:
        return False
    recu = request.args.get("jeton", "")
    return bool(attendu and recu and hmac.compare_digest(
        recu.encode("utf-8"), attendu.encode("utf-8")))


@app.route("/installation", methods=["GET", "POST"])
def installation():
    if not _jeton_installation_valide():
        time.sleep(1)
        abort(404)
    erreur = ""
    if request.method == "POST":
        identifiant = (request.form.get("identifiant") or "").strip().lower()
        motdepasse = request.form.get("motdepasse") or ""
        confirmation = request.form.get("confirmation") or ""
        if not identifiant:
            erreur = "Saisissez un identifiant."
        elif len(motdepasse) < 12:
            erreur = "Le mot de passe doit contenir au moins douze caractères."
        elif motdepasse != confirmation:
            erreur = "Les deux mots de passe sont différents."
        if erreur:
            time.sleep(1)
        else:
            # Le verrou couvre la relecture et le remplacement, même avec
            # plusieurs processus serveur : un seul premier compte peut gagner.
            with open(os.path.join(DONNEES, ".installation.lock"), "a") as verrou:
                os.chmod(verrou.name, 0o600)
                fcntl.flock(verrou, fcntl.LOCK_EX)
                if not _jeton_installation_valide():
                    abort(404)
                with open(CONFIG, encoding="utf-8") as fichier:
                    configuration = json.load(fichier)
                configuration["comptes"] = [{
                    "identifiant": identifiant, "nom": identifiant,
                    "role": "admin", "mdp_hash": generate_password_hash(motdepasse)
                }]
                temporaire = None
                try:
                    with tempfile.NamedTemporaryFile(
                            mode="w", encoding="utf-8", dir=DONNEES,
                            prefix=".installation-", delete=False) as fichier:
                        temporaire = fichier.name
                        os.chmod(temporaire, 0o600)
                        json.dump(configuration, fichier, ensure_ascii=False, indent=2)
                        fichier.flush()
                        os.fsync(fichier.fileno())
                    os.replace(temporaire, CONFIG)
                    temporaire = None
                finally:
                    if temporaire is not None:
                        os.unlink(temporaire)
                os.unlink(os.path.join(DONNEES, "jeton_installation.txt"))
            session.clear()
            session.permanent = True
            session["qui"] = identifiant
            return redirect("/")
    reponse = app.make_response(render_template_string(PAGE_INSTALLATION, erreur=erreur))
    reponse.headers["Cache-Control"] = "no-store"
    reponse.headers["Referrer-Policy"] = "no-referrer"
    return reponse


@app.route("/deconnexion")
def deconnexion():
    session.clear()
    return redirect("/connexion")


PAGE_CONNEXION = """<!doctype html><html lang=fr><head>
<meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Connexion — Post Everyday</title>
<style>
 *{box-sizing:border-box} body{margin:0;min-height:100vh;display:flex;align-items:center;
  justify-content:center;background:#0d1017;color:#e8ebf2;
  font:16px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;padding:20px}
 .boite{width:100%;max-width:360px;background:#141926;border:1px solid #232b3d;
  border-radius:18px;padding:34px 30px}
 h1{margin:0 0 4px;font-size:1.35rem} p.s{margin:0 0 24px;color:#8b93a7;font-size:.92rem}
 label{display:block;font-size:.82rem;color:#8b93a7;margin-bottom:6px}
 input{width:100%;padding:12px 14px;margin-bottom:16px;border-radius:10px;
  background:#0d1017;border:1px solid #232b3d;color:#e8ebf2;font:inherit}
 input:focus{outline:none;border-color:#6c5ce7}
 button{width:100%;padding:12px;border:0;border-radius:10px;background:#6c5ce7;color:#fff;
  font:inherit;font-weight:700;cursor:pointer}
 button:hover{background:#5a4bd4}
 .err{background:#3a1d1d;border:1px solid #7f3535;color:#ffb4b4;padding:10px 12px;
  border-radius:9px;font-size:.88rem;margin-bottom:16px}
</style></head><body>
<form class=boite method=post>
  <h1>Post&nbsp;Everyday</h1>
  <p class=s>Vos réseaux, un seul endroit.</p>
  {% if erreur %}<div class=err>{{ erreur }}</div>{% endif %}
  <label>Identifiant</label>
  <input name=identifiant autocomplete=username autofocus>
  <label>Mot de passe</label>
  <input name=motdepasse type=password autocomplete=current-password>
  <button>Entrer</button>
</form></body></html>"""


PAGE_INSTALLATION = PAGE_CONNEXION.replace(
    "Connexion — Post Everyday", "Installation — Post Everyday"
).replace(
    "Vos réseaux, un seul endroit.",
    "Créez votre premier compte administrateur pour ouvrir le site."
).replace(
    "autocomplete=current-password", "autocomplete=new-password minlength=12 required"
).replace(
    "autocomplete=username autofocus", "autocomplete=username autofocus required"
).replace(
    "<button>Entrer</button>",
    '<label>Confirmez le mot de passe</label>'
    '<input name=confirmation type=password autocomplete=new-password minlength=12 required>'
    '<button>Créer mon compte</button>'
)

PAGE_NON_CONFIGUREE = """<!doctype html><html lang=fr><head>
<meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Installation — Post Everyday</title></head>
<body><p>Le site n’est pas encore configuré.</p></body></html>"""


def espace_courant():
    """Le compte ouvert dans cet onglet, d'apres le petit temoin de session."""
    liste = espaces()
    voulu = request.cookies.get("pe_espace") if has_request_context() else None
    # Un compte rattache a un espace y est enferme : il ne doit pas pouvoir
    # publier au nom d'un autre client en changeant un cookie.
    if has_request_context() and session.get("espace_impose") and not est_admin():
        voulu = session["espace_impose"]
    for e in liste:
        if e["id"] == voulu:
            return e
    return liste[0]


def db():
    chemin = os.path.join(DONNEES, espace_courant()["fichier"])
    neuf = not os.path.exists(chemin)
    c = sqlite3.connect(chemin, timeout=20)
    c.row_factory = sqlite3.Row
    if neuf:
        creer_tables(c)
    return c


def creer_tables(c):
    with c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS profil (
          cle TEXT PRIMARY KEY, valeur TEXT);
        CREATE TABLE IF NOT EXISTS posts (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          reseau TEXT NOT NULL, angle TEXT, mode TEXT,
          titre TEXT, legende TEXT, contenu TEXT,
          media TEXT, etat TEXT NOT NULL DEFAULT 'brouillon',
          prevu_le TEXT, publie_le TEXT, url_publie TEXT, erreur TEXT,
          cree_le TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS comptes (
          plateforme TEXT PRIMARY KEY, nom TEXT, jeton TEXT,
          rafraichir TEXT, expire_le TEXT, extra TEXT, ajoute_le TEXT);
        CREATE INDEX IF NOT EXISTS idx_posts_etat ON posts(etat, prevu_le);
        -- Un relevé d'abonnés par réseau et par jour. Tant qu'un réseau n'est
        -- pas connecté, c'est saisi à la main ; ensuite l'API le remplira.
        CREATE TABLE IF NOT EXISTS abonnes (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          plateforme TEXT NOT NULL, nombre INTEGER NOT NULL,
          source TEXT NOT NULL DEFAULT 'saisie',
          releve_le TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_ab ON abonnes(plateforme, releve_le);
        -- L'adresse publique de chaque compte : saisie à la main tant que l'API
        -- n'est pas branchée, remplie par l'OAuth ensuite.
        CREATE TABLE IF NOT EXISTS liens (
          plateforme TEXT PRIMARY KEY, url TEXT NOT NULL, source TEXT DEFAULT 'saisie');
        -- Les identifiants d'application propres a ce client. Vides tant qu'on
        -- se sert de l'application partagee de l'agence.
        -- Les indicateurs saisis à la main (vues, likes) tant qu'aucune API
        -- ne les transmet. Une ligne par indicateur, la dernière valeur fait foi.
        CREATE TABLE IF NOT EXISTS mesures (
          cle TEXT PRIMARY KEY, valeur INTEGER NOT NULL, releve_le TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS reglages (
          cle TEXT PRIMARY KEY, valeur TEXT);
        """)


def maintenant():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# La base d'origine existe deja : on s'assure seulement qu'elle a ses tables.
for _e in espaces():
    _c = sqlite3.connect(os.path.join(DONNEES, _e["fichier"]), timeout=20)
    creer_tables(_c)
    _c.close()


def config():
    try:
        with open(CONFIG, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def ecrire_config(c):
    with open(CONFIG, "w", encoding="utf-8") as f:
        json.dump(c, f, ensure_ascii=False, indent=2)


# La cle de session doit etre posee ici : plus haut, config() n existe pas encore.
app.secret_key = _cle_session()
app.permanent_session_lifetime = timedelta(days=30)


def cles_reseau(pid):
    """Les identifiants a utiliser pour ce reseau, dans ce compte.

    D'abord ceux du client, sinon ceux de l'agence. Rend aussi d'ou ils
    viennent : l'interface doit pouvoir le dire, sinon on ne comprend plus
    pourquoi une connexion marche ici et pas la.
    """
    o = OAUTH.get(pid) or {}
    noms = [o.get("cle_id"), o.get("cle_secret")]
    with db() as c:
        propres = {l["cle"].split(".", 1)[1]: l["valeur"] for l in c.execute(
            "SELECT cle, valeur FROM reglages WHERE cle LIKE ?", (pid + ".%",))
            if (l["valeur"] or "").strip()}
    if all(propres.get(n) for n in noms if n):
        return propres, "compte"
    partage = {k: v for k, v in (config().get(pid) or {}).items() if (v or "").strip()}
    if all(partage.get(n) for n in noms if n):
        return partage, "agence"
    # Rien de complet : on rend ce qu'on a, l'interface dira ce qui manque.
    return (propres or partage), ("compte" if propres else "agence")


def profil():
    with db() as c:
        lignes = c.execute("SELECT cle, valeur FROM profil").fetchall()
    p = M.profil_defaut()
    saisi = {l["cle"]: l["valeur"] for l in lignes if (l["valeur"] or "").strip()}
    p.update(saisi)
    # Tant que rien n'a ete saisi, on travaille sur le metier d'essai — et on le
    # dit, pour que personne ne prenne l'exemple pour sa propre fiche.
    # On regarde les champs OBLIGATOIRES seulement : le ton, choisi par defaut
    # dans un menu deroulant, ne prouve pas que la fiche a ete remplie.
    p["essai"] = not any(saisi.get(c) for c, _, _, obli in M.CHAMPS_PROFIL if obli)
    return p


# ─────────────────────────────────────────────────────────────────────────────
# Interface
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/")
def accueil():
    return send_from_directory(ICI, "index.html")


# Toutes les plateformes reclament ces deux adresses publiques avant d'examiner
# une application. Elles doivent repondre sans connexion.
@app.route("/guide")
def page_guide():
    return send_from_directory(ICI, "guide.html")


@app.route("/tuto")
def page_tuto():
    """Le mode d'emploi de l'outil, pour quelqu'un qui arrive dessus sans rien
    savoir. Volontairement hors connexion : on doit pouvoir l'envoyer par
    message à une assistante avant même qu'elle ait ses identifiants."""
    return send_from_directory(ICI, "tuto.html")


@app.route("/confidentialite")
def page_confidentialite():
    return send_from_directory(ICI, "confidentialite.html")


@app.route("/conditions")
def page_conditions():
    return send_from_directory(ICI, "conditions.html")


@app.route("/medias/<path:nom>")
def media(nom):
    return send_from_directory(MEDIAS, nom)


# ─────────────────────────────────────────────────────────────────────────────
# Le profil, saisi une seule fois
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/api/profil", methods=["GET", "POST"])
def api_profil():
    if request.method == "POST":
        d = request.get_json(force=True) or {}
        with db() as c:
            for cle, _, _, _ in M.CHAMPS_PROFIL:
                if cle in d:
                    c.execute("INSERT INTO profil(cle,valeur) VALUES(?,?) "
                              "ON CONFLICT(cle) DO UPDATE SET valeur=excluded.valeur",
                              (cle, (d.get(cle) or "").strip()))
    p = profil()
    return jsonify(profil=p, complet=M.profil_complet(p),
                   champs=[{"cle": c, "libelle": l, "exemple": e, "obligatoire": o}
                           for c, l, e, o in M.CHAMPS_PROFIL],
                   tons=M.TONS)


# ─────────────────────────────────────────────────────────────────────────────
# Les agents et leurs propositions
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/api/agents")
def api_agents():
    p = profil()
    out = []
    for rid in M.ORDRE_RESEAUX:
        r = M.RESEAUX[rid]
        with db() as c:
            n = c.execute("SELECT COUNT(*) n FROM posts WHERE reseau=? AND etat='publie'",
                          (rid,)).fetchone()["n"]
            f = c.execute("SELECT COUNT(*) n FROM posts WHERE reseau=? AND etat IN "
                          "('brouillon','prevu')", (rid,)).fetchone()["n"]
            lie = c.execute("SELECT nom FROM comptes WHERE plateforme=?", (rid,)).fetchone()
            url = c.execute("SELECT url FROM liens WHERE plateforme=?", (rid,)).fetchone()
        out.append({"id": rid, "nom": r["nom"], "agent": r["agent"], "complet": r["complet"],
                    "url": url["url"] if url else None,
                    "role": r["role"], "c1": r["c1"], "c2": r["c2"],
                    "replique": r["replique"], "bulles": r["bulles"],
                    "format": r["format"], "conseil": r["conseil"],
                    "publies": n, "en_file": f,
                    "compte": lie["nom"] if lie else None})
    return jsonify(agents=out, patron=M.PATRON, profil_complet=M.profil_complet(p))


@app.route("/api/mesures", methods=["GET", "POST"])
def api_mesures():
    """Vues et likes, saisis à la main faute d'API branchée. On garde la date :
    un chiffre sans date ne veut rien dire au bout de trois semaines."""
    CLES = ("vues", "likes")
    if request.method == "POST":
        d = request.get_json(force=True) or {}
        with db() as c:
            for cle in CLES:
                if cle not in d:
                    continue
                brut = str(d.get(cle) or "").replace(" ", "").replace(",", "")
                if not brut.isdigit():
                    continue
                c.execute("INSERT INTO mesures(cle,valeur,releve_le) VALUES(?,?,?) "
                          "ON CONFLICT(cle) DO UPDATE SET valeur=excluded.valeur,"
                          "releve_le=excluded.releve_le",
                          (cle, int(brut), datetime.now().strftime("%Y-%m-%d")))
    with db() as c:
        return jsonify(mesures={r["cle"]: {"valeur": r["valeur"], "releve_le": r["releve_le"]}
                                for r in c.execute("SELECT * FROM mesures")})


@app.route("/api/liens", methods=["GET", "POST"])
def api_liens():
    """L'adresse publique de chaque compte, pour que le logo soit cliquable."""
    if request.method == "POST":
        d = request.get_json(force=True) or {}
        with db() as c:
            for pid in M.ORDRE_RESEAUX:
                if pid not in d:
                    continue
                u = (d.get(pid) or "").strip()
                if not u:
                    c.execute("DELETE FROM liens WHERE plateforme=?", (pid,))
                    continue
                if not u.startswith(("http://", "https://")):
                    u = "https://" + u
                c.execute("INSERT INTO liens(plateforme,url,source) VALUES(?,?,'saisie') "
                          "ON CONFLICT(plateforme) DO UPDATE SET url=excluded.url",
                          (pid, u))
    with db() as c:
        return jsonify(liens={r["plateforme"]: r["url"] for r in
                              c.execute("SELECT plateforme, url FROM liens")})


@app.route("/api/idees")
def api_idees():
    reseau = request.args.get("reseau", "tiktok")
    decalage = int(request.args.get("decalage", 0))
    return jsonify(idees=M.idees(profil(), reseau, 6, decalage))


@app.route("/api/composer", methods=["POST"])
def api_composer():
    d = request.get_json(force=True) or {}
    try:
        r = M.composer(profil(), d.get("reseau"), d.get("angle"), d.get("mode"),
                       bool(d.get("vedette")))
    except ValueError as e:
        return jsonify(erreur=str(e)), 400

    # Le modèle n'écrit pas à la place du générateur : il repasse dessus. Si
    # quoi que ce soit échoue, on rend le texte maison — jamais d'écran vide.
    if d.get("affiner") and (config().get("llm") or {}).get("actif"):
        p = profil() or {}
        contrainte = M.RESEAUX.get(d.get("reseau"), {})
        brut = json.dumps(r, ensure_ascii=False)
        txt, souci = appel_llm(
            "Tu réécris des contenus pour %s. Métier : %s, à %s, pour %s. "
            "Garde EXACTEMENT la même structure JSON, les mêmes clés, la même longueur "
            "approximative. Rends le ton plus vivant et plus concret, sans inventer de "
            "chiffre ni de promesse. Réponds uniquement par le JSON, rien d'autre."
            % (contrainte.get("nom", d.get("reseau")), p.get("metier", ""),
               p.get("ville", ""), p.get("cible", "")),
            brut)
        if txt:
            try:
                debut, fin = txt.find("{"), txt.rfind("}")
                affine = json.loads(txt[debut:fin + 1]) if debut >= 0 else None
                if isinstance(affine, dict) and affine.get("legende"):
                    affine["affine"] = True
                    return jsonify(affine)
            except Exception:
                pass
        r["affine"] = False
        r["souci_llm"] = souci
    return jsonify(r)


# ─────────────────────────────────────────────────────────────────────────────
# Le tableau de bord
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/api/abonnes", methods=["GET", "POST"])
def api_abonnes():
    """Relevés d'abonnés. Un seul relevé par réseau et par jour : on remplace."""
    if request.method == "POST":
        d = request.get_json(force=True) or {}
        jour = datetime.now().strftime("%Y-%m-%d")
        with db() as c:
            for pid in M.ORDRE_RESEAUX:
                if pid not in d or d[pid] in ("", None):
                    continue
                try:
                    n = int(str(d[pid]).replace(" ", "").replace("\u202f", ""))
                except ValueError:
                    continue
                c.execute("DELETE FROM abonnes WHERE plateforme=? AND substr(releve_le,1,10)=?",
                          (pid, jour))
                c.execute("INSERT INTO abonnes(plateforme,nombre,source,releve_le) VALUES(?,?,?,?)",
                          (pid, n, d.get("source") or "saisie",
                           datetime.now().isoformat(timespec="seconds")))
    with db() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT plateforme, nombre, releve_le FROM abonnes ORDER BY releve_le")]
    return jsonify(releves=rows)


def _gain(c, pid, jours):
    """Ce que le réseau a gagné depuis le relevé le plus proche d'il y a N jours."""
    dernier = c.execute("SELECT nombre, releve_le FROM abonnes WHERE plateforme=?"
                        " ORDER BY releve_le DESC LIMIT 1", (pid,)).fetchone()
    if not dernier:
        return None, None, None
    seuil = (datetime.now() - timedelta(days=jours)).isoformat(timespec="seconds")
    avant = c.execute("SELECT nombre FROM abonnes WHERE plateforme=? AND releve_le<=?"
                      " ORDER BY releve_le DESC LIMIT 1", (pid, seuil)).fetchone()
    if not avant:
        # Pas d'historique assez ancien : on prend le tout premier relevé.
        avant = c.execute("SELECT nombre FROM abonnes WHERE plateforme=?"
                          " ORDER BY releve_le ASC LIMIT 1", (pid,)).fetchone()
    gain = dernier["nombre"] - avant["nombre"] if avant else 0
    return dernier["nombre"], gain, dernier["releve_le"][:10]


@app.route("/api/tableau")
def api_tableau():
    p = profil()
    fenetre = int(request.args.get("jours", 30))
    depuis = (datetime.now() - timedelta(days=fenetre)).isoformat(timespec="seconds")
    lignes, total_ab, total_gain, total_posts, connus = [], 0, 0, 0, 0
    with db() as c:
        for pid in M.ORDRE_RESEAUX:
            r = M.RESEAUX[pid]
            ab, gain, le = _gain(c, pid, fenetre)
            posts = c.execute("SELECT COUNT(*) n FROM posts WHERE reseau=? AND etat='publie'",
                              (pid,)).fetchone()["n"]
            recents = c.execute("SELECT COUNT(*) n FROM posts WHERE reseau=? AND etat='publie'"
                                " AND publie_le>=?", (pid, depuis)).fetchone()["n"]
            file = c.execute("SELECT COUNT(*) n FROM posts WHERE reseau=? AND etat IN"
                             " ('brouillon','prevu')", (pid,)).fetchone()["n"]
            lie = c.execute("SELECT nom FROM comptes WHERE plateforme=?", (pid,)).fetchone()
            if ab is not None:
                total_ab += ab
                total_gain += gain or 0
                connus += 1
            total_posts += posts
            lignes.append({
                "id": pid, "nom": r["nom"], "agent": r["agent"], "c1": r["c1"], "c2": r["c2"],
                "abonnes": ab, "gain": gain, "releve_le": le,
                "posts": posts, "posts_recents": recents, "en_file": file,
                "connecte": bool(lie),
                "par_post": round((gain or 0) / recents, 1) if recents and gain else None,
            })
        total_recents = c.execute("SELECT COUNT(*) n FROM posts WHERE etat='publie'"
                                  " AND publie_le>=?", (depuis,)).fetchone()["n"]
        premier = c.execute("SELECT MIN(releve_le) d FROM abonnes").fetchone()["d"]
    return jsonify(
        lignes=lignes, fenetre=fenetre,
        total_abonnes=total_ab if connus else None,
        total_gain=total_gain if connus else None,
        reseaux_suivis=connus,
        total_posts=total_posts, posts_recents=total_recents,
        gain_par_post=round(total_gain / total_recents, 1) if (connus and total_recents) else None,
        premier_releve=(premier or "")[:10],
        profil_complet=M.profil_complet(p))


@app.route("/api/plan")
def api_plan():
    depart = int(request.args.get("depart", 0))
    par_jour = max(1, min(3, int(request.args.get("par_jour", 1))))
    p = profil()
    plan = M.plan_semaine(p, 7, par_jour, depart)
    with db() as c:
        stats = {r["reseau"]: r["n"] for r in c.execute(
            "SELECT reseau, COUNT(*) n FROM posts WHERE etat='publie' GROUP BY reseau")}
        total = c.execute("SELECT COUNT(*) n FROM posts WHERE etat='publie'").fetchone()["n"]
        file = c.execute("SELECT COUNT(*) n FROM posts WHERE etat IN ('brouillon','prevu')"
                         ).fetchone()["n"]
    return jsonify(plan=plan, publies_par_reseau=stats, total_publies=total, en_file=file)


# ─────────────────────────────────────────────────────────────────────────────
# La file : brouillon → prévu → publié
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/api/posts", methods=["GET", "POST"])
def api_posts():
    if request.method == "POST":
        d = request.get_json(force=True) or {}
        with db() as c:
            cur = c.execute(
                "INSERT INTO posts(reseau,angle,mode,titre,legende,contenu,media,etat,prevu_le,cree_le)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)",
                (d.get("reseau"), d.get("angle"), d.get("mode"), d.get("titre"),
                 d.get("legende"), json.dumps(d.get("contenu") or {}, ensure_ascii=False),
                 d.get("media"), d.get("prevu_le") and "prevu" or "brouillon",
                 d.get("prevu_le"), datetime.now().isoformat(timespec="seconds")))
            return jsonify(id=cur.lastrowid)
    etat = request.args.get("etat")
    q = "SELECT * FROM posts"
    args = []
    if etat:
        q += " WHERE etat=?"
        args.append(etat)
    q += " ORDER BY COALESCE(prevu_le, cree_le) DESC LIMIT 200"
    with db() as c:
        rows = [dict(r) for r in c.execute(q, args)]
    return jsonify(posts=rows)


@app.route("/api/posts/<int:pid>", methods=["PATCH", "DELETE"])
def api_post(pid):
    if request.method == "DELETE":
        with db() as c:
            c.execute("DELETE FROM posts WHERE id=?", (pid,))
        return jsonify(ok=True)
    d = request.get_json(force=True) or {}
    if d.get("etat") == "publie" and "publie_le" not in d:
        d["publie_le"] = datetime.now().isoformat(timespec="seconds")
    champs = [k for k in ("titre", "legende", "media", "etat", "prevu_le", "publie_le") if k in d]
    if not champs:
        return jsonify(ok=True)
    with db() as c:
        c.execute("UPDATE posts SET %s WHERE id=?" % ",".join(k + "=?" for k in champs),
                  [d[k] for k in champs] + [pid])
    return jsonify(ok=True)


# ─────────────────────────────────────────────────────────────────────────────
# Médias : la vidéo du tournage, les planches du carrousel
# ─────────────────────────────────────────────────────────────────────────────

def _en_mp4(chemin):
    """WebM → MP4 H.264. Le navigateur ne sait enregistrer qu'en WebM, et aucun
    reseau social ne l'accepte tel quel : la conversion est indispensable, pas
    un confort. `yuv420p` et `faststart` sont ce qui rend le fichier lisible
    partout, y compris sur les telephones et dans les apercus.

    Renvoie (chemin_mp4, souci). En cas d'echec on garde le WebM : mieux vaut
    un fichier au mauvais format que pas de fichier du tout.
    """
    sortie = os.path.splitext(chemin)[0] + ".mp4"
    cmd = ["ffmpeg", "-y", "-i", chemin,
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
           "-pix_fmt", "yuv420p", "-movflags", "+faststart",
           "-c:a", "aac", "-b:a", "128k", sortie]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=900)
        if r.returncode != 0 or not os.path.exists(sortie):
            return None, (r.stderr or b"").decode("utf-8", "ignore")[-180:]
        try:
            os.remove(chemin)          # l'original n'a plus d'usage
        except OSError:
            pass
        return sortie, ""
    except Exception as e:
        return None, str(e)[:180]


@app.route("/api/media", methods=["POST"])
def api_media():
    f = request.files.get("fichier")
    if not f:
        return jsonify(erreur="aucun fichier"), 400
    ext = os.path.splitext(f.filename or "")[1].lower() or ".bin"
    if ext not in (".webm", ".mp4", ".mov", ".png", ".jpg", ".jpeg", ".m4a", ".mp3", ".wav"):
        return jsonify(erreur="format non accepté"), 400
    nom = "%d_%s%s" % (int(time.time()), secrets.token_hex(4), ext)
    chemin = os.path.join(MEDIAS, nom)
    f.save(chemin)

    converti = False
    souci = ""
    if ext == ".webm":
        mp4, souci = _en_mp4(chemin)
        if mp4:
            nom = os.path.basename(mp4)
            converti = True

    return jsonify(media=nom, url="/medias/" + nom,
                   converti=converti, format=os.path.splitext(nom)[1].lstrip("."),
                   souci=souci)


@app.route("/api/transcrire", methods=["POST"])
def api_transcrire():
    """Sous-titres automatiques : on passe par Whisper, déjà en place côté studio."""
    d = request.get_json(force=True) or {}
    nom = d.get("media")
    chemin = os.path.join(MEDIAS, os.path.basename(nom or ""))
    if not nom or not os.path.exists(chemin):
        return jsonify(erreur="média introuvable"), 404
    # Le studio attend un envoi de formulaire avec le champ `video`, et une cle
    # partagee en en-tete. On envoyait un flux brut sans cle : 403 a tous les coups.
    try:
        with open(chemin, "rb") as fh:
            donnees = fh.read()
        limite = "----postevery" + secrets.token_hex(8)
        corps = b"".join([
            ("--%s\r\n" % limite).encode(),
            ('Content-Disposition: form-data; name="video"; filename="%s"\r\n'
             % os.path.basename(chemin)).encode(),
            b"Content-Type: application/octet-stream\r\n\r\n",
            donnees,
            ("\r\n--%s--\r\n" % limite).encode(),
        ])
        entetes = {"Content-Type": "multipart/form-data; boundary=%s" % limite}
        cle = ((config() or {}).get("studio") or {}).get("relay_key")
        if cle:
            entetes["X-Studio-Key"] = cle
        req = urllib.request.Request(
            STUDIO_API + "/api/montage/transcrire", data=corps, headers=entetes)
        with urllib.request.urlopen(req, timeout=600) as rep:
            return jsonify(json.loads(rep.read().decode("utf-8")))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "ignore")[:200]
        return jsonify(erreur="transcription refusée (%s) : %s" % (e.code, detail)), 502
    except Exception as e:
        return jsonify(erreur="transcription indisponible : %s" % e), 502


# ─────────────────────────────────────────────────────────────────────────────
# Connexion des réseaux
# ─────────────────────────────────────────────────────────────────────────────
# Chaque plateforme demande sa propre application déclarée chez l'éditeur.
# Tant que les identifiants ne sont pas renseignés dans config.json, on le dit
# franchement plutôt que de faire semblant.

OAUTH = {
    "youtube": {
        "auth": "https://accounts.google.com/o/oauth2/v2/auth",
        "jeton": "https://oauth2.googleapis.com/token",
        "scope": "https://www.googleapis.com/auth/youtube.upload "
                 "https://www.googleapis.com/auth/youtube.readonly",
        "cle_id": "client_id", "cle_secret": "client_secret",
        "prealables": "Google doit vérifier l'application pour le champ « youtube.upload ». Avant cette vérification, les vidéos envoyées par l'API restent en privé. Prévoir plusieurs semaines.",
        "ou": "console.cloud.google.com — API YouTube Data v3",
        "extra": {"access_type": "offline", "prompt": "consent"},
    },
    "linkedin": {
        "auth": "https://www.linkedin.com/oauth/v2/authorization",
        "jeton": "https://www.linkedin.com/oauth/v2/accessToken",
        "scope": "openid profile w_member_social",
        "cle_id": "client_id", "cle_secret": "client_secret",
        "prealables": "Rien à attendre : le produit « Share on LinkedIn » s'active tout de suite. Publier sur une page d'entreprise, et non sur le profil, demande en plus l'accès « Community Management », lui soumis à validation.",
        "ou": "linkedin.com/developers — produit « Share on LinkedIn »",
        "extra": {},
    },
    "facebook": {
        "auth": "https://www.facebook.com/v21.0/dialog/oauth",
        "jeton": "https://graph.facebook.com/v21.0/oauth/access_token",
        "scope": "pages_show_list,pages_manage_posts,pages_read_engagement",
        "cle_id": "app_id", "cle_secret": "app_secret",
        "prealables": "En mode développement, l'application ne publie que sur les comptes qui ont un rôle dessus — le tien. Pour publier sur les pages de tes clients : vérification d'entreprise (Business Verification) puis revue de l'application par Meta.",
        "ou": "developers.facebook.com — produit « Facebook Login »",
        "extra": {},
    },
    "instagram": {
        "auth": "https://www.facebook.com/v21.0/dialog/oauth",
        "jeton": "https://graph.facebook.com/v21.0/oauth/access_token",
        "scope": "instagram_basic,instagram_content_publish,pages_show_list",
        "cle_id": "app_id", "cle_secret": "app_secret",
        "prealables": "Même application que Facebook, mêmes validations. Et le compte Instagram doit être professionnel (Business ou Créateur) et rattaché à une page Facebook : un compte personnel ne peut pas publier par l'API.",
        "ou": "developers.facebook.com — mêmes identifiants que Facebook",
        "extra": {},
    },
    "tiktok": {
        "auth": "https://www.tiktok.com/v2/auth/authorize/",
        "jeton": "https://open.tiktokapis.com/v2/oauth/token/",
        "scope": "user.info.basic,video.publish,video.upload",
        "cle_id": "client_key", "cle_secret": "client_secret",
        "prealables": "⚠️ Tant que l'application n'a pas passé l'audit de TikTok, tout ce qui est publié reste en PRIVÉ (self-only), quelle que soit la visibilité demandée. L'audit se demande depuis la console, une fois la connexion testée. Ce n'est pas un défaut du logiciel : c'est la règle de TikTok.",
        "ou": "developers.tiktok.com — produit « Content Posting API »",
        "extra": {},
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# Le mode d'emploi de chaque reseau, pas a pas.
#
# L'assistante n'a reussi a brancher que LinkedIn : c'est le seul reseau dont
# l'application etait declaree. Les autres n'attendaient pas un correctif mais
# une marche a suivre — la console de chaque plateforme est un labyrinthe, et
# une etape oubliee (l'URI de redirection, un produit non ajoute, un compte
# absent des testeurs) se solde par un message d'erreur incomprehensible.
#
# ⚠️ L'ordre compte : chez Google et chez Meta, l'ecran de consentement doit
# exister AVANT que la console laisse creer l'identifiant OAuth.
# {URI} est remplace a l'affichage par l'adresse de retour du reseau.
ETAPES = {
    "youtube": [
        "Ouvrez console.cloud.google.com et créez un projet — appelez-le « Post Everyday ».",
        "Menu « APIs et services » → « Bibliothèque » → cherchez « YouTube Data API v3 » → ACTIVER.",
        "« APIs et services » → « Écran de consentement OAuth » : type Externe, nom de l'application, "
        "votre e-mail d'assistance. Page d'accueil {BASE}/ , confidentialité {BASE}/confidentialite , "
        "conditions {BASE}/conditions — ces trois pages sont publiques exprès, Google les vérifie.",
        "Toujours dans l'écran de consentement : ajoutez les autorisations (scopes) youtube.upload et "
        "youtube.readonly, puis ajoutez VOTRE adresse Google en « Utilisateur de test ». Sans cela, "
        "Google refusera la connexion tant que l'application n'est pas vérifiée.",
        "« Identifiants » → CRÉER DES IDENTIFIANTS → « ID client OAuth » → type « Application Web ». "
        "Dans « URI de redirection autorisés », collez exactement : {URI}",
        "Copiez l'ID client et le code secret, revenez ici, collez-les dans les deux champs, Enregistrer.",
        "Cliquez « Connecter mon compte YouTube » et choisissez le compte Google QUI POSSÈDE LA CHAÎNE. "
        "L'écran « Google n'a pas validé cette application » est normal : « Paramètres avancés » → "
        "« Accéder à … (non sécurisé) ».",
        "⚠️ **Si Google répond « Accès bloqué : … n'a pas terminé la procédure de validation »**, "
        "c'est que votre adresse Google n'est pas dans les **utilisateurs de test**. Retournez dans "
        "« Écran de consentement OAuth » → **Utilisateurs test** → **+ ADD USERS** → ajoutez l'adresse "
        "exacte du compte qui possède la chaîne, puis recommencez. C'est la dernière étape, et celle "
        "qu'on oublie une fois sur deux.",
        "⚠️ Tant que Google n'a pas **vérifié** l'application pour le champ « youtube.upload », les "
        "vidéos envoyées restent **privées** sur la chaîne. La connexion marche, la publication aussi : "
        "c'est la visibilité qui attend la validation de Google.",
    ],
    "facebook": [
        "Ouvrez developers.facebook.com → « Mes applications » → « Créer une application ». "
        "Choisissez « Autre », puis le type « Professionnel ».",
        "Rattachez le portefeuille d'entreprise (Business Manager) qui détient la Page — sans lui, "
        "Meta bloquera la publication sur les Pages.",
        "« Produits » → ajoutez « Connexion Facebook » (le produit, pas le SDK) → « Paramètres ». "
        "Dans « URI de redirection OAuth valides », collez exactement : {URI}",
        "« Paramètres » → « De base » : collez l'URL de confidentialité {BASE}/confidentialite , "
        "choisissez une catégorie, ajoutez une icône. Meta refuse de mettre l'application en ligne "
        "sans ces champs.",
        "Toujours dans « De base » : copiez l'identifiant de l'application et la clé secrète, "
        "revenez ici, collez-les, Enregistrer.",
        "Cliquez « Connecter mon compte Facebook » en étant connecté avec le compte ADMINISTRATEUR "
        "de la Page, et cochez la Page à la question « quelles Pages autorisez-vous ? ».",
    ],
    "instagram": [
        "C'est LA MÊME application que Facebook : si Facebook est déjà déclaré, cochez « Application "
        "de mon agence » là-bas, et recopiez ici le même identifiant et la même clé secrète.",
        "Dans la console Meta, ajoutez le produit « Instagram » (Instagram Graph API) à l'application.",
        "Vérifiez que l'adresse de retour est bien déclarée dans « Connexion Facebook » : {URI}",
        "Le compte Instagram doit être PROFESSIONNEL (Business ou Créateur) : Instagram → Paramètres → "
        "Type de compte. Un compte personnel ne peut pas publier par l'API, c'est une règle d'Instagram.",
        "Le compte Instagram doit être RATTACHÉ à la Page Facebook : depuis la Page → Paramètres → "
        "Instagram, ou depuis Instagram → Paramètres → « Partage sur d'autres applications ».",
        "Collez identifiant et clé secrète ici, Enregistrez, puis « Connecter mon compte Instagram » "
        "avec le compte Facebook administrateur de la Page.",
    ],
    "tiktok": [
        "Ouvrez developers.tiktok.com, connectez-vous avec le compte TikTok de l'agence, "
        "puis « Manage apps » → « Connect an app ».",
        "⚠️ **D'ABORD la propriété du site.** Onglet **URL properties** → ajoutez {BASE} → "
        "vérification **par fichier**. TikTok donne un nom de fichier et une ligne de contenu : "
        "recopiez-les dans le volet « 🔐 Prouver que le site est à nous » ci-dessous, puis cliquez "
        "« Verify » chez TikTok. **Sans cette étape, TikTok refuse l'URL de redirection et rien ne "
        "peut avancer** — c'est le blocage le plus fréquent.",
        "Ajoutez les DEUX produits : « Login Kit » ET « Content Posting API ». Sans Content Posting "
        "API, on peut lire le compte mais rien publier.",
        "Dans « Login Kit » → champ « Redirect URI », collez exactement : {URI} — une barre oblique "
        "en trop à la fin et TikTok répond « redirect_uri mismatch ».",
        "Cochez les autorisations : user.info.basic, video.upload, video.publish.",
        "Renseignez les adresses demandées : {BASE}/confidentialite et {BASE}/conditions .",
        "Onglet « Basic information » : copiez le « Client key » et le « Client secret », "
        "revenez ici, collez-les, Enregistrer.",
        "Ajoutez le compte TikTok à publier dans « Target users » (testeurs), puis « Connecter mon "
        "compte TikTok ». Le compte doit être Business ou Créateur — c'est gratuit, deux clics dans "
        "les réglages TikTok.",
    ],
    "linkedin": [
        "Ouvrez linkedin.com/developers/apps/new. L'application doit être portée par une PAGE "
        "LinkedIn : créez d'abord la page de l'agence si elle n'existe pas, c'est gratuit.",
        "Onglet « Products » : ajoutez « Sign In with LinkedIn using OpenID Connect » ET "
        "« Share on LinkedIn ». Les deux s'activent tout de suite, sans validation.",
        "Onglet « Auth » → « Authorized redirect URLs for your app », collez exactement : {URI}",
        "Toujours dans « Auth » : copiez le « Client ID » et le « Client Secret », "
        "revenez ici, collez-les, Enregistrer.",
        "Onglet « Settings » : faites vérifier la page — LinkedIn envoie un lien à un administrateur "
        "de la page. Sans cette vérification, l'application reste bridée.",
        "« Connecter mon compte LinkedIn » : vous autorisez, et c'est fini.",
        "⚠️ L'autorisation LinkedIn dure 60 JOURS : il faudra recliquer « Reconnecter » deux fois par "
        "an. Publier sur la Page d'une entreprise, et non sur le profil, demande en plus le produit "
        "« Community Management API », accordé au cas par cas.",
    ],
}


# Le mode d'emploi de la SECONDE application LinkedIn.
# ⚠️ Le point que tout le monde rate : LinkedIn refuse que « Community
# Management API » cohabite avec « Sign In » ou « Share on LinkedIn ». Ce n'est
# pas une option a cocher dans l'app existante — c'est une app a part entiere.
ETAPES_PAGE = [
    "Ouvrez linkedin.com/developers/apps/new et créez une **seconde** application, "
    "distincte de celle qui sert au profil. Nommez-la clairement, par exemple "
    "« Post Everyday — Pages ».",
    "⚠️ Cette application doit porter **uniquement** le produit « Community Management API ». "
    "LinkedIn refuse qu'il cohabite avec « Sign In with LinkedIn » ou « Share on LinkedIn » : "
    "c'est pour cela qu'il faut deux applications, et non un produit de plus sur la première.",
    "Onglet « Products » → demandez l'accès à **Community Management API** et remplissez le "
    "formulaire (usage, capture de l'outil, adresses légales : {BASE}/confidentialite et "
    "{BASE}/conditions). **LinkedIn valide à la main**, comptez plusieurs jours.",
    "Onglet « Auth » → « Authorized redirect URLs », collez exactement : {URI}",
    "Vérifiez que les autorisations demandées sont bien : r_organization_social, "
    "w_organization_social, rw_organization_admin.",
    "Onglet « Auth » → copiez le **Client ID** et le **Client Secret** de CETTE application, "
    "revenez ici, collez-les dans « Application Page entreprise », Enregistrer.",
    "Cliquez « Relier une Page entreprise ». LinkedIn vous demande d'autoriser, puis "
    "**nous vous montrons la liste de vos Pages** : choisissez celle qui recevra les publications.",
    "⚠️ Vous devez être **administrateur** de la Page. Un simple rédacteur ne verra aucune Page "
    "dans la liste — demandez le rôle sur la Page, puis recommencez.",
]

# La page exacte ou l'on cree l'application, pour ne pas chercher dans un menu.
CONSOLES = {
    "youtube":   "https://console.cloud.google.com/apis/credentials",
    "linkedin":  "https://www.linkedin.com/developers/apps/new",
    "facebook":  "https://developers.facebook.com/apps/create/",
    "instagram": "https://developers.facebook.com/apps/",
    "tiktok":    "https://developers.tiktok.com/apps",
}


# ─────────────────────────────────────────────────────────────────────────────
# Prouver que le domaine nous appartient
#
# TikTok (« URL properties ») et Google (Search Console) demandent de poser un
# petit fichier A LA RACINE du site et vont le lire eux-memes. Chez nous tout
# passait par la porte de connexion : le fichier repondait « 302 vers
# /connexion », la verification echouait, et l'ecran de TikTok disait seulement
# « unable to verify ». C'est ça qui bloquait TikTok du debut a la fin.
VERIFS = os.path.join(ICI, "verifications")


def _verif_nom(nom):
    """Un nom de fichier de verification, et rien d'autre.

    ⚠️ Servir n'importe quel nom depuis la racine, c'est ouvrir un chemin vers
    le disque. On n'accepte que ce que ces plateformes demandent vraiment.
    """
    nom = (nom or "").strip().lstrip("/")
    if not re.fullmatch(r"(tiktok[A-Za-z0-9]{4,64}\.txt|google[a-f0-9]{8,32}\.html"
                        r"|[A-Za-z0-9_-]{1,64}\.(txt|html))", nom):
        return ""
    return nom


@app.route("/<nom>.txt")
@app.route("/<nom>.html")
def verif_racine(nom):
    """Le fichier de preuve, servi tel quel, sans connexion."""
    for ext in (".txt", ".html"):
        f = _verif_nom(nom + ext)
        if f and os.path.exists(os.path.join(VERIFS, f)):
            return send_from_directory(VERIFS, f)
    return ("Introuvable", 404)


@app.route("/api/verifications")
def api_verifications():
    os.makedirs(VERIFS, exist_ok=True)
    return jsonify(fichiers=[{"nom": n, "url": BASE_PUB + "/" + n}
                             for n in sorted(os.listdir(VERIFS)) if _verif_nom(n)])


@app.route("/api/verifications", methods=["POST"])
def api_verifications_poser():
    d = request.get_json(force=True) or {}
    nom = _verif_nom(d.get("nom"))
    if not nom:
        return jsonify(erreur="Nom de fichier refusé. Attendu : tiktokXXXX.txt "
                              "ou googleXXXX.html, tel que la plateforme vous le donne."), 400
    contenu = (d.get("contenu") or "").strip()
    if not contenu or len(contenu) > 20000:
        return jsonify(erreur="Collez le contenu du fichier donné par la plateforme."), 400
    os.makedirs(VERIFS, exist_ok=True)
    with open(os.path.join(VERIFS, nom), "w", encoding="utf-8") as f:
        f.write(contenu + "\n")
    return jsonify(ok=True, nom=nom, url=BASE_PUB + "/" + nom)


@app.route("/api/verifications/<nom>", methods=["DELETE"])
def api_verifications_retirer(nom):
    f = _verif_nom(nom)
    chemin = os.path.join(VERIFS, f) if f else ""
    if chemin and os.path.exists(chemin):
        os.remove(chemin)
        return jsonify(ok=True)
    return jsonify(erreur="fichier inconnu"), 404

@app.route("/api/appkeys", methods=["POST"])
def api_appkeys():
    """Enregistre les identifiants d'application collés depuis l'interface."""
    d = request.get_json(force=True) or {}
    partage = bool(d.get("partage"))
    cfg = config() if partage else None
    touche = []
    with db() as c:
        for pid, o in OAUTH.items():
            bloc = d.get(pid)
            if not isinstance(bloc, dict):
                continue
            for cle in (o["cle_id"], o["cle_secret"]):
                if cle not in bloc:
                    continue
                v = (bloc[cle] or "").strip()
                # Un champ laissé vide n'efface pas ce qui est déjà en place :
                # on ne perd pas un secret par une sauvegarde distraite.
                if not v:
                    continue
                touche.append(pid)
                if partage:
                    cfg.setdefault(pid, {})[cle] = v
                else:
                    c.execute("INSERT INTO reglages(cle,valeur) VALUES(?,?) "
                              "ON CONFLICT(cle) DO UPDATE SET valeur=excluded.valeur",
                              (pid + "." + cle, v))
    if partage:
        with open(CONFIG, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    return jsonify(ok=True, reseaux=sorted(set(touche)),
                   ou=("agence" if partage else "compte"))


@app.route("/api/appkeys/<pid>", methods=["DELETE"])
def api_appkeys_retirer(pid):
    """Oublie les identifiants propres a ce compte : on repasse sur l'agence."""
    if pid not in OAUTH:
        return jsonify(erreur="plateforme inconnue"), 404
    with db() as c:
        c.execute("DELETE FROM reglages WHERE cle LIKE ?", (pid + ".%",))
    return jsonify(ok=True)


@app.route("/api/reseaux")
def api_reseaux():
    with db() as c:
        lies = {r["plateforme"]: dict(r) for r in c.execute(
            "SELECT plateforme, nom, ajoute_le FROM comptes")}
    out = []
    for pid, o in OAUTH.items():
        ids, provenance = cles_reseau(pid)
        out.append({
            "id": pid, "nom": M.RESEAUX[pid]["nom"],
            "configure": bool(ids.get(o["cle_id"]) and ids.get(o["cle_secret"])),
            "connecte": pid in lies,
            "compte": (lies.get(pid) or {}).get("nom"),
            "ou": o["ou"],
            "prealables": o.get("prealables", ""),
            "redirection": BASE_PUB + "/api/reseaux/" + pid + "/retour",
            "cles_attendues": [o["cle_id"], o["cle_secret"]],
            "console": CONSOLES.get(pid, ""),
            "etapes": [e.replace("{URI}", BASE_PUB + "/api/reseaux/" + pid + "/retour")
                        .replace("{BASE}", BASE_PUB)
                       for e in ETAPES.get(pid, [])],
            "provenance": provenance,
            "valeurs": {o["cle_id"]: ids.get(o["cle_id"]) or "",
                        o["cle_secret"]: ids.get(o["cle_secret"]) or ""},
        })
        # LinkedIn a deux portes : le profil de la personne, ou une Page
        # entreprise. Deux applications, deux autorisations — mais un seul
        # compte relié à la fois, donc une seule carte à l'écran.
        if pid == "linkedin":
            pc, _prov = li_page_cles()
            c = li_cible()
            out[-1].update({
                "page_configure": bool(pc.get("client_id") and pc.get("client_secret")),
                "page_redirection": li_page_retour(),
                "page_console": LI_PAGE["console"],
                "page_scope": LI_PAGE["scope"],
                "page_valeurs": {"client_id": pc.get("client_id") or ""},
                "cible": c.get("cible") or "",
                "cible_nom": c.get("nom") or "",
                "page_etapes": [e.replace("{URI}", li_page_retour()).replace("{BASE}", BASE_PUB)
                                for e in ETAPES_PAGE],
            })
    return jsonify(reseaux=out, base=BASE_PUB)


@app.route("/api/reseaux/<pid>/lier")
def api_lier(pid):
    o = OAUTH.get(pid)
    if not o:
        return jsonify(erreur="plateforme inconnue"), 404
    ids, _ = cles_reseau(pid)
    if not ids.get(o["cle_id"]):
        return jsonify(erreur="%s n'est pas encore configuré : il manque %s, à créer sur %s."
                       % (M.RESEAUX[pid]["nom"], o["cle_id"], o["ou"])), 503
    p = {"response_type": "code",
         "redirect_uri": BASE_PUB + "/api/reseaux/" + pid + "/retour",
         "scope": o["scope"], "state": secrets.token_urlsafe(16)}
    p["client_key" if pid == "tiktok" else "client_id"] = ids[o["cle_id"]]
    p.update(o["extra"])
    return redirect(o["auth"] + "?" + urllib.parse.urlencode(p))


def _page(titre, corps):
    return ("<!doctype html><meta charset='utf-8'><title>%s</title>"
            "<body style='background:#120a24;color:#ecdcff;font:15px/1.6 system-ui;"
            "max-width:600px;margin:70px auto;padding:0 22px'>%s"
            "<p><a style='color:#38e8ff' href='/'>Revenir à Post Everyday</a></p></body>"
            % (titre, corps))


@app.route("/api/reseaux/<pid>/retour")
def api_retour(pid):
    o = OAUTH.get(pid)
    code = request.args.get("code")
    if not o or not code:
        return _page("Connexion annulée",
                     "<h2>Aucun compte connecté</h2><p>L'autorisation a été refusée ou annulée.</p>")
    ids, _ = cles_reseau(pid)
    donnees = {"code": code, "grant_type": "authorization_code",
               "redirect_uri": BASE_PUB + "/api/reseaux/" + pid + "/retour",
               ("client_key" if pid == "tiktok" else "client_id"): ids.get(o["cle_id"]),
               "client_secret": ids.get(o["cle_secret"])}
    try:
        req = urllib.request.Request(
            o["jeton"], data=urllib.parse.urlencode(donnees).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded"})
        with urllib.request.urlopen(req, timeout=30) as rep:
            j = json.loads(rep.read().decode("utf-8"))
    except Exception as e:
        return _page("Échec", "<h2>La plateforme a refusé l'échange</h2><pre>%s</pre>" % e)
    jeton = j.get("access_token") or (j.get("data") or {}).get("access_token")
    if not jeton:
        return _page("Échec", "<h2>Pas de jeton reçu</h2><pre>%s</pre>" % json.dumps(j)[:400])
    with db() as c:
        c.execute("INSERT INTO comptes(plateforme,nom,jeton,rafraichir,expire_le,extra,ajoute_le)"
                  " VALUES(?,?,?,?,?,?,?) ON CONFLICT(plateforme) DO UPDATE SET"
                  " jeton=excluded.jeton, rafraichir=excluded.rafraichir,"
                  " expire_le=excluded.expire_le, ajoute_le=excluded.ajoute_le",
                  (pid, M.RESEAUX[pid]["nom"], jeton, j.get("refresh_token"),
                   str(j.get("expires_in") or ""), json.dumps(j.get("open_id") or ""),
                   datetime.now().isoformat(timespec="seconds")))
    return _page("Connecté", "<h2>%s est connecté</h2><p>Tu peux fermer cette page.</p>"
                 % M.RESEAUX[pid]["nom"])


# ─────────────────────────────────────────────────────────────────────────────
# LinkedIn — la PAGE entreprise, à côté du profil
#
# LinkedIn interdit de cumuler « Community Management API » avec « Sign In » et
# « Share on LinkedIn » dans une meme application : il faut donc une SECONDE
# app, portant ce seul produit, avec ses propres identifiants. C'est pour ça
# que cet ensemble vit à part de OAUTH et ne s'appelle pas « un réseau de plus ».
#
# ⚠️ Volontairement HORS de OAUTH : toutes les boucles de l'application
# parcourent OAUTH et lisent M.RESEAUX[pid]. Y glisser « linkedin_page »
# ajouterait une sixième carte fantôme et ferait planter le tableau de bord.
# La cible (profil ou page) est rangée dans `comptes.extra` de la ligne
# « linkedin » : un seul compte LinkedIn relié à la fois, comme avant.
LI_API = "https://api.linkedin.com"


def _lire(url, entetes=None):
    """Un GET JSON, sans dépendance. Le message d'erreur de LinkedIn remonte
    tel quel : « ACCESS_DENIED » dit tout de suite que la validation manque."""
    req = urllib.request.Request(url)
    for k, v in (entetes or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            d = json.loads(e.read().decode())
        except Exception:
            d = {}
        raise ValueError(d.get("message") or d.get("error_description") or ("HTTP %s" % e.code))
    except Exception as e:
        raise ValueError(str(e)[:140])


LI_PAGE = {
    "auth": "https://www.linkedin.com/oauth/v2/authorization",
    "jeton": "https://www.linkedin.com/oauth/v2/accessToken",
    # r_organization_social pour lire les Pages administrées, w_organization_social
    # pour publier en leur nom. rw_organization_admin sert à lister les rôles.
    "scope": "r_organization_social w_organization_social rw_organization_admin",
    "cle_id": "client_id", "cle_secret": "client_secret",
    "console": "https://www.linkedin.com/developers/apps/new",
}
LI_PAGE_ID = "linkedin_page"


def li_page_retour():
    return BASE_PUB + "/api/linkedin-page/retour"


def li_page_cles():
    """Les identifiants de l'app « Page », ceux du compte d'abord."""
    with db() as c:
        propres = {l["cle"].split(".", 1)[1]: l["valeur"] for l in c.execute(
            "SELECT cle, valeur FROM reglages WHERE cle LIKE ?", (LI_PAGE_ID + ".%",))
            if (l["valeur"] or "").strip()}
    if propres.get("client_id") and propres.get("client_secret"):
        return propres, "compte"
    partage = {k: v for k, v in (config().get(LI_PAGE_ID) or {}).items() if (v or "").strip()}
    return (partage or propres), ("agence" if partage else "compte")


def li_cible():
    """Sur quoi publie-t-on : le profil de la personne, ou une Page ?"""
    with db() as c:
        r = c.execute("SELECT extra, nom FROM comptes WHERE plateforme='linkedin'").fetchone()
    if not r:
        return {}
    try:
        e = json.loads(r["extra"] or "null")
    except Exception:
        e = None
    if isinstance(e, dict) and e.get("urn"):
        return {"cible": "page", "urn": e["urn"], "nom": e.get("nom") or r["nom"]}
    return {"cible": "profil", "nom": r["nom"]}


@app.route("/api/linkedin-page/lier")
def api_li_page_lier():
    ids, _ = li_page_cles()
    if not ids.get("client_id"):
        return jsonify(erreur="L'application « Page LinkedIn » n'est pas encore déclarée : "
                              "il manque le Client ID de l'app Community Management API."), 503
    p = {"response_type": "code", "client_id": ids["client_id"],
         "redirect_uri": li_page_retour(), "scope": LI_PAGE["scope"],
         "state": secrets.token_urlsafe(16)}
    return redirect(LI_PAGE["auth"] + "?" + urllib.parse.urlencode(p))


def _li_pages_administrees(jeton):
    """Les Pages dont la personne est ADMINISTRATEUR, nom compris.

    ⚠️ organizationAcls ne rend que des URN. Le nom se demande à part, sinon
    l'écran propose « urn:li:organization:12345 » — illisible, et on choisit
    la mauvaise page une fois sur deux.
    """
    ent = {"Authorization": "Bearer " + jeton, "X-Restli-Protocol-Version": "2.0.0"}
    url = (LI_API + "/v2/organizationAcls?q=roleAssignee&role=ADMINISTRATOR"
           "&state=APPROVED&count=50")
    d = _lire(url, ent)
    sortie = []
    for el in (d.get("elements") or []):
        urn = el.get("organization") or ""
        if not urn:
            continue
        ident = urn.rsplit(":", 1)[-1]
        nom = ""
        try:
            o = _lire(LI_API + "/v2/organizations/" + ident, ent)
            nom = o.get("localizedName") or o.get("vanityName") or ""
        except Exception:
            nom = ""
        sortie.append({"urn": urn, "nom": nom or ("Page #" + ident)})
    return sortie


@app.route("/api/linkedin-page/retour")
def api_li_page_retour():
    code = request.args.get("code")
    if not code:
        return _page("Connexion annulée",
                     "<h2>Aucune Page reliée</h2><p>L'autorisation a été refusée ou annulée.</p>")
    ids, _ = li_page_cles()
    donnees = {"code": code, "grant_type": "authorization_code",
               "redirect_uri": li_page_retour(),
               "client_id": ids.get("client_id"), "client_secret": ids.get("client_secret")}
    try:
        req = urllib.request.Request(
            LI_PAGE["jeton"], data=urllib.parse.urlencode(donnees).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded"})
        with urllib.request.urlopen(req, timeout=30) as rep:
            j = json.loads(rep.read().decode("utf-8"))
    except Exception as e:
        return _page("Échec", "<h2>LinkedIn a refusé l'échange</h2><pre>%s</pre>" % e)
    jeton = j.get("access_token")
    if not jeton:
        return _page("Échec", "<h2>Pas de jeton reçu</h2><pre>%s</pre>" % json.dumps(j)[:400])

    try:
        pages = _li_pages_administrees(jeton)
    except Exception as e:
        return _page("Échec", "<h2>Impossible de lire vos Pages</h2>"
                     "<p>LinkedIn a répondu : <code>%s</code></p>"
                     "<p>Le plus souvent, l'accès « Community Management API » n'est pas encore "
                     "validé pour cette application.</p>" % str(e)[:200])
    if not pages:
        return _page("Aucune Page",
                     "<h2>Aucune Page administrée</h2><p>Ce compte LinkedIn n'est "
                     "<b>administrateur</b> d'aucune Page entreprise. Demandez le rôle "
                     "administrateur sur la Page, puis recommencez.</p>")

    # Le jeton attend le choix : on ne le range pas encore dans `comptes`, sinon
    # une page à moitié choisie remplacerait une connexion qui marchait.
    session["li_page_jeton"] = jeton
    session["li_page_exp"] = str(j.get("expires_in") or "")
    session["li_page_liste"] = pages
    boutons = "".join(
        "<form method='post' action='/api/linkedin-page/choisir' style='margin:8px 0'>"
        "<input type='hidden' name='urn' value='%s'><input type='hidden' name='nom' value='%s'>"
        "<button style='font:inherit;cursor:pointer;background:#0a66c2;color:#fff;border:0;"
        "border-radius:10px;padding:12px 18px;width:100%%;text-align:left'>💼 %s</button></form>"
        % (p["urn"], p["nom"].replace("'", "&#39;"), p["nom"]) for p in pages)
    return _page("Choisissez la Page",
                 "<h2>Sur quelle Page publie-t-on ?</h2>"
                 "<p>Vous administrez %d Page(s). Choisissez celle qui recevra les publications.</p>%s"
                 % (len(pages), boutons))


@app.route("/api/linkedin-page/choisir", methods=["POST"])
def api_li_page_choisir():
    jeton = session.get("li_page_jeton")
    urn = (request.form.get("urn") or "").strip()
    nom = (request.form.get("nom") or "").strip()
    permis = {p["urn"] for p in (session.get("li_page_liste") or [])}
    if not jeton or urn not in permis:
        return _page("Expiré", "<h2>Le choix n'est plus valable</h2>"
                     "<p>Recommencez la connexion depuis « Connexions ».</p>")
    with db() as c:
        c.execute("INSERT INTO comptes(plateforme,nom,jeton,rafraichir,expire_le,extra,ajoute_le)"
                  " VALUES(?,?,?,?,?,?,?) ON CONFLICT(plateforme) DO UPDATE SET"
                  " nom=excluded.nom, jeton=excluded.jeton, rafraichir=excluded.rafraichir,"
                  " expire_le=excluded.expire_le, extra=excluded.extra, ajoute_le=excluded.ajoute_le",
                  ("linkedin", "Page : " + (nom or "entreprise"), jeton, None,
                   session.get("li_page_exp") or "",
                   json.dumps({"cible": "page", "urn": urn, "nom": nom}, ensure_ascii=False),
                   datetime.now().isoformat(timespec="seconds")))
    for k in ("li_page_jeton", "li_page_exp", "li_page_liste"):
        session.pop(k, None)
    return _page("Page reliée", "<h2>La Page « %s » est reliée</h2>"
                 "<p>Les publications LinkedIn partiront désormais au nom de cette Page.</p>" % nom)


@app.route("/api/linkedin-page/reglages", methods=["POST"])
def api_li_page_reglages():
    """Les identifiants de la SECONDE app LinkedIn (Community Management)."""
    d = request.get_json(force=True) or {}
    partage = bool(d.get("partage"))
    if partage:
        cfg = config()
        for cle in ("client_id", "client_secret"):
            v = (d.get(cle) or "").strip()
            if v:
                cfg.setdefault(LI_PAGE_ID, {})[cle] = v
        ecrire_config(cfg)
    else:
        with db() as c:
            for cle in ("client_id", "client_secret"):
                v = (d.get(cle) or "").strip()
                if v:
                    c.execute("INSERT INTO reglages(cle,valeur) VALUES(?,?) "
                              "ON CONFLICT(cle) DO UPDATE SET valeur=excluded.valeur",
                              (LI_PAGE_ID + "." + cle, v))
    return jsonify(ok=True, ou=("agence" if partage else "compte"))

@app.route("/api/reseaux/<pid>/delier", methods=["POST"])
def api_delier(pid):
    with db() as c:
        c.execute("DELETE FROM comptes WHERE plateforme=?", (pid,))
    return jsonify(ok=True)


@app.route("/api/publier/<int:pid>", methods=["POST"])
def api_publier(pid):
    """Publication réelle. Sans compte lié, on refuse clairement."""
    with db() as c:
        post = c.execute("SELECT * FROM posts WHERE id=?", (pid,)).fetchone()
        if not post:
            return jsonify(erreur="post introuvable"), 404
        compte = c.execute("SELECT * FROM comptes WHERE plateforme=?",
                           (post["reseau"],)).fetchone()
    if not compte:
        return jsonify(erreur="%s n'est pas connecté. Va dans « Connexions » pour lier le compte."
                       % M.RESEAUX[post["reseau"]]["nom"]), 409

    reseau = post["reseau"]
    depot = depots.DEPOTS.get(reseau)
    if not depot:
        return jsonify(erreur="Plateforme inconnue : %s" % reseau), 400

    # Le média sur le disque, et son adresse publique — Instagram et Facebook
    # vont chercher le fichier eux-mêmes, ils ne savent pas lire notre disque.
    chemin, url_pub = None, ""
    if post["media"]:
        candidat = os.path.join(MEDIAS, os.path.basename(post["media"]))
        if os.path.exists(candidat):
            chemin = candidat
            url_pub = BASE_PUB + "/medias/" + os.path.basename(post["media"])

    ids, _ = cles_reseau(reseau)
    try:
        url, souci = depot(dict(post), dict(compte), ids, chemin, url_pub)
    except Exception as e:                       # un réseau qui change son API
        url, souci = "", "erreur inattendue : %s" % str(e)[:200]

    with db() as c:
        if url:
            c.execute("UPDATE posts SET etat='publie', publie_le=?, url_publie=?, erreur='' "
                      "WHERE id=?", (datetime.now().isoformat(timespec="seconds"), url, pid))
        else:
            c.execute("UPDATE posts SET erreur=? WHERE id=?", (souci, pid))

    if url:
        return jsonify(ok=True, url=url, reseau=reseau)
    return jsonify(erreur=souci, reseau=reseau), 502


# ─────────────────────────────────────────────────────────────────────────────
# Le modèle de langage — facultatif, il affine, il ne remplace pas
# ─────────────────────────────────────────────────────────────────────────────
# Tous ces services parlent le même protocole que l'API OpenAI, sauf Anthropic
# qui a le sien. C'est ce qui permet d'en brancher un nouveau en changeant
# simplement l'adresse : rien à recoder.
FOURNISSEURS_LLM = [
    {"id": "google", "nom": "Google Gemini", "gratuit": True,
     "url": "https://generativelanguage.googleapis.com/v1beta/openai",
     "lien": "https://aistudio.google.com/apikey",
     "modeles": ["gemini-flash-lite-latest", "gemini-2.5-flash", "gemini-2.5-pro"],
     "mot": "Le meilleur français des offres gratuites."},
    {"id": "groq", "nom": "Groq", "gratuit": True,
     "url": "https://api.groq.com/openai/v1",
     "lien": "https://console.groq.com/keys",
     "modeles": ["llama-3.3-70b-versatile"],
     "mot": "Réponses quasi instantanées, quota large."},
    {"id": "cerebras", "nom": "Cerebras", "gratuit": True,
     "url": "https://api.cerebras.ai/v1",
     "lien": "https://cloud.cerebras.ai",
     "modeles": ["llama-3.3-70b", "qwen-3-235b-a22b"],
     "mot": "Le plus rapide du lot."},
    {"id": "zhipu", "nom": "Zhipu GLM (Chine)", "gratuit": True,
     "url": "https://open.bigmodel.cn/api/paas/v4",
     "lien": "https://open.bigmodel.cn/usercenter/apikeys",
     "modeles": ["glm-4-flash", "glm-4-plus"],
     "mot": "GLM-4-Flash est gratuit sans limite de durée."},
    {"id": "qwen", "nom": "Qwen / Alibaba (Chine)", "gratuit": True,
     "url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
     "lien": "https://bailian.console.alibabacloud.com",
     "modeles": ["qwen-plus", "qwen-turbo"],
     "mot": "Crédits d'essai offerts, puis très bon marché."},
    {"id": "mistral", "nom": "Mistral", "gratuit": True,
     "url": "https://api.mistral.ai/v1",
     "lien": "https://console.mistral.ai/api-keys",
     "modeles": ["mistral-large-latest", "mistral-small-latest"],
     "mot": "Français, hébergé en Europe."},
    {"id": "openrouter", "nom": "OpenRouter", "gratuit": True,
     "url": "https://openrouter.ai/api/v1",
     "lien": "https://openrouter.ai/keys",
     "modeles": ["deepseek/deepseek-chat-v3:free", "meta-llama/llama-3.3-70b-instruct:free"],
     "mot": "Une seule clé pour des dizaines de modèles ; ceux en « :free » ne coûtent rien."},
    {"id": "github", "nom": "GitHub Models", "gratuit": True,
     "url": "https://models.inference.ai.azure.com",
     "lien": "https://github.com/settings/tokens",
     "modeles": ["gpt-4o-mini", "Llama-3.3-70B-Instruct"],
     "mot": "Gratuit avec un simple compte GitHub."},
    {"id": "deepseek", "nom": "DeepSeek (Chine)", "gratuit": False,
     "url": "https://api.deepseek.com/v1",
     "lien": "https://platform.deepseek.com/api_keys",
     "modeles": ["deepseek-chat", "deepseek-reasoner"],
     "mot": "Le meilleur rapport qualité-prix du marché."},
    {"id": "moonshot", "nom": "Kimi / Moonshot (Chine)", "gratuit": False,
     "url": "https://api.moonshot.ai/v1",
     "lien": "https://platform.moonshot.ai/console/api-keys",
     "modeles": ["kimi-k2-0905-preview", "moonshot-v1-8k"],
     "mot": "Excellent sur les textes longs."},
    {"id": "openai", "nom": "OpenAI", "gratuit": False,
     "url": "https://api.openai.com/v1",
     "lien": "https://platform.openai.com/api-keys",
     "modeles": ["gpt-4o-mini", "gpt-4o"],
     "mot": "La référence, la plus chère à qualité égale."},
    {"id": "anthropic", "nom": "Anthropic (Claude)", "gratuit": False,
     "url": "https://api.anthropic.com/v1",
     "lien": "https://console.anthropic.com/settings/keys",
     "modeles": ["claude-sonnet-5", "claude-haiku-4-5-20251001"],
     "mot": "Le plus soigné en rédaction française."},
    {"id": "autre", "nom": "Autre (compatible OpenAI)", "gratuit": False,
     "url": "", "lien": "", "modeles": [],
     "mot": "Colle l'adresse de n'importe quel service compatible."},
]


def _fournisseur(fid):
    for f in FOURNISSEURS_LLM:
        if f["id"] == fid:
            return f
    return FOURNISSEURS_LLM[0]


def appel_llm(systeme, message, delai=60, ignorer_actif=False):
    """Renvoie (texte, souci). Ne lève jamais : l'écriture doit survivre à tout.

    `ignorer_actif` sert au bouton Tester : on essaie une clé AVANT de la mettre
    en service, sinon il faudrait activer à l'aveugle pour savoir si elle marche.
    """
    llm = (config().get("llm") or {})
    if not ignorer_actif and not llm.get("actif"):
        return "", ("la clé est enregistrée mais le modèle n'est pas activé — "
                    "coche « Laisser le modèle affiner »")
    if not (llm.get("api_key") or "").strip():
        return "", "aucune clé enregistrée"
    f = _fournisseur(llm.get("provider") or "google")
    base = (llm.get("base_url") or f["url"] or "").rstrip("/")
    modele = llm.get("model") or (f["modeles"][0] if f["modeles"] else "")
    cle = llm["api_key"].strip()
    if not base or not modele:
        return "", "adresse ou modèle manquant"
    try:
        if f["id"] == "anthropic":
            corps = {"model": modele, "max_tokens": 1200,
                     "system": systeme, "messages": [{"role": "user", "content": message}]}
            req = urllib.request.Request(
                base + "/messages", data=json.dumps(corps).encode(),
                headers={"x-api-key": cle, "anthropic-version": "2023-06-01",
                         "content-type": "application/json"})
            with urllib.request.urlopen(req, timeout=delai) as rep:
                return json.loads(rep.read().decode())["content"][0]["text"], ""
        corps = {"model": modele, "temperature": 0.7, "max_tokens": 1200,
                 "messages": [{"role": "system", "content": systeme},
                              {"role": "user", "content": message}]}
        req = urllib.request.Request(
            base + "/chat/completions", data=json.dumps(corps).encode(),
            headers={"Authorization": "Bearer " + cle, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=delai) as rep:
            return json.loads(rep.read().decode())["choices"][0]["message"]["content"], ""
    except urllib.error.HTTPError as e:
        return "", "%s %s" % (e.code, e.read().decode("utf-8", "ignore")[:180])
    except Exception as e:
        return "", str(e)[:180]


@app.route("/api/llm", methods=["GET", "POST"])
def api_llm():
    cfg = config()
    if request.method == "POST":
        d = request.get_json(force=True) or {}
        llm = cfg.setdefault("llm", {})
        for champ in ("provider", "model", "base_url"):
            if champ in d:
                llm[champ] = str(d[champ] or "").strip()
        if d.get("api_key") and d["api_key"] != "••••••••":
            llm["api_key"] = str(d["api_key"]).strip()
        if "actif" in d:
            llm["actif"] = bool(d["actif"])
        with open(CONFIG, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        return jsonify(ok=True)

    llm = cfg.get("llm") or {}
    f = _fournisseur(llm.get("provider") or "google")
    return jsonify(
        provider=llm.get("provider") or "google",
        model=llm.get("model") or (f["modeles"][0] if f["modeles"] else ""),
        base_url=llm.get("base_url") or f["url"],
        api_key="••••••••" if (llm.get("api_key") or "").strip() else "",
        actif=bool(llm.get("actif")),
        fournisseurs=FOURNISSEURS_LLM)


@app.route("/api/llm/test", methods=["POST"])
def api_llm_test():
    debut = time.time()
    txt, souci = appel_llm(
        "Tu es un rédacteur de contenus courts pour les réseaux sociaux. "
        "Réponds en français, en une seule phrase.",
        "Dis bonjour et annonce que tu es prêt à écrire.",
        ignorer_actif=True)
    return jsonify(ok=bool(txt), reponse=txt[:400], souci=souci,
                   duree_ms=int((time.time() - debut) * 1000))


# ─────────────────────────────────────────────────────────────────────────────
# L'avatar qui parle — on s'appuie sur le studio, on ne réimplémente rien
# ─────────────────────────────────────────────────────────────────────────────
def _studio(chemin, methode="GET", corps=None, delai=120):
    """Appel au studio avec la clé partagée. Rend (statut, objet)."""
    cle = ((config() or {}).get("studio") or {}).get("relay_key") or ""
    entetes = {"Content-Type": "application/json"}
    if cle:
        entetes["X-Studio-Key"] = cle
    donnees = json.dumps(corps).encode() if corps is not None else None
    req = urllib.request.Request(STUDIO_API + chemin, data=donnees,
                                 headers=entetes, method=methode)
    try:
        with urllib.request.urlopen(req, timeout=delai) as rep:
            return rep.status, json.loads(rep.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        brut = e.read().decode("utf-8", "ignore")
        try:
            return e.code, json.loads(brut)
        except ValueError:
            return e.code, {"error": brut[:200]}
    except Exception as e:
        return 0, {"error": str(e)[:200]}



# ══════════════════════════════════════════════════════════════════════════
#  LE VISUEL — une image, on l'anime, on la fait parler
#
#  Trois gestes, pas un de plus. Chaque utilisateur colle SA clé : elle est
#  rangée dans SON espace (table `reglages`), jamais partagée avec un autre
#  client, jamais dans le code.
#
#  fal.ai est mis en tête parce qu'une seule clé couvre les trois gestes et
#  que c'est de loin le moins cher : quelques centimes par image, quelques
#  dizaines de centimes par vidéo. HeyGen reste proposé pour « faire parler »,
#  c'est le plus soigné, mais c'est aussi le plus cher.
# ══════════════════════════════════════════════════════════════════════════
GESTES = ("image", "anime", "parle")

FOURNISSEURS_VISUEL = [
    {"id": "fal", "nom": "fal.ai", "gestes": ["image", "anime", "parle"],
     "lien": "https://fal.ai/dashboard/keys",
     "mot": "Une seule clé pour les trois. Le moins cher : ~0,003 $ l'image, ~0,20 $ la vidéo.",
     "prix": "€",
     "modeles": {"image": "fal-ai/flux/schnell",
                 "anime": "fal-ai/wan-i2v",
                 "parle": "fal-ai/sadtalker"}},
    {"id": "replicate", "nom": "Replicate", "gestes": ["image", "anime", "parle"],
     "lien": "https://replicate.com/account/api-tokens",
     "mot": "Une seule clé aussi, catalogue plus large, un peu plus cher que fal.",
     "prix": "€€",
     "modeles": {"image": "black-forest-labs/flux-schnell",
                 "anime": "wan-video/wan-2.2-i2v-fast",
                 "parle": "cjwbw/sadtalker"}},
    {"id": "minimax", "nom": "MiniMax / Hailuo", "gestes": ["image", "anime"],
     "lien": "https://platform.minimax.io/user-center/basic-information/interface-key",
     "mot": "Le meilleur mouvement du lot pour animer une image. Pas de lip-sync.",
     "prix": "€€",
     "modeles": {"image": "image-01", "anime": "MiniMax-Hailuo-02"}},
    {"id": "google", "nom": "Google Gemini", "gestes": ["image"],
     "lien": "https://aistudio.google.com/apikey",
     "mot": "Gratuit pour générer des images. N'anime pas et ne fait pas parler.",
     "prix": "gratuit",
     "modeles": {"image": "gemini-2.5-flash-image"}},
    {"id": "heygen", "nom": "HeyGen", "gestes": ["parle"],
     "lien": "https://app.heygen.com/settings/api",
     "mot": "Le plus soigné pour faire parler un visage, et le plus cher.",
     "prix": "€€€",
     "modeles": {"parle": "photo-avatar"}},
]


def _visuel_reglages():
    """Le fournisseur et la clé de CE compte client, geste par geste."""
    out = {}
    with db() as c:
        lignes = {l["cle"]: l["valeur"] for l in c.execute(
            "SELECT cle, valeur FROM reglages WHERE cle LIKE 'visuel.%'")}
    for g in GESTES:
        out[g] = {"fournisseur": lignes.get("visuel.%s.fournisseur" % g) or "",
                  "cle": lignes.get("visuel.%s.cle" % g) or ""}
    return out


def _fournisseur_visuel(fid):
    for f in FOURNISSEURS_VISUEL:
        if f["id"] == fid:
            return f
    return None


@app.route("/api/visuel", methods=["GET", "POST"])
def api_visuel():
    if request.method == "POST":
        d = request.get_json(force=True) or {}
        with db() as c:
            for g in GESTES:
                bloc = d.get(g)
                if not isinstance(bloc, dict):
                    continue
                if "fournisseur" in bloc:
                    c.execute("INSERT OR REPLACE INTO reglages (cle, valeur) VALUES (?,?)",
                              ("visuel.%s.fournisseur" % g, (bloc["fournisseur"] or "").strip()))
                # Un champ vide n'efface pas la clé déjà posée : on ne perd pas
                # un secret par une sauvegarde distraite.
                v = (bloc.get("cle") or "").strip()
                if v and not v.startswith("•"):
                    c.execute("INSERT OR REPLACE INTO reglages (cle, valeur) VALUES (?,?)",
                              ("visuel.%s.cle" % g, v))
            c.commit()
        return jsonify(ok=True)

    r = _visuel_reglages()
    return jsonify(
        gestes={g: {"fournisseur": r[g]["fournisseur"],
                    "cle": "••••••••" if r[g]["cle"] else "",
                    "pret": bool(r[g]["fournisseur"] and r[g]["cle"])} for g in GESTES},
        fournisseurs=FOURNISSEURS_VISUEL,
        # « Faire parler » marche déjà sans clé : le studio de l'agence est branché.
        studio_dispo=bool(((config() or {}).get("studio") or {}).get("relay_key")))



def _http(url, methode="GET", corps=None, entetes=None, delai=180):
    """Un appel HTTP qui ne lève jamais : rend (statut, objet)."""
    donnees = json.dumps(corps).encode() if corps is not None else None
    req = urllib.request.Request(url, data=donnees, method=methode,
                                 headers={"Content-Type": "application/json", **(entetes or {})})
    try:
        with urllib.request.urlopen(req, timeout=delai) as rep:
            brut = rep.read().decode("utf-8", "ignore")
            try:
                return rep.status, json.loads(brut or "{}")
            except ValueError:
                return rep.status, {"brut": brut}
    except urllib.error.HTTPError as e:
        brut = e.read().decode("utf-8", "ignore")
        try:
            return e.code, json.loads(brut or "{}")
        except ValueError:
            return e.code, {"erreur": brut[:300]}
    except Exception as e:
        return 0, {"erreur": str(e)[:200]}


def _ranger_media(url, extension):
    """Rapatrie le fichier chez nous : l'adresse du fournisseur expire, pas la nôtre."""
    nom = "%s_%s%s" % (int(time.time()), secrets.token_hex(4), extension)
    chemin = os.path.join(MEDIAS, nom)
    os.makedirs(os.path.dirname(chemin), exist_ok=True)
    try:
        with urllib.request.urlopen(url, timeout=180) as r, open(chemin, "wb") as f:
            f.write(r.read())
    except Exception as e:
        return None, str(e)[:150]
    return nom, None


# ── Les travaux en cours, en mémoire du processus. Un identifiant, un état.
_TRAVAUX = {}


def _reglage_geste(geste):
    r = _visuel_reglages()[geste]
    f = _fournisseur_visuel(r["fournisseur"])
    return r, f


@app.route("/api/visuel/<geste>", methods=["POST"])
def api_visuel_faire(geste):
    if geste not in GESTES:
        return jsonify(erreur="geste inconnu"), 404
    d = request.get_json(force=True) or {}
    r, f = _reglage_geste(geste)

    # « Faire parler » sans clé : on passe par le studio de l'agence, comme avant.
    if geste == "parle" and (not f or not r["cle"]):
        image, texte = (d.get("image") or "").strip(), (d.get("texte") or "").strip()
        if not image.startswith("data:image"):
            return jsonify(erreur="Choisis d'abord une photo de visage."), 400
        if not texte:
            return jsonify(erreur="Il n'y a pas de texte à faire dire."), 400
        st, rep = _studio("/api/heygen/animate", "POST", {
            "image": image, "mode": "parle", "script": texte[:1500],
            "format": d.get("format") or "9:16", "resolution": "720p"})
        if st == 200 and rep.get("video_id"):
            return jsonify(id="studio:" + rep["video_id"], statut="en cours")
        return jsonify(erreur=(rep.get("error") or "le studio a refusé")), 502

    if not f:
        return jsonify(erreur="Choisis d'abord un fournisseur pour ce geste."), 400
    if not r["cle"]:
        return jsonify(erreur="Colle ta clé %s pour t'en servir." % f["nom"]), 400
    if geste not in f["gestes"]:
        return jsonify(erreur="%s ne sait pas faire ça." % f["nom"]), 400

    if f["id"] == "fal":
        return _fal(geste, f, r["cle"], d)
    return jsonify(erreur="%s n'est pas encore branché pour ce geste — "
                          "fal.ai l'est, et couvre les trois." % f["nom"]), 501


def _fal(geste, f, cle, d):
    """fal.ai : on dépose le travail dans la file, on rend son identifiant.

    L'API répond tout de suite avec un `request_id` ; c'est /api/visuel/etat
    qui ira voir où il en est. Une génération vidéo prend une à trois minutes,
    on ne tient pas une requête HTTP ouverte aussi longtemps.
    """
    modele = f["modeles"][geste]
    entetes = {"Authorization": "Key " + cle}
    if geste == "image":
        corps = {"prompt": (d.get("prompt") or "").strip()[:1200],
                 "image_size": d.get("format") == "1:1" and "square_hd" or "portrait_16_9"}
        if not corps["prompt"]:
            return jsonify(erreur="Décris l'image que tu veux."), 400
    elif geste == "anime":
        if not (d.get("image") or "").strip():
            return jsonify(erreur="Il faut une image de départ."), 400
        corps = {"image_url": d["image"], "prompt": (d.get("prompt") or "").strip()[:600]}
    else:
        if not (d.get("image") or "").strip():
            return jsonify(erreur="Il faut une image de départ."), 400
        corps = {"source_image_url": d["image"], "driven_audio_url": d.get("audio") or ""}
        if not corps["driven_audio_url"]:
            return jsonify(erreur="Ce fournisseur a besoin d'un fichier audio. "
                                  "Sans clé, « faire parler » passe par le studio."), 400

    st, rep = _http("https://queue.fal.run/" + modele, "POST", corps, entetes, delai=60)
    ident = rep.get("request_id")
    if st not in (200, 201) or not ident:
        return jsonify(erreur=_message_fal(st, rep)), 502
    _TRAVAUX[ident] = {"modele": modele, "cle": cle, "geste": geste, "ne_le": time.time()}
    return jsonify(id="fal:" + ident, statut="en cours")


def _message_fal(st, rep):
    if st == 401:
        return "Clé fal.ai refusée. Vérifie qu'elle est bien copiée en entier."
    if st == 402:
        return "Crédit fal.ai épuisé — recharge ton compte."
    d = rep.get("detail") or rep.get("erreur") or rep.get("error")
    return "fal.ai a refusé : %s" % (json.dumps(d)[:200] if d else "réponse inattendue")


@app.route("/api/visuel/etat")
def api_visuel_etat():
    ident = (request.args.get("id") or "").strip()
    if ident.startswith("studio:"):
        return api_avatar_etat_interne(ident.split(":", 1)[1])
    if not ident.startswith("fal:"):
        return jsonify(erreur="identifiant inconnu"), 400
    rid = ident.split(":", 1)[1]
    t = _TRAVAUX.get(rid)
    if not t:
        return jsonify(erreur="travail inconnu — relance la génération"), 404

    entetes = {"Authorization": "Key " + t["cle"]}
    base = "https://queue.fal.run/%s/requests/%s" % (t["modele"], rid)
    st, rep = _http(base + "/status", entetes=entetes, delai=30)
    etat = (rep.get("status") or "").upper()
    if etat in ("IN_QUEUE", "IN_PROGRESS"):
        return jsonify(statut="en cours")
    if st != 200 or etat not in ("COMPLETED", "OK"):
        return jsonify(erreur=_message_fal(st, rep)), 502

    st, rep = _http(base, entetes=entetes, delai=60)
    url = ""
    for champ in ("images", "video", "image", "output"):
        v = rep.get(champ)
        if isinstance(v, list) and v and isinstance(v[0], dict):
            url = v[0].get("url") or ""
        elif isinstance(v, dict):
            url = v.get("url") or ""
        elif isinstance(v, str) and v.startswith("http"):
            url = v
        if url:
            break
    if not url:
        return jsonify(erreur="le fournisseur n'a rendu aucun fichier"), 502
    ext = ".mp4" if t["geste"] in ("anime", "parle") else ".png"
    nom, souci = _ranger_media(url, ext)
    if not nom:
        return jsonify(erreur="téléchargement impossible : " + (souci or "")), 502
    return jsonify(statut="pret", media=nom, url="/medias/" + nom)


@app.route("/api/jumeau/<path:reste>", methods=["GET", "POST"])
def api_jumeau(reste):
    """Passe-plat vers le studio de photos, avec DEUX garanties :
    le compte est celui de l'espace ouvert (jamais celui que la page prétend),
    et la clé de relais reste côté serveur."""
    espace = espace_courant()["id"]
    if request.method == "GET":
        args = dict(request.args)
        args["compte"] = espace
        chemin = "/api/photos/" + reste + "?" + urllib.parse.urlencode(args)
        st, rep_ = _studio(chemin, "GET", None, delai=120)
    else:
        corps = request.get_json(force=True, silent=True) or {}
        corps["compte"] = espace          # on écrase : c'est le serveur qui décide
        st, rep_ = _studio("/api/photos/" + reste, "POST", corps, delai=300)
    if st == 0:
        return jsonify(erreur=rep_.get("error") or "studio injoignable"), 502
    return jsonify(rep_), (200 if st == 200 else st)


@app.route("/api/avatar", methods=["POST"])
def api_avatar():
    """Lance l'animation d'une photo qui dit le scénario."""
    d = request.get_json(force=True) or {}
    image = (d.get("image") or "").strip()
    texte = (d.get("texte") or "").strip()
    if not image.startswith("data:image"):
        return jsonify(erreur="Choisis d'abord une photo de visage."), 400
    if not texte:
        return jsonify(erreur="Il n'y a pas de texte à faire dire."), 400
    st, rep = _studio("/api/heygen/animate", "POST", {
        "image": image, "mode": "parle", "script": texte[:1500],
        "format": d.get("format") or "9:16", "resolution": "720p"})
    if st == 200 and rep.get("video_id"):
        return jsonify(id=rep["video_id"], statut=rep.get("statut") or "en cours")
    return jsonify(erreur=(rep.get("error") or "le studio a refusé")), 502


def api_avatar_etat_interne(vid):
    """Le corps de /api/avatar/etat, appelable aussi depuis le nouveau connecteur."""
    return _avatar_etat(vid)


@app.route("/api/avatar/etat")
def api_avatar_etat():
    """Où en est l'animation ? Quand elle est prête, on rapatrie le fichier :
    l'adresse de HeyGen expire, celle de nos médias non."""
    return _avatar_etat((request.args.get("id") or "").strip())


def _avatar_etat(vid):
    if not vid:
        return jsonify(erreur="identifiant manquant"), 400
    st, rep = _studio("/api/heygen/status?id=" + urllib.parse.quote(vid))
    if st != 200:
        return jsonify(erreur=(rep.get("error") or "état indisponible")), 502
    statut = (rep.get("statut") or "").lower()
    if statut not in ("completed", "success", "done") or not rep.get("url"):
        return jsonify(statut=statut or "en cours")

    nom = "avatar_%d_%s.mp4" % (int(time.time()), secrets.token_hex(3))
    try:
        with urllib.request.urlopen(rep["url"], timeout=600) as flux:
            with open(os.path.join(MEDIAS, nom), "wb") as f:
                f.write(flux.read())
    except Exception as e:
        return jsonify(erreur="téléchargement impossible : %s" % str(e)[:150]), 502
    return jsonify(statut="pret", media=nom, url="/medias/" + nom,
                   duree=rep.get("duree"))


# ─────────────────────────────────────────────────────────────────────────────
# Les comptes clients — un atelier, plusieurs enseignes
# ─────────────────────────────────────────────────────────────────────────────
def _sillon(nom):
    """Un identifiant de fichier lisible et sans surprise."""
    base = re.sub(r"[^a-z0-9]+", "-", (nom or "").lower().strip()).strip("-")[:28]
    return base or "compte"


@app.route("/api/espaces")
def api_espaces():
    liste = espaces()
    courant = espace_courant()["id"]
    sortie = []
    for e in liste:
        chemin = os.path.join(DONNEES, e["fichier"])
        n = f = 0
        try:
            c = sqlite3.connect(chemin, timeout=10)
            n = c.execute("SELECT COUNT(*) FROM posts WHERE etat='publie'").fetchone()[0]
            f = c.execute("SELECT COUNT(*) FROM posts WHERE etat IN "
                          "('brouillon','prevu')").fetchone()[0]
            m = c.execute("SELECT valeur FROM profil WHERE cle='metier'").fetchone()
            e = dict(e, metier=(m[0] if m and m[0] else ""))
            c.close()
        except Exception:
            e = dict(e, metier="")
        sortie.append(dict(e, publies=n, en_file=f, courant=(e["id"] == courant)))
    return jsonify(espaces=sortie, courant=courant)


@app.route("/api/espaces", methods=["POST"])
def api_espace_creer():
    d = request.get_json(force=True) or {}
    nom = (d.get("nom") or "").strip()
    if not nom:
        return jsonify(erreur="Donne un nom a ce compte."), 400
    liste = espaces()
    if len(liste) >= 40:
        return jsonify(erreur="Quarante comptes, c'est deja beaucoup."), 400
    pris = {e["id"] for e in liste}
    eid = _sillon(nom)
    while eid in pris:
        eid = _sillon(nom) + "-" + secrets.token_hex(2)
    e = {"id": eid, "nom": nom[:60], "fichier": "pe_%s.db" % eid,
         "cree_le": maintenant()}
    # On cree la base tout de suite : un compte vide vaut mieux qu'un compte
    # qui n'existe qu'a moitie.
    c = sqlite3.connect(os.path.join(DONNEES, e["fichier"]), timeout=20)
    creer_tables(c)
    metier = (d.get("metier") or "").strip()
    if metier:
        with c:
            c.execute("INSERT INTO profil(cle,valeur) VALUES('metier',?) "
                      "ON CONFLICT(cle) DO UPDATE SET valeur=excluded.valeur", (metier,))
    c.close()
    liste.append(e)
    ecrire_espaces(liste)
    rep = jsonify(ok=True, espace=e)
    rep.set_cookie("pe_espace", eid, max_age=60 * 60 * 24 * 365, samesite="Lax")
    return rep


@app.route("/api/espaces/<eid>/choisir", methods=["POST"])
def api_espace_choisir(eid):
    if not any(e["id"] == eid for e in espaces()):
        return jsonify(erreur="Ce compte n'existe pas."), 404
    rep = jsonify(ok=True)
    rep.set_cookie("pe_espace", eid, max_age=60 * 60 * 24 * 365, samesite="Lax")
    return rep


@app.route("/api/espaces/<eid>", methods=["PATCH", "DELETE"])
def api_espace_modifier(eid):
    liste = espaces()
    e = next((x for x in liste if x["id"] == eid), None)
    if not e:
        return jsonify(erreur="Ce compte n'existe pas."), 404
    if request.method == "DELETE":
        if len(liste) <= 1:
            return jsonify(erreur="C'est le dernier compte : on le garde."), 400
        # La base n'est pas effacee, seulement mise de cote : on peut se
        # tromper de bouton, on ne peut pas revenir sur un fichier detruit.
        chemin = os.path.join(DONNEES, e["fichier"])
        if os.path.exists(chemin):
            os.rename(chemin, chemin + ".retire_%d" % int(time.time()))
        liste = [x for x in liste if x["id"] != eid]
        ecrire_espaces(liste)
        rep = jsonify(ok=True)
        rep.set_cookie("pe_espace", liste[0]["id"], max_age=60 * 60 * 24 * 365,
                       samesite="Lax")
        return rep
    d = request.get_json(force=True) or {}
    nom = (d.get("nom") or "").strip()
    if nom:
        e["nom"] = nom[:60]
        ecrire_espaces(liste)
    return jsonify(ok=True, espace=e)



# ─────────────────────────────────────────────────────────────────────────────
# L'agenda : quatorze jours, un rendez-vous a la fois
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/api/cadence")
def api_cadence():
    """Le rythme conseille, reseau par reseau."""
    return jsonify(cadence={r: M.cadence(r) for r in M.ORDRE_RESEAUX},
                   formats=list(M.LES_TROIS))


JOURS_SEM = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")
NOMS_MOIS = ("janvier", "février", "mars", "avril", "mai", "juin", "juillet",
             "août", "septembre", "octobre", "novembre", "décembre")


def _grille_mois(mois):
    """Les six semaines qui couvrent ce mois, du lundi au dimanche.

    Un calendrier commence toujours un lundi : on remonte au lundi qui precede
    le 1er, et on descend jusqu'au dimanche qui suit le dernier jour.
    """
    try:
        an, mo = [int(x) for x in (mois or "").split("-")[:2]]
        premier = date(an, mo, 1)
    except Exception:
        auj = date.today()
        premier = date(auj.year, auj.month, 1)
    dernier = date(premier.year + (premier.month == 12),
                   premier.month % 12 + 1, 1) - timedelta(days=1)
    depart = premier - timedelta(days=premier.weekday())
    arrivee = dernier + timedelta(days=6 - dernier.weekday())
    n = (arrivee - depart).days + 1
    return premier, [depart + timedelta(days=i) for i in range(n)]


@app.route("/api/agenda")
def api_agenda():
    """Le mois demandé en grille, ou les prochains jours en liste."""
    mois = request.args.get("mois")
    if mois:
        premier, grille = _grille_mois(mois)
        debut = datetime.combine(grille[0], datetime.min.time())
        fin = datetime.combine(grille[-1] + timedelta(days=1), datetime.min.time())
        jours = len(grille)
    else:
        premier, grille = None, None
        jours = max(3, min(42, int(request.args.get("jours", 14))))
        debut = datetime.now()
        fin = debut + timedelta(days=jours)

    with db() as c:
        cales = [dict(r) for r in c.execute(
            "SELECT id, reseau, mode, titre, etat, prevu_le, publie_le, media "
            "FROM posts WHERE etat IN ('prevu','publie') AND prevu_le IS NOT NULL "
            "AND prevu_le != '' ORDER BY prevu_le")]
        attente = [dict(r) for r in c.execute(
            "SELECT id, reseau, mode, titre, etat, media FROM posts "
            "WHERE etat='brouillon' ORDER BY cree_le DESC")]

    pris = set()
    for p in cales:
        pris.add((p["reseau"], (p["prevu_le"] or "")[:16]))

    # On ne propose pas tous les creneaux possibles : on propose ce qui manque
    # cette semaine pour tenir la cadence, et rien de plus. Vingt propositions
    # par jour, ce n'est plus un conseil, c'est du bruit.
    libres = []
    for r in M.ORDRE_RESEAUX:
        cad = M.CADENCE.get(r) or {}
        quota = cad.get("total", 0)
        if not quota:
            continue
        # Les modes a couvrir, du plus important au moins important.
        besoins = [m for m, (n, _) in sorted(cad["formats"].items(),
                                             key=lambda kv: -kv[1][0]) for _ in range(n)]
        # On ne propose des creneaux que pour la semaine qui vient. Au-dela,
        # l'agenda ne montre plus que ce qui est reellement cale : quatorze
        # jours de pointilles, ce n'est plus un plan, c'est un reproche.
        for semaine in range(0, min(jours, 7), 7):
            borne = (debut + timedelta(days=semaine)), (debut + timedelta(days=min(semaine + 7, jours)))
            deja = sum(1 for p in cales if p["reseau"] == r
                       and borne[0].isoformat() <= (p["prevu_le"] or "") < borne[1].isoformat())
            manque = max(0, quota - deja)
            if not manque:
                continue
            k = 0
            for quand in M.creneaux(r, borne[0], combien=quota + 4):
                if quand >= borne[1].isoformat(timespec="minutes"):
                    break
                if (r, quand) in pris:
                    continue
                libres.append({"reseau": r, "quand": quand,
                               "mode": besoins[k % len(besoins)] if besoins else "parle"})
                k += 1
                if k >= manque:
                    break

    # Un jour = une ligne. On assemble ici plutot que dans le navigateur :
    # la logique de dates se teste, celle du navigateur beaucoup moins.
    par_jour = {}
    for p in cales:
        par_jour.setdefault((p["prevu_le"] or "")[:10], {"cales": [], "libres": []})["cales"].append(p)
    for l in libres:
        par_jour.setdefault(l["quand"][:10], {"cales": [], "libres": []})["libres"].append(l)

    auj = date.today()
    dates = grille if grille else [(debut + timedelta(days=j)).date() for j in range(jours)]
    sortie = []
    for d in dates:
        cle = d.isoformat()
        bloc = par_jour.get(cle) or {"cales": [], "libres": []}
        bloc["cales"].sort(key=lambda x: x.get("prevu_le") or "")
        bloc["libres"].sort(key=lambda x: x["quand"])
        sortie.append({"date": cle, "jour": M.JOURS_NOM[d.weekday()],
                       "numero": d.day, "aujourdhui": d == auj, "passe": d < auj,
                       "weekend": d.weekday() >= 5,
                       "hors_mois": bool(premier) and d.month != premier.month,
                       "cales": bloc["cales"], "libres": bloc["libres"]})
    rep = {"jours": sortie, "en_attente": attente,
           "cadence": {r: M.cadence(r) for r in M.ORDRE_RESEAUX}}
    if premier:
        prec = premier - timedelta(days=1)
        suiv = date(premier.year + (premier.month == 12), premier.month % 12 + 1, 1)
        rep.update(mois="%04d-%02d" % (premier.year, premier.month),
                   titre="%s %d" % (NOMS_MOIS[premier.month - 1], premier.year),
                   precedent="%04d-%02d" % (prec.year, prec.month),
                   suivant="%04d-%02d" % (suiv.year, suiv.month),
                   semaine=[j[:3] for j in JOURS_SEM])
    return jsonify(**rep)


@app.route("/api/agenda/caler", methods=["POST"])
def api_agenda_caler():
    """Pose un post en attente sur un creneau, ou deplace un post deja cale."""
    d = request.get_json(force=True) or {}
    pid = d.get("id")
    quand = (d.get("quand") or "").strip()
    if not pid:
        return jsonify(erreur="quel post ?"), 400
    with db() as c:
        post = c.execute("SELECT * FROM posts WHERE id=?", (pid,)).fetchone()
        if not post:
            return jsonify(erreur="ce post n'existe plus"), 404
        if post["etat"] == "publie":
            return jsonify(erreur="ce post est déjà parti."), 400
        if not quand:
            # Retirer du calendrier, sans rien perdre : il repasse en attente.
            c.execute("UPDATE posts SET etat='brouillon', prevu_le=NULL WHERE id=?", (pid,))
            return jsonify(ok=True, etat="brouillon")
        c.execute("UPDATE posts SET etat='prevu', prevu_le=? WHERE id=?", (quand, pid))
    return jsonify(ok=True, etat="prevu", prevu_le=quand)


@app.route("/api/sante")
def sante():
    with db() as c:
        n = c.execute("SELECT COUNT(*) n FROM posts").fetchone()["n"]
    return jsonify(ok=True, posts=n, profil_complet=M.profil_complet(profil()))


if __name__ == "__main__":
    app.run("127.0.0.1", 5010, debug=False)
