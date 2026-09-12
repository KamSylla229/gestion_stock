from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from inventory import services
from inventory.models import Categorie, Fournisseur, Mouvement, Produit


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


class AuthentificationTests(TestCase):
    """Vérifie que les pages privées sont bien protégées."""

    def test_pages_privees_redirigent_vers_connexion(self):
        urls = [
            reverse("inventory:produit_liste"),
            reverse("inventory:produit_creer"),
            reverse("inventory:mouvement_liste"),
            reverse("inventory:entree_stock"),
            reverse("inventory:sortie_stock"),
        ]
        for url in urls:
            with self.subTest(url=url):
                reponse = self.client.get(url)
                self.assertEqual(reponse.status_code, 302)
                self.assertIn(reverse("inventory:connexion"), reponse.url)

    def test_page_connexion_accessible_sans_authentification(self):
        reponse = self.client.get(reverse("inventory:connexion"))
        self.assertEqual(reponse.status_code, 200)

    def test_connexion_par_le_formulaire(self):
        """Connexion via le vrai formulaire de la vue, pas via client.login()."""
        User.objects.create_user(username="testeur", password="motdepasse123")

        reponse = self.client.post(
            reverse("inventory:connexion"),
            {"username": "testeur", "password": "motdepasse123"},
        )
        self.assertEqual(reponse.status_code, 302)
        self.assertEqual(reponse.url, reverse("inventory:produit_liste"))

        # La page privée est maintenant accessible.
        reponse = self.client.get(reverse("inventory:produit_liste"))
        self.assertEqual(reponse.status_code, 200)

    def test_connexion_refusee_avec_mauvais_mot_de_passe(self):
        User.objects.create_user(username="testeur", password="motdepasse123")

        reponse = self.client.post(
            reverse("inventory:connexion"),
            {"username": "testeur", "password": "mauvais"},
        )
        self.assertEqual(reponse.status_code, 200)
        self.assertContains(reponse, "Identifiants incorrects")


class ParcoursUtilisateurTests(TestCase):
    """
    Parcours complet : connexion, création de produit, recherche, fiche,
    entrée, sortie, sortie refusée, historique, filtres, déconnexion.
    """

    def setUp(self):
        self.utilisateur = User.objects.create_user(username="testeur", password="motdepasse123")
        self.categorie = Categorie.objects.create(nom="Alimentation")
        self.fournisseur = Fournisseur.objects.create(nom="Fournisseur test")

    def test_parcours_complet(self):
        # 1-2. Connexion
        self.assertTrue(self.client.login(username="testeur", password="motdepasse123"))

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
        self.client.login(username="testeur", password="motdepasse123")
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


class ProduitListeFiltresTests(TestCase):
    """Filtres et recherche de la liste des produits."""

    def setUp(self):
        self.utilisateur = User.objects.create_user(username="testeur", password="motdepasse123")
        self.client.login(username="testeur", password="motdepasse123")
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
