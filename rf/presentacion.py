"""Todo lo visual que se calcula en Python: temas, logos, avatares y diseños a medida de cada negocio."""
import json, os, re
from flask import current_app, url_for
from markupsafe import Markup, escape

TEMA_BASE = dict(fondo="#f2f4f1", tinta="#16322c", acento="#e0a526", linea="#cfd8d3", sup="#ffffff",
                 radio="8px", fuente="system-ui,sans-serif", titulo="system-ui,sans-serif", icono="✓")


def _tema_json(txt):
    try:
        return json.loads(txt or "{}")
    except ValueError:
        return {}


def tema_de(com):
    """Tema base (de respaldo) de un negocio: sirve cuando todavía no tiene diseño a medida."""
    t = dict(TEMA_BASE, nombre="", tagline="")
    if com is not None:
        t.update(_tema_json(com["tema"]))
        t["nombre"], t["tagline"] = com["nombre"], com["tagline"] or ""
    return t


def info_diseno(slug):
    """¿Tiene este negocio un diseño hecho a medida? Busca su carpeta de plantillas y de estilos."""
    tpl = os.path.join(current_app.root_path, current_app.template_folder, "disenos", slug)
    est = os.path.join(current_app.static_folder, "disenos", slug)
    plantillas = sorted(f for f in os.listdir(tpl) if f.endswith(".html")) if os.path.isdir(tpl) else []
    archivos = sorted(os.listdir(est)) if os.path.isdir(est) else []
    logo = next((f for f in archivos if re.fullmatch(r"logo\.(svg|png|jpe?g|webp)", f)), None)
    return dict(
        plantillas=plantillas, archivos=archivos, a_medida=bool(plantillas or archivos),
        estilo=url_for("static", filename=f"disenos/{slug}/estilo.css") if "estilo.css" in archivos else None,
        logo=url_for("static", filename=f"disenos/{slug}/{logo}") if logo else None)


def iniciales(nombre):
    pal = re.findall(r"[A-Za-z0-9ÁÉÍÓÚÑáéíóúñ]+", nombre or "")[:2]
    return "".join(p[0] for p in pal).upper() or "•"


def monograma(t):
    return Markup('<svg viewBox="0 0 48 48" width="50" height="50" role="img" aria-label="%s"><circle cx="24" cy="24" r="23" fill="%s"/>'
                  '<text x="24" y="31" text-anchor="middle" font-size="20" font-weight="700" font-family="Georgia,serif" fill="%s">%s</text></svg>'
                  ) % (t["nombre"], t["acento"], t["tinta"], iniciales(t["nombre"]))


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
    t = dict(TEMA_BASE, **_tema_json(tema))
    if tiene_logo and slug:
        return Markup('<img class="avatar" src="%s" alt="" width="%d" height="%d">') % (
            url_for("cliente.logo", slug=slug, v=ver or 0), tam, tam)
    return Markup('<span class="avatar" style="width:%dpx;height:%dpx;background:%s;color:%s">%s</span>') % (
        tam, tam, t["acento"], t["tinta"], iniciales(nombre))
