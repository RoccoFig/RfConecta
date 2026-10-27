"""Panel de cada negocio: solo ve y modifica lo suyo. La plataforma puede entrar a cualquiera como soporte."""
import re
from flask import Blueprint, abort, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash
from . import motor
from .db import db, ahora
from .graficos import barras
from .presentacion import tema_de
from .seguridad import (bloqueado, destino_seguro, es_plataforma, iniciar, registrar_fallo, requiere_negocio,
                        verificar_negocio)

bp = Blueprint("negocio", __name__, url_prefix="/negocio/<slug>")

NAV = [(None, [("resumen", "Resumen", "layout-dashboard"), ("clientes", "Clientes", "users"), ("actividad", "Actividad", "activity")]),
       ("Programa de lealtad", [("premios", "Premios", "gift"), ("canjes", "Canjes", "ticket"), ("carteles", "Carteles NFC", "nfc")]),
       ("Cuenta", [("ajustes", "Ajustes", "settings")])]


def entero(v, lo, hi):
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    return n if lo <= n <= hi else None


@bp.context_processor
def contexto():
    slug = (request.view_args or {}).get("slug")
    com = g.get("com") or (motor.comercio(slug) if slug else None)
    if com is None:
        return {}
    ep = (request.endpoint or "").split(".")[-1]
    pend = motor.un("""SELECT COUNT(*) FROM canjes k JOIN clientes c ON c.id=k.cliente_id
                       WHERE c.comercio_id=? AND k.estado='pendiente'""", com["id"]) if (es_plataforma() or session.get("negocio") == slug) else 0
    grupos = [dict(titulo=t, items=[dict(
        url=url_for("negocio." + n, slug=slug), etiqueta=e, icono=i, badge=(pend or None) if n == "canjes" else None,
        activo=ep == n or (n == "clientes" and ep == "cliente")) for n, e, i in items]) for t, items in NAV]
    return dict(ambito="negocio", nav=grupos, marca_nombre=com["nombre"], marca_com=com, marca_tema=tema_de(com),
                inicio_url=url_for("negocio.resumen", slug=slug), salir_url=url_for("negocio.salir", slug=slug),
                soporte=es_plataforma(), ruta=lambda n, **k: url_for("negocio." + n, slug=slug, **k), com=com)


# ---------- acceso ----------
@bp.route("/ingresar", methods=["GET", "POST"])
def ingresar(slug):
    com = motor.comercio(slug)
    if es_plataforma() or (session.get("rol") == "negocio" and session.get("negocio") == slug):
        return redirect(url_for("negocio.resumen", slug=slug))
    error, ambito = None, f"negocio:{slug}"
    if request.method == "POST":
        if bloqueado(ambito):
            error = "Demasiados intentos. Esperá unos minutos y probá de nuevo."
        elif verificar_negocio(com, request.form.get("clave", "")):
            iniciar("negocio", slug)
            return redirect(destino_seguro(request.form.get("siguiente"), url_for("negocio.resumen", slug=slug)))
        else:
            registrar_fallo(ambito)
            error = "Clave incorrecta."
    return render_template("panel/ingresar.html", titulo="Ingresar", encabezado=com["nombre"],
                           texto="Entrá al panel de tu programa de lealtad.", error=error,
                           siguiente=request.args.get("siguiente", ""), marca=com), (429 if error and "Demasiados" in error else 200)


@bp.post("/salir")
def salir(slug):
    session.clear()
    return redirect(url_for("negocio.ingresar", slug=slug))


# ---------- resumen ----------
@bp.route("/")
@requiere_negocio
def resumen(slug):
    cid = g.com["id"]
    dias = entero(request.args.get("dias"), 1, 365)
    dias = dias if dias in (7, 30, 90) else 30
    et, val = motor.serie_visitas(dias, cid)
    return render_template("negocio/resumen.html", titulo="Resumen", subtitulo="Tu programa de lealtad de un vistazo.",
                           k=motor.kpis(cid), dias=dias, et=et, total_periodo=sum(val), grafico=barras(val, et),
                           populares=motor.premios_populares(cid), reciente=motor.actividad(cid, por_pagina=7)["filas"],
                           reglas=motor.reglas(cid), ntags=motor.un("SELECT COUNT(*) FROM tags WHERE comercio_id=? AND activo=1", cid))


# ---------- clientes ----------
@bp.route("/clientes")
@requiere_negocio
def clientes(slug):
    q, orden = request.args.get("q", "").strip()[:60], request.args.get("orden", "recientes")
    r = motor.clientes_lista(g.com["id"], q, orden, entero(request.args.get("pagina"), 1, 100000) or 1)
    return render_template("panel/clientes.html", titulo="Clientes", subtitulo=f"{r['total']} registrados en tu programa.",
                           r=r, q=q, orden=orden, slug="", lista_negocios=None)


@bp.route("/clientes/<int:cliente_id>")
@requiere_negocio
def cliente(slug, cliente_id):
    c = db().execute("SELECT * FROM clientes WHERE id=? AND comercio_id=?", (cliente_id, g.com["id"])).fetchone() or abort(404)
    movs = db().execute("SELECT * FROM puntos_mov WHERE cliente_id=? ORDER BY creado DESC LIMIT 40", (cliente_id,)).fetchall()
    canjes = db().execute("""SELECT k.*, p.nombre AS premio FROM canjes k JOIN premios p ON p.id=k.premio_id
                             WHERE k.cliente_id=? ORDER BY k.creado DESC""", (cliente_id,)).fetchall()
    pv, st, _ = motor.reglas(g.com["id"])
    return render_template("negocio/cliente.html", titulo=c["nombre"], subtitulo="Ficha del cliente", c=c, movs=movs, canjes=canjes,
                           saldo=motor.saldo(cliente_id), visitas=motor.visitas(cliente_id), sellos=motor.visitas(cliente_id) % st, tarjeta=st,
                           ultima=motor.un("SELECT MAX(creado) FROM puntos_mov WHERE cliente_id=? AND motivo='visita'", cliente_id))


@bp.route("/actividad")
@requiere_negocio
def actividad(slug):
    tipo, q = request.args.get("tipo", ""), request.args.get("q", "").strip()[:60]
    r = motor.actividad(g.com["id"], tipo if tipo in ("visita", "canje", "registro") else None, q,
                        entero(request.args.get("pagina"), 1, 100000) or 1)
    return render_template("panel/actividad.html", titulo="Actividad", subtitulo="Visitas, canjes y registros de tus clientes.",
                           r=r, q=q, tipo=tipo, slug="", lista_negocios=None)


# ---------- premios ----------
@bp.route("/premios")
@requiere_negocio
def premios(slug):
    filas = db().execute("""SELECT p.*, (SELECT COUNT(*) FROM canjes k WHERE k.premio_id=p.id) AS canjeados
                            FROM premios p WHERE p.comercio_id=? AND p.activo=1 ORDER BY p.costo""", (g.com["id"],)).fetchall()
    return render_template("negocio/premios.html", titulo="Premios", subtitulo="Lo que tus clientes pueden canjear con sus puntos.", premios=filas)


@bp.post("/premios")
@requiere_negocio
def premio_crear(slug):
    nombre, costo = request.form.get("nombre", "").strip()[:60], entero(request.form.get("costo"), 1, 100000)
    if not nombre or costo is None:
        flash("Revisá el nombre y los puntos del premio.", "error")
    else:
        db().execute("INSERT INTO premios(comercio_id,nombre,costo) VALUES(?,?,?)", (g.com["id"], nombre, costo))
        db().commit()
        flash("Premio agregado.", "ok")
    return redirect(url_for("negocio.premios", slug=slug))


def _premio(premio_id):
    return db().execute("SELECT id FROM premios WHERE id=? AND comercio_id=? AND activo=1", (premio_id, g.com["id"])).fetchone() or abort(404)


@bp.post("/premios/<int:premio_id>")
@requiere_negocio
def premio_editar(slug, premio_id):
    _premio(premio_id)
    nombre, costo = request.form.get("nombre", "").strip()[:60], entero(request.form.get("costo"), 1, 100000)
    if not nombre or costo is None:
        flash("Revisá el nombre y los puntos del premio.", "error")
    else:
        db().execute("UPDATE premios SET nombre=?, costo=? WHERE id=?", (nombre, costo, premio_id))
        db().commit()
        flash("Premio guardado.", "ok")
    return redirect(url_for("negocio.premios", slug=slug))


@bp.post("/premios/<int:premio_id>/quitar")
@requiere_negocio
def premio_quitar(slug, premio_id):
    _premio(premio_id)
    db().execute("UPDATE premios SET activo=2 WHERE id=?", (premio_id,))  # archivado: los canjes ya generados siguen valiendo
    db().commit()
    flash("Premio quitado. Los canjes ya generados siguen siendo válidos.", "ok")
    return redirect(url_for("negocio.premios", slug=slug))


# ---------- canjes ----------
@bp.route("/canjes")
@requiere_negocio
def canjes(slug):
    estado, q = request.args.get("estado", ""), request.args.get("q", "").strip()[:60]
    r = motor.canjes_lista(g.com["id"], estado, q, entero(request.args.get("pagina"), 1, 100000) or 1)
    return render_template("panel/canjes.html", titulo="Canjes", subtitulo="Validá y entregá los premios que piden tus clientes.",
                           r=r, q=q, estado=estado, slug="", lista_negocios=None, puede_validar=True)


@bp.post("/canjes/validar")
@requiere_negocio
def canje_validar(slug):
    codigo = request.form.get("codigo", "")
    ok = motor.entregar_canje(codigo, g.com["id"])
    flash("Canje entregado." if ok else "Código inválido o ya usado.", "ok" if ok else "error")
    return redirect(request.form.get("volver") if (request.form.get("volver") or "").startswith("/negocio/") else url_for("negocio.canjes", slug=slug))


# ---------- carteles ----------
@bp.route("/carteles", methods=["GET", "POST"])
@requiere_negocio
def carteles(slug):
    if request.method == "POST":
        uid = re.sub(r"[^0-9A-Fa-f]", "", request.form.get("uid", "")).upper()
        if len(uid) != 14:
            flash("El UID debe tener 14 caracteres (7 bytes).", "error")
        else:
            cur = db().execute("INSERT OR IGNORE INTO tags(comercio_id,uid) VALUES(?,?)", (g.com["id"], uid))
            db().commit()
            flash("Cartel registrado." if cur.rowcount else "Ese cartel ya estaba registrado.", "ok" if cur.rowcount else "error")
        return redirect(url_for("negocio.carteles", slug=slug))
    return render_template("panel/carteles.html", titulo="Carteles NFC", subtitulo="Los carteles donde tus clientes apoyan el celular.",
                           tags=motor.tags_lista(g.com["id"]), lista_negocios=None, global_=False)


@bp.post("/carteles/<int:tag_id>/estado")
@requiere_negocio
def carteles_estado(slug, tag_id):
    db().execute("UPDATE tags SET activo=1-activo WHERE id=? AND comercio_id=?", (tag_id, g.com["id"]))
    db().commit()
    flash("Estado del cartel actualizado.", "ok")
    return redirect(url_for("negocio.carteles", slug=slug))


# ---------- ajustes ----------
@bp.route("/ajustes")
@requiere_negocio
def ajustes(slug):
    tab = request.args.get("tab", "programa")
    if tab not in ("programa", "cuenta"):
        abort(404)
    pv, st, pt = motor.reglas(g.com["id"])
    return render_template("negocio/ajustes.html", titulo="Ajustes", subtitulo="Reglas de tu programa y datos de tu cuenta.",
                           tab=tab, r={"puntos": pv, "sellos": st, "premio": pt})


@bp.post("/ajustes/reglas")
@requiere_negocio
def ajustes_reglas(slug):
    pv, st = entero(request.form.get("puntos_visita"), 1, 1000), entero(request.form.get("sellos_tarjeta"), 2, 20)
    pt = request.form.get("premio_tarjeta", "").strip()[:60]
    if pv is None or st is None or not pt:
        flash("Revisá los valores de las reglas.", "error")
    else:
        db().execute("UPDATE comercios SET puntos_visita=?, sellos_tarjeta=?, premio_tarjeta=? WHERE id=?", (pv, st, pt, g.com["id"]))
        db().commit()
        flash("Reglas guardadas.", "ok")
    return redirect(url_for("negocio.ajustes", slug=slug))


@bp.post("/ajustes/clave")
@requiere_negocio
def ajustes_clave(slug):
    actual, nueva = request.form.get("actual", ""), request.form.get("nueva", "")
    if not es_plataforma() and not verificar_negocio(g.com, actual):
        flash("La clave actual no es correcta.", "error")
    elif len(nueva) < 8:
        flash("La clave nueva debe tener al menos 8 caracteres.", "error")
    else:
        db().execute("UPDATE comercios SET clave_hash=? WHERE id=?", (generate_password_hash(nueva), g.com["id"]))
        db().commit()
        flash("Clave actualizada.", "ok")
    return redirect(url_for("negocio.ajustes", slug=slug, tab="cuenta"))


# ---------- direcciones anteriores ----------
def registrar_redirecciones(app):
    @app.route("/l/<slug>/admin")
    def _viejo_panel(slug):
        return redirect(url_for("negocio.resumen", slug=slug), 301)

    @app.route("/admin")
    def _viejo_admin():
        return redirect(url_for("plataforma.resumen"), 301)
