# -*- coding: utf-8 -*-
"""Les fichiers de vérification de domaine — et le retour OAuth de la Page."""
import io, ast, os
C = "/docker/posteveryday/app.py"
s = io.open(C, encoding="utf-8").read()

# 1. ⚠️ Correctif : le retour de la Page LinkedIn était derrière la porte.
old = '''LIBRES = ("/connexion", "/deconnexion", "/guide", "/tuto", "/confidentialite",
          "/conditions", "/api/sante", "/api/reseaux/", "/medias/", "/favicon")'''
new = '''LIBRES = ("/connexion", "/deconnexion", "/guide", "/tuto", "/confidentialite",
          "/conditions", "/api/sante", "/api/reseaux/", "/medias/", "/favicon",
          # Le retour de LinkedIn arrive comme les autres : avant toute
          # reconnaissance. ⚠️ On n'ouvre QUE le retour — « /reglages » pose
          # les identifiants de l'application, il reste derrière la porte.
          "/api/linkedin-page/retour",
          # Les plateformes viennent lire un fichier a la racine du domaine
          # pour prouver qu'il nous appartient. Redirige vers /connexion, et
          # la verification echoue sans jamais dire pourquoi.
          "/verifications")'''
assert old in s, "ancre LIBRES"
s = s.replace(old, new, 1)

BLOC = '''

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
    if not re.fullmatch(r"(tiktok[A-Za-z0-9]{4,64}\\.txt|google[a-f0-9]{8,32}\\.html"
                        r"|[A-Za-z0-9_-]{1,64}\\.(txt|html))", nom):
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
        f.write(contenu + "\\n")
    return jsonify(ok=True, nom=nom, url=BASE_PUB + "/" + nom)


@app.route("/api/verifications/<nom>", methods=["DELETE"])
def api_verifications_retirer(nom):
    f = _verif_nom(nom)
    chemin = os.path.join(VERIFS, f) if f else ""
    if chemin and os.path.exists(chemin):
        os.remove(chemin)
        return jsonify(ok=True)
    return jsonify(erreur="fichier inconnu"), 404

'''
ancre = "\n\n@app.route(\"/api/appkeys\", methods=[\"POST\"])"
assert ancre in s
s = s.replace(ancre, BLOC + ancre.lstrip("\n"), 1)

ast.parse(s)
io.open(C, "w", encoding="utf-8").write(s)
print("vérification de domaine + retour LinkedIn libérés")
