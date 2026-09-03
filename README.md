# StockFlow

Application Django de gestion de stock pour PME au Bénin.

## Stack

- Python 3.12, Django 5.x
- Templates Django + Bootstrap 5 (CDN)
- SQLite en dev, PostgreSQL en prod (Render)
- django-environ, openpyxl, whitenoise

## Installation

```bash
python -m venv venv
venv\Scripts\activate          # Windows
pip install -r requirements.txt
cp .env.example .env           # puis renseigner les valeurs
python manage.py migrate
python manage.py createsuperuser
python manage.py seed          # données de démonstration
python manage.py runserver
```

Admin disponible sur `/admin/`.

## Structure

```
config/      projet Django (settings, urls)
inventory/   application unique (modèles, admin, services, seed)
```

Modèles : `Categorie`, `Fournisseur`, `Produit`, `Mouvement`.

`Produit.quantite_stock` ne doit jamais être modifié directement : toute
entrée/sortie passe par `inventory/services.py::enregistrer_mouvement`.
