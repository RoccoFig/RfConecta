"""Tema base de respaldo (colores, forma, tipografía, logo). Los diseños a medida van en rf/templates/disenos/."""
import json, re
from flask import request
from .db import db, ahora

FORMAS = {"recta": "4px", "suave": "12px", "redonda": "22px"}
FUENTES = {"clasica": ("system-ui,sans-serif", "Georgia,Cambria,serif"),
           "moderna": ("system-ui,sans-serif", "system-ui,sans-serif"),
           "redondeada": ("Trebuchet MS,Verdana,sans-serif", "Trebuchet MS,Verdana,sans-serif")}
MAX_LOGO = 400 * 1024


def _rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def mezclar(a, b, p):
    ra, rb = _rgb(a), _rgb(b)
    return "#%02x%02x%02x" % tuple(round(ra[i] * (1 - p) + rb[i] * p) for i in range(3))


def _lum(h):
    def c(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (c(x) for x in _rgb(h))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contraste(a, b):
    alto, bajo = sorted((_lum(a), _lum(b)), reverse=True)
    return (alto + 0.05) / (bajo + 0.05)


def tipo_imagen(b):
    if b.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if b.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        return "image/webp"
    return None


def valores_actuales(t):
    forma = t.get("forma") or next((k for k, v in FORMAS.items() if v == t["radio"]), "suave")
    tip = t.get("tipografia") or ("clasica" if t["titulo"].startswith("Georgia")
                                  else "redondeada" if t["titulo"].startswith("Trebuchet") else "moderna")
    return dict(fondo=t["fondo"], tinta=t["tinta"], acento=t["acento"], forma=forma, tipografia=tip, icono=t["icono"])


def guardar_tema(com):
    """Valida el formulario del tema de respaldo y lo guarda. Devuelve un mensaje de error o None."""
    f = request.form
    c = {k: f.get(k, "") for k in ("fondo", "tinta", "acento")}
    forma, tip = f.get("forma", ""), f.get("tipografia", "")
    icono = f.get("icono", "").strip()[:8] or "✓"
    if any(not re.fullmatch(r"#[0-9a-fA-F]{6}", v) for v in c.values()) or forma not in FORMAS or tip not in FUENTES:
        return "Revisá los colores y las opciones de estilo."
    if contraste(c["tinta"], c["fondo"]) < 4.5:
        return "El texto y el fondo se confunden: elegí colores con más contraste."
    if contraste(c["tinta"], "#ffffff") < 4.5:
        return "El color del texto es muy claro: los botones llevan letras blancas encima."
    if contraste(c["tinta"], c["acento"]) < 3:
        return "El color destacado se parece demasiado al del texto."
    img = mime = None
    logo = request.files.get("logo")
    if logo and logo.filename:
        datos = logo.read(MAX_LOGO + 1)
        mime = tipo_imagen(datos)
        if len(datos) > MAX_LOGO or not mime:
            return "El logo debe ser PNG, JPG o WebP de hasta 400 KB."
        img = datos
    tema = dict(c, sup=mezclar(c["fondo"], "#ffffff", 0.6), linea=mezclar(c["fondo"], c["tinta"], 0.18),
                radio=FORMAS[forma], fuente=FUENTES[tip][0], titulo=FUENTES[tip][1], icono=icono, forma=forma, tipografia=tip)
    db().execute("UPDATE comercios SET tema=? WHERE id=?", (json.dumps(tema), com["id"]))
    if img:
        db().execute("UPDATE comercios SET logo_img=?, logo_mime=?, logo_ver=? WHERE id=?", (img, mime, ahora(), com["id"]))
    db().commit()
    return None
