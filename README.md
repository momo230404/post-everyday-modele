# Post Everyday

Une rédaction : Nadia et cinq agents (YouTube, Instagram, TikTok, LinkedIn,
Facebook) qui écrivent, découpent et publient les contenus d'un client.

> **Ceci est un modèle.** Cliquez sur **Use this template** en haut de cette
> page : vous obtiendrez une copie complète, à vous, sans aucun lien avec ce
> dépôt. Rien de ce que vous y ferez ne remontera ici, et rien de ce qui change
> ici ne descendra chez vous.
>
> 📘 **Le guide d'installation pas à pas**, du dépôt jusqu'à la mise en ligne :
> <https://momo230404.github.io/post-everyday-modele/>

Le mode d'emploi destiné aux utilisateurs est **servi par l'application
elle-même**, sans compte : `/guide` (ou `/tuto`). C'est la page à lire d'abord
pour comprendre ce que fait le produit avant de lire le code.

## Ce qu'il faut remplacer avant de mettre en ligne

Le projet vous est livré sans aucune identité. Trois choses vous appartiennent :

| Où | Quoi |
|---|---|
| `conditions.html`, `confidentialite.html` | `VOTRE SOCIÉTÉ`, `VOTRE-ADRESSE@exemple.fr`, `votre-domaine.fr` |
| `guide.html`, `tuto.html` | `votre-domaine.fr` dans les adresses de redirection |
| `config.json` | vos comptes, vos clés, votre `secret` (voir `config.exemple.json`) |

⚠️ Publier les pages légales sans les modifier reviendrait à annoncer quelqu'un
d'autre comme éditeur de votre service.

---

## Démarrer en local

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
cp config.exemple.json config.json     # puis remplir ce qu'on veut essayer
./venv/bin/python app.py               # http://127.0.0.1:5010
```

Les bases SQLite se créent toutes seules au premier lancement. Rien d'autre à
installer : pas de base de données à monter, pas de service externe obligatoire.

---

## Comment c'est fait

### Un client = un fichier de base

Il n'y a **pas** de colonne `compte_id` dans quarante requêtes. Chaque espace
client a **sa propre base SQLite** :

```
pe.db                  l'espace principal
pe_demo-imprimerie.db  un espace client
pe_<id>.db             …
espaces.json           le registre : quel id, quel nom, quel fichier
```

`espace_courant()` lit le cookie `pe_espace`, `db()` ouvre le bon fichier. Un
compte rattaché à un espace y est **enfermé côté serveur** (`espace_impose` en
session) : changer le cookie à la main ne permet pas de publier au nom d'un
autre client. Seul un administrateur bascule d'un espace à l'autre.

Supprimer un espace **renomme** son fichier en `.retire_<horodatage>` — rien
n'est jamais effacé.

### Les comptes

Dans `config.json` → `comptes` : `{identifiant, nom, role, espace?, mdp_hash}`.
Mots de passe en scrypt (Werkzeug), jamais en clair. Deux rôles : `admin` (voit
tout, bascule d'espace) et `membre` (enfermé dans le sien).

`LIBRES` dans `app.py` liste les chemins ouverts sans connexion : `/connexion`,
`/guide`, `/tuto`, `/conditions`, `/confidentialite`. Tout le reste exige une
session.

### Les fichiers

| Fichier | Rôle |
|---|---|
| `app.py` | Le serveur Flask : comptes, espaces, profil, idées, composition, agenda, médias, OAuth des cinq réseaux. |
| `moteur.py` | L'écriture : angles × modes, profil métier, plan de la semaine. Tirage déterministe — même entrée, même sortie. |
| `depots.py` | Le dépôt réel d'un post sur chaque réseau. |
| `index.html` | Tout le front en une page : rédaction, agenda, carrousels, prompteur, tableau de bord. |
| `tuto.html` | Le mode d'emploi, servi sur `/guide` et `/tuto`. |
| `demos_posteveryday.py` | Fabrique les cinq espaces de démonstration. |
| `conditions.html`, `confidentialite.html` | Pages légales (gabarit `_legal_base.html`). |

### Ce qui n'est pas dans ce dépôt, et pourquoi

```
config.json     les clés d'API et les mots de passe des comptes
espaces.json    le registre des clients
pe*.db          les données de chaque client
medias/         les fichiers déposés et les vidéos rendues
```

Ce sont des **données et des secrets**. Ils vivent sur le serveur et n'en
sortent pas. `config.exemple.json` donne la forme exacte du fichier à remplir.

> ⚠️ Conséquence à connaître : **ce dépôt ne sauvegarde pas les données.** Il n'y
> a à ce jour aucune copie des bases clients en dehors du disque du VPS.

---

## Mettre en ligne ailleurs

L'application écrit ses bases et ses médias **sur le disque**. Il lui faut donc
un hébergeur qui fournit un vrai disque persistant — ce qui exclut les
plateformes sans état (Vercel, Netlify).

`Procfile` porte la commande de lancement pour tout hébergeur qui le lit.

> ⚠️ **`render.yaml` est trompeur en l'état.** Il déclare un disque monté sur
> `/var/donnees` et une variable `PE_DONNEES` — mais le code ne lit **jamais**
> `PE_DONNEES` : il écrit toujours à côté de `app.py`. Déployer sur Render tel
> quel donne une application qui marche et qui **perd ses données à chaque mise
> en ligne**. Tant que ce n'est pas corrigé, prenez un VPS.

| Variable | Rôle |
|---|---|
| `PE_BASE` | L'URL publique, celle des retours OAuth. Sans elle, les cinq réseaux renvoient sur le mauvais domaine. |

Le reste de la configuration passe par `config.json`.

---

## État d'avancement

- **La rédaction, l'agenda, les carrousels, le prompteur** : en service.
- **La publication réelle** renvoie `501` tant que les applications éditeurs ne
  sont pas créées chez chaque réseau et renseignées. Ce n'est pas un défaut de
  code : chaque réseau exige une application validée, au nom du client ou de
  l'agence. `cles_reseau()` prend d'abord les clés du client, puis celles de
  l'agence en repli.
- **L'avatar vidéo** appelle un studio externe (`config["studio"]`, Whisper et
  Remotion sur une autre machine). Sans lui, l'animation maison (canvas + voix
  du navigateur) reste disponible.

## Ce qu'il faudrait pour en faire un vrai SaaS

Dit franchement, pour que le repreneur sache où il met les pieds :

2. **Inscription et paiement en ligne.** Les comptes se créent à la main dans
   `config.json`.
3. **Une seule machine, un seul processus** : pas de bascule en cas de panne.
4. **Pas de gestion de versions du schéma** : un changement de structure se fait
   à la main sur chaque fichier client.

L'architecture, elle, tient : l'isolation par fichier est un choix assumé et
solide pour un petit nombre de clients.
