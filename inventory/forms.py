from django import forms

from inventory.models import Mouvement, Produit


def appliquer_classes_bootstrap(champs):
    """Ajoute les classes Bootstrap 5 aux widgets d'un formulaire."""
    for champ in champs.values():
        if isinstance(champ.widget, forms.CheckboxInput):
            champ.widget.attrs.setdefault("class", "form-check-input")
        elif isinstance(champ.widget, forms.Select):
            champ.widget.attrs.setdefault("class", "form-select")
        else:
            champ.widget.attrs.setdefault("class", "form-control")


class ProduitForm(forms.ModelForm):
    """
    Formulaire de création / modification d'un produit.

    quantite_stock est volontairement absent : il ne peut être modifié
    que par inventory/services.py, via un Mouvement.
    """

    class Meta:
        model = Produit
        fields = [
            "reference",
            "nom",
            "categorie",
            "fournisseur",
            "prix_achat",
            "prix_vente",
            "seuil_alerte",
            "actif",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        appliquer_classes_bootstrap(self.fields)


class MouvementForm(forms.Form):
    """
    Formulaire d'entrée, de sortie ou d'ajustement de stock.

    Ce n'est volontairement PAS un ModelForm : la création du Mouvement et
    la mise à jour du stock doivent passer par services.enregistrer_mouvement(),
    jamais par un form.save() qui contournerait la logique métier.

    Les libellés s'adaptent au type de mouvement passé par la vue.
    """

    produit = forms.ModelChoiceField(
        queryset=Produit.objects.filter(actif=True),
        label="Produit",
        empty_label="— Choisir un produit —",
    )
    quantite = forms.IntegerField(min_value=1, label="Quantité")
    motif = forms.CharField(max_length=255, required=False, label="Motif")
    document = forms.CharField(
        max_length=50, required=False, label="Référence du bon",
        widget=forms.TextInput(attrs={"placeholder": "Facultatif"}),
    )
    destination = forms.CharField(
        max_length=255, required=False, label="Client ou destination",
        widget=forms.TextInput(attrs={"placeholder": "Facultatif"}),
    )

    def __init__(self, *args, type_mouvement=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.type_mouvement = type_mouvement

        if type_mouvement == Mouvement.ENTREE:
            self.fields["destination"].label = "Provenance"
            self.fields["document"].label = "Référence du bordereau"
        elif type_mouvement == Mouvement.AJUSTEMENT:
            # Un ajustement constate une perte : ni client, ni bon de sortie.
            del self.fields["destination"]
            self.fields["motif"].required = True
            self.fields["motif"].label = "Motif de l'ajustement"
            self.fields["motif"].widget.attrs["placeholder"] = "Casse, vol, écart d'inventaire…"

        appliquer_classes_bootstrap(self.fields)
