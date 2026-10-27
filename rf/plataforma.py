"""Panel de RF: administración de todos los negocios, clientes, actividad, canjes y carteles."""
import json, os, re, sqlite3, unicodedata
from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, session, url_for
from werkzeug.security import generate_password_hash
from . import motor, temas
from .db import DB_PATH, db, ahora
from .graficos import barras
from .presentacion import info_diseno, tema_de
from .seguridad import (admin_password, bloqueado, destino_seguro, es_plataforma, iniciar, registrar_fallo,
                        requiere_plataforma, verificar_plataforma)

bp = Blueprint("plataforma", __name__, url_prefix="/plataforma")

RUBROS = ["Cafetería", "Heladería", "Restaurante", "Panadería", "Gimnasio", "Indumentaria", "Peluquería", "Kiosco", "Otro"]
NAV = [(None, [("resumen", "Resumen", "layout-dashboard"), ("negocios", "Negocios", "store"), ("clientes", "Clientes", "users")]),
       ("Operación", [("actividad", "Actividad", "activity"), ("canjes", "Canjes", "gift"), ("carteles", "Carteles NFC", "nfc")]),
       ("Sistema", [("ajustes", "Ajustes", "settings")])]


@bp.context_processor
def contexto():
    ep = (request.endpoint or "").split(".")[-1]
    viejos = motor.un("SELECT COUNT(*) FROM canjes WHERE estado='pendiente' AND creado<?", ahora() - 2 * 86400) if es_plataforma() else 0
    grupos = [dict(titulo=t, items=[dict(
        url=url_for("plataforma." + n), etiqueta=e, icono=i, badge=(viejos or None) if n == "canjes" else None,
        activo=ep == n or (n == "negocios" and ep in ("negocio", "negocio_nuevo"))) for n, e, i in items]) for t, items in NAV]
    return dict(ambito="plataforma", nav=grupos, marca_nombre="RF Conecta", inicio_url=url_for("plataforma.resumen"),
                salir_url=url_for("plataforma.salir"), soporte=False, ruta=lambda n, **k: url_for("plataforma." + n, **k))


def slugify(txt):
    a = unicodedata.normalize("NFKD", txt).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", a).strip("-")[:40]


def dominio_limpio(txt):
    return re.sub(r"^https?://|/.*$", "", (txt or "").strip().lower()) or None


def entero(v, lo, hi):
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    return n if lo <= n <= hi else None


# ---------- acceso ----------
@bp.route("/ingresar", methods=["GET", "POST"])
def ingresar():
    if es_plataforma():
        return redirect(url_for("plataforma.resumen"))
    error = None
    if request.method == "POST":
        if not admin_password():
            error = "Falta configurar ADMIN_PASSWORD en el servidor."
        elif bloqueado("plataforma"):
            error = "Demasiados intentos. Esperá unos minutos y probá de nuevo."
        elif verificar_plataforma(request.form.get("clave", "")):
            iniciar("plataforma")
            return redirect(destino_seguro(request.form.get("siguiente"), url_for("plataforma.resumen")))
        else:
            registrar_fallo("plataforma")
            error = "Contraseña incorrecta."
    return render_template("panel/ingresar.html", titulo="Ingresar", encabezado="Panel de RF",
                           texto="Administrá todos los negocios de la plataforma.", error=error,
                           siguiente=request.args.get("siguiente", ""), marca=None), (429 if error and "Demasiados" in error else 200)


@bp.post("/salir")
def salir():
    session.clear()
    return redirect(url_for("plataforma.ingresar"))


# ---------- resumen ----------
@bp.route("/")
@requiere_plataforma
def resumen():
    k = motor.kpis()
    et, val = motor.serie_visitas(30)
    return render_template("plataforma/resumen.html", titulo="Resumen", subtitulo="Cómo está funcionando toda la plataforma.",
                           k=k, et=et, grafico=barras(val, et), alertas=motor.alertas_plataforma(),
                           reciente=motor.actividad(por_pagina=8)["filas"], ranking=motor.ranking_negocios(5))


# ---------- negocios ----------
@bp.route("/negocios")
@requiere_plataforma
def negocios():
    filas = motor.negocios_resumen()
    return render_template("plataforma/negocios.html", titulo="Negocios", subtitulo=f"{len(filas)} en la plataforma.",
                           negocios=filas, a_medida={n["slug"]: info_diseno(n["slug"])["a_medida"] for n in filas},
                           rubros=sorted({n["categoria"] for n in filas if n["categoria"]}))


@bp.route("/negocios/nuevo", methods=["GET", "POST"])
@requiere_plataforma
def negocio_nuevo():
    f = request.form
    if request.method == "POST":
        nombre, slug = f.get("nombre", "").strip()[:60], slugify(f.get("nombre", ""))
        clave, dom = f.get("clave", ""), dominio_limpio(f.get("dominio"))
        error = None
        if not nombre or not slug or slug in ("nuevo",):
            error = "Escribí un nombre válido para el negocio."
        elif len(clave) < 8:
            error = "La clave del dueño debe tener al menos 8 caracteres."
        elif db().execute("SELECT 1 FROM comercios WHERE slug=?", (slug,)).fetchone():
            error = "Ya existe un negocio con ese nombre."
        if not error:
            try:
                db().execute("INSERT INTO comercios(slug,nombre,tagline,tema,clave_hash,dominio,categoria,creado) VALUES(?,?,?,?,?,?,?,?)",
                             (slug, nombre, f.get("tagline", "").strip()[:80], "{}", generate_password_hash(clave), dom,
                              f.get("categoria", "Otro")[:40], ahora()))
                db().commit()
                flash(f"Negocio creado. Ahora definí su diseño: /l/{slug}", "ok")
                return redirect(url_for("plataforma.negocio", slug=slug, tab="diseno"))
            except sqlite3.IntegrityError:
                error = "Ese dominio ya lo usa otro negocio."
        flash(error, "error")
    return render_template("plataforma/nuevo.html", titulo="Nuevo negocio", subtitulo="Primero los datos; el diseño se crea a medida después.",
                           rubros=RUBROS, f=f)


@bp.route("/negocios/<slug>")
@requiere_plataforma
def negocio(slug):
    com = motor.comercio(slug)
    tab = request.args.get("tab", "resumen")
    if tab not in ("resumen", "configuracion", "diseno", "carteles"):
        abort(404)
    ctx = dict(com=com, tab=tab, diseno=info_diseno(slug), rubros=RUBROS)
    if tab == "resumen":
        et, val = motor.serie_visitas(30, com["id"])
        ctx.update(k=motor.kpis(com["id"]), et=et, grafico=barras(val, et), populares=motor.premios_populares(com["id"]))
    elif tab == "carteles":
        ctx["tags"] = motor.tags_lista(com["id"])
    elif tab == "diseno":
        ctx.update(v=temas.valores_actuales(tema_de(com)), formas=temas.FORMAS, tipografias=temas.FUENTES)
    return render_template("plataforma/negocio.html", titulo=com["nombre"], subtitulo=f"{com['categoria'] or 'Negocio'} · /l/{slug}", **ctx)


def _volver(slug, tab):
    return redirect(url_for("plataforma.negocio", slug=slug, tab=tab))


@bp.post("/negocios/<slug>/datos")
@requiere_plataforma
def negocio_datos(slug):
    com = motor.comercio(slug)
    nombre = request.form.get("nombre", "").strip()[:60]
    if not nombre:
        flash("El nombre no puede estar vacío.", "error")
        return _volver(slug, "configuracion")
    try:
        db().execute("UPDATE comercios SET nombre=?, categoria=?, tagline=?, dominio=? WHERE id=?",
                     (nombre, request.form.get("categoria", "Otro")[:40], request.form.get("tagline", "").strip()[:80],
                      dominio_limpio(request.form.get("dominio")), com["id"]))
        db().commit()
        flash("Datos guardados.", "ok")
    except sqlite3.IntegrityError:
        flash("Ese dominio ya lo usa otro negocio.", "error")
    return _volver(slug, "configuracion")


@bp.post("/negocios/<slug>/estado")
@requiere_plataforma
def negocio_estado(slug):
    com = motor.comercio(slug)
    nuevo = "suspendido" if com["estado"] == "activo" else "activo"
    db().execute("UPDATE comercios SET estado=? WHERE id=?", (nuevo, com["id"]))
    db().commit()
    flash("Programa suspendido: los clientes ven un aviso." if nuevo == "suspendido" else "Programa reactivado.", "ok")
    return _volver(slug, "configuracion")


@bp.post("/negocios/<slug>/clave")
@requiere_plataforma
def negocio_clave(slug):
    com = motor.comercio(slug)
    clave = request.form.get("clave", "")
    if len(clave) < 8:
        flash("La clave debe tener al menos 8 caracteres.", "error")
    else:
        db().execute("UPDATE comercios SET clave_hash=? WHERE id=?", (generate_password_hash(clave), com["id"]))
        db().commit()
        flash("Clave del dueño actualizada.", "ok")
    return _volver(slug, "configuracion")


@bp.post("/negocios/<slug>/tema")
@requiere_plataforma
def negocio_tema(slug):
    error = temas.guardar_tema(motor.comercio(slug))
    flash(error or "Tema base guardado.", "error" if error else "ok")
    return _volver(slug, "diseno")


# ---------- listados globales (con filtros y paginación en el servidor) ----------
def _filtros():
    slug = request.args.get("negocio", "")
    fila = db().execute("SELECT id FROM comercios WHERE slug=?", (slug,)).fetchone() if slug else None
    pagina = entero(request.args.get("pagina"), 1, 100000) or 1
    return (fila["id"] if fila else None), slug, request.args.get("q", "").strip()[:60], pagina


def _lista_negocios():
    return db().execute("SELECT slug, nombre FROM comercios ORDER BY nombre").fetchall()


@bp.route("/clientes")
@requiere_plataforma
def clientes():
    cid, slug, q, pagina = _filtros()
    orden = request.args.get("orden", "recientes")
    r = motor.clientes_lista(cid, q, orden, pagina)
    return render_template("panel/clientes.html", titulo="Clientes", subtitulo=f"{r['total']} en toda la plataforma.", r=r, q=q,
                           slug=slug, orden=orden, lista_negocios=_lista_negocios())


@bp.route("/actividad")
@requiere_plataforma
def actividad():
    cid, slug, q, pagina = _filtros()
    tipo = request.args.get("tipo", "")
    r = motor.actividad(cid, tipo if tipo in ("visita", "canje", "registro") else None, q, pagina)
    return render_template("panel/actividad.html", titulo="Actividad", subtitulo="Todo lo que pasa en la plataforma, en orden.",
                           r=r, q=q, slug=slug, tipo=tipo, lista_negocios=_lista_negocios())


@bp.route("/canjes")
@requiere_plataforma
def canjes():
    cid, slug, q, pagina = _filtros()
    estado = request.args.get("estado", "")
    r = motor.canjes_lista(cid, estado, q, pagina)
    return render_template("panel/canjes.html", titulo="Canjes", subtitulo="Premios canjeados por los clientes. La entrega la valida cada negocio.",
                           r=r, q=q, slug=slug, estado=estado, lista_negocios=_lista_negocios(), puede_validar=False)


@bp.route("/carteles", methods=["GET", "POST"])
@requiere_plataforma
def carteles():
    if request.method == "POST":
        uid = re.sub(r"[^0-9A-Fa-f]", "", request.form.get("uid", "")).upper()
        com = db().execute("SELECT id FROM comercios WHERE slug=?", (request.form.get("negocio", ""),)).fetchone()
        if len(uid) != 14 or not com:
            flash("Elegí un negocio y escribí el UID completo (14 caracteres).", "error")
        else:
            cur = db().execute("INSERT OR IGNORE INTO tags(comercio_id,uid) VALUES(?,?)", (com["id"], uid))
            db().commit()
            flash("Cartel registrado." if cur.rowcount else "Ese cartel ya estaba registrado.", "ok" if cur.rowcount else "error")
        return redirect(url_for("plataforma.carteles"))
    tags = motor.tags_lista()
    return render_template("panel/carteles.html", titulo="Carteles NFC", subtitulo=f"{sum(1 for t in tags if t['activo'])} activos.",
                           tags=tags, lista_negocios=_lista_negocios(), global_=True)


@bp.post("/carteles/<int:tag_id>/estado")
@requiere_plataforma
def carteles_estado(tag_id):
    db().execute("UPDATE tags SET activo=1-activo WHERE id=?", (tag_id,))
    db().commit()
    flash("Estado del cartel actualizado.", "ok")
    return redirect(url_for("plataforma.carteles"))


@bp.route("/ajustes")
@requiere_plataforma
def ajustes():
    en_render = bool(os.environ.get("RENDER"))
    tam = os.path.getsize(DB_PATH) if os.path.exists(DB_PATH) else 0
    persistente = os.path.abspath(DB_PATH).startswith("/var/data")
    cfg = current_app.config
    revisiones = [
        ("Clave de sesiones", bool(os.environ.get("SECRET_KEY")), "Configurada." if os.environ.get("SECRET_KEY") else "Falta SECRET_KEY: las sesiones usan una clave de desarrollo."),
        ("Contraseña de la plataforma", bool(admin_password()), "Configurada en ADMIN_PASSWORD." if admin_password() else "Falta ADMIN_PASSWORD."),
        ("Claves de los carteles NFC", bool(cfg["SDM_META_KEY"] and cfg["SDM_FILE_KEY"]),
         "Configuradas: los carteles firmados funcionan." if cfg["SDM_META_KEY"] else "Faltan SDM_META_KEY y SDM_FILE_KEY: ningún cartel real puede sumar sellos."),
        ("Modo desarrollo apagado", os.environ.get("MODO_DEV") != "1", "Apagado." if os.environ.get("MODO_DEV") != "1" else "MODO_DEV=1 permite sumar sellos sin cartel. Quitalo en producción."),
        ("Conexión segura (HTTPS)", request.is_secure, "Activa." if request.is_secure else "Estás usando HTTP. En producción debe ser HTTPS."),
        ("Datos en disco persistente", persistente or not en_render, f"{DB_PATH} · {tam // 1024} KB" + ("" if persistente or not en_render else " — sin disco persistente los datos se pierden al reiniciar.")),
        ("Tiempo entre sellos", motor.COOLDOWN_HORAS >= 1, f"{motor.COOLDOWN_HORAS:g} h entre visitas del mismo cliente." + ("" if motor.COOLDOWN_HORAS >= 1 else " Es muy bajo: es para pruebas.")),
        ("Mensajes por WhatsApp", False, "En modo de prueba: los códigos de ingreso salen por el registro del servidor, no llegan al cliente."),
    ]
    return render_template("plataforma/ajustes.html", titulo="Ajustes", subtitulo="Estado de la plataforma y de su configuración.",
                           revisiones=revisiones, k=motor.kpis())
