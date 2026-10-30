"""Todo lo visual que se calcula en Python: temas, logos, avatares y diseños a medida de cada negocio."""
import os, re
from flask import current_app, url_for
from markupsafe import Markup
from . import temas

TEMA_BASE = temas.normalizar({})


def tema_de(com):
    """Tema completo y validado de un negocio (rf/temas.py). Lo usan todas las pantallas del cliente."""
    t = temas.normalizar(temas.leer(com["tema"]) if com is not None else {})
    t["nombre"], t["tagline"] = (com["nombre"], com["tagline"] or "") if com is not None else ("", "")
    return t


def fondo_de(com):
    """Dirección de la imagen de fondo del negocio, si subió una."""
    if com is None or "tiene_fondo" not in com.keys() or not com["tiene_fondo"]:
        return None
    return url_for("cliente.fondo", slug=com["slug"], v=com["fondo_ver"] or 0)


def info_diseno(slug):
    """¿Tiene este negocio un diseño hecho a medida? Busca su carpeta de plantillas y de estilos."""
    tpl = os.path.join(current_app.root_path, current_app.template_folder, "disenos", slug)
    est = os.path.join(current_app.static_folder, "disenos", slug)
    plantillas = sorted(f for f in os.listdir(tpl) if f.endswith(".html")) if os.path.isdir(tpl) else []
    archivos = sorted(os.listdir(est)) if os.path.isdir(est) else []
    def url(nombre):
        # ?v=<fecha del archivo>: cuando cambiás el archivo, el navegador baja la versión nueva en vez de la guardada
        try:
            v = int(os.path.getmtime(os.path.join(est, nombre)))
        except OSError:
            v = 0
        return url_for("static", filename=f"disenos/{slug}/{nombre}", v=v)

    logo = next((f for f in ("logo.svg", "logo.png", "logo.webp", "logo.jpg", "logo.jpeg") if f in archivos), None)
    return dict(
        plantillas=plantillas, archivos=archivos, a_medida=bool(plantillas or archivos),
        estilo=url("estilo.css") if "estilo.css" in archivos else None,
        logo=url(logo) if logo else None)


def iniciales(nombre):
    pal = re.findall(r"[A-Za-z0-9ÁÉÍÓÚÑáéíóúñ]+", nombre or "")[:2]
    return "".join(p[0] for p in pal).upper() or "•"


def monograma(t):
    return Markup('<svg viewBox="0 0 48 48" width="50" height="50" role="img" aria-label="%s"><circle cx="24" cy="24" r="23" fill="%s"/>'
                  '<text x="24" y="31" text-anchor="middle" font-size="20" font-weight="700" font-family="Georgia,serif" fill="%s">%s</text></svg>'
                  ) % (t["nombre"], t["acento"], t["acento_texto"], iniciales(t["nombre"]))


def logo_de(com, t):
    """Logo para la cabecera del cliente: imagen subida > logo del diseño a medida > logo heredado > iniciales."""
    if com is None:
        return ""
    if com["tiene_logo"]:
        return Markup('<img src="%s" alt="" width="50" height="50" style="border-radius:50%%;object-fit:cover">'
                      ) % url_for("cliente.logo", slug=com["slug"], v=com["logo_ver"] or 0)
    d = info_diseno(com["slug"])
    if d["logo"]:
        return Markup('<img src="%s" alt="" width="50" height="50">') % d["logo"]
    if com["logo_svg"]:
        return Markup(com["logo_svg"])
    return monograma(t)


def avatar(nombre, tema=None, slug=None, tiene_logo=False, ver=0, tam=34):
    """Miniatura cuadrada de un negocio para tablas y listas del panel."""
    t = temas.normalizar(temas.leer(tema))
    if tiene_logo and slug:
        return Markup('<img class="avatar" src="%s" alt="" width="%d" height="%d">') % (
            url_for("cliente.logo", slug=slug, v=ver or 0), tam, tam)
    return Markup('<span class="avatar" style="width:%dpx;height:%dpx;background:%s;color:%s">%s</span>') % (
        tam, tam, t["acento"], t["acento_texto"], iniciales(nombre))
