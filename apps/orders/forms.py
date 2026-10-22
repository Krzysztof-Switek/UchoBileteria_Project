from django import forms

TERMS_ERROR = "Zaakceptuj regulamin sprzedaży biletów."


class SeatPurchaseForm(forms.Form):
    """Seated event: the map posts the chosen seat keys, comma separated."""

    buyer_email = forms.EmailField(label="Twój e-mail")
    seats = forms.CharField(max_length=4000)
    accept_terms = forms.BooleanField(error_messages={"required": TERMS_ERROR})

    def clean_seats(self):
        keys = [k.strip() for k in self.cleaned_data["seats"].split(",") if k.strip()]
        if not keys:
            raise forms.ValidationError("Wybierz miejsca na mapie.")
        return keys


class PurchaseForm(forms.Form):
    buyer_email = forms.EmailField(label="Twój e-mail")
    quantity = forms.IntegerField(label="Liczba biletów", min_value=1, initial=1)
    accept_terms = forms.BooleanField(error_messages={"required": TERMS_ERROR})

    def __init__(self, *args, max_quantity: int = 10, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["quantity"].max_value = max_quantity
        self.fields["quantity"].widget = forms.Select(
            choices=[(i, i) for i in range(1, max_quantity + 1)]
        )
