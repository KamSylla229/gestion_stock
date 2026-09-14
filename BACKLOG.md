# Backlog StockFlow

Fonctionnalités volontairement reportées après la v1.0. Le périmètre de la v1.0
a été tenu fermé pour livrer un produit fiable plutôt qu'un produit large.

## Priorité haute

### Traçabilité utilisateur sur les mouvements
Le modèle `Mouvement` ne mémorise pas **qui** a enregistré l'entrée ou la sortie.
Aujourd'hui, l'historique dit ce qui s'est passé, pas qui l'a fait.

C'est la limite la plus gênante pour un produit vendu à une PME avec deux rôles :
en cas d'écart d'inventaire, on ne peut pas remonter à l'opérateur.

À faire : ajouter `utilisateur = ForeignKey(User, on_delete=PROTECT, null=True)`
sur `Mouvement`, le renseigner dans `services.enregistrer_mouvement()` (paramètre
explicite, pas de variable globale), puis l'afficher dans l'historique, sur la
fiche produit et le tableau de bord, et l'ajouter comme filtre de l'historique.
Prévoir `null=True` pour les mouvements déjà enregistrés.

### Déploiement
Volontairement exclu de cette itération.
- Installer `whitenoise`, réactiver son middleware et le `STORAGES` associé
  (les deux emplacements sont commentés dans `config/settings.py`)
- Installer `psycopg[binary]` et basculer `DATABASE_URL` sur PostgreSQL
- Renseigner `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, `SITE_URL` et `DEBUG=False`
- Planifier `rapport_quotidien` (cron ou tâche planifiée de l'hébergeur)

### SMTP réel
La configuration est en place et pilotée par `.env`, mais l'envoi n'a été validé
qu'avec le backend console. À faire : renseigner un fournisseur réel (Gmail avec
mot de passe d'application, ou Brevo), puis valider avec `python manage.py tester_email`.

## Priorité moyenne

### Gestion des catégories et fournisseurs dans l'application
Ils ne se créent et se modifient aujourd'hui que via `/admin/`. Une interface
dédiée éviterait de donner l'accès à l'administration Django au gérant.

### Inventaire physique et régularisation
Pouvoir enregistrer un comptage réel et générer automatiquement le mouvement
d'ajustement correspondant — avec un type `AJUSTEMENT` en plus de
`ENTREE` / `SORTIE`, et un motif obligatoire.

### Rapport hebdomadaire et mensuel
La commande `rapport_quotidien` accepte déjà un paramètre `--jour`. Étendre le
principe à une période (produits les plus vendus, rotation du stock).

### Anti-répétition des alertes
Une alerte part au franchissement du seuil. Si un produit remonte au-dessus du
seuil puis repasse dessous, une nouvelle alerte part — c'est voulu. À surveiller
en usage réel : si le volume d'emails devient gênant, ajouter une temporisation
(pas plus d'une alerte par produit et par 24 h).

## Priorité basse

- **Multi-entrepôts** : un stock par dépôt, avec transferts entre dépôts
- **Rôles supplémentaires** : comptable (lecture seule des données financières),
  vendeur (sorties uniquement)
- **Audit avancé** : journal des connexions et des modifications de prix
- **Scan code-barres / QR** : saisie des mouvements au lecteur ou au téléphone
- **Notifications WhatsApp** : canal plus efficace que l'email au Bénin
- **API REST** : pour une intégration avec une caisse ou un logiciel comptable
- **Application mobile** : consultation du stock hors du bureau
- **Intégration fournisseurs** : commandes de réapprovisionnement automatiques
- **Prévision de demande** : estimation des besoins à partir de l'historique
- **Graphiques** : courbes d'évolution du stock et des ventes

## Dette technique identifiée

- **Python 3.11** alors que la cible du projet est 3.12. Sans impact fonctionnel
  connu, à aligner lors de la mise en production.
- **Concurrence sur le stock** : `enregistrer_mouvement()` est atomique, mais
  deux sorties simultanées sur le même produit pourraient théoriquement lire le
  même stock de départ. Sous PostgreSQL, ajouter un `select_for_update()` sur le
  produit règle le problème. Non critique pour une PME mono-poste.
