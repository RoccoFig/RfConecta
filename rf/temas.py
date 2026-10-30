"""Identidad visual de cada negocio. Un solo esquema de tema, validado en el servidor, que consumen TODAS las pantallas
del cliente (inicio, registro, ingreso, tarjeta, premios, avisos). Vive en comercios.tema (JSON) y las imágenes en la
misma fila (logo_img, fondo_img).

Nada de lo que se guarda es CSS o HTML libre: los colores son #rrggbb, las opciones salen de listas cerradas y los textos
se muestran escapados. Las pantallas reciben el tema como variables CSS (css_tema) y clases del <body> (clases)."""
import json, re
from flask import request
from markupsafe import Markup
from .db import db, ahora

HEX = re.compile(r"#[0-9a-fA-F]{6}")
FORMAS = {"recta": "4px", "suave": "12px", "redonda": "22px"}
# (texto, títulos). Solo fuentes del sistema o servidas por la app: la política de seguridad no admite fuentes externas
FUENTES = {"moderna": ("system-ui,sans-serif", "system-ui,sans-serif"),
           "clasica": ("system-ui,sans-serif", "Georgia,Cambria,serif"),
           "redondeada": ("Trebuchet MS,Verdana,sans-serif", "Trebuchet MS,Verdana,sans-serif"),
           "geometrica": ("DM Sans,system-ui,sans-serif", "DM Sans,system-ui,sans-serif"),
           "grotesca": ("DM Sans,system-ui,sans-serif", "Bricolage Grotesque,system-ui,sans-serif"),
           "elegante": ("Georgia,Cambria,serif", "Palatino Linotype,Book Antiqua,Palatino,Georgia,serif"),
           "impacto": ("system-ui,sans-serif", "Impact,Haettenschweiler,Arial Narrow Bold,sans-serif"),
           "tecnica": ("system-ui,sans-serif", "ui-monospace,SFMono-Regular,Menlo,Consolas,monospace")}

# opciones de listas cerradas: clave -> {valor: etiqueta para el panel}
OPCIONES = {
    "tipografia": {"moderna": "Moderna (del sistema)", "clasica": "Clásica (títulos con serifa)", "redondeada": "Redondeada",
                   "geometrica": "Geométrica", "grotesca": "Grotesca de carácter", "elegante": "Elegante",
                   "impacto": "Impacto (condensada)", "tecnica": "Técnica (monoespaciada)"},
    "estilo": {"clasico": "Clásico", "editorial": "Editorial", "audaz": "Audaz", "suave": "Suave"},
    "forma": {"recta": "Rectas", "suave": "Suaves", "redonda": "Muy redondeadas"},
    "botones": {"solido": "Sólidos", "contorno": "Con contorno", "pildora": "Píldora", "sombra": "Con sombra marcada"},
    "tarjetas": {"borde": "Con borde fino", "sombra": "Flotantes (sombra)", "plano": "Planas", "marco": "Marco grueso"},
    "cabecera": {"barra": "Barra de color", "limpia": "Limpia (sin fondo)", "centrada": "Centrada con logo grande"},
    "densidad": {"compacta": "Compacto", "normal": "Normal", "amplia": "Aireado"},
    "decoracion": {"ninguna": "Ninguna", "puntos": "Puntos", "rayas": "Rayas diagonales", "circulos": "Círculos",
                   "degradado": "Degradado suave"},
}
COLORES = ("fondo", "tinta", "primario", "acento", "sup", "boton", "boton_texto")
AUTOMATICOS = ("primario", "sup", "boton", "boton_texto")  # si no se eligen, se calculan a partir de los demás
TEXTOS = {"icono": 8, "titular": 80, "bajada": 240, "llamado": 30}
DEFECTO = dict(fondo="#f2f4f1", tinta="#16322c", acento="#e0a526", tipografia="moderna", estilo="clasico", forma="suave",
               botones="solido", tarjetas="borde", cabecera="barra", densidad="normal", decoracion="ninguna",
               icono="✓", titular="", bajada="", llamado="")
DENSIDAD = {"compacta": ".75", "normal": "1", "amplia": "1.35"}
MAX_LOGO = 400 * 1024
MAX_FONDO = 500 * 1024


# ---------- color ----------
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


def legible(fondo):
    """Blanco o casi negro: el que mejor se lea encima de ese color (siempre da 4.5:1 o más)."""
    return "#ffffff" if contraste(fondo, "#ffffff") >= contraste(fondo, "#141414") else "#141414"


def _color(v):
    v = (v or "").strip() if isinstance(v, str) else ""
    return v.lower() if HEX.fullmatch(v) else None


def _texto(v, n):
    v = v if isinstance(v, str) else ""
    return re.sub(r"[\x00-\x1f\x7f]", " ", v).strip()[:n]


def tipo_imagen(b):
    if b.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if b.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        return "image/webp"
    return None


# ---------- el tema completo ----------
def leer(txt):
    try:
        r = json.loads(txt or "{}")
    except (ValueError, TypeError):
        return {}
    return r if isinstance(r, dict) else {}


def normalizar(raw):
    """Tema completo y seguro a partir de lo guardado: lo que falte o no sea válido toma un valor por defecto.
    Entiende también los temas de versiones anteriores (radio/fuente/titulo en vez de forma/tipografia)."""
    r = raw if isinstance(raw, dict) else {}
    t = {k: _color(r.get(k)) or DEFECTO[k] for k in ("fondo", "tinta", "acento")}
    oscuro = _lum(t["fondo"]) < 0.2
    t["primario"] = _color(r.get("primario")) or t["tinta"]
    t["sup"] = _color(r.get("sup")) or mezclar(t["fondo"], "#ffffff", 0.08 if oscuro else 0.6)
    t["boton"] = _color(r.get("boton")) or t["primario"]
    bt = _color(r.get("boton_texto"))
    t["boton_texto"] = bt if bt and contraste(bt, t["boton"]) >= 4.5 else legible(t["boton"])
    for k, ops in OPCIONES.items():
        t[k] = r.get(k) if r.get(k) in ops else DEFECTO[k]
    if r.get("forma") not in FORMAS:  # temas viejos: guardaban el radio en px
        t["forma"] = next((k for k, v in FORMAS.items() if v == r.get("radio")), DEFECTO["forma"])
    if r.get("tipografia") not in FUENTES:  # temas viejos: guardaban la familia tipográfica
        tit, fu = str(r.get("titulo") or ""), str(r.get("fuente") or "")
        t["tipografia"] = ("clasica" if tit.startswith("Georgia") else "redondeada" if (tit or fu).startswith("Trebuchet")
                           else DEFECTO["tipografia"])
    for k, n in TEXTOS.items():
        t[k] = _texto(r.get(k), n) or DEFECTO[k]
    # valores derivados: nadie los elige, se calculan para que todo combine y se lea bien
    t["linea"] = mezclar(t["fondo"], t["tinta"], 0.18)
    t["suave"] = mezclar(t["tinta"], t["fondo"], 0.3)
    t["primario_texto"] = legible(t["primario"])
    t["acento_texto"] = legible(t["acento"])
    t["resalte"] = t["primario"] if contraste(t["primario"], t["sup"]) >= 3 and contraste(t["primario"], t["fondo"]) >= 3 else t["tinta"]
    t["radio"] = FORMAS[t["forma"]]
    t["radio_boton"] = "999px" if t["botones"] == "pildora" else t["radio"]
    t["fuente"], t["titulo"] = FUENTES[t["tipografia"]]
    t["clases"] = " ".join(f"{p}-{t[k]}" for p, k in (("e", "estilo"), ("cab", "cabecera"), ("bt", "botones"),
                                                       ("tj", "tarjetas"), ("den", "densidad"), ("dec", "decoracion")))
    return t


def css_tema(t, fondo_url=None):
    """Variables CSS del tema. Todos los valores salen de normalizar(): colores #rrggbb y listas cerradas."""
    v = dict(fondo=t["fondo"], tinta=t["tinta"], primario=t["primario"], acento=t["acento"], sup=t["sup"], linea=t["linea"],
             suave=t["suave"], boton=t["boton"], **{"boton-texto": t["boton_texto"], "primario-texto": t["primario_texto"],
             "acento-texto": t["acento_texto"], "radio-boton": t["radio_boton"]}, resalte=t["resalte"], radio=t["radio"],
             fuente=t["fuente"], titulo=t["titulo"], esp=DENSIDAD[t["densidad"]])
    if fondo_url and re.fullmatch(r"/[A-Za-z0-9/_.?=-]+", fondo_url):
        v["fondo-img"] = f"url({fondo_url})"
    return Markup(":root{%s}") % Markup(";".join(f"--{k}:{x}" for k, x in v.items()))


RADIO_PANEL = {"recta": ("6px", "4px"), "suave": ("14px", "9px"), "redonda": ("20px", "13px")}


def css_panel(t):
    """Variables del panel del negocio (dueño y caja) a partir del MISMO tema que ven sus clientes.
    El panel es una herramienta de trabajo: siempre queda claro y con texto oscuro, aunque la página del cliente sea
    oscura o muy colorida. De la marca toma colores de acción, tipografía de títulos y forma; nada de efectos."""
    claro = _lum(t["fondo"]) > 0.6
    papel = mezclar(t["fondo"], "#ffffff", 0.35) if claro else mezclar("#f3f3f1", t["primario"], 0.05)
    texto = t["tinta"] if claro and contraste(t["tinta"], "#ffffff") >= 7 else "#17201b"
    tinta = next((c for c in (t["primario"], t["tinta"], t["boton"]) if contraste(c, "#ffffff") >= 7), "#0d1310")
    marca_texto = next((c for c in (t["primario"], t["boton"], t["acento"]) if contraste(c, "#ffffff") >= 4.5), texto)
    r, r_sm = RADIO_PANEL[t["forma"]]
    v = {"marca": t["boton"], "marca-ink": t["boton_texto"], "marca-texto": marca_texto,
         "marca-suave": mezclar("#ffffff", t["primario"], 0.1), "marca-2": t["acento"],
         "ink": tinta, "ink-2": mezclar(tinta, "#ffffff", 0.08), "ink-3": mezclar(tinta, "#ffffff", 0.16),
         "paper": papel, "line": mezclar(papel, texto, 0.13), "line-2": mezclar(papel, texto, 0.07), "neutro-bg": mezclar(papel, texto, 0.06),
         "text": texto, "muted": mezclar(texto, "#ffffff", 0.36), "titulo-marca": t["titulo"], "r": r, "r-sm": r_sm}
    return Markup(":root{%s}") % Markup(";".join(f"--{k}:{x}" for k, x in v.items()))


def problemas(t):
    """Combinaciones que se leerían mal. Se avisan en la vista previa y no se dejan guardar."""
    p = []
    if contraste(t["tinta"], t["fondo"]) < 4.5:
        p.append("El texto y el fondo se confunden: elegí colores con más contraste.")
    if contraste(t["tinta"], t["sup"]) < 4.5:
        p.append("El texto no se lee bien sobre las tarjetas: cambiá el color de superficie o el del texto.")
    if contraste(t["acento"], t["fondo"]) < 1.5 and contraste(t["acento"], t["sup"]) < 1.5:
        p.append("El color destacado casi no se distingue del fondo.")
    return p


def combinar(valores, actual):
    """Aplica al tema guardado los campos que llegaron (formulario o vista previa). Devuelve (tema_crudo, error).
    Los campos que no llegan conservan su valor; los que llegan con algo inválido se rechazan."""
    actual = actual if isinstance(actual, dict) else {}
    t = normalizar(actual)
    # los colores automáticos (se calculan de otros) solo se guardan si alguien los eligió a propósito
    nuevo = {k: t[k] for k in COLORES + tuple(OPCIONES) + tuple(TEXTOS) if k not in AUTOMATICOS or _color(actual.get(k))}
    error = None
    for k in COLORES:
        if k in valores:
            c = _color(valores.get(k))
            if not c:
                error = "Revisá los colores: tienen que ser del selector de colores."
            elif not (k in AUTOMATICOS and k not in nuevo and c == t[k]):  # llegó igual que el automático: sigue automático
                nuevo[k] = c
    for k, ops in OPCIONES.items():
        if k in valores:
            if valores.get(k) in ops:
                nuevo[k] = valores.get(k)
            else:
                error = "Revisá las opciones de estilo."
    for k, n in TEXTOS.items():
        if k in valores:
            nuevo[k] = _texto(valores.get(k), n)
    # lo que coincide con el valor automático no se guarda: así sigue al color del que depende
    if nuevo.get("primario") == nuevo["tinta"]:
        nuevo.pop("primario")
    n = normalizar(nuevo)
    if nuevo.get("boton") == n["primario"]:
        nuevo.pop("boton")
    if nuevo.get("boton_texto") == legible(n["boton"]):
        nuevo.pop("boton_texto")
    return nuevo, error


def _imagen(campo, maximo):
    f = request.files.get(campo)
    if not (f and f.filename):
        return None, None, None
    datos = f.read(maximo + 1)
    mime = tipo_imagen(datos)
    if len(datos) > maximo or not mime:
        return None, None, f"La imagen debe ser PNG, JPG o WebP de hasta {maximo // 1024} KB."
    return datos, mime, None


def guardar_tema(com):
    """Valida el formulario del editor y guarda el tema SOLO del negocio recibido (que ya validó el servidor).
    Devuelve un mensaje de error o None."""
    crudo, error = combinar(request.form, leer(com["tema"]))
    if error:
        return error
    p = problemas(normalizar(crudo))
    if p:
        return p[0]
    logo, logo_mime, err_logo = _imagen("logo", MAX_LOGO)
    fondo, fondo_mime, err_fondo = _imagen("fondo_img", MAX_FONDO)
    if err_logo or err_fondo:
        return ("Logo: " + err_logo) if err_logo else ("Imagen de fondo: " + err_fondo)
    d = db()
    d.execute("UPDATE comercios SET tema=? WHERE id=?", (json.dumps(crudo, ensure_ascii=False), com["id"]))
    if logo:
        d.execute("UPDATE comercios SET logo_img=?, logo_mime=?, logo_ver=? WHERE id=?", (logo, logo_mime, ahora(), com["id"]))
    elif request.form.get("quitar_logo"):
        d.execute("UPDATE comercios SET logo_img=NULL, logo_mime=NULL, logo_ver=? WHERE id=?", (ahora(), com["id"]))
    if fondo:
        d.execute("UPDATE comercios SET fondo_img=?, fondo_mime=?, fondo_ver=? WHERE id=?", (fondo, fondo_mime, ahora(), com["id"]))
    elif request.form.get("quitar_fondo"):
        d.execute("UPDATE comercios SET fondo_img=NULL, fondo_mime=NULL, fondo_ver=? WHERE id=?", (ahora(), com["id"]))
    tagline = request.form.get("tagline")
    if tagline is not None:
        d.execute("UPDATE comercios SET tagline=? WHERE id=?", (_texto(tagline, 80), com["id"]))
    d.commit()
    return None
