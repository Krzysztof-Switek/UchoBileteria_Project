"""Terms of sale ("Regulamin sprzedaży biletów", templates/legal/regulamin.html).

Every order stores the version the buyer accepted. Change the text -> add an
entry to the change history at the bottom of the template and bump the
version here (date of the new text); orders placed earlier keep theirs.
"""

TERMS_VERSION = "2026-10-06b"
