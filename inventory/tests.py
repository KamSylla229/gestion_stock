from decimal import Decimal
from io import BytesIO

from django.contrib.auth.models import Group, User
from django.core import mail
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from openpyxl import load_workbook

from inventory import exports, services, statistiques
from inventory.models import Categorie, Commande, Fournisseur, Mouvement, Produit

# Backend mémoire : les emails sont collectés dans mail.outbox au lieu d'être envoyés.
PARAMETRES_EMAIL_TEST = {
    "EMAIL_BACKEND": "django.core.mail.backends.locmem.EmailBackend",
    "ALERTE_EMAIL_DESTINATAIRE": "gerant@stockflow.test",
    "DEFAULT_FROM_EMAIL": "stockflow@stockflow.test",
}

MOT_DE_PASSE_TEST = "motdepasse123"


def creer_utilisateur(nom, groupe=None):
    """Crée un utilisateur de test, éventuellement rattaché à un groupe métier."""
    utilisateur = User.objects.create_user(username=nom, password=MOT_DE_PASSE_TEST)
    if groupe:
        utilisateur.groups.add(Group.objects.get(name=groupe))
    return utilisateur


class BaseApplicationTestCase(TestCase):
    """
    Base des tests qui passent par l'interface.

    Les groupes Gerant/Magasinier sont créés par la commande de déploiement,
    pas par une migration : on l'exécute donc pour chaque classe de test.
    """

    @classmethod
    def setUpTestData(cls):
        call_command("initialiser_groupes", verbosity=0)


class EnregistrerMouvementTests(TestCase):
    """Tests de la logique métier de services.enregistrer_mouvement()."""

    def setUp(self):
        self.categorie = Categorie.objects.create(nom="Test")
        # On fixe directement quantite_stock ici : il s'agit de préparer l'état
        # initial d'un test, pas d'une opération métier.
        self.produit = Produit.objects.create(
            reference="TST-001",
            nom="Produit test",
            categorie=self.categorie,
            prix_achat=100,
            prix_vente=150,
            quantite_stock=10,
        )

    def test_entree_normale(self):
        """Stock 10 + entrée 5 = 15, et le mouvement est enregistré."""
        mouvement = services.enregistrer_mouvement(
            self.produit, Mouvement.ENTREE, 5, motif="Réception"
        )

        self.produit.refresh_from_db()
        self.assertEqual(self.produit.quantite_stock, 15)
        self.assertEqual(Mouvement.objects.count(), 1)
        self.assertEqual(mouvement.type_mouvement, Mouvement.ENTREE)
        self.assertEqual(mouvement.quantite, 5)
        self.assertEqual(mouvement.produit, self.produit)

    def test_sortie_normale(self):
        """Stock 10 - sortie 4 = 6, et le mouvement est enregistré."""
        mouvement = services.enregistrer_mouvement(
            self.produit, Mouvement.SORTIE, 4, motif="Vente"
        )

        self.produit.refresh_from_db()
        self.assertEqual(self.produit.quantite_stock, 6)
        self.assertEqual(Mouvement.objects.count(), 1)
        self.assertEqual(mouvement.type_mouvement, Mouvement.SORTIE)
        self.assertEqual(mouvement.quantite, 4)

    def test_sortie_superieure_au_stock(self):
        """Sortie de 15 sur un stock de 10 : refusée, stock inchangé."""
        with self.assertRaises(ValidationError):
            services.enregistrer_mouvement(self.produit, Mouvement.SORTIE, 15)

        self.produit.refresh_from_db()
        self.assertEqual(self.produit.quantite_stock, 10)
        self.assertEqual(Mouvement.objects.count(), 0)

    def test_quantite_nulle_refusee(self):
        """Une quantité de 0 est refusée, en entrée comme en sortie."""
        for type_mouvement in (Mouvement.ENTREE, Mouvement.SORTIE):
            with self.subTest(type_mouvement=type_mouvement):
                with self.assertRaises(ValidationError):
                    services.enregistrer_mouvement(self.produit, type_mouvement, 0)

        self.produit.refresh_from_db()
        self.assertEqual(self.produit.quantite_stock, 10)
        self.assertEqual(Mouvement.objects.count(), 0)

    def test_type_mouvement_inconnu_refuse(self):
        """Un type de mouvement hors ENTREE/SORTIE est refusé."""
        with self.assertRaises(ValidationError):
            services.enregistrer_mouvement(self.produit, "TRANSFERT", 5)

        self.produit.refresh_from_db()
        self.assertEqual(self.produit.quantite_stock, 10)
        self.assertEqual(Mouvement.objects.count(), 0)


    def test_transaction_atomique_rollback_complet(self):
        """
        Si la création du Mouvement échoue, la mise à jour du stock est annulée.

        On simule une panne au moment de créer le mouvement et on vérifie qu'on
        ne se retrouve pas avec un stock modifié sans historique correspondant.
        """
        from unittest.mock import patch

        with patch.object(
            Mouvement.objects, "create", side_effect=RuntimeError("panne base de données")
        ):
            with self.assertRaises(RuntimeError):
                services.enregistrer_mouvement(self.produit, Mouvement.ENTREE, 5)

        self.produit.refresh_from_db()
        self.assertEqual(self.produit.quantite_stock, 10)  # stock intact
        self.assertEqual(Mouvement.objects.count(), 0)


@override_settings(**PARAMETRES_EMAIL_TEST)
class AlerteStockTests(TestCase):
    """Déclenchement de l'alerte email au franchissement du seuil."""

    def setUp(self):
        self.categorie = Categorie.objects.create(nom="Test")
        self.produit = Produit.objects.create(
            reference="ALE-001",
            nom="Produit surveillé",
            categorie=self.categorie,
            prix_achat=100,
            prix_vente=150,
            quantite_stock=15,
            seuil_alerte=10,
        )

    def test_franchit_seuil_alerte(self):
        """La règle de franchissement, testée isolément."""
        self.assertTrue(services.franchit_seuil_alerte(15, 8, 10))    # franchissement
        self.assertTrue(services.franchit_seuil_alerte(11, 10, 10))   # atteint le seuil
        self.assertFalse(services.franchit_seuil_alerte(8, 6, 10))    # déjà sous le seuil
        self.assertFalse(services.franchit_seuil_alerte(20, 15, 10))  # reste au-dessus

    def test_sortie_qui_franchit_le_seuil_envoie_un_email(self):
        """Stock 15, seuil 10, sortie 7 -> stock 8 : une alerte part."""
        # captureOnCommitCallbacks exécute les callbacks transaction.on_commit,
        # qui ne se déclenchent pas seuls à l'intérieur d'un TestCase.
        with self.captureOnCommitCallbacks(execute=True):
            services.enregistrer_mouvement(self.produit, Mouvement.SORTIE, 7)

        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertIn("Produit surveillé", message.subject)
        self.assertEqual(message.to, ["gerant@stockflow.test"])

        # L'email contient bien une version HTML en plus du texte.
        self.assertEqual(len(message.alternatives), 1)
        corps_html = message.alternatives[0][0]
        self.assertIn("text/html", message.alternatives[0][1])
        self.assertIn("Alerte de stock", corps_html)
        self.assertIn("STOCK CRITIQUE", corps_html)
        # Stock avant, quantité sortie et stock actuel sont présents.
        self.assertIn("15", corps_html)
        self.assertIn("7", corps_html)
        self.assertIn("8", corps_html)

    def test_produit_deja_sous_le_seuil_n_envoie_pas_d_email(self):
        """Stock 8 (déjà sous le seuil 10), sortie 2 -> aucune alerte."""
        self.produit.quantite_stock = 8
        self.produit.save(update_fields=["quantite_stock"])

        with self.captureOnCommitCallbacks(execute=True):
            services.enregistrer_mouvement(self.produit, Mouvement.SORTIE, 2)

        self.produit.refresh_from_db()
        self.assertEqual(self.produit.quantite_stock, 6)
        self.assertEqual(len(mail.outbox), 0)

    def test_aucun_email_inutile(self):
        """Ni une entrée, ni une sortie qui reste au-dessus du seuil n'alertent."""
        with self.captureOnCommitCallbacks(execute=True):
            services.enregistrer_mouvement(self.produit, Mouvement.ENTREE, 50)
        self.assertEqual(len(mail.outbox), 0)

        with self.captureOnCommitCallbacks(execute=True):
            services.enregistrer_mouvement(self.produit, Mouvement.SORTIE, 5)
        self.assertEqual(len(mail.outbox), 0)

    def test_rupture_totale_signalee_comme_telle(self):
        """Stock tombé à 0 : l'email annonce une rupture, pas un stock critique."""
        with self.captureOnCommitCallbacks(execute=True):
            services.enregistrer_mouvement(self.produit, Mouvement.SORTIE, 15)

        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("RUPTURE", mail.outbox[0].alternatives[0][0])

    def test_sortie_refusee_n_envoie_aucun_email(self):
        """Un mouvement annulé ne doit générer aucune alerte."""
        with self.captureOnCommitCallbacks(execute=True):
            with self.assertRaises(ValidationError):
                services.enregistrer_mouvement(self.produit, Mouvement.SORTIE, 999)

        self.assertEqual(len(mail.outbox), 0)

    def test_panne_email_ne_casse_pas_la_sortie_de_stock(self):
        """Un SMTP en panne ne doit jamais faire échouer une vente."""
        from unittest.mock import patch

        with patch.object(
            services.EmailMultiAlternatives, "send", side_effect=OSError("SMTP injoignable")
        ):
            with self.captureOnCommitCallbacks(execute=True):
                services.enregistrer_mouvement(self.produit, Mouvement.SORTIE, 7)

        self.produit.refresh_from_db()
        self.assertEqual(self.produit.quantite_stock, 8)  # la sortie est bien passée
        self.assertEqual(Mouvement.objects.count(), 1)


class AuthentificationTests(BaseApplicationTestCase):
    """Vérifie que les pages privées sont bien protégées."""

    def test_pages_privees_redirigent_vers_connexion(self):
        """AUCUNE page de l'application n'est accessible sans connexion."""
        categorie = Categorie.objects.create(nom="Test")
        produit = Produit.objects.create(
            reference="AUT-001", nom="Produit", categorie=categorie,
            prix_achat=100, prix_vente=150,
        )
        urls = [
            reverse("inventory:produit_liste"),
            reverse("inventory:produit_creer"),
            reverse("inventory:produit_detail", kwargs={"pk": produit.pk}),
            reverse("inventory:produit_modifier", kwargs={"pk": produit.pk}),
            reverse("inventory:mouvement_liste"),
            reverse("inventory:entree_stock"),
            reverse("inventory:sortie_stock"),
        ]
        for url in urls:
            with self.subTest(url=url):
                reponse = self.client.get(url)
                self.assertEqual(reponse.status_code, 302)
                self.assertIn(reverse("inventory:connexion"), reponse.url)

    def test_aucun_lien_prive_visible_sans_connexion(self):
        """La page de connexion ne doit exposer ni menu ni lien vers l'app."""
        reponse = self.client.get(reverse("inventory:connexion"))
        contenu = reponse.content.decode()

        self.assertNotIn(reverse("inventory:produit_liste"), contenu)
        self.assertNotIn(reverse("inventory:mouvement_liste"), contenu)
        self.assertNotIn(reverse("inventory:entree_stock"), contenu)
        self.assertNotIn(reverse("inventory:sortie_stock"), contenu)

    def test_utilisateur_connecte_ne_voit_pas_la_page_de_connexion(self):
        """Déjà connecté (y compris via /admin/) : /connexion/ renvoie vers l'app."""
        creer_utilisateur("testeur", groupe="Magasinier")
        self.client.login(username="testeur", password=MOT_DE_PASSE_TEST)

        reponse = self.client.get(reverse("inventory:connexion"))
        self.assertEqual(reponse.status_code, 302)
        self.assertEqual(reponse.url, reverse("inventory:produit_liste"))

    def test_page_connexion_accessible_sans_authentification(self):
        reponse = self.client.get(reverse("inventory:connexion"))
        self.assertEqual(reponse.status_code, 200)

    def test_connexion_par_le_formulaire(self):
        """Connexion via le vrai formulaire de la vue, pas via client.login()."""
        creer_utilisateur("testeur", groupe="Magasinier")

        reponse = self.client.post(
            reverse("inventory:connexion"),
            {"username": "testeur", "password": MOT_DE_PASSE_TEST},
        )
        self.assertEqual(reponse.status_code, 302)
        self.assertEqual(reponse.url, reverse("inventory:produit_liste"))

        # La page privée est maintenant accessible.
        reponse = self.client.get(reverse("inventory:produit_liste"))
        self.assertEqual(reponse.status_code, 200)

    def test_connexion_refusee_avec_mauvais_mot_de_passe(self):
        creer_utilisateur("testeur", groupe="Magasinier")

        reponse = self.client.post(
            reverse("inventory:connexion"),
            {"username": "testeur", "password": "mauvais"},
        )
        self.assertEqual(reponse.status_code, 200)
        self.assertContains(reponse, "Identifiants incorrects")


class ParcoursUtilisateurTests(BaseApplicationTestCase):
    """
    Parcours complet : connexion, création de produit, recherche, fiche,
    entrée, sortie, sortie refusée, historique, filtres, déconnexion.
    """

    def setUp(self):
        # Le parcours complet inclut la creation de produits : role Gerant.
        self.utilisateur = creer_utilisateur("testeur", groupe="Gerant")
        self.categorie = Categorie.objects.create(nom="Alimentation")
        self.fournisseur = Fournisseur.objects.create(nom="Fournisseur test")

    def test_parcours_complet(self):
        # 1-2. Connexion
        self.assertTrue(self.client.login(username="testeur", password=MOT_DE_PASSE_TEST))

        # 3. Liste des produits
        reponse = self.client.get(reverse("inventory:produit_liste"))
        self.assertEqual(reponse.status_code, 200)

        # 4. Création d'un produit
        reponse = self.client.post(
            reverse("inventory:produit_creer"),
            {
                "reference": "PAR-001",
                "nom": "Riz parfumé 25kg",
                "categorie": self.categorie.pk,
                "fournisseur": self.fournisseur.pk,
                "prix_achat": "12000",
                "prix_vente": "14000",
                "seuil_alerte": "5",
                "actif": "on",
            },
        )
        self.assertEqual(reponse.status_code, 302)
        produit = Produit.objects.get(reference="PAR-001")
        self.assertEqual(produit.quantite_stock, 0)

        # 5-6. Le produit est dans la liste et retrouvable par recherche
        reponse = self.client.get(reverse("inventory:produit_liste"), {"q": "Riz parfumé"})
        self.assertContains(reponse, "PAR-001")
        reponse = self.client.get(reverse("inventory:produit_liste"), {"q": "PAR-001"})
        self.assertContains(reponse, "Riz parfumé")

        # 7. Fiche produit
        reponse = self.client.get(produit.get_absolute_url())
        self.assertEqual(reponse.status_code, 200)

        # 8-9. Entrée de stock : le stock augmente
        reponse = self.client.post(
            reverse("inventory:entree_stock"),
            {"produit": produit.pk, "quantite": "20", "motif": "Réception"},
        )
        self.assertEqual(reponse.status_code, 302)
        produit.refresh_from_db()
        self.assertEqual(produit.quantite_stock, 20)

        # 10-11. Sortie normale : le stock diminue
        reponse = self.client.post(
            reverse("inventory:sortie_stock"),
            {"produit": produit.pk, "quantite": "8", "motif": "Vente"},
        )
        self.assertEqual(reponse.status_code, 302)
        produit.refresh_from_db()
        self.assertEqual(produit.quantite_stock, 12)

        # 12-14. Sortie supérieure au stock : refusée proprement, stock inchangé
        reponse = self.client.post(
            reverse("inventory:sortie_stock"),
            {"produit": produit.pk, "quantite": "999", "motif": "Vente"},
        )
        self.assertEqual(reponse.status_code, 200)  # formulaire réaffiché
        self.assertContains(reponse, "Stock insuffisant")
        produit.refresh_from_db()
        self.assertEqual(produit.quantite_stock, 12)
        self.assertEqual(Mouvement.objects.count(), 2)  # seuls l'entrée et la sortie valide

        # 15. Historique
        reponse = self.client.get(reverse("inventory:mouvement_liste"))
        self.assertEqual(reponse.status_code, 200)
        self.assertEqual(len(reponse.context["mouvements"]), 2)

        # 16. Filtres combinés de l'historique
        reponse = self.client.get(
            reverse("inventory:mouvement_liste"),
            {"produit": produit.pk, "type": Mouvement.SORTIE},
        )
        self.assertEqual(len(reponse.context["mouvements"]), 1)
        self.assertEqual(reponse.context["mouvements"][0].type_mouvement, Mouvement.SORTIE)

        # 17. Les 20 derniers mouvements sur la fiche produit
        reponse = self.client.get(produit.get_absolute_url())
        self.assertEqual(len(reponse.context["mouvements"]), 2)

        # 18. Déconnexion (POST obligatoire depuis Django 5)
        reponse = self.client.post(reverse("inventory:deconnexion"))
        self.assertEqual(reponse.status_code, 302)
        reponse = self.client.get(reverse("inventory:produit_liste"))
        self.assertEqual(reponse.status_code, 302)  # redirigé vers la connexion

    def test_fiche_produit_limite_a_20_mouvements(self):
        self.client.login(username="testeur", password=MOT_DE_PASSE_TEST)
        produit = Produit.objects.create(
            reference="LIM-001",
            nom="Produit limite",
            categorie=self.categorie,
            prix_achat=100,
            prix_vente=150,
        )
        for _ in range(25):
            services.enregistrer_mouvement(produit, Mouvement.ENTREE, 1)

        reponse = self.client.get(produit.get_absolute_url())
        self.assertEqual(len(reponse.context["mouvements"]), 20)


class ProduitListeFiltresTests(BaseApplicationTestCase):
    """Filtres et recherche de la liste des produits."""

    def setUp(self):
        self.utilisateur = creer_utilisateur("testeur", groupe="Magasinier")
        self.client.login(username="testeur", password=MOT_DE_PASSE_TEST)
        self.categorie_a = Categorie.objects.create(nom="Alimentation")
        self.categorie_b = Categorie.objects.create(nom="Boissons")
        self.produit_a = Produit.objects.create(
            reference="A-001", nom="Riz", categorie=self.categorie_a,
            prix_achat=100, prix_vente=150, quantite_stock=100, seuil_alerte=10,
        )
        self.produit_b = Produit.objects.create(
            reference="B-001", nom="Jus", categorie=self.categorie_b,
            prix_achat=100, prix_vente=150, quantite_stock=2, seuil_alerte=10, actif=False,
        )

    def test_filtre_categorie(self):
        reponse = self.client.get(reverse("inventory:produit_liste"), {"categorie": self.categorie_b.pk})
        self.assertEqual(list(reponse.context["produits"]), [self.produit_b])

    def test_filtre_statut_actif(self):
        reponse = self.client.get(reverse("inventory:produit_liste"), {"actif": "1"})
        self.assertEqual(list(reponse.context["produits"]), [self.produit_a])

    def test_filtre_stock_bas(self):
        reponse = self.client.get(reverse("inventory:produit_liste"), {"stock_bas": "1"})
        self.assertEqual(list(reponse.context["produits"]), [self.produit_b])

    def test_filtre_invalide_est_ignore_sans_erreur(self):
        reponse = self.client.get(reverse("inventory:produit_liste"), {"categorie": "abc"})
        self.assertEqual(reponse.status_code, 200)
        self.assertEqual(len(reponse.context["produits"]), 2)


class TableauBordTests(BaseApplicationTestCase):
    """KPI, produits en alerte et derniers mouvements du tableau de bord."""

    def setUp(self):
        self.gerant = creer_utilisateur("gerant", groupe="Gerant")
        self.client.login(username="gerant", password=MOT_DE_PASSE_TEST)

        self.categorie = Categorie.objects.create(nom="Alimentation")
        # 10 x 1000 = 10 000 de valeur, stock normal
        self.produit_normal = Produit.objects.create(
            reference="TB-001", nom="Produit normal", categorie=self.categorie,
            prix_achat=Decimal("1000.00"), prix_vente=Decimal("1500.00"),
            quantite_stock=10, seuil_alerte=5,
        )
        # 3 <= 5 : sous seuil. 3 x 500 = 1 500
        self.produit_sous_seuil = Produit.objects.create(
            reference="TB-002", nom="Produit sous seuil", categorie=self.categorie,
            prix_achat=Decimal("500.00"), prix_vente=Decimal("800.00"),
            quantite_stock=3, seuil_alerte=5,
        )
        # 0 : rupture. Valeur 0
        self.produit_rupture = Produit.objects.create(
            reference="TB-003", nom="Produit en rupture", categorie=self.categorie,
            prix_achat=Decimal("200.00"), prix_vente=Decimal("300.00"),
            quantite_stock=0, seuil_alerte=5,
        )
        # Inactif : ne doit compter dans aucun KPI
        self.produit_inactif = Produit.objects.create(
            reference="TB-004", nom="Produit inactif", categorie=self.categorie,
            prix_achat=Decimal("9999.00"), prix_vente=Decimal("9999.00"),
            quantite_stock=50, seuil_alerte=5, actif=False,
        )

    def test_calcul_des_kpis(self):
        kpis = statistiques.kpis_stock()

        self.assertEqual(kpis["produits_actifs"], 3)                 # l'inactif est exclu
        self.assertEqual(kpis["quantite_totale"], 13)                # 10 + 3 + 0
        self.assertEqual(kpis["valeur_stock"], Decimal("11500.00"))  # 10000 + 1500 + 0
        self.assertEqual(kpis["produits_en_alerte"], 2)              # sous seuil + rupture
        self.assertEqual(kpis["produits_en_rupture"], 1)

    def test_kpis_sur_base_vide(self):
        """Aucun produit : les agrégats valent 0, pas None."""
        Produit.objects.all().delete()
        kpis = statistiques.kpis_stock()

        self.assertEqual(kpis["produits_actifs"], 0)
        self.assertEqual(kpis["quantite_totale"], 0)
        self.assertEqual(kpis["valeur_stock"], Decimal("0.00"))

    def test_produits_en_alerte(self):
        alertes = list(statistiques.produits_en_alerte())

        # Triés par stock croissant : la rupture d'abord.
        self.assertEqual(alertes, [self.produit_rupture, self.produit_sous_seuil])
        self.assertNotIn(self.produit_normal, alertes)
        self.assertNotIn(self.produit_inactif, alertes)

    def test_derniers_mouvements_limite_et_ordre(self):
        for i in range(12):
            services.enregistrer_mouvement(self.produit_normal, Mouvement.ENTREE, i + 1)

        derniers = list(statistiques.derniers_mouvements(10))

        self.assertEqual(len(derniers), 10)
        self.assertEqual(derniers[0].quantite, 12)  # le plus récent en premier

    def test_page_tableau_bord_affiche_kpis_et_alertes(self):
        reponse = self.client.get(reverse("inventory:tableau_bord"))

        self.assertEqual(reponse.status_code, 200)
        self.assertEqual(reponse.context["kpis"]["produits_actifs"], 3)
        self.assertEqual(len(reponse.context["produits_en_alerte"]), 2)
        self.assertContains(reponse, "Produit en rupture")
        self.assertContains(reponse, "Rupture")

    def test_etat_vide_aucune_alerte(self):
        """Sans produit en alerte, un message remplace le tableau."""
        self.produit_sous_seuil.delete()
        self.produit_rupture.delete()

        reponse = self.client.get(reverse("inventory:tableau_bord"))
        self.assertContains(reponse, "Aucun produit en alerte")

    def test_etat_vide_aucun_mouvement(self):
        reponse = self.client.get(reverse("inventory:tableau_bord"))
        self.assertContains(reponse, "Aucun mouvement enregistr")


class ExportExcelTests(BaseApplicationTestCase):
    """Génération et formatage du fichier Excel."""

    def setUp(self):
        self.gerant = creer_utilisateur("gerant", groupe="Gerant")
        self.client.login(username="gerant", password=MOT_DE_PASSE_TEST)

        self.categorie = Categorie.objects.create(nom="Alimentation")
        self.fournisseur = Fournisseur.objects.create(nom="Fournisseur A")
        self.produit_normal = Produit.objects.create(
            reference="EX-001", nom="Riz 25kg", unite="sac", categorie=self.categorie,
            fournisseur=self.fournisseur, prix_achat=Decimal("12000.00"),
            prix_vente=Decimal("14000.00"), quantite_stock=10, seuil_alerte=5,
        )
        self.produit_rupture = Produit.objects.create(
            reference="EX-002", nom="Huile 5L", categorie=self.categorie,
            prix_achat=Decimal("5000.00"), prix_vente=Decimal("6000.00"),
            quantite_stock=0, seuil_alerte=5,
        )

    def _telecharger_classeur(self, parametres=None):
        reponse = self.client.get(reverse("inventory:export_stock_excel"), parametres or {})
        self.assertEqual(reponse.status_code, 200)
        return reponse, load_workbook(BytesIO(reponse.content))

    def test_generation_du_fichier(self):
        reponse, classeur = self._telecharger_classeur()

        self.assertEqual(
            reponse["Content-Type"],
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        self.assertIn("attachment;", reponse["Content-Disposition"])
        self.assertIn(".xlsx", reponse["Content-Disposition"])
        self.assertEqual(classeur.active.title, "État du stock")

    def test_presence_des_colonnes(self):
        _, classeur = self._telecharger_classeur()
        feuille = classeur.active

        entetes = [cellule.value for cellule in feuille[4]]
        self.assertEqual(entetes, [titre for titre, _ in exports.COLONNES])

    def test_donnees_et_statuts(self):
        _, classeur = self._telecharger_classeur()
        feuille = classeur.active

        lignes = {feuille.cell(row=l, column=1).value: l for l in range(5, feuille.max_row + 1)}
        self.assertEqual(set(lignes), {"EX-001", "EX-002"})

        ligne_normale = lignes["EX-001"]
        self.assertEqual(feuille.cell(row=ligne_normale, column=2).value, "Riz 25kg")
        self.assertEqual(feuille.cell(row=ligne_normale, column=3).value, "sac")
        self.assertEqual(feuille.cell(row=ligne_normale, column=4).value, "Alimentation")
        self.assertEqual(feuille.cell(row=ligne_normale, column=5).value, "Fournisseur A")
        self.assertEqual(feuille.cell(row=ligne_normale, column=6).value, 10)
        self.assertEqual(feuille.cell(row=ligne_normale, column=8).value, "Stock normal")
        # Valeur du stock = quantité x prix d'achat
        self.assertEqual(feuille.cell(row=ligne_normale, column=10).value, Decimal("120000.00"))

        self.assertEqual(feuille.cell(row=lignes["EX-002"], column=8).value, "Rupture")

    def test_formatage_de_base(self):
        _, classeur = self._telecharger_classeur()
        feuille = classeur.active

        # En-têtes en gras
        self.assertTrue(feuille.cell(row=4, column=1).font.bold)
        # Filtre automatique et volets figés
        self.assertIsNotNone(feuille.auto_filter.ref)
        self.assertEqual(feuille.freeze_panes, "A5")
        # Largeur de colonne adaptée
        self.assertEqual(feuille.column_dimensions["B"].width, 34)
        # Format monétaire sur la valeur du stock
        self.assertEqual(feuille.cell(row=5, column=10).number_format, "#,##0.00")
        # Statut coloré
        self.assertNotEqual(feuille.cell(row=5, column=8).fill.fgColor.rgb, "00000000")
        # Date de génération présente
        self.assertIn("Document", feuille["A2"].value)

    def test_export_respecte_les_filtres(self):
        """L'export porte sur ce que l'utilisateur a filtré à l'écran."""
        _, classeur = self._telecharger_classeur({"stock_bas": "1"})
        feuille = classeur.active

        references = [
            feuille.cell(row=l, column=1).value for l in range(5, feuille.max_row + 1)
        ]
        self.assertEqual(references, ["EX-002"])  # seul le produit en rupture


class PermissionsTests(BaseApplicationTestCase):
    """Les restrictions sont appliquées côté serveur, pas seulement masquées."""

    def setUp(self):
        creer_utilisateur("gerant", groupe="Gerant")
        creer_utilisateur("magasinier", groupe="Magasinier")
        creer_utilisateur("sans_role")  # aucun groupe

        self.categorie = Categorie.objects.create(nom="Alimentation")
        self.produit = Produit.objects.create(
            reference="PER-001", nom="Produit", categorie=self.categorie,
            prix_achat=100, prix_vente=150, quantite_stock=10,
        )

    def _connecter(self, nom):
        self.assertTrue(self.client.login(username=nom, password=MOT_DE_PASSE_TEST))

    def test_acces_gerant(self):
        """Le gérant accède à tout, y compris aux fonctions sensibles."""
        self._connecter("gerant")
        urls = [
            reverse("inventory:tableau_bord"),
            reverse("inventory:export_stock_excel"),
            reverse("inventory:produit_liste"),
            reverse("inventory:produit_creer"),
            reverse("inventory:produit_modifier", kwargs={"pk": self.produit.pk}),
            reverse("inventory:mouvement_liste"),
            reverse("inventory:entree_stock"),
            reverse("inventory:sortie_stock"),
        ]
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_acces_magasinier(self):
        """Le magasinier gère le quotidien : consultation et mouvements."""
        self._connecter("magasinier")
        urls_autorisees = [
            reverse("inventory:produit_liste"),
            reverse("inventory:produit_detail", kwargs={"pk": self.produit.pk}),
            reverse("inventory:mouvement_liste"),
            reverse("inventory:entree_stock"),
            reverse("inventory:sortie_stock"),
        ]
        for url in urls_autorisees:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_acces_refuse_au_magasinier_sur_les_vues_sensibles(self):
        """Tableau de bord, export et gestion des produits lui sont interdits."""
        self._connecter("magasinier")
        urls_interdites = [
            reverse("inventory:tableau_bord"),
            reverse("inventory:export_stock_excel"),
            reverse("inventory:produit_creer"),
            reverse("inventory:produit_modifier", kwargs={"pk": self.produit.pk}),
        ]
        for url in urls_interdites:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)

    def test_magasinier_ne_peut_pas_creer_un_produit_en_post(self):
        """La restriction tient aussi en POST, pas seulement à l'affichage."""
        self._connecter("magasinier")
        reponse = self.client.post(
            reverse("inventory:produit_creer"),
            {
                "reference": "HACK-001", "nom": "Produit interdit",
                "categorie": self.categorie.pk, "prix_achat": "1", "prix_vente": "2",
                "seuil_alerte": "1", "actif": "on",
            },
        )
        self.assertEqual(reponse.status_code, 403)
        self.assertFalse(Produit.objects.filter(reference="HACK-001").exists())

    def test_utilisateur_sans_role_na_acces_a_rien(self):
        self._connecter("sans_role")
        for url in [
            reverse("inventory:produit_liste"),
            reverse("inventory:mouvement_liste"),
            reverse("inventory:tableau_bord"),
            reverse("inventory:export_stock_excel"),
        ]:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)

    def test_liens_sensibles_masques_pour_le_magasinier(self):
        """L'interface est cohérente avec les droits (en plus du contrôle serveur)."""
        self._connecter("magasinier")
        contenu = self.client.get(reverse("inventory:produit_liste")).content.decode()

        self.assertNotIn(reverse("inventory:tableau_bord"), contenu)
        self.assertNotIn(reverse("inventory:export_stock_excel"), contenu)
        self.assertNotIn(reverse("inventory:produit_creer"), contenu)


@override_settings(**PARAMETRES_EMAIL_TEST)
class RapportQuotidienTests(BaseApplicationTestCase):
    """Commande rapport_quotidien."""

    def setUp(self):
        self.categorie = Categorie.objects.create(nom="Alimentation")
        self.produit = Produit.objects.create(
            reference="RAP-001", nom="Riz 25kg", categorie=self.categorie,
            prix_achat=Decimal("12000.00"), prix_vente=Decimal("14000.00"),
            quantite_stock=20, seuil_alerte=5,
        )

    def test_statistiques_du_jour(self):
        services.enregistrer_mouvement(self.produit, Mouvement.ENTREE, 10)
        services.enregistrer_mouvement(self.produit, Mouvement.SORTIE, 4)
        services.enregistrer_mouvement(self.produit, Mouvement.SORTIE, 6)

        stats = statistiques.statistiques_du_jour()

        self.assertEqual(stats["entrees_nombre"], 1)
        self.assertEqual(stats["entrees_quantite"], 10)
        self.assertEqual(stats["sorties_nombre"], 2)
        self.assertEqual(stats["sorties_quantite"], 10)
        self.assertEqual(stats["mouvements_nombre"], 3)

    def test_commande_envoie_le_rapport(self):
        services.enregistrer_mouvement(self.produit, Mouvement.SORTIE, 18)  # passe sous le seuil
        mail.outbox = []

        call_command("rapport_quotidien", verbosity=0)

        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertIn("Rapport quotidien", message.subject)
        self.assertEqual(message.to, ["gerant@stockflow.test"])

        corps_html = message.alternatives[0][0]
        self.assertIn("Rapport quotidien", corps_html)
        self.assertIn("Riz 25kg", corps_html)  # produit en alerte listé
        self.assertIn("Mouvements de la journ", corps_html)

        # La version texte existe aussi.
        self.assertIn("RAPPORT QUOTIDIEN", message.body)

    def test_commande_mode_apercu_n_envoie_rien(self):
        call_command("rapport_quotidien", "--apercu", verbosity=0)
        self.assertEqual(len(mail.outbox), 0)

    def test_commande_refuse_une_date_invalide(self):
        from django.core.management.base import CommandError

        with self.assertRaises(CommandError):
            call_command("rapport_quotidien", "--jour", "pas-une-date", verbosity=0)

    @override_settings(ALERTE_EMAIL_DESTINATAIRE="")
    def test_commande_echoue_proprement_sans_destinataire(self):
        from django.core.management.base import CommandError

        with self.assertRaises(CommandError):
            call_command("rapport_quotidien", verbosity=0)


class InitialiserGroupesTests(TestCase):
    """La commande de création des groupes est rejouable sans effet de bord."""

    def test_commande_idempotente(self):
        call_command("initialiser_groupes", verbosity=0)
        call_command("initialiser_groupes", verbosity=0)

        self.assertEqual(Group.objects.filter(name__in=["Gerant", "Magasinier"]).count(), 2)
        gerant = Group.objects.get(name="Gerant")
        magasinier = Group.objects.get(name="Magasinier")

        # On compare aux listes déclarées par la commande plutôt qu'à des
        # nombres en dur : ajouter une permission ne casse plus ce test.
        from inventory.management.commands.initialiser_groupes import (
            PERMISSIONS_GERANT,
            PERMISSIONS_MAGASINIER,
        )

        self.assertEqual(
            set(gerant.permissions.values_list("codename", flat=True)),
            set(PERMISSIONS_GERANT),
        )
        codes_magasinier = set(magasinier.permissions.values_list("codename", flat=True))
        self.assertEqual(codes_magasinier, set(PERMISSIONS_MAGASINIER))

        # Les fonctions sensibles restent hors de portée du magasinier.
        for interdit in (
            "acceder_tableau_bord",
            "exporter_stock",
            "add_produit",
            "add_commande",
            "change_commande",
        ):
            with self.subTest(permission=interdit):
                self.assertNotIn(interdit, codes_magasinier)


@override_settings(**PARAMETRES_EMAIL_TEST)
class EncodageEmailsTests(TestCase):
    """
    Non-regression : le backend console de Django ecrit sur la sortie standard,
    encodee en cp1252 sous Windows. Un caractere hors cp1252 dans un template
    email (ex : le signe moins typographique U+2212) faisait echouer l'envoi
    silencieusement en developpement.
    """

    def setUp(self):
        self.categorie = Categorie.objects.create(nom="Alimentation")
        self.produit = Produit.objects.create(
            reference="ENC-001", nom="Produit accentue", categorie=self.categorie,
            prix_achat=Decimal("1000.00"), prix_vente=Decimal("1500.00"),
            quantite_stock=15, seuil_alerte=10,
        )

    def _verifier_encodable(self, message):
        for contenu in [message.subject, message.body] + [a[0] for a in message.alternatives]:
            contenu.encode("cp1252")  # leve UnicodeEncodeError si un caractere passe

    def test_email_alerte_encodable_en_console_windows(self):
        with self.captureOnCommitCallbacks(execute=True):
            services.enregistrer_mouvement(self.produit, Mouvement.SORTIE, 7)

        self.assertEqual(len(mail.outbox), 1)
        self._verifier_encodable(mail.outbox[0])

    def test_email_rapport_encodable_en_console_windows(self):
        call_command("rapport_quotidien", verbosity=0)

        self.assertEqual(len(mail.outbox), 1)
        self._verifier_encodable(mail.outbox[0])


class TemplatesTests(BaseApplicationTestCase):
    """
    Non-regression : en Django, {# ... #} ne commente QUE sur une seule ligne.
    Un commentaire etale sur plusieurs lignes n'est pas reconnu et s'affiche
    tel quel dans la page (bug constate sur le menu de navigation).
    """

    def setUp(self):
        creer_utilisateur("gerant", groupe="Gerant")
        self.client.login(username="gerant", password=MOT_DE_PASSE_TEST)
        self.categorie = Categorie.objects.create(nom="Alimentation")
        self.produit = Produit.objects.create(
            reference="TPL-001", nom="Produit", categorie=self.categorie,
            prix_achat=100, prix_vente=150, quantite_stock=10,
        )

    def test_aucun_marqueur_de_template_dans_les_pages(self):
        urls = [
            reverse("inventory:tableau_bord"),
            reverse("inventory:produit_liste"),
            reverse("inventory:produit_detail", kwargs={"pk": self.produit.pk}),
            reverse("inventory:produit_creer"),
            reverse("inventory:mouvement_liste"),
            reverse("inventory:entree_stock"),
            reverse("inventory:sortie_stock"),
        ]
        for url in urls:
            with self.subTest(url=url):
                contenu = self.client.get(url).content.decode()
                for marqueur in ("{#", "#}", "{%", "%}", "{{", "}}"):
                    self.assertNotIn(
                        marqueur, contenu,
                        f"Marqueur de template {marqueur!r} visible dans la page {url}",
                    )

    def test_page_connexion_sans_marqueur_de_template(self):
        self.client.logout()
        contenu = self.client.get(reverse("inventory:connexion")).content.decode()
        for marqueur in ("{#", "#}", "{%", "%}", "{{", "}}"):
            self.assertNotIn(marqueur, contenu)


class ValeurParCategorieTests(BaseApplicationTestCase):
    """Ventilation de la valeur du stock par categorie (tableau de bord)."""

    def setUp(self):
        self.gerant = creer_utilisateur("gerant", groupe="Gerant")
        self.client.login(username="gerant", password=MOT_DE_PASSE_TEST)

        self.alimentation = Categorie.objects.create(nom="Alimentation")
        self.boissons = Categorie.objects.create(nom="Boissons")
        Categorie.objects.create(nom="Categorie vide")

        # 10 x 1000 = 10 000
        Produit.objects.create(
            reference="VC-001", nom="Riz", categorie=self.alimentation,
            prix_achat=Decimal("1000.00"), prix_vente=Decimal("1500.00"), quantite_stock=10,
        )
        # 4 x 500 = 2 000
        Produit.objects.create(
            reference="VC-002", nom="Jus", categorie=self.boissons,
            prix_achat=Decimal("500.00"), prix_vente=Decimal("800.00"), quantite_stock=4,
        )
        # Produit desactive : ne doit pas compter
        Produit.objects.create(
            reference="VC-003", nom="Ancien", categorie=self.boissons,
            prix_achat=Decimal("9999.00"), prix_vente=Decimal("9999.00"),
            quantite_stock=50, actif=False,
        )

    def test_valeurs_et_ordre(self):
        categories = statistiques.valeur_par_categorie()

        self.assertEqual([c.nom for c in categories], ["Alimentation", "Boissons"])
        self.assertEqual(categories[0].valeur, Decimal("10000.00"))
        self.assertEqual(categories[1].valeur, Decimal("2000.00"))

    def test_categorie_sans_stock_exclue(self):
        noms = [c.nom for c in statistiques.valeur_par_categorie()]
        self.assertNotIn("Categorie vide", noms)

    def test_pourcentages_relatifs_au_maximum(self):
        categories = statistiques.valeur_par_categorie()

        self.assertEqual(categories[0].pourcentage, 100)
        self.assertEqual(categories[1].pourcentage, 20)  # 2000 / 10000

    def test_affichage_sur_le_tableau_de_bord(self):
        reponse = self.client.get(reverse("inventory:tableau_bord"))

        self.assertEqual(reponse.status_code, 200)
        self.assertContains(reponse, "Valeur du stock par catégorie")
        self.assertContains(reponse, "Alimentation")

    def test_etat_vide_sans_aucun_stock(self):
        Produit.objects.all().update(quantite_stock=0)

        reponse = self.client.get(reverse("inventory:tableau_bord"))
        self.assertContains(reponse, "Aucune valeur à afficher")


class TracabiliteMouvementTests(BaseApplicationTestCase):
    """Champs de traçabilité : utilisateur, stock après, document, destination."""

    def setUp(self):
        self.magasinier = creer_utilisateur("magasinier", groupe="Magasinier")
        self.categorie = Categorie.objects.create(nom="Alimentation")
        self.produit = Produit.objects.create(
            reference="TRA-001", nom="Riz 25kg", categorie=self.categorie,
            prix_achat=Decimal("1000.00"), prix_vente=Decimal("1400.00"),
            quantite_stock=50, seuil_alerte=10,
        )

    def test_service_enregistre_l_utilisateur(self):
        mouvement = services.enregistrer_mouvement(
            self.produit, Mouvement.SORTIE, 5, motif="Vente",
            utilisateur=self.magasinier,
        )
        self.assertEqual(mouvement.utilisateur, self.magasinier)

    def test_utilisateur_facultatif(self):
        """Les scripts (seed, import) peuvent enregistrer sans utilisateur."""
        mouvement = services.enregistrer_mouvement(self.produit, Mouvement.ENTREE, 5)
        self.assertIsNone(mouvement.utilisateur)

    def test_stock_apres_est_fige(self):
        """stock_apres garde la valeur du stock au moment du mouvement."""
        premier = services.enregistrer_mouvement(self.produit, Mouvement.SORTIE, 20)
        second = services.enregistrer_mouvement(self.produit, Mouvement.SORTIE, 10)

        self.assertEqual(premier.stock_apres, 30)
        self.assertEqual(second.stock_apres, 20)

        # Le stock du produit continue d'évoluer, l'historique ne bouge plus.
        services.enregistrer_mouvement(self.produit, Mouvement.ENTREE, 100)
        premier.refresh_from_db()
        self.assertEqual(premier.stock_apres, 30)

    def test_document_et_destination_enregistres(self):
        mouvement = services.enregistrer_mouvement(
            self.produit, Mouvement.SORTIE, 5, motif="Vente",
            document="BON-0412", destination="Chantier Godomey",
        )
        self.assertEqual(mouvement.document, "BON-0412")
        self.assertEqual(mouvement.destination, "Chantier Godomey")

    def test_vue_sortie_enregistre_l_utilisateur_connecte(self):
        """La traçabilité vient de la session, pas d'un champ du formulaire."""
        self.client.login(username="magasinier", password=MOT_DE_PASSE_TEST)

        reponse = self.client.post(
            reverse("inventory:sortie_stock"),
            {
                "produit": self.produit.pk, "quantite": "5", "motif": "Vente",
                "document": "BON-0001", "destination": "Client Sossou",
            },
        )
        self.assertEqual(reponse.status_code, 302)

        mouvement = Mouvement.objects.get()
        self.assertEqual(mouvement.utilisateur, self.magasinier)
        self.assertEqual(mouvement.stock_apres, 45)
        self.assertEqual(mouvement.document, "BON-0001")
        self.assertEqual(mouvement.destination, "Client Sossou")

    def test_colonnes_visibles_dans_l_historique(self):
        self.client.login(username="magasinier", password=MOT_DE_PASSE_TEST)
        services.enregistrer_mouvement(
            self.produit, Mouvement.SORTIE, 5, motif="Vente",
            utilisateur=self.magasinier, document="BON-0412",
            destination="Chantier Godomey",
        )

        reponse = self.client.get(reverse("inventory:mouvement_liste"))

        self.assertContains(reponse, "BON-0412")
        self.assertContains(reponse, "Chantier Godomey")
        self.assertContains(reponse, "magasinier")
        self.assertContains(reponse, "Stock après")

    def test_mouvement_ancien_sans_stock_apres_s_affiche(self):
        """Les mouvements antérieurs au champ ne cassent pas l'affichage."""
        self.client.login(username="magasinier", password=MOT_DE_PASSE_TEST)
        mouvement = services.enregistrer_mouvement(self.produit, Mouvement.SORTIE, 5)
        Mouvement.objects.filter(pk=mouvement.pk).update(stock_apres=None)

        reponse = self.client.get(reverse("inventory:mouvement_liste"))
        self.assertEqual(reponse.status_code, 200)


class AjustementStockTests(BaseApplicationTestCase):
    """Ajustement : constat de perte, casse ou écart d'inventaire."""

    def setUp(self):
        self.magasinier = creer_utilisateur("magasinier", groupe="Magasinier")
        self.client.login(username="magasinier", password=MOT_DE_PASSE_TEST)
        self.categorie = Categorie.objects.create(nom="Alimentation")
        self.produit = Produit.objects.create(
            reference="AJU-001", nom="Bouteille", categorie=self.categorie,
            prix_achat=Decimal("500.00"), prix_vente=Decimal("800.00"),
            quantite_stock=50, seuil_alerte=10,
        )

    def test_ajustement_diminue_le_stock(self):
        mouvement = services.enregistrer_mouvement(
            self.produit, Mouvement.AJUSTEMENT, 3, motif="Casse",
        )

        self.produit.refresh_from_db()
        self.assertEqual(self.produit.quantite_stock, 47)
        self.assertEqual(mouvement.type_mouvement, Mouvement.AJUSTEMENT)
        self.assertEqual(mouvement.stock_apres, 47)

    def test_motif_obligatoire(self):
        """Un ajustement sans justification est refusé."""
        with self.assertRaises(ValidationError):
            services.enregistrer_mouvement(self.produit, Mouvement.AJUSTEMENT, 3)
        with self.assertRaises(ValidationError):
            services.enregistrer_mouvement(self.produit, Mouvement.AJUSTEMENT, 3, motif="   ")

        self.produit.refresh_from_db()
        self.assertEqual(self.produit.quantite_stock, 50)
        self.assertEqual(Mouvement.objects.count(), 0)

    def test_ajustement_superieur_au_stock_refuse(self):
        with self.assertRaises(ValidationError):
            services.enregistrer_mouvement(
                self.produit, Mouvement.AJUSTEMENT, 999, motif="Casse",
            )

        self.produit.refresh_from_db()
        self.assertEqual(self.produit.quantite_stock, 50)

    @override_settings(**PARAMETRES_EMAIL_TEST)
    def test_ajustement_qui_franchit_le_seuil_alerte(self):
        """Une casse qui fait passer sous le seuil prévient le gérant."""
        with self.captureOnCommitCallbacks(execute=True):
            services.enregistrer_mouvement(
                self.produit, Mouvement.AJUSTEMENT, 45, motif="Casse",
            )

        self.produit.refresh_from_db()
        self.assertEqual(self.produit.quantite_stock, 5)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Bouteille", mail.outbox[0].subject)

    def test_formulaire_ajustement_exige_un_motif(self):
        reponse = self.client.post(
            reverse("inventory:ajustement_stock"),
            {"produit": self.produit.pk, "quantite": "3", "motif": ""},
        )

        self.assertEqual(reponse.status_code, 200)  # formulaire réaffiché
        self.produit.refresh_from_db()
        self.assertEqual(self.produit.quantite_stock, 50)
        self.assertEqual(Mouvement.objects.count(), 0)

    def test_formulaire_ajustement_sans_champ_destination(self):
        """Un ajustement n'a ni client ni destination."""
        reponse = self.client.get(reverse("inventory:ajustement_stock"))
        self.assertNotIn("destination", reponse.context["form"].fields)

    def test_vue_ajustement_complete(self):
        reponse = self.client.post(
            reverse("inventory:ajustement_stock"),
            {"produit": self.produit.pk, "quantite": "3", "motif": "Casse au dépôt"},
        )

        self.assertEqual(reponse.status_code, 302)
        mouvement = Mouvement.objects.get()
        self.assertEqual(mouvement.type_mouvement, Mouvement.AJUSTEMENT)
        self.assertEqual(mouvement.motif, "Casse au dépôt")
        self.assertEqual(mouvement.utilisateur, self.magasinier)

    def test_badge_ajustement_dans_l_historique(self):
        services.enregistrer_mouvement(
            self.produit, Mouvement.AJUSTEMENT, 3, motif="Casse",
        )
        reponse = self.client.get(reverse("inventory:mouvement_liste"))
        self.assertContains(reponse, "Ajustement")


class FiltresHistoriqueTests(BaseApplicationTestCase):
    """Recherche libre, filtre utilisateur et filtre par type dans l'historique."""

    def setUp(self):
        self.gerant = creer_utilisateur("gerant", groupe="Gerant")
        self.magasinier = creer_utilisateur("magasinier", groupe="Magasinier")
        self.client.login(username="gerant", password=MOT_DE_PASSE_TEST)

        self.categorie = Categorie.objects.create(nom="Alimentation")
        self.riz = Produit.objects.create(
            reference="FIL-RIZ", nom="Riz 25kg", categorie=self.categorie,
            prix_achat=Decimal("1000.00"), prix_vente=Decimal("1400.00"), quantite_stock=100,
        )
        self.huile = Produit.objects.create(
            reference="FIL-HUI", nom="Huile 5L", categorie=self.categorie,
            prix_achat=Decimal("500.00"), prix_vente=Decimal("700.00"), quantite_stock=100,
        )

        services.enregistrer_mouvement(
            self.riz, Mouvement.SORTIE, 5, motif="Vente",
            utilisateur=self.gerant, document="BON-1000",
        )
        services.enregistrer_mouvement(
            self.huile, Mouvement.ENTREE, 10, motif="Livraison",
            utilisateur=self.magasinier, document="BL-2000",
        )
        services.enregistrer_mouvement(
            self.huile, Mouvement.AJUSTEMENT, 2, motif="Casse",
            utilisateur=self.magasinier,
        )

    def _mouvements(self, parametres):
        reponse = self.client.get(reverse("inventory:mouvement_liste"), parametres)
        self.assertEqual(reponse.status_code, 200)
        return list(reponse.context["mouvements"])

    def test_filtre_par_utilisateur(self):
        resultats = self._mouvements({"utilisateur": self.magasinier.pk})

        self.assertEqual(len(resultats), 2)
        self.assertTrue(all(m.utilisateur == self.magasinier for m in resultats))

    def test_recherche_par_reference_de_bon(self):
        resultats = self._mouvements({"q": "BON-1000"})

        self.assertEqual(len(resultats), 1)
        self.assertEqual(resultats[0].document, "BON-1000")

    def test_recherche_par_nom_de_produit(self):
        resultats = self._mouvements({"q": "Huile"})
        self.assertEqual(len(resultats), 2)

    def test_recherche_par_reference_produit(self):
        resultats = self._mouvements({"q": "FIL-RIZ"})
        self.assertEqual(len(resultats), 1)

    def test_filtre_par_type_ajustement(self):
        resultats = self._mouvements({"type": Mouvement.AJUSTEMENT})

        self.assertEqual(len(resultats), 1)
        self.assertEqual(resultats[0].motif, "Casse")

    def test_filtres_combines(self):
        resultats = self._mouvements({
            "utilisateur": self.magasinier.pk,
            "type": Mouvement.ENTREE,
        })

        self.assertEqual(len(resultats), 1)
        self.assertEqual(resultats[0].document, "BL-2000")

    def test_valeur_du_mouvement_annotee(self):
        """La valeur est calculée par la base : quantité x prix d'achat."""
        resultats = self._mouvements({"q": "BON-1000"})
        self.assertEqual(resultats[0].valeur, Decimal("5000.00"))  # 5 x 1000

    def test_filtre_utilisateur_invalide_ignore(self):
        resultats = self._mouvements({"utilisateur": "abc"})
        self.assertEqual(len(resultats), 3)

    def test_liste_des_utilisateurs_limitee_aux_actifs(self):
        """Seuls les utilisateurs ayant enregistré un mouvement sont proposés."""
        creer_utilisateur("jamais_actif", groupe="Magasinier")

        reponse = self.client.get(reverse("inventory:mouvement_liste"))
        noms = [u.username for u in reponse.context["utilisateurs"]]

        self.assertIn("gerant", noms)
        self.assertIn("magasinier", noms)
        self.assertNotIn("jamais_actif", noms)


@override_settings(**PARAMETRES_EMAIL_TEST)
class RapportAvecAjustementsTests(BaseApplicationTestCase):
    """Le rapport quotidien compte aussi les ajustements."""

    def setUp(self):
        self.categorie = Categorie.objects.create(nom="Alimentation")
        self.produit = Produit.objects.create(
            reference="RAJ-001", nom="Riz", categorie=self.categorie,
            prix_achat=Decimal("1000.00"), prix_vente=Decimal("1400.00"), quantite_stock=100,
        )

    def test_statistiques_du_jour_incluent_les_ajustements(self):
        services.enregistrer_mouvement(self.produit, Mouvement.ENTREE, 10)
        services.enregistrer_mouvement(self.produit, Mouvement.SORTIE, 4)
        services.enregistrer_mouvement(self.produit, Mouvement.AJUSTEMENT, 2, motif="Casse")

        stats = statistiques.statistiques_du_jour()

        self.assertEqual(stats["ajustements_nombre"], 1)
        self.assertEqual(stats["ajustements_quantite"], 2)
        # Le total additionne bien les trois types.
        self.assertEqual(stats["mouvements_nombre"], 3)

    def test_rapport_email_mentionne_les_ajustements(self):
        services.enregistrer_mouvement(self.produit, Mouvement.AJUSTEMENT, 2, motif="Casse")
        mail.outbox = []

        call_command("rapport_quotidien", verbosity=0)

        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Ajustements", mail.outbox[0].alternatives[0][0])
        self.assertIn("Ajustements", mail.outbox[0].body)


class OngletsEtatProduitsTests(BaseApplicationTestCase):
    """Onglets Tous / En stock / Sous seuil / Rupture / Désactivés."""

    def setUp(self):
        creer_utilisateur("gerant", groupe="Gerant")
        self.client.login(username="gerant", password=MOT_DE_PASSE_TEST)
        self.categorie = Categorie.objects.create(nom="Alimentation")

        def produit(reference, stock, seuil=10, actif=True):
            return Produit.objects.create(
                reference=reference, nom=f"Produit {reference}", categorie=self.categorie,
                prix_achat=Decimal("1000.00"), prix_vente=Decimal("1500.00"),
                quantite_stock=stock, seuil_alerte=seuil, actif=actif,
            )

        self.en_stock = produit("ET-001", 50)
        self.sous_seuil = produit("ET-002", 5)
        self.rupture = produit("ET-003", 0)
        self.desactive = produit("ET-004", 30, actif=False)

    def _produits(self, parametres=None):
        reponse = self.client.get(reverse("inventory:produit_liste"), parametres or {})
        self.assertEqual(reponse.status_code, 200)
        return list(reponse.context["produits"])

    def test_onglet_tous(self):
        self.assertEqual(len(self._produits()), 4)

    def test_onglet_en_stock(self):
        self.assertEqual(self._produits({"etat": "en_stock"}), [self.en_stock])

    def test_onglet_sous_seuil_exclut_les_ruptures(self):
        """« Sous seuil » ne montre que ce qui a encore du stock."""
        self.assertEqual(self._produits({"etat": "sous_seuil"}), [self.sous_seuil])

    def test_onglet_rupture(self):
        self.assertEqual(self._produits({"etat": "rupture"}), [self.rupture])

    def test_onglet_desactives(self):
        self.assertEqual(self._produits({"etat": "desactives"}), [self.desactive])

    def test_etat_inconnu_ignore(self):
        self.assertEqual(len(self._produits({"etat": "n_importe_quoi"})), 4)

    def test_etat_combine_avec_la_recherche(self):
        resultats = self._produits({"etat": "en_stock", "q": "ET-001"})
        self.assertEqual(resultats, [self.en_stock])

    def test_valeur_du_stock_annotee(self):
        resultats = self._produits({"etat": "en_stock"})
        self.assertEqual(resultats[0].valeur_stock, Decimal("50000.00"))  # 50 x 1000


class ActivitePeriodeTests(BaseApplicationTestCase):
    """Sélecteur de période et indicateurs d'activité du tableau de bord."""

    def setUp(self):
        creer_utilisateur("gerant", groupe="Gerant")
        self.client.login(username="gerant", password=MOT_DE_PASSE_TEST)
        self.categorie = Categorie.objects.create(nom="Alimentation")
        self.produit = Produit.objects.create(
            reference="ACT-001", nom="Riz", categorie=self.categorie,
            prix_achat=Decimal("1000.00"), prix_vente=Decimal("1500.00"),
            quantite_stock=100, seuil_alerte=5,
        )

    def test_jours_de_la_periode(self):
        self.assertEqual(statistiques.jours_de_la_periode("jour"), 1)
        self.assertEqual(statistiques.jours_de_la_periode("semaine"), 7)
        self.assertEqual(statistiques.jours_de_la_periode("mois"), 30)
        self.assertEqual(statistiques.jours_de_la_periode("inconnu"), 7)

    def test_activite_compte_chaque_type(self):
        services.enregistrer_mouvement(self.produit, Mouvement.ENTREE, 10)
        services.enregistrer_mouvement(self.produit, Mouvement.SORTIE, 4)
        services.enregistrer_mouvement(self.produit, Mouvement.SORTIE, 6)
        services.enregistrer_mouvement(self.produit, Mouvement.AJUSTEMENT, 2, motif="Casse")

        activite = statistiques.activite_periode(7)

        self.assertEqual(activite["entrees"], 1)
        self.assertEqual(activite["sorties"], 2)
        self.assertEqual(activite["ajustements"], 1)
        self.assertEqual(activite["total"], 4)

    def test_valeur_des_sorties_inclut_les_ajustements(self):
        """Sorties et ajustements quittent tous deux le stock."""
        services.enregistrer_mouvement(self.produit, Mouvement.SORTIE, 4)
        services.enregistrer_mouvement(self.produit, Mouvement.AJUSTEMENT, 2, motif="Casse")
        services.enregistrer_mouvement(self.produit, Mouvement.ENTREE, 50)  # ne compte pas

        activite = statistiques.activite_periode(7)
        self.assertEqual(activite["valeur_sorties"], Decimal("6000.00"))  # (4+2) x 1000

    def test_activite_vide(self):
        activite = statistiques.activite_periode(7)

        self.assertEqual(activite["total"], 0)
        self.assertEqual(activite["valeur_sorties"], Decimal("0.00"))

    def test_selecteur_de_periode_sur_la_page(self):
        reponse = self.client.get(reverse("inventory:tableau_bord"), {"periode": "mois"})

        self.assertEqual(reponse.status_code, 200)
        self.assertEqual(reponse.context["periode_active"], "mois")
        self.assertEqual(reponse.context["activite"]["jours"], 30)

    def test_periode_par_defaut(self):
        reponse = self.client.get(reverse("inventory:tableau_bord"))
        self.assertEqual(reponse.context["activite"]["jours"], 7)

    def test_periode_invalide_retombe_sur_sept_jours(self):
        reponse = self.client.get(reverse("inventory:tableau_bord"), {"periode": "siecle"})
        self.assertEqual(reponse.context["activite"]["jours"], 7)

    def test_produits_les_plus_mouvementes(self):
        autre = Produit.objects.create(
            reference="ACT-002", nom="Huile", categorie=self.categorie,
            prix_achat=Decimal("500.00"), prix_vente=Decimal("700.00"), quantite_stock=100,
        )
        services.enregistrer_mouvement(self.produit, Mouvement.SORTIE, 30)
        services.enregistrer_mouvement(autre, Mouvement.SORTIE, 5)
        services.enregistrer_mouvement(autre, Mouvement.ENTREE, 90)  # ne compte pas

        classement = list(statistiques.produits_les_plus_mouvementes(7))

        self.assertEqual([p.nom for p in classement], ["Riz", "Huile"])
        self.assertEqual(classement[0].total_sorties, 30)
        self.assertEqual(classement[1].total_sorties, 5)

    def test_produit_sans_sortie_absent_du_classement(self):
        services.enregistrer_mouvement(self.produit, Mouvement.ENTREE, 10)
        self.assertEqual(list(statistiques.produits_les_plus_mouvementes(7)), [])


class StatistiquesProduitTests(BaseApplicationTestCase):
    """Indicateurs de la fiche produit : rythme, couverture, franchissements."""

    def setUp(self):
        creer_utilisateur("gerant", groupe="Gerant")
        self.client.login(username="gerant", password=MOT_DE_PASSE_TEST)
        self.categorie = Categorie.objects.create(nom="Alimentation")
        self.produit = Produit.objects.create(
            reference="STA-001", nom="Riz", categorie=self.categorie,
            prix_achat=Decimal("1000.00"), prix_vente=Decimal("1250.00"),
            quantite_stock=300, seuil_alerte=50,
        )

    def test_sorties_et_moyenne(self):
        services.enregistrer_mouvement(self.produit, Mouvement.SORTIE, 30)
        services.enregistrer_mouvement(self.produit, Mouvement.SORTIE, 30)

        stats = statistiques.statistiques_produit(self.produit, jours=30)

        self.assertEqual(stats["sorties_total"], 60)
        self.assertEqual(stats["sorties_moyenne"], 2.0)  # 60 / 30 jours

    def test_couverture(self):
        """240 en stock, 2 par jour : 120 jours de couverture."""
        services.enregistrer_mouvement(self.produit, Mouvement.SORTIE, 60)

        stats = statistiques.statistiques_produit(self.produit, jours=30)
        self.assertEqual(stats["couverture"], 120)

    def test_couverture_indeterminee_sans_sortie(self):
        stats = statistiques.statistiques_produit(self.produit, jours=30)

        self.assertEqual(stats["sorties_total"], 0)
        self.assertIsNone(stats["couverture"])

    def test_derniere_entree(self):
        services.enregistrer_mouvement(self.produit, Mouvement.ENTREE, 100)
        services.enregistrer_mouvement(self.produit, Mouvement.SORTIE, 10)

        stats = statistiques.statistiques_produit(self.produit)

        self.assertIsNotNone(stats["derniere_entree"])
        self.assertEqual(stats["derniere_entree"].quantite, 100)

    def test_ajustement_compte_comme_une_sortie(self):
        services.enregistrer_mouvement(self.produit, Mouvement.AJUSTEMENT, 30, motif="Casse")

        stats = statistiques.statistiques_produit(self.produit, jours=30)
        self.assertEqual(stats["sorties_total"], 30)

    def test_franchissements_du_seuil(self):
        """Descendre, remonter, redescendre = deux franchissements."""
        services.enregistrer_mouvement(self.produit, Mouvement.SORTIE, 270)  # 300 -> 30
        services.enregistrer_mouvement(self.produit, Mouvement.ENTREE, 270)  # 30 -> 300
        services.enregistrer_mouvement(self.produit, Mouvement.SORTIE, 280)  # 300 -> 20

        stats = statistiques.statistiques_produit(self.produit, jours=30)
        self.assertEqual(stats["franchissements_seuil"], 2)

    def test_sorties_consecutives_sous_le_seuil_comptent_pour_une(self):
        services.enregistrer_mouvement(self.produit, Mouvement.SORTIE, 270)  # 300 -> 30
        services.enregistrer_mouvement(self.produit, Mouvement.SORTIE, 5)    # 30 -> 25
        services.enregistrer_mouvement(self.produit, Mouvement.SORTIE, 5)    # 25 -> 20

        stats = statistiques.statistiques_produit(self.produit, jours=30)
        self.assertEqual(stats["franchissements_seuil"], 1)

    def test_aucun_franchissement_si_stock_reste_haut(self):
        services.enregistrer_mouvement(self.produit, Mouvement.SORTIE, 10)

        stats = statistiques.statistiques_produit(self.produit, jours=30)
        self.assertEqual(stats["franchissements_seuil"], 0)

    def test_affichage_sur_la_fiche_produit(self):
        services.enregistrer_mouvement(self.produit, Mouvement.SORTIE, 60)

        reponse = self.client.get(self.produit.get_absolute_url())

        self.assertEqual(reponse.status_code, 200)
        self.assertContains(reponse, "Couverture")
        self.assertContains(reponse, "Sorties sur 30 jours")
        self.assertContains(reponse, "Dernière entrée")
        self.assertEqual(reponse.context["stats"]["sorties_total"], 60)

    def test_marge_en_pourcentage(self):
        reponse = self.client.get(self.produit.get_absolute_url())
        # Achat 1000, vente 1250 : marge de 250, soit 25 %.
        self.assertEqual(reponse.context["marge_pourcentage"], Decimal("25"))


class PanneauMouvementTests(BaseApplicationTestCase):
    """Compteurs du jour, valeur du mouvement et bouton « Tout sortir »."""

    def setUp(self):
        self.magasinier = creer_utilisateur("magasinier", groupe="Magasinier")
        self.client.login(username="magasinier", password=MOT_DE_PASSE_TEST)
        self.categorie = Categorie.objects.create(nom="Alimentation")
        self.produit = Produit.objects.create(
            reference="PAN-001", nom="Riz", categorie=self.categorie,
            prix_achat=Decimal("1000.00"), prix_vente=Decimal("1500.00"),
            quantite_stock=40, seuil_alerte=5,
        )

    def test_compteur_du_jour(self):
        services.enregistrer_mouvement(self.produit, Mouvement.SORTIE, 2)
        services.enregistrer_mouvement(self.produit, Mouvement.SORTIE, 3)
        services.enregistrer_mouvement(self.produit, Mouvement.ENTREE, 10)  # autre type

        reponse = self.client.get(
            reverse("inventory:sortie_stock"), {"produit": self.produit.pk}
        )
        self.assertEqual(reponse.context["mouvements_du_jour"], 2)

    def test_bouton_tout_sortir_preremplit_la_quantite(self):
        reponse = self.client.get(
            reverse("inventory:sortie_stock"),
            {"produit": self.produit.pk, "quantite": self.produit.quantite_stock},
        )

        self.assertEqual(reponse.status_code, 200)
        self.assertEqual(reponse.context["form"].initial["quantite"], "40")
        self.assertContains(reponse, "Tout sortir (40)")

    def test_valeur_du_mouvement_calculee(self):
        reponse = self.client.get(
            reverse("inventory:sortie_stock"),
            {"produit": self.produit.pk, "quantite": "10"},
        )
        self.assertEqual(reponse.context["valeur_mouvement"], Decimal("10000.00"))

    def test_pas_de_valeur_sans_quantite(self):
        reponse = self.client.get(
            reverse("inventory:sortie_stock"), {"produit": self.produit.pk}
        )
        self.assertIsNone(reponse.context.get("valeur_mouvement"))

    def test_bouton_absent_sur_une_entree(self):
        reponse = self.client.get(
            reverse("inventory:entree_stock"), {"produit": self.produit.pk}
        )
        self.assertNotContains(reponse, "Tout sortir")

    def test_quantite_invalide_ignoree(self):
        reponse = self.client.get(
            reverse("inventory:sortie_stock"),
            {"produit": self.produit.pk, "quantite": "beaucoup"},
        )
        self.assertEqual(reponse.status_code, 200)
        self.assertNotIn("quantite", reponse.context["form"].initial)


class ExportMouvementsTests(BaseApplicationTestCase):
    """Export Excel de l'historique des mouvements."""

    def setUp(self):
        self.gerant = creer_utilisateur("gerant", groupe="Gerant")
        creer_utilisateur("magasinier", groupe="Magasinier")
        self.client.login(username="gerant", password=MOT_DE_PASSE_TEST)

        self.categorie = Categorie.objects.create(nom="Alimentation")
        self.produit = Produit.objects.create(
            reference="EXM-001", nom="Riz 25kg", categorie=self.categorie,
            prix_achat=Decimal("1000.00"), prix_vente=Decimal("1400.00"), quantite_stock=100,
        )
        services.enregistrer_mouvement(
            self.produit, Mouvement.ENTREE, 20, motif="Livraison",
            utilisateur=self.gerant, document="BL-1000",
        )
        services.enregistrer_mouvement(
            self.produit, Mouvement.SORTIE, 5, motif="Vente",
            utilisateur=self.gerant, document="BON-2000", destination="Client A",
        )
        services.enregistrer_mouvement(
            self.produit, Mouvement.AJUSTEMENT, 2, motif="Casse", utilisateur=self.gerant,
        )

    def _classeur(self, parametres=None):
        reponse = self.client.get(
            reverse("inventory:export_mouvements_excel"), parametres or {}
        )
        self.assertEqual(reponse.status_code, 200)
        return reponse, load_workbook(BytesIO(reponse.content))

    def test_generation_du_fichier(self):
        reponse, classeur = self._classeur()

        self.assertEqual(
            reponse["Content-Type"],
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        self.assertIn("mouvements", reponse["Content-Disposition"])
        self.assertEqual(classeur.active.title, "Historique")

    def test_colonnes(self):
        _, classeur = self._classeur()
        entetes = [cellule.value for cellule in classeur.active[4]]
        self.assertEqual(entetes, [titre for titre, _ in exports.COLONNES_MOUVEMENTS])

    def test_contenu_des_lignes(self):
        _, classeur = self._classeur()
        feuille = classeur.active

        lignes = {}
        for numero in range(5, feuille.max_row + 1):
            lignes[feuille.cell(row=numero, column=4).value] = numero
        self.assertEqual(set(lignes), {"Entrée", "Sortie", "Ajustement"})

        ligne_sortie = lignes["Sortie"]
        self.assertEqual(feuille.cell(row=ligne_sortie, column=2).value, "EXM-001")
        self.assertEqual(feuille.cell(row=ligne_sortie, column=5).value, 5)
        self.assertEqual(feuille.cell(row=ligne_sortie, column=8).value, "Client A")
        self.assertEqual(feuille.cell(row=ligne_sortie, column=9).value, "BON-2000")
        self.assertEqual(feuille.cell(row=ligne_sortie, column=10).value, Decimal("5000.00"))
        self.assertEqual(feuille.cell(row=ligne_sortie, column=11).value, "gerant")

    def test_formatage(self):
        _, classeur = self._classeur()
        feuille = classeur.active

        self.assertTrue(feuille.cell(row=4, column=1).font.bold)
        self.assertIsNotNone(feuille.auto_filter.ref)
        self.assertEqual(feuille.freeze_panes, "A5")
        self.assertEqual(feuille.cell(row=5, column=10).number_format, "#,##0.00")
        # Le type est coloré selon sa nature.
        self.assertNotEqual(feuille.cell(row=5, column=4).fill.fgColor.rgb, "00000000")

    def test_export_respecte_les_filtres(self):
        _, classeur = self._classeur({"type": Mouvement.AJUSTEMENT})
        feuille = classeur.active

        types = [
            feuille.cell(row=numero, column=4).value
            for numero in range(5, feuille.max_row + 1)
        ]
        self.assertEqual(types, ["Ajustement"])

    def test_export_interdit_au_magasinier(self):
        self.client.login(username="magasinier", password=MOT_DE_PASSE_TEST)
        reponse = self.client.get(reverse("inventory:export_mouvements_excel"))
        self.assertEqual(reponse.status_code, 403)


class PaginationNumeroteeTests(BaseApplicationTestCase):
    """Pagination numérotée avec élision."""

    def setUp(self):
        creer_utilisateur("gerant", groupe="Gerant")
        self.client.login(username="gerant", password=MOT_DE_PASSE_TEST)
        self.categorie = Categorie.objects.create(nom="Alimentation")
        for numero in range(45):
            Produit.objects.create(
                reference=f"PAG-{numero:03d}", nom=f"Produit {numero:03d}",
                categorie=self.categorie, prix_achat=Decimal("100.00"),
                prix_vente=Decimal("150.00"), quantite_stock=10,
            )

    def test_numeros_de_pages_presents(self):
        reponse = self.client.get(reverse("inventory:produit_liste"))

        self.assertEqual(reponse.status_code, 200)
        self.assertEqual(list(reponse.context["pages_affichees"]), [1, 2, 3])

    def test_resume_de_pagination(self):
        reponse = self.client.get(reverse("inventory:produit_liste"))
        self.assertEqual(reponse.context["resume_pagination"], "20 produits sur 45")

    def test_pas_de_pagination_sur_une_seule_page(self):
        Produit.objects.all().delete()
        Produit.objects.create(
            reference="SEUL-001", nom="Seul", categorie=self.categorie,
            prix_achat=Decimal("100.00"), prix_vente=Decimal("150.00"),
        )

        reponse = self.client.get(reverse("inventory:produit_liste"))
        self.assertEqual(list(reponse.context["pages_affichees"]), [])

    def test_navigation_sur_la_derniere_page(self):
        reponse = self.client.get(reverse("inventory:produit_liste"), {"page": 3})

        self.assertEqual(reponse.status_code, 200)
        self.assertEqual(reponse.context["resume_pagination"], "5 produits sur 45")


class UniteProduitTests(BaseApplicationTestCase):
    """Unité de vente affichée à côté des quantités."""

    def setUp(self):
        creer_utilisateur("gerant", groupe="Gerant")
        self.client.login(username="gerant", password=MOT_DE_PASSE_TEST)
        self.categorie = Categorie.objects.create(nom="Matériaux")
        self.avec_unite = Produit.objects.create(
            reference="UNI-001", nom="Ciment 50kg", unite="sac", categorie=self.categorie,
            prix_achat=Decimal("4200.00"), prix_vente=Decimal("5000.00"), quantite_stock=12,
        )
        self.sans_unite = Produit.objects.create(
            reference="UNI-002", nom="Divers", categorie=self.categorie,
            prix_achat=Decimal("100.00"), prix_vente=Decimal("150.00"), quantite_stock=3,
        )

    def test_unite_affichee_par_defaut(self):
        self.assertEqual(self.avec_unite.unite_affichee, "sac")
        self.assertEqual(self.sans_unite.unite_affichee, "unité")

    def test_unite_sur_la_fiche_produit(self):
        reponse = self.client.get(self.avec_unite.get_absolute_url())

        self.assertEqual(reponse.status_code, 200)
        self.assertContains(reponse, "sac")

    def test_unite_dans_la_liste(self):
        reponse = self.client.get(reverse("inventory:produit_liste"))
        self.assertContains(reponse, "sac")

    def test_unite_modifiable_par_le_formulaire(self):
        reponse = self.client.post(
            reverse("inventory:produit_modifier", kwargs={"pk": self.avec_unite.pk}),
            {
                "reference": "UNI-001", "nom": "Ciment 50kg", "unite": "palette",
                "categorie": self.categorie.pk, "prix_achat": "4200",
                "prix_vente": "5000", "seuil_alerte": "10", "actif": "on",
            },
        )
        self.assertEqual(reponse.status_code, 302)
        self.avec_unite.refresh_from_db()
        self.assertEqual(self.avec_unite.unite, "palette")

    def test_unite_facultative(self):
        """Un produit sans unité reste valide."""
        reponse = self.client.post(
            reverse("inventory:produit_creer"),
            {
                "reference": "UNI-003", "nom": "Sans unité", "unite": "",
                "categorie": self.categorie.pk, "prix_achat": "100",
                "prix_vente": "150", "seuil_alerte": "5", "actif": "on",
            },
        )
        self.assertEqual(reponse.status_code, 302)
        self.assertTrue(Produit.objects.filter(reference="UNI-003").exists())

    def test_unite_dans_l_export_excel(self):
        reponse = self.client.get(reverse("inventory:export_stock_excel"))
        feuille = load_workbook(BytesIO(reponse.content)).active

        entetes = [cellule.value for cellule in feuille[4]]
        self.assertIn("Unité", entetes)

        colonne_unite = entetes.index("Unité") + 1
        unites = {
            feuille.cell(row=numero, column=colonne_unite).value
            for numero in range(5, feuille.max_row + 1)
        }
        self.assertIn("sac", unites)


class FournisseurContactTests(BaseApplicationTestCase):
    """Contact et délai de livraison du fournisseur."""

    def setUp(self):
        creer_utilisateur("gerant", groupe="Gerant")
        self.client.login(username="gerant", password=MOT_DE_PASSE_TEST)
        self.categorie = Categorie.objects.create(nom="Matériaux")
        self.fournisseur = Fournisseur.objects.create(
            nom="SOTRACOM", contact="M. Zinsou",
            telephone="+229 97 00 00 12", delai_jours=4,
        )
        self.produit = Produit.objects.create(
            reference="FOU-001", nom="Fer à béton 8mm", unite="barre",
            categorie=self.categorie, fournisseur=self.fournisseur,
            prix_achat=Decimal("3400.00"), prix_vente=Decimal("4250.00"),
            quantite_stock=300, seuil_alerte=25,
        )

    def test_contact_et_delai_sur_la_fiche(self):
        reponse = self.client.get(self.produit.get_absolute_url())

        self.assertContains(reponse, "M. Zinsou")
        self.assertContains(reponse, "+229 97 00 00 12")
        self.assertContains(reponse, "4 jours")

    def test_champs_facultatifs(self):
        """Un fournisseur sans contact ni délai reste valide."""
        fournisseur = Fournisseur.objects.create(nom="Sans détails")

        self.assertEqual(fournisseur.contact, "")
        self.assertIsNone(fournisseur.delai_jours)

    def test_avertissement_couverture_insuffisante(self):
        """Stock qui ne tient pas jusqu'à la prochaine livraison."""
        # 300 sorties sur 30 jours = 10 par jour. Reste 10 -> 1 jour de
        # couverture, alors que le fournisseur livre en 4 jours.
        services.enregistrer_mouvement(self.produit, Mouvement.SORTIE, 290)

        reponse = self.client.get(self.produit.get_absolute_url())

        self.assertTrue(reponse.context["couverture_insuffisante"])
        self.assertContains(reponse, "Commandez maintenant")

    def test_pas_d_avertissement_si_couverture_suffisante(self):
        services.enregistrer_mouvement(self.produit, Mouvement.SORTIE, 30)

        reponse = self.client.get(self.produit.get_absolute_url())
        self.assertFalse(reponse.context["couverture_insuffisante"])

    def test_pas_d_avertissement_sans_delai_connu(self):
        """Sans délai renseigné, aucune conclusion possible."""
        self.fournisseur.delai_jours = None
        self.fournisseur.save(update_fields=["delai_jours"])
        services.enregistrer_mouvement(self.produit, Mouvement.SORTIE, 290)

        reponse = self.client.get(self.produit.get_absolute_url())
        self.assertFalse(reponse.context["couverture_insuffisante"])

    def test_pas_d_avertissement_sans_fournisseur(self):
        self.produit.fournisseur = None
        self.produit.save(update_fields=["fournisseur"])
        services.enregistrer_mouvement(self.produit, Mouvement.SORTIE, 290)

        reponse = self.client.get(self.produit.get_absolute_url())
        self.assertFalse(reponse.context["couverture_insuffisante"])

    @override_settings(**PARAMETRES_EMAIL_TEST)
    def test_email_d_alerte_indique_qui_appeler(self):
        """L'alerte doit dire au gérant qui contacter et sous quel délai."""
        with self.captureOnCommitCallbacks(execute=True):
            services.enregistrer_mouvement(self.produit, Mouvement.SORTIE, 280)

        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        corps_html = message.alternatives[0][0]

        self.assertIn("SOTRACOM", corps_html)
        self.assertIn("M. Zinsou", corps_html)
        self.assertIn("+229 97 00 00 12", corps_html)
        self.assertIn("4 jours", corps_html)

        # La version texte porte la même information.
        self.assertIn("SOTRACOM", message.body)
        self.assertIn("M. Zinsou", message.body)

    @override_settings(**PARAMETRES_EMAIL_TEST)
    def test_email_d_alerte_sans_fournisseur(self):
        """Un produit sans fournisseur ne casse pas l'email."""
        self.produit.fournisseur = None
        self.produit.save(update_fields=["fournisseur"])

        with self.captureOnCommitCallbacks(execute=True):
            services.enregistrer_mouvement(self.produit, Mouvement.SORTIE, 280)

        self.assertEqual(len(mail.outbox), 1)
        self.assertNotIn("SOTRACOM", mail.outbox[0].alternatives[0][0])


class SeedDonneesTests(TestCase):
    """La commande seed renseigne unités et coordonnées fournisseurs."""

    def test_seed_remplit_les_nouveaux_champs(self):
        call_command("seed", verbosity=0)

        self.assertEqual(Produit.objects.count(), 25)
        self.assertEqual(Fournisseur.objects.count(), 4)

        # Chaque produit a une unité.
        self.assertFalse(Produit.objects.filter(unite="").exists())

        # Chaque fournisseur a un contact et un délai.
        self.assertFalse(Fournisseur.objects.filter(contact="").exists())
        self.assertFalse(Fournisseur.objects.filter(delai_jours__isnull=True).exists())

        riz = Produit.objects.get(reference="ALI-001")
        self.assertEqual(riz.unite, "sac")


class CommandeServiceTests(BaseApplicationTestCase):
    """Cycle de vie d'une commande, côté service."""

    def setUp(self):
        self.gerant = creer_utilisateur("gerant", groupe="Gerant")
        self.fournisseur = Fournisseur.objects.create(
            nom="SOTRACOM", contact="M. Zinsou", delai_jours=4
        )
        self.categorie = Categorie.objects.create(nom="Matériaux")
        self.produit = Produit.objects.create(
            reference="CMD-P1", nom="Fer à béton", unite="barre",
            categorie=self.categorie, fournisseur=self.fournisseur,
            prix_achat=Decimal("3400.00"), prix_vente=Decimal("4250.00"),
            quantite_stock=10, seuil_alerte=25,
        )
        self.autre_produit = Produit.objects.create(
            reference="CMD-P2", nom="Ciment", unite="sac",
            categorie=self.categorie, fournisseur=self.fournisseur,
            prix_achat=Decimal("4200.00"), prix_vente=Decimal("5000.00"),
            quantite_stock=0, seuil_alerte=20,
        )

    def _commande_avec_ligne(self, quantite=100):
        commande = services.creer_commande(self.fournisseur, utilisateur=self.gerant)
        services.ajouter_ligne_commande(commande, self.produit, quantite)
        return commande

    def test_reference_generee(self):
        premiere = services.creer_commande(self.fournisseur)
        seconde = services.creer_commande(self.fournisseur)

        self.assertTrue(premiere.reference.startswith("CMD-"))
        self.assertNotEqual(premiere.reference, seconde.reference)

    def test_commande_ouverte_en_brouillon(self):
        commande = services.creer_commande(self.fournisseur, utilisateur=self.gerant)

        self.assertEqual(commande.statut, Commande.BROUILLON)
        self.assertTrue(commande.modifiable)
        self.assertFalse(commande.receptionnable)
        self.assertEqual(commande.cree_par, self.gerant)

    def test_ajout_de_ligne_fige_le_prix(self):
        commande = self._commande_avec_ligne()
        ligne = commande.lignes.get()

        self.assertEqual(ligne.prix_unitaire, Decimal("3400.00"))

        # Le prix du produit change : la commande garde son prix.
        self.produit.prix_achat = Decimal("5000.00")
        self.produit.save(update_fields=["prix_achat"])
        ligne.refresh_from_db()
        self.assertEqual(ligne.prix_unitaire, Decimal("3400.00"))

    def test_prix_negocie(self):
        commande = services.creer_commande(self.fournisseur)
        ligne = services.ajouter_ligne_commande(
            commande, self.produit, 10, prix_unitaire=Decimal("3000.00")
        )
        self.assertEqual(ligne.prix_unitaire, Decimal("3000.00"))

    def test_montant_total(self):
        commande = services.creer_commande(self.fournisseur)
        services.ajouter_ligne_commande(commande, self.produit, 10)       # 34 000
        services.ajouter_ligne_commande(commande, self.autre_produit, 5)  # 21 000

        self.assertEqual(commande.montant_total, Decimal("55000.00"))

    def test_produit_en_double_refuse(self):
        commande = self._commande_avec_ligne()

        with self.assertRaises(ValidationError):
            services.ajouter_ligne_commande(commande, self.produit, 50)
        self.assertEqual(commande.lignes.count(), 1)

    def test_quantite_invalide_refusee(self):
        commande = services.creer_commande(self.fournisseur)

        for quantite in (0, -5):
            with self.subTest(quantite=quantite):
                with self.assertRaises(ValidationError):
                    services.ajouter_ligne_commande(commande, self.produit, quantite)

    def test_envoi_fige_la_commande(self):
        commande = self._commande_avec_ligne()
        services.envoyer_commande(commande)

        self.assertEqual(commande.statut, Commande.ENVOYEE)
        self.assertIsNotNone(commande.date_envoi)
        self.assertFalse(commande.modifiable)
        self.assertTrue(commande.receptionnable)

        with self.assertRaises(ValidationError):
            services.ajouter_ligne_commande(commande, self.autre_produit, 10)

    def test_commande_vide_non_envoyable(self):
        commande = services.creer_commande(self.fournisseur)

        with self.assertRaises(ValidationError):
            services.envoyer_commande(commande)
        self.assertEqual(commande.statut, Commande.BROUILLON)

    def test_double_envoi_refuse(self):
        commande = self._commande_avec_ligne()
        services.envoyer_commande(commande)

        with self.assertRaises(ValidationError):
            services.envoyer_commande(commande)

    def test_retrait_de_ligne_en_brouillon(self):
        commande = self._commande_avec_ligne()
        services.retirer_ligne_commande(commande.lignes.get())

        self.assertEqual(commande.lignes.count(), 0)

    def test_retrait_impossible_apres_envoi(self):
        commande = self._commande_avec_ligne()
        services.envoyer_commande(commande)

        with self.assertRaises(ValidationError):
            services.retirer_ligne_commande(commande.lignes.get())
        self.assertEqual(commande.lignes.count(), 1)

    def test_date_livraison_prevue(self):
        commande = self._commande_avec_ligne()
        services.envoyer_commande(commande)

        prevue = commande.date_livraison_prevue
        self.assertIsNotNone(prevue)
        self.assertEqual((prevue - commande.date_envoi).days, 4)

    def test_pas_de_date_prevue_sans_delai(self):
        self.fournisseur.delai_jours = None
        self.fournisseur.save(update_fields=["delai_jours"])
        commande = self._commande_avec_ligne()
        services.envoyer_commande(commande)

        self.assertIsNone(commande.date_livraison_prevue)

    def test_annulation(self):
        commande = self._commande_avec_ligne()
        services.annuler_commande(commande)

        self.assertEqual(commande.statut, Commande.ANNULEE)
        # Rien n'est effacé : la commande reste consultable.
        self.assertTrue(Commande.objects.filter(pk=commande.pk).exists())

    def test_annulation_impossible_apres_reception_partielle(self):
        commande = self._commande_avec_ligne()
        services.envoyer_commande(commande)
        services.receptionner_ligne_commande(commande.lignes.get(), 10)

        with self.assertRaises(ValidationError):
            services.annuler_commande(commande)


class ReceptionCommandeTests(BaseApplicationTestCase):
    """La réception crée le stock : c'est le point le plus sensible."""

    def setUp(self):
        self.gerant = creer_utilisateur("gerant", groupe="Gerant")
        self.magasinier = creer_utilisateur("magasinier", groupe="Magasinier")
        self.fournisseur = Fournisseur.objects.create(nom="SOTRACOM", delai_jours=4)
        self.categorie = Categorie.objects.create(nom="Matériaux")
        self.produit = Produit.objects.create(
            reference="REC-001", nom="Fer à béton", unite="barre",
            categorie=self.categorie, fournisseur=self.fournisseur,
            prix_achat=Decimal("3400.00"), prix_vente=Decimal("4250.00"),
            quantite_stock=10, seuil_alerte=25,
        )
        self.commande = services.creer_commande(self.fournisseur, utilisateur=self.gerant)
        self.ligne = services.ajouter_ligne_commande(self.commande, self.produit, 100)
        services.envoyer_commande(self.commande)

    def test_reception_complete(self):
        mouvement = services.receptionner_ligne_commande(
            self.ligne, 100, utilisateur=self.magasinier
        )

        self.produit.refresh_from_db()
        self.ligne.refresh_from_db()
        self.commande.refresh_from_db()

        self.assertEqual(self.produit.quantite_stock, 110)  # 10 + 100
        self.assertEqual(self.ligne.quantite_recue, 100)
        self.assertTrue(self.ligne.soldee)
        self.assertEqual(self.commande.statut, Commande.RECUE)
        self.assertIsNotNone(self.commande.date_reception)

        # Le mouvement est une entrée tracée, rattachée à la commande.
        self.assertEqual(mouvement.type_mouvement, Mouvement.ENTREE)
        self.assertEqual(mouvement.quantite, 100)
        self.assertEqual(mouvement.document, self.commande.reference)
        self.assertEqual(mouvement.utilisateur, self.magasinier)
        self.assertEqual(mouvement.ligne_commande, self.ligne)
        self.assertEqual(mouvement.stock_apres, 110)

    def test_livraison_partielle(self):
        services.receptionner_ligne_commande(self.ligne, 40)

        self.produit.refresh_from_db()
        self.ligne.refresh_from_db()
        self.commande.refresh_from_db()

        self.assertEqual(self.produit.quantite_stock, 50)
        self.assertEqual(self.ligne.quantite_recue, 40)
        self.assertEqual(self.ligne.quantite_restante, 60)
        self.assertFalse(self.ligne.soldee)
        self.assertEqual(self.commande.statut, Commande.PARTIELLE)
        self.assertIsNone(self.commande.date_reception)

    def test_livraisons_successives_soldent_la_commande(self):
        services.receptionner_ligne_commande(self.ligne, 40)
        services.receptionner_ligne_commande(self.ligne, 60)

        self.produit.refresh_from_db()
        self.commande.refresh_from_db()

        self.assertEqual(self.produit.quantite_stock, 110)
        self.assertEqual(self.commande.statut, Commande.RECUE)
        self.assertEqual(Mouvement.objects.filter(type_mouvement=Mouvement.ENTREE).count(), 2)

    def test_reception_superieure_au_reste_refusee(self):
        with self.assertRaises(ValidationError):
            services.receptionner_ligne_commande(self.ligne, 150)

        self.produit.refresh_from_db()
        self.ligne.refresh_from_db()
        self.assertEqual(self.produit.quantite_stock, 10)  # inchangé
        self.assertEqual(self.ligne.quantite_recue, 0)
        self.assertEqual(Mouvement.objects.count(), 0)

    def test_reception_quantite_nulle_refusee(self):
        with self.assertRaises(ValidationError):
            services.receptionner_ligne_commande(self.ligne, 0)
        self.assertEqual(Mouvement.objects.count(), 0)

    def test_reception_impossible_sur_un_brouillon(self):
        brouillon = services.creer_commande(self.fournisseur)
        ligne = services.ajouter_ligne_commande(brouillon, self.produit, 10)

        with self.assertRaises(ValidationError):
            services.receptionner_ligne_commande(ligne, 5)

        self.produit.refresh_from_db()
        self.assertEqual(self.produit.quantite_stock, 10)

    def test_reception_impossible_sur_commande_soldee(self):
        services.receptionner_ligne_commande(self.ligne, 100)

        with self.assertRaises(ValidationError):
            services.receptionner_ligne_commande(self.ligne, 1)

    def test_commande_a_plusieurs_lignes(self):
        """La commande ne se solde qu'une fois TOUTES les lignes livrées."""
        autre = Produit.objects.create(
            reference="REC-002", nom="Ciment", categorie=self.categorie,
            fournisseur=self.fournisseur, prix_achat=Decimal("4200.00"),
            prix_vente=Decimal("5000.00"), quantite_stock=0, seuil_alerte=20,
        )
        commande = services.creer_commande(self.fournisseur)
        ligne_a = services.ajouter_ligne_commande(commande, self.produit, 10)
        ligne_b = services.ajouter_ligne_commande(commande, autre, 20)
        services.envoyer_commande(commande)

        services.receptionner_ligne_commande(ligne_a, 10)
        commande.refresh_from_db()
        self.assertEqual(commande.statut, Commande.PARTIELLE)

        services.receptionner_ligne_commande(ligne_b, 20)
        commande.refresh_from_db()
        self.assertEqual(commande.statut, Commande.RECUE)

    def test_reception_apparait_dans_l_historique(self):
        self.client.login(username="gerant", password=MOT_DE_PASSE_TEST)
        services.receptionner_ligne_commande(self.ligne, 100, utilisateur=self.magasinier)

        reponse = self.client.get(
            reverse("inventory:mouvement_liste"), {"q": self.commande.reference}
        )
        mouvements = list(reponse.context["mouvements"])

        self.assertEqual(len(mouvements), 1)
        self.assertEqual(mouvements[0].motif, "Réception de commande")

    @override_settings(**PARAMETRES_EMAIL_TEST)
    def test_reception_n_envoie_aucune_alerte(self):
        """Une entrée ne déclenche jamais d'alerte de stock bas."""
        with self.captureOnCommitCallbacks(execute=True):
            services.receptionner_ligne_commande(self.ligne, 100)

        self.assertEqual(len(mail.outbox), 0)


class CommandeVuesTests(BaseApplicationTestCase):
    """Parcours complet d'une commande dans l'interface."""

    def setUp(self):
        self.gerant = creer_utilisateur("gerant", groupe="Gerant")
        self.magasinier = creer_utilisateur("magasinier", groupe="Magasinier")
        self.fournisseur = Fournisseur.objects.create(nom="SOTRACOM", delai_jours=4)
        self.categorie = Categorie.objects.create(nom="Matériaux")
        self.produit = Produit.objects.create(
            reference="VUE-001", nom="Fer à béton", unite="barre",
            categorie=self.categorie, fournisseur=self.fournisseur,
            prix_achat=Decimal("3400.00"), prix_vente=Decimal("4250.00"),
            quantite_stock=10, seuil_alerte=25,
        )

    def test_parcours_complet(self):
        self.client.login(username="gerant", password=MOT_DE_PASSE_TEST)

        # 1. Ouverture de la commande
        reponse = self.client.post(
            reverse("inventory:commande_creer"),
            {"fournisseur": self.fournisseur.pk, "commentaire": "Réassort urgent"},
        )
        self.assertEqual(reponse.status_code, 302)
        commande = Commande.objects.get()
        self.assertEqual(commande.statut, Commande.BROUILLON)
        self.assertEqual(commande.cree_par, self.gerant)

        # 2. Ajout d'une ligne
        reponse = self.client.post(
            reverse("inventory:commande_ajouter_ligne", kwargs={"pk": commande.pk}),
            {"produit": self.produit.pk, "quantite": "100", "prix_unitaire": ""},
        )
        self.assertEqual(reponse.status_code, 302)
        self.assertEqual(commande.lignes.count(), 1)

        # 3. Envoi
        reponse = self.client.post(
            reverse("inventory:commande_envoyer", kwargs={"pk": commande.pk})
        )
        commande.refresh_from_db()
        self.assertEqual(commande.statut, Commande.ENVOYEE)

        # 4. Réception partielle
        ligne = commande.lignes.get()
        reponse = self.client.post(
            reverse("inventory:ligne_receptionner", kwargs={"pk": ligne.pk}),
            {"quantite": "40"},
        )
        self.assertEqual(reponse.status_code, 302)
        commande.refresh_from_db()
        self.produit.refresh_from_db()
        self.assertEqual(commande.statut, Commande.PARTIELLE)
        self.assertEqual(self.produit.quantite_stock, 50)

        # 5. Solde
        self.client.post(
            reverse("inventory:ligne_receptionner", kwargs={"pk": ligne.pk}),
            {"quantite": "60"},
        )
        commande.refresh_from_db()
        self.produit.refresh_from_db()
        self.assertEqual(commande.statut, Commande.RECUE)
        self.assertEqual(self.produit.quantite_stock, 110)

    def test_bouton_commander_depuis_la_fiche_produit(self):
        """Le bouton pré-remplit fournisseur et première ligne."""
        self.client.login(username="gerant", password=MOT_DE_PASSE_TEST)

        reponse = self.client.post(
            reverse("inventory:commande_creer") + f"?produit={self.produit.pk}&quantite=120",
            {"fournisseur": self.fournisseur.pk, "commentaire": ""},
        )
        self.assertEqual(reponse.status_code, 302)

        commande = Commande.objects.get()
        ligne = commande.lignes.get()
        self.assertEqual(ligne.produit, self.produit)
        self.assertEqual(ligne.quantite_commandee, 120)

    def test_quantite_conseillee_sur_la_fiche(self):
        self.client.login(username="gerant", password=MOT_DE_PASSE_TEST)
        reponse = self.client.get(self.produit.get_absolute_url())

        self.assertGreater(reponse.context["stats"]["quantite_conseillee"], 0)
        self.assertContains(reponse, "Commander")

    def test_erreur_metier_affichee(self):
        """Une commande vide envoyée : message clair, pas de 500."""
        self.client.login(username="gerant", password=MOT_DE_PASSE_TEST)
        commande = services.creer_commande(self.fournisseur)

        reponse = self.client.post(
            reverse("inventory:commande_envoyer", kwargs={"pk": commande.pk}),
            follow=True,
        )
        self.assertEqual(reponse.status_code, 200)
        commande.refresh_from_db()
        self.assertEqual(commande.statut, Commande.BROUILLON)
        self.assertContains(reponse, "vide")

    def test_liste_filtrable_par_statut(self):
        self.client.login(username="gerant", password=MOT_DE_PASSE_TEST)
        brouillon = services.creer_commande(self.fournisseur)
        envoyee = services.creer_commande(self.fournisseur)
        services.ajouter_ligne_commande(envoyee, self.produit, 10)
        services.envoyer_commande(envoyee)

        reponse = self.client.get(
            reverse("inventory:commande_liste"), {"statut": Commande.ENVOYEE}
        )
        self.assertEqual(list(reponse.context["commandes"]), [envoyee])

    def test_actions_refusees_en_get(self):
        """Changer un état par un simple lien doit être impossible."""
        self.client.login(username="gerant", password=MOT_DE_PASSE_TEST)
        commande = services.creer_commande(self.fournisseur)
        services.ajouter_ligne_commande(commande, self.produit, 10)

        reponse = self.client.get(
            reverse("inventory:commande_envoyer", kwargs={"pk": commande.pk})
        )
        self.assertEqual(reponse.status_code, 405)  # méthode non autorisée
        commande.refresh_from_db()
        self.assertEqual(commande.statut, Commande.BROUILLON)


class PermissionsCommandeTests(BaseApplicationTestCase):
    """Le magasinier réceptionne mais ne passe pas les commandes."""

    def setUp(self):
        creer_utilisateur("gerant", groupe="Gerant")
        creer_utilisateur("magasinier", groupe="Magasinier")
        self.fournisseur = Fournisseur.objects.create(nom="SOTRACOM")
        self.categorie = Categorie.objects.create(nom="Matériaux")
        self.produit = Produit.objects.create(
            reference="PER-C1", nom="Fer", categorie=self.categorie,
            fournisseur=self.fournisseur, prix_achat=Decimal("100.00"),
            prix_vente=Decimal("150.00"), quantite_stock=0, seuil_alerte=5,
        )
        self.commande = services.creer_commande(self.fournisseur)
        self.ligne = services.ajouter_ligne_commande(self.commande, self.produit, 50)
        services.envoyer_commande(self.commande)

    def test_gerant_accede_a_tout(self):
        self.client.login(username="gerant", password=MOT_DE_PASSE_TEST)

        for url in [
            reverse("inventory:commande_liste"),
            reverse("inventory:commande_creer"),
            reverse("inventory:commande_detail", kwargs={"pk": self.commande.pk}),
        ]:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_magasinier_consulte_les_commandes(self):
        self.client.login(username="magasinier", password=MOT_DE_PASSE_TEST)

        for url in [
            reverse("inventory:commande_liste"),
            reverse("inventory:commande_detail", kwargs={"pk": self.commande.pk}),
        ]:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_magasinier_ne_peut_pas_creer_de_commande(self):
        self.client.login(username="magasinier", password=MOT_DE_PASSE_TEST)

        self.assertEqual(
            self.client.get(reverse("inventory:commande_creer")).status_code, 403
        )
        reponse = self.client.post(
            reverse("inventory:commande_creer"), {"fournisseur": self.fournisseur.pk}
        )
        self.assertEqual(reponse.status_code, 403)
        self.assertEqual(Commande.objects.count(), 1)

    def test_magasinier_ne_peut_pas_envoyer_ni_annuler(self):
        self.client.login(username="magasinier", password=MOT_DE_PASSE_TEST)

        for nom in ("inventory:commande_envoyer", "inventory:commande_annuler"):
            with self.subTest(nom=nom):
                reponse = self.client.post(reverse(nom, kwargs={"pk": self.commande.pk}))
                self.assertEqual(reponse.status_code, 403)

    def test_magasinier_peut_receptionner(self):
        self.client.login(username="magasinier", password=MOT_DE_PASSE_TEST)

        reponse = self.client.post(
            reverse("inventory:ligne_receptionner", kwargs={"pk": self.ligne.pk}),
            {"quantite": "50"},
        )
        self.assertEqual(reponse.status_code, 302)

        self.produit.refresh_from_db()
        self.assertEqual(self.produit.quantite_stock, 50)

    def test_utilisateur_sans_role_na_acces_a_rien(self):
        creer_utilisateur("sans_role")
        self.client.login(username="sans_role", password=MOT_DE_PASSE_TEST)

        self.assertEqual(
            self.client.get(reverse("inventory:commande_liste")).status_code, 403
        )
