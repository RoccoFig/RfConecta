"""Panel de cada negocio: solo ve y modifica lo suyo. La plataforma puede entrar a cualquiera como soporte.
Dos niveles: el DUEÑO ve todo; la CAJA solo confirma visitas y entrega premios."""
import re
from flask import Blueprint, Response, abort, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash
from . import motor, qr, temas
from .db import db, ahora
from .graficos import barras
from .presentacion import logo_de, tema_de
from .seguridad import (actor_actual, bloqueado, destino_seguro, es_plataforma, iniciar, registrar_fallo,
                        requiere_negocio, verificar_negocio)

bp = Blueprint("negocio", __name__, url_prefix="/negocio/<slug>")

NAV = [(None, [("resumen", "Resumen", "layout-dashboard"), ("caja", "Caja", "scan-line"), ("clientes", "Clientes", "users"), ("actividad", "Actividad", "activity")]),
       ("Programa de lealtad", [("premios", "Premios", "gift"), ("canjes", "Canjes", "ticket"), ("carteles", "Carteles y QR", "nfc")]),
       ("Cuenta", [("diseno", "Diseño", "sparkles"), ("ajustes", "Ajustes", "settings")])]
SOLO_CAJA = {"caja"}


def entero(v, lo, hi):
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    return n if lo <= n <= hi else None


def es_dueno():
    return es_plataforma() or session.get("nivel") == "dueno"


@bp.context_processor
def contexto():
    slug = (request.view_args or {}).get("slug")
    com = g.get("com") or (motor.comercio(slug) if slug else None)
    if com is None:
        return {}
    ep = (request.endpoint or "").split(".")[-1]
    dentro = es_plataforma() or session.get("negocio") == slug
    pend = motor.un("SELECT COUNT(*) FROM canjes WHERE comercio_id=? AND estado='pendiente' AND vence_en>?", com["id"], ahora()) if dentro else 0
    sol = len(motor.solicitudes_pendientes(com["id"])) if dentro else 0
    grupos = [dict(titulo=t, items=[dict(
        url=url_for("negocio." + n, slug=slug), etiqueta=e, icono=i, badge=((pend or None) if n == "canjes" else (sol or None) if n == "caja" else None),
        activo=ep == n or (n == "clientes" and ep == "cliente") or (n == "carteles" and ep.startswith("qr"))) for n, e, i in items
        if es_dueno() or n in SOLO_CAJA]) for t, items in NAV]
    return dict(ambito="negocio", nav=[x for x in grupos if x["items"]], marca_nombre=com["nombre"], marca_com=com, marca_tema=tema_de(com),
                inicio_url=url_for("negocio.resumen" if es_dueno() else "negocio.caja", slug=slug), salir_url=url_for("negocio.salir", slug=slug),
                soporte=es_plataforma(), ruta=lambda n, **k: url_for("negocio." + n, slug=slug, **k), com=com, es_dueno=es_dueno())


# ---------- acceso ----------
@bp.route("/ingresar", methods=["GET", "POST"])
def ingresar(slug):
    com = motor.comercio(slug)
    if es_plataforma() or (session.get("rol") == "negocio" and session.get("negocio") == slug):
        return redirect(url_for("negocio.resumen" if es_dueno() else "negocio.caja", slug=slug))
    error, ambito = None, f"negocio:{slug}"
    if request.method == "POST":
        if bloqueado(ambito):
            motor.auditar("ingreso_bloqueado", "aviso", "anonimo", com["id"], None, "demasiados intentos")
            error = "Demasiados intentos. Esperá unos minutos y probá de nuevo."
        elif (nivel := verificar_negocio(com, request.form.get("clave", ""))):
            iniciar("negocio", slug, nivel)
            motor.auditar("ingreso", "info", f"{nivel}:{slug}", com["id"])
            por_defecto = url_for("negocio.resumen" if nivel == "dueno" else "negocio.caja", slug=slug)
            return redirect(destino_seguro(request.form.get("siguiente"), por_defecto))
        else:
            registrar_fallo(ambito)
            motor.auditar("ingreso_fallido", "aviso", "anonimo", com["id"], None, "clave incorrecta")
            error = "Clave incorrecta."
    return render_template("panel/ingresar.html", titulo="Ingresar", encabezado=com["nombre"],
                           texto="Entrá al panel de tu programa de lealtad.", error=error,
                           siguiente=request.args.get("siguiente", ""), marca=com), (429 if error and "Demasiados" in error else 200)


@bp.post("/salir")
def salir(slug):
    session.clear()
    return redirect(url_for("negocio.ingresar", slug=slug))


# ---------- caja: confirmar visitas y entregar premios (dueño y personal) ----------
@bp.route("/caja")
@requiere_negocio(caja=True)
def caja(slug):
    codigo = request.args.get("codigo", "").strip().upper()[:12]
    sol, no_encontrada = None, False
    if codigo:
        if bloqueado(f"caja:{slug}", 10, 600):
            flash("Demasiados códigos incorrectos. Esperá unos minutos.", "error")
        else:
            sol = motor.solicitud_para_caja(g.com["id"], codigo)
            if not sol:
                registrar_fallo(f"caja:{slug}")
                motor.auditar("codigo_visita_invalido", "aviso", actor_actual(), g.com["id"], None, "código desconocido, vencido o de otro negocio")
                no_encontrada = True
    return render_template("negocio/caja.html", titulo="Caja", subtitulo="Confirmá visitas y entregá premios.", sol=sol, codigo=codigo,
                           no_encontrada=no_encontrada, pendientes=motor.solicitudes_pendientes(g.com["id"]), cfg=motor.config(g.com["id"]))


@bp.post("/caja/visita/<int:sol_id>")
@requiere_negocio(caja=True)
def caja_resolver(slug, sol_id):
    confirmar = request.form.get("accion") == "confirmar"
    r = motor.resolver_solicitud(sol_id, g.com["id"], actor_actual(), confirmar)
    from .cliente import MENSAJES
    texto = {"ok": ("ok", "Visita confirmada: se sumaron los puntos y el sello."), "rechazada": ("error", "No se pudo registrar la visita."),
             "vencida": ("error", "El código venció. Pedile al cliente que genere uno nuevo."),
             "ya_resuelta": ("error", "Ese pedido ya estaba resuelto."), "no_existe": ("error", "No encontramos ese pedido."),
             "duplicada": ("error", "Esa visita ya estaba registrada.")}[r["estado"]]
    if r["estado"] == "rechazada" and r.get("motivo") in MENSAJES and confirmar:
        texto = ("error", "No se puede sumar: el cliente ya registró una visita hace poco." if r["motivo"] == "intervalo" else "El cliente llegó al máximo de visitas de hoy.")
    elif r["estado"] == "rechazada" and not confirmar:
        texto = ("ok", "Pedido rechazado.")
    flash(texto[1], texto[0])
    return redirect(url_for("negocio.caja", slug=slug))


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
    movs = db().execute("SELECT * FROM puntos_mov WHERE cliente_id=? ORDER BY id DESC LIMIT 40", (cliente_id,)).fetchall()
    canjes = db().execute("""SELECT k.*, p.nombre AS premio FROM canjes k JOIN premios p ON p.id=k.premio_id
                             WHERE k.cliente_id=? ORDER BY k.creado DESC""", (cliente_id,)).fetchall()
    cfg = motor.config(g.com["id"])
    n = motor.visitas(cliente_id)
    return render_template("negocio/cliente.html", titulo=c["nombre"], subtitulo="Ficha del cliente", c=c, movs=movs, canjes=canjes,
                           saldo=motor.saldo(cliente_id), visitas=n, sellos=n % cfg["sellos"], tarjeta=cfg["sellos"],
                           ultima=motor.un("SELECT MAX(creado) FROM puntos_mov WHERE cliente_id=? AND motivo='visita'", cliente_id))


@bp.post("/clientes/<int:cliente_id>/ajustar")
@requiere_negocio
def cliente_ajustar(slug, cliente_id):
    delta = entero(request.form.get("puntos"), -motor.MAX_AJUSTE, motor.MAX_AJUSTE) or 0
    r = motor.ajustar_puntos(cliente_id, g.com["id"], delta, request.form.get("motivo", ""), actor_actual())
    flash({"ok": "Ajuste registrado en el historial.", "motivo": "Escribí el motivo del ajuste (entre 3 y 120 letras).",
           "monto": f"El ajuste debe ser distinto de cero y de hasta {motor.MAX_AJUSTE} puntos.", "saldo": "No se puede dejar el saldo en negativo.",
           "no_existe": "No encontramos al cliente."}[r["estado"]], "ok" if r["estado"] == "ok" else "error")
    return redirect(url_for("negocio.cliente", slug=slug, cliente_id=cliente_id))


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
        motor.auditar("premio_creado", "info", actor_actual(), g.com["id"], nombre, f"{costo} puntos")
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
        motor.auditar("premio_editado", "info", actor_actual(), g.com["id"], f"premio:{premio_id}", f"{nombre}: {costo} puntos")
        flash("Premio guardado. Los canjes ya generados no cambian.", "ok")
    return redirect(url_for("negocio.premios", slug=slug))


@bp.post("/premios/<int:premio_id>/quitar")
@requiere_negocio
def premio_quitar(slug, premio_id):
    _premio(premio_id)
    db().execute("UPDATE premios SET activo=2 WHERE id=?", (premio_id,))  # archivado: los canjes ya generados siguen valiendo
    motor.auditar("premio_quitado", "info", actor_actual(), g.com["id"], f"premio:{premio_id}")
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
@requiere_negocio(caja=True)
def canje_validar(slug):
    if bloqueado(f"canje:{slug}", 10, 600):
        flash("Demasiados códigos incorrectos. Esperá unos minutos.", "error")
    else:
        r = motor.entregar_canje(request.form.get("codigo", ""), g.com["id"], actor_actual())
        if r["estado"] == "no_existe":
            registrar_fallo(f"canje:{slug}")
        flash({"ok": "Canje entregado.", "no_existe": "Ese código no existe en este negocio.", "ya_usado": "Ese canje ya fue entregado.",
               "cancelado": "Ese canje fue cancelado.", "vencido": "Ese canje está vencido."}[r["estado"]], "ok" if r["estado"] == "ok" else "error")
    volver = request.form.get("volver") or ""
    return redirect(destino_seguro(volver, url_for("negocio.canjes", slug=slug)) if volver.startswith(f"/negocio/{slug}/") else url_for("negocio.canjes", slug=slug))


@bp.post("/canjes/<int:canje_id>/cancelar")
@requiere_negocio
def canje_cancelar(slug, canje_id):
    r = motor.cancelar_canje(canje_id, g.com["id"], actor_actual(), request.form.get("motivo", ""))
    flash(f"Canje cancelado. Se devolvieron {r['devuelto']} puntos al cliente." if r["estado"] == "ok" else "Ese canje ya no se puede cancelar.",
          "ok" if r["estado"] == "ok" else "error")
    return redirect(url_for("negocio.canjes", slug=slug))


# ---------- carteles NFC y QR ----------
@bp.route("/carteles", methods=["GET", "POST"])
@requiere_negocio
def carteles(slug):
    if request.method == "POST":
        uid = re.sub(r"[^0-9A-Fa-f]", "", request.form.get("uid", "")).upper()
        if len(uid) != 14:
            flash("El UID debe tener 14 caracteres (7 bytes).", "error")
        else:
            cur = db().execute("INSERT OR IGNORE INTO tags(comercio_id,uid) VALUES(?,?)", (g.com["id"], uid))
            if cur.rowcount:
                motor.auditar("cartel_registrado", "info", actor_actual(), g.com["id"], f"uid:{uid}")
            db().commit()
            flash("Cartel registrado." if cur.rowcount else "Ese cartel ya estaba registrado (en este u otro negocio).", "ok" if cur.rowcount else "error")
        return redirect(url_for("negocio.carteles", slug=slug))
    return render_template("panel/carteles.html", titulo="Carteles NFC y QR", subtitulo="Los carteles donde tus clientes apoyan el celular.",
                           tags=motor.tags_lista(g.com["id"]), lista_negocios=None, global_=False)


@bp.post("/carteles/<int:tag_id>/estado")
@requiere_negocio
def carteles_estado(slug, tag_id):
    cur = db().execute("UPDATE tags SET activo=1-activo WHERE id=? AND comercio_id=?", (tag_id, g.com["id"]))
    if cur.rowcount:
        motor.auditar("cartel_activado_o_desactivado", "aviso", actor_actual(), g.com["id"], f"tag:{tag_id}")
    db().commit()
    flash("Estado del cartel actualizado.", "ok")
    return redirect(url_for("negocio.carteles", slug=slug))


@bp.route("/qr")
@requiere_negocio
def qr_pagina(slug):
    return render_template("negocio/qr.html", titulo="Código QR del negocio", subtitulo="Para quienes no pueden usar NFC.",
                           url=qr.url_negocio(slug), qr_svg=qr.svg_en_linea(qr.url_negocio(slug), 6), hay_base=bool(request.url_root))


@bp.route("/qr.png")
@requiere_negocio
def qr_png(slug):
    return Response(qr.png(qr.url_negocio(slug)), mimetype="image/png", headers={"Content-Disposition": f"attachment; filename=qr-{slug}.png"})


@bp.route("/qr.svg")
@requiere_negocio
def qr_svg(slug):
    return Response(qr.svg(qr.url_negocio(slug)), mimetype="image/svg+xml", headers={"Content-Disposition": f"attachment; filename=qr-{slug}.svg"})


@bp.route("/qr/imprimible")
@requiere_negocio
def qr_imprimible(slug):
    t = tema_de(g.com)
    return render_template("negocio/qr_imprimible.html", url=qr.url_negocio(slug), qr_svg=qr.svg_en_linea(qr.url_negocio(slug), 8), t=t, logo=logo_de(g.com, t))


# ---------- diseño: la identidad visual de ESTE negocio (el de la sesión, nunca uno elegido desde el formulario) ----------
@bp.route("/diseno")
@requiere_negocio
def diseno(slug):
    return render_template("negocio/diseno.html", titulo="Diseño", subtitulo="Tu identidad en todas las pantallas de tus clientes.",
                           t=tema_de(g.com), opciones=temas.OPCIONES)


@bp.post("/diseno")
@requiere_negocio
def diseno_guardar(slug):
    error = temas.guardar_tema(g.com)
    if not error:
        motor.auditar("tema_modificado", "info", actor_actual(), g.com["id"])
    flash(error or "Diseño guardado: ya se ve en todas las pantallas de tus clientes.", "error" if error else "ok")
    return redirect(url_for("negocio.diseno", slug=slug))


# ---------- ajustes ----------
@bp.route("/ajustes")
@requiere_negocio
def ajustes(slug):
    tab = request.args.get("tab", "programa")
    if tab not in ("programa", "cuenta"):
        abort(404)
    cfg = motor.config(g.com["id"])
    return render_template("negocio/ajustes.html", titulo="Ajustes", subtitulo="Reglas de tu programa y datos de tu cuenta.", tab=tab,
                           r={"puntos": cfg["puntos"], "sellos": cfg["sellos"], "premio": cfg["premio"]}, cfg=cfg,
                           tiene_caja=bool(g.com["clave_caja_hash"]))


@bp.post("/ajustes/reglas")
@requiere_negocio
def ajustes_reglas(slug):
    f = request.form
    pv, st = entero(f.get("puntos_visita"), 1, 1000), entero(f.get("sellos_tarjeta"), 2, 20)
    iv, md, vd = entero(f.get("intervalo_horas"), 1, 720), entero(f.get("max_visitas_dia"), 0, 20), entero(f.get("canje_vence_dias"), 1, 365)
    pt = f.get("premio_tarjeta", "").strip()[:60]
    if None in (pv, st, iv, md, vd) or not pt:
        flash("Revisá los valores de las reglas.", "error")
    else:
        db().execute("""UPDATE comercios SET puntos_visita=?, sellos_tarjeta=?, premio_tarjeta=?, intervalo_horas=?, max_visitas_dia=?,
                        canje_vence_dias=? WHERE id=?""", (pv, st, pt, iv, md, vd, g.com["id"]))
        motor.auditar("reglas_modificadas", "aviso", actor_actual(), g.com["id"], None,
                      f"{pv} puntos, {st} sellos, cada {iv} h, máx {md}/día, vencen {vd} d")
        flash("Reglas guardadas.", "ok")
    return redirect(url_for("negocio.ajustes", slug=slug))


@bp.post("/ajustes/clave")
@requiere_negocio
def ajustes_clave(slug):
    tipo = "caja" if request.form.get("tipo") == "caja" else "dueno"
    actual, nueva = request.form.get("actual", ""), request.form.get("nueva", "")
    campo, otro = ("clave_caja_hash", "clave_hash") if tipo == "caja" else ("clave_hash", "clave_caja_hash")
    if not es_plataforma() and verificar_negocio(g.com, actual) != "dueno":
        flash("Tu clave actual de dueño no es correcta.", "error")
    elif len(nueva) < 8:
        flash("La clave nueva debe tener al menos 8 caracteres.", "error")
    elif g.com[otro] and check_password_hash(g.com[otro], nueva):
        flash("La clave de caja y la del dueño tienen que ser distintas.", "error")
    else:
        db().execute(f"UPDATE comercios SET {campo}=? WHERE id=?", (generate_password_hash(nueva), g.com["id"]))
        motor.auditar("clave_cambiada", "aviso", actor_actual(), g.com["id"], tipo)
        flash("Clave de caja actualizada." if tipo == "caja" else "Clave actualizada.", "ok")
    return redirect(url_for("negocio.ajustes", slug=slug, tab="cuenta"))


# ---------- direcciones anteriores ----------
def registrar_redirecciones(app):
    @app.route("/l/<slug>/admin")
    def _viejo_panel(slug):
        return redirect(url_for("negocio.resumen", slug=slug), 301)

    @app.route("/admin")
    def _viejo_admin():
        return redirect(url_for("plataforma.resumen"), 301)
