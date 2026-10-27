"""Lo que ve y hace el cliente final: registro, tarjeta de sellos, canje de premios y escaneo del cartel NFC."""
import hashlib, hmac, os, re, secrets
from flask import (Blueprint, Response, abort, current_app, make_response, redirect, render_template, request,
                   session, url_for)
from . import sdm
from .db import db, ahora
from .mensajes import enviar_whatsapp
from .motor import canjear as motor_canjear, comercio, reglas, saldo, sumar_visita, un, visitas
from .presentacion import info_diseno, logo_de, tema_de

bp = Blueprint("cliente", __name__)
OTP_MINUTOS = 10


def render_cliente(vista, com, **ctx):
    """Usa la plantilla propia del negocio (templates/disenos/<slug>/<vista>.html) si existe; si no, la base."""
    t = tema_de(com)
    return render_template([f"disenos/{com['slug']}/{vista}.html", f"cliente/{vista}.html"], com=com, t=t,
                           logo=logo_de(com, t), diseno=info_diseno(com["slug"]), **ctx)


def activo_o_pagina(com):
    return None if com["estado"] == "activo" else render_cliente("suspendido", com)


def cliente_actual(com):
    t = request.cookies.get("ct_" + com["slug"])
    if not t:
        return None
    return db().execute("SELECT * FROM clientes WHERE token=? AND comercio_id=?", (t, com["id"])).fetchone()


def poner_cookie(resp, slug, token):
    resp.set_cookie("ct_" + slug, token, max_age=365 * 86400, httponly=True, samesite="Lax", secure=request.is_secure)
    return resp


@bp.route("/")
def raiz():
    host = request.host.split(":")[0].lower()
    com = db().execute("SELECT slug FROM comercios WHERE dominio=?", (host,)).fetchone()
    if com:
        return redirect(url_for("cliente.local", slug=com["slug"]))
    if session.get("rol") == "plataforma":
        return redirect(url_for("plataforma.resumen"))
    return redirect(url_for("cliente.local", slug="demo"))


@bp.route("/l/<slug>")
def local(slug):
    com = comercio(slug)
    if (p := activo_o_pagina(com)):
        return p
    c = cliente_actual(com)
    if not c:
        return render_cliente("registro", com, error=request.args.get("e"))
    res = session.pop("resultado", None) or {}
    sumo = bool(res.get("sumo"))
    premios = db().execute("SELECT * FROM premios WHERE comercio_id=? AND activo=1 ORDER BY costo", (com["id"],)).fetchall()
    pend = db().execute("""SELECT k.codigo, p.nombre FROM canjes k JOIN premios p ON p.id=k.premio_id
                           WHERE k.cliente_id=? AND k.estado='pendiente'""", (c["id"],)).fetchall()
    pv, st, pt = reglas(com["id"])
    n = visitas(c["id"])
    sellos = ((n - 1) % st) + 1 if n else 0
    total = saldo(c["id"])
    siguiente = min((p["costo"] for p in premios if p["costo"] > total), default=None)
    return render_cliente("home", com, c=c, sumo=sumo, pts=pv, total=total, premios=premios, pendientes=pend,
                          sellos=sellos, tarjeta=st, premio_tarjeta=pt, completa=bool(res.get("completa")), ya=bool(res) and not sumo,
                          falta=(siguiente - total) if siguiente else None)


@bp.post("/l/<slug>/registro")
def registro(slug):
    com = comercio(slug)
    if activo_o_pagina(com):
        abort(404)
    nombre = request.form.get("nombre", "").strip()[:80]
    tel = re.sub(r"\D", "", request.form.get("telefono", ""))
    email = request.form.get("email", "").strip()[:120]
    acepta = 1 if request.form.get("acepta") else 0
    def volver(msg):
        return redirect(url_for("cliente.local", slug=slug, e=msg))
    if not nombre or not (8 <= len(tel) <= 15):
        return volver("Revisá tu nombre y tu teléfono.")
    if db().execute("SELECT 1 FROM clientes WHERE comercio_id=? AND telefono=?", (com["id"], tel)).fetchone():
        return volver("Ese teléfono ya está registrado. Usá «Ya tengo cuenta» para entrar con un código.")
    token = secrets.token_urlsafe(24)
    db().execute("""INSERT INTO clientes(comercio_id,token,nombre,telefono,email,acepta_whatsapp,creado)
                    VALUES(?,?,?,?,?,?,?)""", (com["id"], token, nombre, tel, email, acepta, ahora()))
    db().commit()
    if session.pop("scan_ok", None) == slug:
        registrar_visita(db().execute("SELECT * FROM clientes WHERE token=?", (token,)).fetchone())
    return poner_cookie(make_response(redirect(url_for("cliente.local", slug=slug))), slug, token)


@bp.post("/l/<slug>/canjear/<int:premio_id>")
def canjear(slug, premio_id):
    com = comercio(slug)
    c = cliente_actual(com) or abort(403)
    p = db().execute("SELECT * FROM premios WHERE id=? AND comercio_id=? AND activo=1", (premio_id, com["id"])).fetchone() or abort(404)
    motor_canjear(c["id"], p)
    return redirect(url_for("cliente.local", slug=slug))


@bp.post("/l/<slug>/baja")
def baja(slug):
    com = comercio(slug)
    c = cliente_actual(com) or abort(403)
    db().execute("UPDATE clientes SET acepta_whatsapp=0 WHERE id=?", (c["id"],))
    db().commit()
    return redirect(url_for("cliente.local", slug=slug))


# ---------- ingreso con código por WhatsApp ----------
def hash_otp(codigo, tel):
    return hmac.new(current_app.secret_key.encode(), f"{tel}:{codigo}".encode(), hashlib.sha256).hexdigest()


@bp.route("/l/<slug>/entrar", methods=["GET", "POST"])
def entrar(slug):
    com = comercio(slug)
    if request.method == "GET":
        return render_cliente("entrar", com)
    tel = re.sub(r"\D", "", request.form.get("telefono", ""))
    existe = db().execute("SELECT 1 FROM clientes WHERE comercio_id=? AND telefono=?", (com["id"], tel)).fetchone()
    recientes = un("SELECT COUNT(*) FROM otp WHERE comercio_id=? AND telefono=? AND creado>?", com["id"], tel, ahora() - 3600)
    if existe and recientes < 3:  # máximo 3 códigos por hora
        codigo = f"{secrets.randbelow(10**6):06d}"
        db().execute("INSERT INTO otp(comercio_id,telefono,codigo_hash,expira,creado) VALUES(?,?,?,?,?)",
                     (com["id"], tel, hash_otp(codigo, tel), ahora() + OTP_MINUTOS * 60, ahora()))
        db().commit()
        enviar_whatsapp(tel, f"Tu código de {com['nombre']} es {codigo}. Vence en {OTP_MINUTOS} minutos.")
    return redirect(url_for("cliente.verificar", slug=slug, tel=tel))


@bp.route("/l/<slug>/verificar", methods=["GET", "POST"])
def verificar(slug):
    com = comercio(slug)
    tel = re.sub(r"\D", "", request.values.get("tel", ""))
    if request.method == "GET":
        return render_cliente("verificar", com, tel=tel, error=request.args.get("e"))
    codigo = request.form.get("codigo", "").strip()
    o = db().execute("""SELECT * FROM otp WHERE comercio_id=? AND telefono=? AND usado=0 AND expira>?
                        ORDER BY id DESC LIMIT 1""", (com["id"], tel, ahora())).fetchone()
    if o and o["intentos"] < 5:  # máximo 5 intentos por código
        if hmac.compare_digest(o["codigo_hash"], hash_otp(codigo, tel)):
            db().execute("UPDATE otp SET usado=1 WHERE id=?", (o["id"],))
            db().commit()
            c = db().execute("SELECT token FROM clientes WHERE comercio_id=? AND telefono=?", (com["id"], tel)).fetchone()
            return poner_cookie(make_response(redirect(url_for("cliente.local", slug=slug))), slug, c["token"])
        db().execute("UPDATE otp SET intentos=intentos+1 WHERE id=?", (o["id"],))
        db().commit()
    return redirect(url_for("cliente.verificar", slug=slug, tel=tel, e="Código incorrecto o vencido."))


# ---------- escaneo del cartel ----------
def registrar_visita(c):
    sumo = sumar_visita(c["id"])
    session["resultado"] = {"sumo": sumo, "completa": sumo and visitas(c["id"]) % reglas(c["comercio_id"])[1] == 0}


def tras_escaneo_valido(com):
    c = cliente_actual(com)
    if c:
        registrar_visita(c)
    else:
        session["scan_ok"] = com["slug"]  # su primera visita se suma al registrarse
    return redirect(url_for("cliente.local", slug=com["slug"]))


@bp.route("/t")
def escaneo():
    meta, archivo = current_app.config["SDM_META_KEY"], current_app.config["SDM_FILE_KEY"]
    if not (meta and archivo):
        abort(503)
    try:
        uid, ctr = sdm.verificar(request.args.get("picc_data", ""), request.args.get("cmac", ""),
                                 bytes.fromhex(meta), bytes.fromhex(archivo))
    except ValueError as e:
        current_app.logger.warning("Escaneo rechazado: %s", e)
        abort(403)
    tag = db().execute("SELECT * FROM tags WHERE uid=? AND activo=1", (uid,)).fetchone()
    if not tag:
        current_app.logger.warning("Escaneo de un cartel sin registrar. UID: %s", uid)
        abort(403)
    com = comercio(db().execute("SELECT slug FROM comercios WHERE id=?", (tag["comercio_id"],)).fetchone()["slug"])
    if com["estado"] != "activo":
        return redirect(url_for("cliente.local", slug=com["slug"]))
    cur = db().execute("UPDATE tags SET ultimo_contador=?, ultimo_escaneo=? WHERE id=? AND ultimo_contador<?",
                       (ctr, ahora(), tag["id"], ctr))
    db().commit()
    if not cur.rowcount:  # el contador no avanzó: es un escaneo repetido o copiado, no suma
        session["resultado"] = {"sumo": False, "completa": False}
        return redirect(url_for("cliente.local", slug=com["slug"]))
    return tras_escaneo_valido(com)


@bp.route("/l/<slug>/logo")
def logo(slug):
    r = db().execute("SELECT logo_img, logo_mime FROM comercios WHERE slug=?", (slug,)).fetchone()
    if not r or not r["logo_img"]:
        abort(404)
    resp = Response(r["logo_img"], mimetype=r["logo_mime"])
    resp.headers["Cache-Control"] = "public, max-age=86400"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


def registrar_modo_dev(app):
    """Simula apoyar el celular en el cartel. Solo existe si arrancás con MODO_DEV=1 (nunca en producción)."""
    @app.route("/dev/escanear/<slug>")
    def dev_escanear(slug):
        return tras_escaneo_valido(comercio(slug))
