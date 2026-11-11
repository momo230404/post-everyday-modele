import json
import logging
from concurrent.futures import ThreadPoolExecutor
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# Importer avec un dossier de données isolé, sans toucher aux données réelles.
_donnees_import = tempfile.TemporaryDirectory()
os.environ["PE_DONNEES"] = _donnees_import.name
import app as serveur
from werkzeug.security import check_password_hash


class InstallationTests(unittest.TestCase):
    def setUp(self):
        self.dossier = tempfile.TemporaryDirectory()
        self.addCleanup(self.dossier.cleanup)
        self.racine = Path(self.dossier.name)
        self.configuration = self.racine / "config.json"
        self.jeton = self.racine / "jeton_installation.txt"
        self.initial = {"comptes": [], "secret": "cle-test", "autre": {"conserve": True}}
        self.configuration.write_text(json.dumps(self.initial))
        self.jeton.write_text("jeton-de-test-long-uniquement-pour-les-tests")
        for nom, valeur in [("DONNEES", str(self.racine)), ("CONFIG", str(self.configuration))]:
            remplacement = patch.object(serveur, nom, valeur)
            remplacement.start()
            self.addCleanup(remplacement.stop)
        sommeil = patch.object(serveur.time, "sleep")
        self.sommeil = sommeil.start()
        self.addCleanup(sommeil.stop)
        serveur.app.config.update(TESTING=True, SECRET_KEY="cle-test")
        self.client = serveur.app.test_client()
        self.url = "/installation?jeton=" + self.jeton.read_text()

    def envoyer(self, **champs):
        donnees = {"identifiant": "Admin", "motdepasse": "mot-de-passe-solide", "confirmation": "mot-de-passe-solide"}
        donnees.update(champs)
        return self.client.post(self.url, data=donnees)

    def test_jeton_faux(self):
        self.assertEqual(self.client.get("/installation?jeton=faux").status_code, 404)
        self.sommeil.assert_called_once_with(1)

    def test_jeton_absent(self):
        self.assertEqual(self.client.get("/installation").status_code, 404)

    def test_fichier_absent(self):
        self.jeton.unlink()
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_mots_de_passe_differents(self):
        reponse = self.envoyer(confirmation="different")
        self.assertIn("différents", reponse.get_data(as_text=True))
        self.assertEqual(json.loads(self.configuration.read_text()), self.initial)
        self.assertTrue(self.jeton.exists())

    def test_mot_de_passe_court(self):
        reponse = self.envoyer(motdepasse="court", confirmation="court")
        self.assertIn("douze caractères", reponse.get_data(as_text=True))
        self.assertEqual(json.loads(self.configuration.read_text()), self.initial)

    def test_identifiant_vide(self):
        self.assertIn("Saisissez un identifiant", self.envoyer(identifiant=" ").get_data(as_text=True))

    def test_creation_et_connexion(self):
        self.assertEqual(self.envoyer().status_code, 302)
        configuration = json.loads(self.configuration.read_text())
        self.assertEqual(configuration["autre"], self.initial["autre"])
        compte = configuration["comptes"][0]
        self.assertEqual(compte["identifiant"], "admin")
        self.assertEqual(compte["role"], "admin")
        self.assertTrue(check_password_hash(compte["mdp_hash"], "mot-de-passe-solide"))
        self.assertEqual(self.configuration.stat().st_mode & 0o777, 0o600)
        self.assertFalse(self.jeton.exists())
        with self.client.session_transaction() as session:
            self.assertEqual(session["qui"], "admin")
        self.client.get("/deconnexion")
        reponse = self.client.post("/connexion", data={"identifiant": "ADMIN", "motdepasse": "mot-de-passe-solide"})
        self.assertEqual(reponse.status_code, 302)

    def test_seconde_tentative(self):
        self.envoyer()
        avant = self.configuration.read_bytes()
        self.assertEqual(self.envoyer().status_code, 404)
        self.jeton.write_text("jeton-de-test-long-uniquement-pour-les-tests")
        self.assertEqual(self.envoyer().status_code, 404)
        self.assertEqual(self.configuration.read_bytes(), avant)

    def test_site_ferme(self):
        for chemin in ["/", "/api/profil", "/preuve.html"]:
            self.assertEqual(self.client.get(chemin).status_code, 503)
        self.assertEqual(self.client.get("/api/sante").status_code, 200)
        self.assertEqual(self.client.get("/connexion").status_code, 200)
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_comptes_existants(self):
        self.envoyer()
        autre = serveur.app.test_client()
        self.assertEqual(autre.get("/").status_code, 302)
        self.assertEqual(autre.get("/api/profil").status_code, 401)

    def test_remplacement_echoue(self):
        with patch.object(serveur.os, "replace", side_effect=OSError("échec simulé")):
            with self.assertRaises(OSError):
                self.envoyer()
        self.assertEqual(json.loads(self.configuration.read_text()), self.initial)
        self.assertTrue(self.jeton.exists())
        self.assertFalse(list(self.racine.glob(".installation-*")))

    def test_journal_masque_jeton(self):
        record = logging.LogRecord("werkzeug", logging.INFO, "", 0,
                                   '"GET %s HTTP/1.1" 200', (self.url,), None)
        serveur._MasquerJetonInstallation().filter(record)
        self.assertNotIn(self.jeton.read_text(), record.getMessage())
        self.assertIn("[masqué]", record.getMessage())

    def test_creation_concurrente(self):
        from threading import Barrier
        barriere = Barrier(2)
        def poster(identifiant):
            client = serveur.app.test_client()
            barriere.wait()
            return client.post(self.url, data={"identifiant": identifiant,
                "motdepasse": "mot-de-passe-solide", "confirmation": "mot-de-passe-solide"}).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            resultats = list(pool.map(poster, ["premier", "second"]))
        self.assertEqual(sorted(resultats), [302, 404])
        self.assertEqual(len(json.loads(self.configuration.read_text())["comptes"]), 1)

    def test_entetes_et_unicode(self):
        reponse = self.client.get(self.url)
        self.assertEqual(reponse.headers["Cache-Control"], "no-store")
        self.assertEqual(reponse.headers["Referrer-Policy"], "no-referrer")
        self.assertEqual(self.client.get("/installation?jeton=é").status_code, 404)


if __name__ == "__main__":
    unittest.main()
