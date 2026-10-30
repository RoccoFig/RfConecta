"""Códigos QR. Solo contienen una dirección pública: nunca claves, datos de clientes ni saldos."""
import io
import segno
from flask import current_app, request
from markupsafe import Markup


def base_publica():
    """Dirección permanente del sistema. Definí PUBLIC_BASE_URL antes de imprimir carteles: cambiarla rompe los QR impresos."""
    return (current_app.config.get("PUBLIC_BASE_URL") or request.url_root).rstrip("/")


def url_negocio(slug):
    return f"{base_publica()}/l/{slug}"


def _qr(url):
    return segno.make(url, error="m", micro=False)


def svg_en_linea(url, escala=4):
    return Markup(_qr(url).svg_inline(scale=escala, border=4, dark="#000000", light="#ffffff", title="Código QR", desc=url,
                                      svgclass="qr", omitsize=False, lineclass="qr-l"))


def png(url, escala=12):
    b = io.BytesIO()
    _qr(url).save(b, kind="png", scale=escala, border=4, dark="#000000", light="#ffffff")
    return b.getvalue()


def svg(url, escala=10):
    b = io.BytesIO()
    _qr(url).save(b, kind="svg", scale=escala, border=4, dark="#000000", light="#ffffff", xmldecl=True, title="Código QR", desc=url)
    return b.getvalue()
