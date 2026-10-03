# -*- coding: utf-8 -*-
"""Le dépôt réel d'un post sur chaque réseau.

Cinq plateformes, cinq protocoles qui n'ont rien en commun :
  · YouTube   — envoi « reprenable » en deux temps (métadonnées puis octets)
  · Instagram — conteneur puis publication, et le média doit être joignable par une URL publique
  · Facebook  — un jeton de PAGE, différent du jeton personnel obtenu à la connexion
  · LinkedIn  — enregistrement de l'envoi, dépôt binaire, puis création du post
  · TikTok    — initialisation qui rend une adresse d'envoi, puis dépôt des octets

Aucune bibliothèque tierce : `urllib` seulement, comme le reste de l'application.
Chaque fonction renvoie (url_publiee, erreur). L'une des deux est toujours vide.
"""
import json
import mimetypes
import os
import urllib.error
import urllib.parse
import urllib.request


# ─────────────────────────────────────────────────────────────────────────────
# Petits outils réseau
# ─────────────────────────────────────────────────────────────────────────────
def _http(url, methode="GET", entetes=None, corps=None, form=None, jsonc=None, delai=180):
    """Un appel HTTP qui rend (statut, objet_ou_texte). Ne lève jamais."""
    e = dict(entetes or {})
    donnees = None
    if jsonc is not None:
        donnees = json.dumps(jsonc).encode("utf-8")
        e.setdefault("Content-Type", "application/json")
    elif form is not None:
        donnees = urllib.parse.urlencode(form).encode()
        e.setdefault("Content-Type", "application/x-www-form-urlencoded")
    elif corps is not None:
        donnees = corps
    req = urllib.request.Request(url, data=donnees, headers=e, method=methode)
    try:
        with urllib.request.urlopen(req, timeout=delai) as rep:
            brut = rep.read().decode("utf-8", "ignore")
            try:
                return rep.status, json.loads(brut) if brut else {}
            except ValueError:
                return rep.status, brut
    except urllib.error.HTTPError as ex:
        brut = ex.read().decode("utf-8", "ignore")
        try:
            return ex.code, json.loads(brut)
        except ValueError:
            return ex.code, brut
    except Exception as ex:
        return 0, str(ex)


def _souci(rep):
    """Sort le message d'erreur d'une réponse, quel que soit le format du réseau."""
    if isinstance(rep, dict):
        for chemin in (("error", "message"), ("error", "error_user_msg"),
                       ("error", "code"), ("message",), ("error_description",)):
            v = rep
            for c in chemin:
                v = v.get(c) if isinstance(v, dict) else None
                if v is None:
                    break
            if v:
                return str(v)[:220]
    return str(rep)[:220]


def _mime(chemin):
    return mimetypes.guess_type(chemin)[0] or "application/octet-stream"


def _est_video(chemin):
    return _mime(chemin).startswith("video/")


# ─────────────────────────────────────────────────────────────────────────────
# YouTube — envoi reprenable
# ─────────────────────────────────────────────────────────────────────────────
def _jeton_google(compte, ids):
    """Le jeton Google expire en une heure : on le renouvelle avec le jeton
    de rafraîchissement obtenu au premier consentement (`access_type=offline`)."""
    if not compte.get("rafraichir"):
        return compte.get("jeton"), ""
    st, rep = _http("https://oauth2.googleapis.com/token", "POST", form={
        "client_id": ids.get("client_id"), "client_secret": ids.get("client_secret"),
        "refresh_token": compte["rafraichir"], "grant_type": "refresh_token"})
    if st == 200 and isinstance(rep, dict) and rep.get("access_token"):
        return rep["access_token"], ""
    return compte.get("jeton"), ""


def depot_youtube(post, compte, ids, chemin, url_publique):
    if not chemin or not _est_video(chemin):
        return "", "YouTube attend une vidéo — ce post n'en a pas."
    jeton, err = _jeton_google(compte, ids)
    if err:
        return "", err

    taille = os.path.getsize(chemin)
    meta = {
        "snippet": {
            "title": (post.get("titre") or post.get("legende") or "Nouvelle vidéo")[:100],
            "description": (post.get("legende") or "")[:4900],
            "categoryId": "22",
        },
        "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False},
    }
    st, rep = _http(
        "https://www.googleapis.com/upload/youtube/v3/videos"
        "?uploadType=resumable&part=snippet,status", "POST",
        entetes={"Authorization": "Bearer " + jeton,
                 "X-Upload-Content-Length": str(taille),
                 "X-Upload-Content-Type": _mime(chemin)},
        jsonc=meta)
    # L'adresse d'envoi arrive dans l'en-tête Location : urllib ne la rend pas
    # avec _http, on refait donc l'appel en gardant la réponse complète.
    req = urllib.request.Request(
        "https://www.googleapis.com/upload/youtube/v3/videos"
        "?uploadType=resumable&part=snippet,status",
        data=json.dumps(meta).encode(),
        headers={"Authorization": "Bearer " + jeton,
                 "Content-Type": "application/json",
                 "X-Upload-Content-Length": str(taille),
                 "X-Upload-Content-Type": _mime(chemin)},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            lieu = r.headers.get("Location")
    except urllib.error.HTTPError as ex:
        return "", "YouTube a refusé l'ouverture de l'envoi : " + ex.read().decode("utf-8", "ignore")[:200]
    except Exception as ex:
        return "", "YouTube injoignable : %s" % ex
    if not lieu:
        return "", "YouTube n'a pas rendu d'adresse d'envoi : " + _souci(rep)

    with open(chemin, "rb") as fh:
        st, rep = _http(lieu, "PUT",
                        entetes={"Content-Type": _mime(chemin),
                                 "Content-Length": str(taille)},
                        corps=fh.read(), delai=1800)
    if st in (200, 201) and isinstance(rep, dict) and rep.get("id"):
        return "https://youtu.be/" + rep["id"], ""
    return "", "YouTube : " + _souci(rep)


# ─────────────────────────────────────────────────────────────────────────────
# Facebook — il faut le jeton de la PAGE, pas celui de la personne
# ─────────────────────────────────────────────────────────────────────────────
GRAPH = "https://graph.facebook.com/v21.0"


def _page_facebook(jeton):
    st, rep = _http(GRAPH + "/me/accounts?fields=id,name,access_token&access_token="
                    + urllib.parse.quote(jeton))
    if st != 200 or not isinstance(rep, dict):
        return None, "Facebook : " + _souci(rep)
    pages = rep.get("data") or []
    if not pages:
        return None, ("Aucune page Facebook trouvée sur ce compte. La publication passe "
                      "obligatoirement par une page, pas par un profil personnel.")
    return pages[0], ""


def depot_facebook(post, compte, ids, chemin, url_publique):
    page, err = _page_facebook(compte.get("jeton") or "")
    if err:
        return "", err
    jp, pid = page["access_token"], page["id"]
    legende = post.get("legende") or post.get("titre") or ""

    if chemin and _est_video(chemin):
        st, rep = _http("%s/%s/videos" % (GRAPH, pid), "POST",
                        form={"file_url": url_publique, "description": legende,
                              "access_token": jp}, delai=600)
        if st == 200 and isinstance(rep, dict) and rep.get("id"):
            return "https://facebook.com/%s" % rep["id"], ""
        return "", "Facebook (vidéo) : " + _souci(rep)

    if chemin:
        st, rep = _http("%s/%s/photos" % (GRAPH, pid), "POST",
                        form={"url": url_publique, "caption": legende, "access_token": jp})
        if st == 200 and isinstance(rep, dict) and rep.get("post_id", rep.get("id")):
            return "https://facebook.com/%s" % rep.get("post_id", rep["id"]), ""
        return "", "Facebook (photo) : " + _souci(rep)

    st, rep = _http("%s/%s/feed" % (GRAPH, pid), "POST",
                    form={"message": legende, "access_token": jp})
    if st == 200 and isinstance(rep, dict) and rep.get("id"):
        return "https://facebook.com/%s" % rep["id"], ""
    return "", "Facebook : " + _souci(rep)


# ─────────────────────────────────────────────────────────────────────────────
# Instagram — conteneur puis publication ; le média doit être joignable de l'extérieur
# ─────────────────────────────────────────────────────────────────────────────
def depot_instagram(post, compte, ids, chemin, url_publique):
    if not chemin:
        return "", "Instagram exige une image ou une vidéo : un texte seul n'est pas publiable."
    if not url_publique.startswith("https://"):
        return "", ("Instagram va chercher le média lui-même : il faut une adresse HTTPS "
                    "publique. Vérifie que le site est bien servi en HTTPS.")
    jeton = compte.get("jeton") or ""
    page, err = _page_facebook(jeton)
    if err:
        return "", err
    st, rep = _http("%s/%s?fields=instagram_business_account&access_token=%s"
                    % (GRAPH, page["id"], urllib.parse.quote(jeton)))
    igid = ((rep or {}).get("instagram_business_account") or {}).get("id") if isinstance(rep, dict) else None
    if not igid:
        return "", ("Aucun compte Instagram professionnel rattaché à la page Facebook. "
                    "Le compte doit être « professionnel » et lié à la page.")

    legende = post.get("legende") or ""
    champs = {"caption": legende[:2200], "access_token": jeton}
    if _est_video(chemin):
        champs["video_url"] = url_publique
        champs["media_type"] = "REELS"
    else:
        champs["image_url"] = url_publique

    st, rep = _http("%s/%s/media" % (GRAPH, igid), "POST", form=champs, delai=300)
    cid = rep.get("id") if isinstance(rep, dict) else None
    if not cid:
        return "", "Instagram (préparation) : " + _souci(rep)

    # Une vidéo est transcodée par Instagram : on attend qu'elle soit prête.
    if _est_video(chemin):
        import time as _t
        for _ in range(30):
            _t.sleep(4)
            st, r2 = _http("%s/%s?fields=status_code&access_token=%s"
                           % (GRAPH, cid, urllib.parse.quote(jeton)))
            etat = r2.get("status_code") if isinstance(r2, dict) else None
            if etat == "FINISHED":
                break
            if etat == "ERROR":
                return "", "Instagram n'a pas réussi à traiter la vidéo."
        else:
            return "", "Instagram met trop longtemps à traiter la vidéo — réessaie dans un moment."

    st, rep = _http("%s/%s/media_publish" % (GRAPH, igid), "POST",
                    form={"creation_id": cid, "access_token": jeton}, delai=300)
    if isinstance(rep, dict) and rep.get("id"):
        return "https://www.instagram.com/p/%s/" % rep["id"], ""
    return "", "Instagram (publication) : " + _souci(rep)


# ─────────────────────────────────────────────────────────────────────────────
# LinkedIn — enregistrer l'envoi, déposer, puis créer le post
# ─────────────────────────────────────────────────────────────────────────────
LI = "https://api.linkedin.com"


def depot_linkedin(post, compte, ids, chemin, url_publique):
    jeton = compte.get("jeton") or ""
    ent = {"Authorization": "Bearer " + jeton, "X-Restli-Protocol-Version": "2.0.0"}

    # Profil ou Page entreprise : seul l'AUTEUR change, tout le reste du dépôt
    # est identique. La cible a été choisie à la connexion et rangée dans
    # `comptes.extra` ; on ne la redemande pas à chaque publication.
    # ⚠️ Un jeton de Page ne répond PAS sur /v2/userinfo (ce n'est pas une
    # personne) : appeler userinfo quand même ferait échouer toute publication
    # de Page avec un message qui parle du « compte ».
    cible = {}
    try:
        cible = json.loads(compte.get("extra") or "null") or {}
    except Exception:
        cible = {}
    if isinstance(cible, dict) and cible.get("urn"):
        auteur = cible["urn"]
    else:
        st, rep = _http(LI + "/v2/userinfo", entetes=ent)
        sub = rep.get("sub") if isinstance(rep, dict) else None
        if not sub:
            return "", "LinkedIn : impossible d'identifier le compte — " + _souci(rep)
        auteur = "urn:li:person:" + sub
    texte = post.get("legende") or post.get("titre") or ""

    media = []
    if chemin:
        recette = ("urn:li:digitalmediaRecipe:feedshare-video" if _est_video(chemin)
                   else "urn:li:digitalmediaRecipe:feedshare-image")
        st, rep = _http(LI + "/v2/assets?action=registerUpload", "POST", entetes=ent, jsonc={
            "registerUploadRequest": {
                "recipes": [recette], "owner": auteur,
                "serviceRelationships": [{"relationshipType": "OWNER",
                                          "identifier": "urn:li:userGeneratedContent"}]}})
        val = (rep or {}).get("value") if isinstance(rep, dict) else None
        if not val:
            return "", "LinkedIn (préparation du média) : " + _souci(rep)
        cible = (val["uploadMechanism"]
                 ["com.linkedin.digitalmedia.uploading.MediaUploadHttpRequest"]["uploadUrl"])
        with open(chemin, "rb") as fh:
            st, r2 = _http(cible, "PUT",
                           entetes={"Authorization": "Bearer " + jeton,
                                    "Content-Type": _mime(chemin)},
                           corps=fh.read(), delai=1800)
        if st not in (200, 201):
            return "", "LinkedIn (dépôt du média) : " + _souci(r2)
        media = [{"status": "READY", "media": val["asset"]}]

    corps = {
        "author": auteur,
        "lifecycleState": "PUBLISHED",
        "specificContent": {"com.linkedin.ugc.ShareContent": {
            "shareCommentary": {"text": texte[:2900]},
            "shareMediaCategory": ("VIDEO" if (chemin and _est_video(chemin))
                                   else ("IMAGE" if chemin else "NONE")),
        }},
        "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"},
    }
    if media:
        corps["specificContent"]["com.linkedin.ugc.ShareContent"]["media"] = media

    st, rep = _http(LI + "/v2/ugcPosts", "POST",
                    entetes=dict(ent, **{"Content-Type": "application/json"}), jsonc=corps)
    pid = rep.get("id") if isinstance(rep, dict) else None
    if pid:
        return "https://www.linkedin.com/feed/update/" + pid, ""
    return "", "LinkedIn : " + _souci(rep)


# ─────────────────────────────────────────────────────────────────────────────
# TikTok — initialiser l'envoi, déposer les octets
# ─────────────────────────────────────────────────────────────────────────────
def depot_tiktok(post, compte, ids, chemin, url_publique):
    if not chemin or not _est_video(chemin):
        return "", "TikTok attend une vidéo — ce post n'en a pas."
    jeton = compte.get("jeton") or ""
    taille = os.path.getsize(chemin)
    ent = {"Authorization": "Bearer " + jeton, "Content-Type": "application/json"}
    st, rep = _http("https://open.tiktokapis.com/v2/post/publish/video/init/", "POST",
                    entetes=ent, jsonc={
                        "post_info": {
                            "title": (post.get("legende") or post.get("titre") or "")[:2200],
                            "privacy_level": "SELF_ONLY",
                            "disable_comment": False,
                        },
                        "source_info": {
                            "source": "FILE_UPLOAD",
                            "video_size": taille,
                            "chunk_size": taille,
                            "total_chunk_count": 1,
                        }})
    d = (rep or {}).get("data") if isinstance(rep, dict) else None
    if not d or not d.get("upload_url"):
        return "", "TikTok (préparation) : " + _souci(rep)

    with open(chemin, "rb") as fh:
        st, r2 = _http(d["upload_url"], "PUT",
                       entetes={"Content-Type": _mime(chemin),
                                "Content-Length": str(taille),
                                "Content-Range": "bytes 0-%d/%d" % (taille - 1, taille)},
                       corps=fh.read(), delai=1800)
    if st not in (200, 201, 204):
        return "", "TikTok (dépôt) : " + _souci(r2)
    # TikTok ne rend pas d'adresse publique : la vidéo atterrit dans la boîte de
    # réception du compte, à valider dans l'application. C'est leur règle pour
    # les applications non auditées, ce n'est pas un défaut de notre côté.
    return "https://www.tiktok.com/", ""


DEPOTS = {
    "youtube": depot_youtube,
    "facebook": depot_facebook,
    "instagram": depot_instagram,
    "linkedin": depot_linkedin,
    "tiktok": depot_tiktok,
}
