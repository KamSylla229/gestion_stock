from django.conf import settings
from django.core.mail import get_connection, send_mail
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = (
        "Teste la configuration email : affiche les réglages actifs, vérifie la "
        "connexion au serveur puis envoie un email de test."
    )

    def add_arguments(self, parseur):
        parseur.add_argument(
            "--destinataire",
            help="Adresse de test (par défaut : ALERTE_EMAIL_DESTINATAIRE).",
        )

    def handle(self, *args, **options):
        destinataire = options["destinataire"] or getattr(settings, "ALERTE_EMAIL_DESTINATAIRE", "")
        if not destinataire:
            raise CommandError(
                "Aucun destinataire. Renseignez ALERTE_EMAIL_DESTINATAIRE dans .env "
                "ou utilisez --destinataire."
            )

        self.stdout.write("Configuration email active :")
        self.stdout.write(f"  EMAIL_BACKEND      : {settings.EMAIL_BACKEND}")
        self.stdout.write(f"  EMAIL_HOST         : {settings.EMAIL_HOST}")
        self.stdout.write(f"  EMAIL_PORT         : {settings.EMAIL_PORT}")
        self.stdout.write(f"  EMAIL_USE_TLS      : {settings.EMAIL_USE_TLS}")
        self.stdout.write(f"  EMAIL_HOST_USER    : {settings.EMAIL_HOST_USER or '(vide)'}")
        # Le mot de passe n'est jamais affiché, seulement sa présence.
        self.stdout.write(
            f"  EMAIL_HOST_PASSWORD: {'(defini)' if settings.EMAIL_HOST_PASSWORD else '(vide)'}"
        )
        self.stdout.write(f"  DEFAULT_FROM_EMAIL : {settings.DEFAULT_FROM_EMAIL}")
        self.stdout.write(f"  Destinataire       : {destinataire}")
        self.stdout.write("")

        # Étape 1 : ouverture de connexion, pour distinguer un problème de
        # connexion/authentification d'un problème d'envoi.
        try:
            connexion = get_connection()
            connexion.open()
            connexion.close()
            self.stdout.write(self.style.SUCCESS("Connexion au serveur : OK"))
        except Exception as erreur:
            raise CommandError(
                f"Connexion au serveur email impossible : {type(erreur).__name__} : {erreur}"
            )

        # Étape 2 : envoi réel.
        try:
            envoyes = send_mail(
                subject="[StockFlow] Email de test",
                message=(
                    "Cet email confirme que la configuration email de StockFlow "
                    "fonctionne correctement."
                ),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[destinataire],
                fail_silently=False,
            )
        except Exception as erreur:
            raise CommandError(f"Échec de l'envoi : {type(erreur).__name__} : {erreur}")

        if envoyes:
            self.stdout.write(self.style.SUCCESS(f"Email de test envoyé à {destinataire}."))
        else:
            raise CommandError("Aucun email n'a été envoyé (le backend a renvoyé 0).")
