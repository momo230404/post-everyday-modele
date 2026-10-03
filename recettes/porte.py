# -*- coding: utf-8 -*-
import io, ast
C = "/docker/posteveryday/app.py"
s = io.open(C, encoding="utf-8").read()
old = '''    chemin = request.path
    if any(chemin == l or chemin.startswith(l) for l in LIBRES):
        return None'''
new = '''    chemin = request.path
    if any(chemin == l or chemin.startswith(l) for l in LIBRES):
        return None
    # ⚠️ Les fichiers de preuve de domaine sont A LA RACINE : aucun préfixe de
    # LIBRES ne peut les couvrir. On les laisse passer un par un, et seulement
    # s'ils existent vraiment — on n'ouvre pas la racine du site.
    if chemin.count("/") == 1 and (chemin.endswith(".txt") or chemin.endswith(".html")):
        f = _verif_nom(chemin)
        if f and os.path.exists(os.path.join(VERIFS, f)):
            return None'''
assert old in s
s = s.replace(old, new, 1)
ast.parse(s)
io.open(C, "w", encoding="utf-8").write(s)
print("porte ouverte pour les fichiers de preuve existants")
