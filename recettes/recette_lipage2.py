# -*- coding: utf-8 -*-
"""Le chemin complet, sans app LinkedIn réelle : on vérifie NOTRE moitié."""
import sys, json, sqlite3, os
sys.path.insert(0, "/docker/posteveryday")
os.chdir("/docker/posteveryday")
import app as A, depots

ok = 0
def v(c, q):
    global ok
    assert c, "✗ " + q
    ok += 1; print("  ✓", q)

cli = A.app.test_client()
with cli.session_transaction() as s:
    s["ok"] = True; s["qui"] = "mohamed"

print("\n1) Déclarer l'application « Page »")
r = cli.post("/api/linkedin-page/reglages", json={"client_id": "ZZRECETTE_ID", "client_secret": "ZZRECETTE_SECRET"})
v(r.get_json().get("ok"), "les identifiants de la seconde app s'enregistrent")
ids, ou = A.li_page_cles()
v(ids.get("client_id") == "ZZRECETTE_ID" and ids.get("client_secret") == "ZZRECETTE_SECRET",
  "ils sont relus depuis le compte (%s)" % ou)
v(A.config().get("linkedin_page") is None, "et ils ne sont PAS partis dans la config de l'agence")

print("\n2) Le départ chez LinkedIn")
r = cli.get("/api/linkedin-page/lier")
loc = r.headers.get("Location", "")
v(r.status_code == 302, "on part bien chez LinkedIn")
v("linkedin.com/oauth/v2/authorization" in loc, "sur le bon dialogue")
for sc in ("r_organization_social", "w_organization_social", "rw_organization_admin"):
    v(sc in loc, "le scope %s est demandé" % sc)
v("client_id=ZZRECETTE_ID" in loc, "avec l'ID de l'app PAGE, pas celui de l'app profil")
v("%2Flinkedin-page%2Fretour" in loc, "et l'adresse de retour de la Page")

print("\n3) On ne range pas une Page à moitié choisie")
r = cli.post("/api/linkedin-page/choisir", data={"urn": "urn:li:organization:999", "nom": "Fausse"})
v("n'est plus valable" in r.get_data(as_text=True), "un choix sans autorisation en cours est refusé")
c = sqlite3.connect("pe.db")
v(c.execute("SELECT COUNT(*) FROM comptes WHERE plateforme='linkedin'").fetchone()[0] == 0,
  "aucun compte LinkedIn n'a été créé au passage")

print("\n4) Le choix d'une Page, une fois l'autorisation faite")
with cli.session_transaction() as s:
    s["li_page_jeton"] = "ZZ_JETON_PAGE"
    s["li_page_exp"] = "5184000"
    s["li_page_liste"] = [{"urn": "urn:li:organization:12345", "nom": "Konig IA"}]
r = cli.post("/api/linkedin-page/choisir", data={"urn": "urn:li:organization:12345", "nom": "Konig IA"})
v("Konig IA" in r.get_data(as_text=True), "la page choisie est confirmée à l'écran")
cible = A.li_cible()
v(cible.get("cible") == "page", "la cible enregistrée est bien une Page")
v(cible.get("urn") == "urn:li:organization:12345", "avec son URN d'organisation")
v(cible.get("nom") == "Konig IA", "et son nom lisible")

print("\n5) La publication part au nom de la Page")
appels = {}
def faux_http(url, methode="GET", entetes=None, jsonc=None, corps=None, delai=60):
    appels[methode + " " + url.split("?")[0]] = jsonc
    if "/v2/userinfo" in url:
        return 200, {"sub": "NE_DOIT_PAS_SERVIR"}
    if "/v2/ugcPosts" in url:
        return 201, {"id": "urn:li:share:987"}
    return 200, {}
vrai = depots._http
depots._http = faux_http
compte = {"jeton": "ZZ_JETON_PAGE",
          "extra": json.dumps({"cible": "page", "urn": "urn:li:organization:12345", "nom": "Konig IA"})}
url, souci = depots.depot_linkedin({"legende": "Bonjour la Page"}, compte, {}, None, "")
v(not souci, "la publication passe : " + (souci or url))
corps = appels.get("POST https://api.linkedin.com/v2/ugcPosts")
v(corps["author"] == "urn:li:organization:12345", "author = l'URN de la Page, pas une personne")
v("GET https://api.linkedin.com/v2/userinfo" not in appels,
  "userinfo n'est PAS appelé pour une Page (un jeton de Page n'y répond pas)")

# Et le profil continue de marcher exactement comme avant.
appels.clear()
url2, souci2 = depots.depot_linkedin({"legende": "Bonjour le profil"},
                                     {"jeton": "J", "extra": '""'}, {}, None, "")
v(appels.get("POST https://api.linkedin.com/v2/ugcPosts")["author"] == "urn:li:person:NE_DOIT_PAS_SERVIR",
  "le profil, lui, passe toujours par userinfo — rien n'a changé pour lui")
depots._http = vrai

print("\n6) Ménage : on rend l'installation comme on l'a trouvée")
c.execute("DELETE FROM comptes WHERE plateforme='linkedin'")
c.execute("DELETE FROM reglages WHERE cle LIKE 'linkedin_page.%'")
c.commit()
v(c.execute("SELECT COUNT(*) FROM reglages WHERE cle LIKE 'linkedin_page.%'").fetchone()[0] == 0,
  "clés de recette retirées")
v(A.li_cible() == {}, "plus aucune cible LinkedIn enregistrée")
c.close()
print("\n%d vérifications ✓" % ok)
