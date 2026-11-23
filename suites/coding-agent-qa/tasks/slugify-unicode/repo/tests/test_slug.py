from slug import slugify

def test_accents():
    assert slugify("Café Déjà Vu!") == "cafe-deja-vu"
