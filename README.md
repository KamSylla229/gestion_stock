# StockFlow

Application de gestion de stock pour les PME, conçue pour le contexte béninois.

## Problème

Dans la plupart des PME, le stock est suivi sur un cahier ou un tableur. Les
conséquences sont toujours les mêmes :

- **Ruptures non anticipées** : on découvre qu'un produit manque au moment où un
  client le demande. La vente est perdue.
- **Stock théorique faux** : le cahier et la réalité divergent, sans qu'on puisse
  savoir quand ni pourquoi l'écart est apparu.
- **Aucune traçabilité** : impossible de reconstituer l'historique des entrées et
  sorties d'un article.
- **Trésorerie invisible** : le gérant ne sait pas combien d'argent dort dans son
  stock.

## Solution

StockFlow enregistre chaque entrée et chaque sortie, et en déduit le stock.
Le principe central : **le stock n'est jamais saisi à la main**, il découle
uniquement des mouvements enregistrés.

Conséquences concrètes :

- le stock affiché correspond toujours à la somme des mouvements ;
- une sortie supérieure au stock disponible est refusée ;
- chaque produit a un seuil d'alerte, et le gérant est prévenu par email au
  moment exact où ce seuil est franchi ;
- un rapport quotidien récapitule l'activité et les produits à réapprovisionner.

## Fonctionnalités

**Gestion du catalogue**
- Produits, catégories, fournisseurs (avec contact et délai de livraison)
- Unité de vente par produit (sac, barre, casier…)
- Recherche par nom ou référence
- Filtres par catégorie, fournisseur, statut et stock bas
- Désactivation plutôt que suppression : aucune donnée n'est perdue

**Mouvements de stock**
- Entrées (réceptions), sorties (ventes) et ajustements (casses, écarts)
- Contrôle du stock disponible avant toute sortie
- Traçabilité complète : qui a fait quoi, quand, avec quelle pièce justificative
- Stock après chaque mouvement figé dans l'historique
- Historique paginé, filtrable par produit, type, utilisateur et période
- Recherche libre par nom de produit, référence ou référence de bon
- Les 20 derniers mouvements sur chaque fiche produit

**Pilotage**
- Tableau de bord : 5 indicateurs, période ajustable (jour / 7 jours / mois)
- Valeur du stock ventilée par catégorie
- Produits à réapprovisionner et produits les plus mouvementés
- Par produit : rythme de sortie, couverture en jours, dernière entrée
- Alertes email automatiques
- Rapport quotidien par email
- Export Excel de l'état du stock **et** de l'historique des mouvements

**Commandes fournisseurs**
- Cycle complet : brouillon, envoi, réception partielle, réception soldée
- Plusieurs produits par commande, prix figé au moment de la commande
- La réception crée automatiquement l'entrée de stock, tracée et justifiée
- Quantité de commande conseillée d'après le rythme de consommation
- Date de livraison prévue d'après le délai du fournisseur

**Sécurité**
- Authentification obligatoire sur toute l'application
- Deux rôles : Gérant et Magasinier, contrôlés côté serveur

## Dashboard

Quatre indicateurs, calculés en base de données (`aggregate` / `annotate`) :

| Indicateur | Ce qu'il dit au gérant |
|---|---|
| **Références actives** | Taille réelle du catalogue exploité, et nombre total d'unités en stock |
| **Valeur du stock** | Argent immobilisé (quantité × prix d'achat) |
| **En alerte** | Produits au niveau ou sous leur seuil, dont ceux en rupture totale |
| **Mouvements** | Entrées et sorties sur la période choisie |
| **Sorties valorisées** | Ce qui est sorti du stock sur la période, au prix d'achat |

La période se règle en haut de page : jour, 7 jours ou mois.

Le tableau de bord affiche également la ventilation de la valeur du stock par
catégorie, la liste des produits à réapprovisionner (triés du plus critique au
moins critique) et les 10 derniers mouvements.

## Automatisation

**Alertes email.** À chaque sortie, StockFlow compare le stock avant et après.
L'alerte part uniquement lorsque le seuil vient d'être *franchi* :

| Stock avant | Sortie | Stock après | Seuil | Alerte |
|---|---|---|---|---|
| 15 | 7 | 8 | 10 | **Oui** — le seuil vient d'être franchi |
| 8 | 2 | 6 | 10 | Non — le produit était déjà sous le seuil |
| 20 | 5 | 15 | 10 | Non — toujours au-dessus |

L'email (HTML + version texte) précise le stock avant, la quantité sortie, le
stock actuel, le seuil et le niveau d'urgence (stock critique ou rupture).
Il est envoyé après validation de la transaction : un mouvement annulé ne
déclenche jamais d'alerte, et une panne du serveur email n'interrompt jamais une
vente.

**Rapport quotidien.** À lancer une fois par jour (tâche planifiée) :

```bash
python manage.py rapport_quotidien              # génère et envoie
python manage.py rapport_quotidien --apercu     # affiche sans envoyer
python manage.py rapport_quotidien --jour 2026-09-14
```

Il contient l'état du stock, les mouvements de la journée (entrées et sorties,
en nombre et en quantité) et la liste des produits à réapprovisionner.

**Export Excel.** Deux exports, tous deux générés à la volée et respectant les
filtres appliqués à l'écran :

- **État du stock** — depuis la liste des produits ou le tableau de bord
- **Historique des mouvements** — depuis la page Historique

En-têtes en gras, filtre automatique, volets figés, formats numériques, et
colonne colorée selon la situation (rupture / sous seuil / normal pour le stock,
entrée / sortie / ajustement pour l'historique).

## Stack

- Python 3.11, Django 5.2
- Templates Django + Bootstrap 5 (CDN)
- SQLite en développement, PostgreSQL en production
- django-environ (configuration), openpyxl (Excel)
- Envoi d'emails par SMTP (Gmail, Brevo ou tout autre fournisseur)

## Installation locale

```bash
# 1. Récupérer le code
git clone https://github.com/KamSylla229/gestion_stock.git
cd gestion_stock

# 2. Environnement virtuel
python -m venv venv
venv\Scripts\Activate.ps1        # Windows PowerShell
# source venv/bin/activate       # Linux / macOS

# 3. Dépendances
pip install -r requirements.txt

# 4. Configuration
copy .env.example .env           # Windows   (cp .env.example .env ailleurs)
# puis renseigner SECRET_KEY et les autres variables dans .env
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"

# 5. Base de données
python manage.py migrate

# 6. Rôles et compte administrateur
python manage.py initialiser_groupes
python manage.py createsuperuser

# 7. Jeu de données de démonstration (optionnel)
python manage.py seed

# 8. Lancer
python manage.py runserver
```

L'application est disponible sur http://127.0.0.1:8000/ et l'administration
Django sur http://127.0.0.1:8000/admin/.

Pour attribuer un rôle à un utilisateur : `/admin/` → Utilisateurs → choisir le
groupe **Gerant** ou **Magasinier**.

## Variables d'environnement

Toutes les valeurs sensibles vivent dans `.env`, qui n'est jamais versionné.
Le modèle complet et commenté se trouve dans `.env.example`.

| Variable | Rôle |
|---|---|
| `SECRET_KEY` | Clé de signature Django. Obligatoire, unique par installation |
| `DEBUG` | `True` en développement, `False` en production |
| `ALLOWED_HOSTS` | Domaines autorisés, séparés par des virgules |
| `CSRF_TRUSTED_ORIGINS` | Origines HTTPS autorisées à soumettre des formulaires |
| `SITE_URL` | URL publique, utilisée pour les liens dans les emails |
| `DATABASE_URL` | `sqlite:///db.sqlite3` ou `postgres://user:pass@hote:5432/base` |
| `EMAIL_BACKEND` | Backend console en développement, SMTP en production |
| `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_USE_TLS` | Serveur d'envoi |
| `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD` | Identifiants SMTP |
| `DEFAULT_FROM_EMAIL` | Adresse expéditrice |
| `ALERTE_EMAIL_DESTINATAIRE` | Adresse du gérant : alertes et rapport quotidien |

Vérifier la configuration email :

```bash
python manage.py tester_email
python manage.py tester_email --destinataire adresse@exemple.com
```

La commande affiche les réglages actifs (sans jamais afficher le mot de passe),
teste la connexion au serveur, puis envoie un email de test.

## Rôles et permissions

| | Gérant | Magasinier |
|---|---|---|
| Consulter les produits et l'historique | Oui | Oui |
| Enregistrer entrées, sorties et ajustements | Oui | Oui |
| Consulter les commandes | Oui | Oui |
| Réceptionner une livraison | Oui | Oui |
| Passer, envoyer et annuler une commande | Oui | Non |
| Créer et modifier des produits (donc les prix) | Oui | Non |
| Tableau de bord (données financières) | Oui | Non |
| Export Excel | Oui | Non |

Les restrictions sont appliquées **côté serveur** (`PermissionRequiredMixin`) :
masquer un lien ne suffit pas, l'accès direct à l'URL est refusé par un 403.

## Tests

```bash
python manage.py test
```

180 tests couvrent la logique métier (entrées, sorties, stock insuffisant,
quantité nulle, transaction atomique), les alertes email, les KPI du tableau de
bord, l'export Excel, les permissions par rôle et le parcours utilisateur complet.

## Démonstration

1. Se connecter avec un compte du groupe **Gerant**
2. Ouvrir le **tableau de bord** : indicateurs, produits à réapprovisionner,
   derniers mouvements
3. Choisir un produit et noter son stock et son seuil d'alerte
4. Enregistrer une **entrée** : le stock augmente
5. Enregistrer une **sortie** qui fait passer le stock sous le seuil :
   l'alerte email part automatiquement
6. Tenter une sortie supérieure au stock : elle est refusée proprement,
   le stock reste inchangé
7. Revenir au tableau de bord : le produit apparaît dans les alertes
8. Consulter l'**historique** et le filtrer par produit, type ou période
9. Télécharger l'**export Excel**
10. Lancer `python manage.py rapport_quotidien`

## Structure du projet

```
config/                 projet Django (settings, urls)
inventory/              application unique
├── models.py           Categorie, Fournisseur, Produit, Mouvement,
│                       Commande, LigneCommande
├── services.py         logique métier : mouvements de stock et emails
├── statistiques.py     calculs ORM partagés (dashboard et rapport)
├── exports.py          génération du fichier Excel
├── views.py            vues génériques Django
├── forms.py            ModelForm produit, formulaire de mouvement
├── admin.py            administration Django
├── templates/          Bootstrap 5, dont les templates email
├── static/             feuille de style StockFlow (palette vert / blanc)
└── management/commands/
    ├── seed.py                 données de démonstration
    ├── initialiser_groupes.py  rôles Gerant / Magasinier
    ├── rapport_quotidien.py    rapport quotidien par email
    └── tester_email.py         diagnostic de la configuration email
```

## Règle d'architecture

`Produit.quantite_stock` n'est modifiable que par
`inventory/services.py::enregistrer_mouvement()`, qui met à jour le stock et
crée le mouvement dans une seule transaction atomique. Aucune vue, aucun script
et aucun formulaire ne touche directement à ce champ — l'administration Django
l'affiche d'ailleurs en lecture seule, et l'ajout de mouvements y est désactivé
pour la même raison.

La réception d'une commande ne fait pas exception : elle passe par
`receptionner_ligne_commande()`, qui appelle le même service. Une livraison
apparaît donc dans l'historique comme n'importe quelle entrée, avec la
référence de la commande en pièce justificative.
