#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Simulation sur le compte de démonstration « Rocher & Fils (BTP) ».

Trois chiffres imposés par l'user — 50 000 vues, 1 000 nouveaux abonnés,
700 likes — et une file bien remplie, pour voir ce que donne un compte qui
tourne depuis plusieurs mois.

⚠️ Les abonnés ne se posent pas directement : `_gain()` calcule l'écart entre le
dernier relevé et celui d'il y a trente jours. Pour afficher **exactement**
+1 000, on écrit donc deux relevés par réseau — un à J-30, un aujourd'hui —
dont la différence fait le compte.

Ce script est rejouable : il réécrit les relevés au lieu d'en empiler.
"""
import json
import os
import random
import sqlite3
import sys
from datetime import datetime, timedelta

sys.path.insert(0, "/docker/posteveryday")
os.chdir("/docker/posteveryday")
import moteur as M                                    # noqa: E402

ESPACE = "demo-btp"
BASE = "/docker/posteveryday/pe_%s.db" % ESPACE
VUES, ABONNES, LIKES = 50000, 1000, 700

# Comment les 1 000 abonnés se répartissent : on ne met pas 200 partout, un
# compte réel n'est jamais régulier.
PART = {"tiktok": 420, "instagram": 300, "facebook": 130, "youtube": 100, "linkedin": 50}
SOCLE = {"youtube": 1180, "instagram": 4260, "tiktok": 5720, "linkedin": 1240, "facebook": 4880}

c = sqlite3.connect(BASE, timeout=30)
c.row_factory = sqlite3.Row
auj = datetime.now()

# ── 1 · Vues et likes ───────────────────────────────────────────────────────
c.execute("""CREATE TABLE IF NOT EXISTS mesures (
             cle TEXT PRIMARY KEY, valeur INTEGER NOT NULL, releve_le TEXT NOT NULL)""")
for cle, val in (("vues", VUES), ("likes", LIKES)):
    c.execute("INSERT INTO mesures(cle,valeur,releve_le) VALUES(?,?,?) "
              "ON CONFLICT(cle) DO UPDATE SET valeur=excluded.valeur, releve_le=excluded.releve_le",
              (cle, val, auj.strftime("%Y-%m-%d")))

# ── 2 · Les abonnés : deux relevés dont l'écart fait +1 000 ─────────────────
c.execute("DELETE FROM abonnes")
for pid, gain in PART.items():
    depart = SOCLE[pid]
    # J-30, J-15 puis aujourd'hui : trois points, pour que la courbe monte au
    # lieu de sauter d'un coup.
    for jours, part in ((30, 0.0), (15, 0.55), (0, 1.0)):
        c.execute("INSERT INTO abonnes(plateforme,nombre,source,releve_le) VALUES(?,?,'saisie',?)",
                  (pid, int(depart + gain * part),
                   (auj - timedelta(days=jours)).strftime("%Y-%m-%d")))

# ── 3 · De quoi remplir la file ─────────────────────────────────────────────
# On complète ce qui existe : des posts prêts, pas encore datés. C'est ce qu'on
# voit dans « Vos contenus en cours » et dans la réserve de l'agenda.
profil = {r["cle"]: r["valeur"] for r in c.execute("SELECT cle,valeur FROM profil")}
angles = [a["id"] for a in M.ANGLES]
modes = ["parle", "carrousel", "anime"]
deja = c.execute("SELECT COUNT(*) n FROM posts WHERE etat='brouillon'").fetchone()["n"]
a_faire = max(0, 24 - deja)
rnd = random.Random(4)
faits = 0
for i in range(a_faire):
    reseau = M.ORDRE_RESEAUX[i % len(M.ORDRE_RESEAUX)]
    angle = angles[(i * 3 + 5) % len(angles)]
    mode = modes[i % 3]
    try:
        r = M.composer(profil, reseau, angle, mode, False)
    except Exception as e:
        print("  ! %s/%s : %s" % (reseau, angle, str(e)[:60]))
        continue
    c.execute("INSERT INTO posts(reseau,angle,mode,titre,legende,contenu,etat,cree_le) "
              "VALUES(?,?,?,?,?,?,'brouillon',?)",
              (reseau, angle, mode, r.get("titre"), r.get("legende"),
               json.dumps(r, ensure_ascii=False),
               (auj - timedelta(hours=i * 5)).isoformat(timespec="seconds")))
    faits += 1
c.commit()

n = {r["etat"]: r["n"] for r in c.execute("SELECT etat, COUNT(*) n FROM posts GROUP BY etat")}
print("vues %s · abonnés +%d · likes %s" % (format(VUES, ',d').replace(',', ' '),
                                            sum(PART.values()), LIKES))
print("posts : %d publiés · %d calés · %d en réserve (+%d écrits)"
      % (n.get("publie", 0), n.get("prevu", 0), n.get("brouillon", 0), faits))
c.close()
