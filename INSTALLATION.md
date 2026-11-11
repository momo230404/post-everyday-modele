# Premier compte administrateur

Le site répond 503 tant que `config.json` ne contient aucun compte. Les adresses publiques déjà définies dans `LIBRES` et `/api/sante` restent disponibles.

Le script d’installation doit créer `jeton_installation.txt` dans le dossier `PE_DONNEES`, avec un secret aléatoire long (par exemple `secrets.token_urlsafe(48)`) et les droits 600. Il conserve la configuration existante. Il remet ensuite à la personne l’adresse HTTPS `/installation?jeton=VALEUR`. La personne choisit son identifiant et son mot de passe dans cette page, sans Terminal.

La page n’existe que si le fichier du jeton existe et qu’aucun compte n’existe. Après création, le compte est administrateur, la personne est connectée, le jeton est supprimé et la page répond 404, même si un fichier de jeton est recréé alors que le compte existe.

## Journaux

L’application masque la chaîne de requête de `/installation` dans les journaux Werkzeug et ne journalise pas le formulaire. Sur le serveur, les journaux d’accès du mandataire et de Gunicorn doivent également omettre la chaîne de requête pour cette adresse. Un filtre Flask ne peut pas modifier les journaux d’un mandataire externe. Ne jamais conserver le lien d’installation dans des journaux ou un dépôt.

## Vérification

Avec les dépendances de `requirements.txt` installées et un Python disposant de scrypt :

```sh
python -m unittest test_installation -v
```

Les tests utilisent des dossiers temporaires et ne touchent pas aux comptes réels. Ils couvrent notamment les jetons invalides, les champs refusés, les droits du fichier, la préservation de la configuration, les requêtes simultanées et l’échec d’écriture.

Le script d’installation fourni séparément ne fait pas partie de ce dépôt. Son adaptation pour produire le fichier de jeton reste à intégrer avant l’utilisation sur serveur.
