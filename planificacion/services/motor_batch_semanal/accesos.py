def score_acceso_sitio(sitio):
    texto = (sitio.condicion_acceso or "").strip().lower()

    if not texto:
        return 50.0

    if "libre" in texto:
        return 100.0

    if "correo" in texto:
        return 75.0

    if "confirm" in texto:
        return 70.0

    if "certific" in texto:
        return 55.0

    return 60.0


def score_acceso_conjunto(sitios):
    if not sitios:
        return 0.0

    scores = [score_acceso_sitio(sitio) for sitio in sitios]

    return round(
        sum(scores) / len(scores),
        2,
    )
