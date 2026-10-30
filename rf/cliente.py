"""Lo que ve y hace el cliente final: registro, tarjeta de sellos, canje de premios, escaneo del cartel NFC
y el pedido de visita por QR que confirma el personal. Toda suma de puntos pasa por rf.motor."""
import hashlib, hmac, re, secrets, sqlite3
from flask import (Blueprint, Response, abort, current_app, g, make_response, redirect, render_template, request,
                   session, url_for)
from . import motor, qr, sdm, temas
from .db import ahora, db, transaccion
from .mensajes import enviar_whatsapp
from .presentacion import fondo_de, info_diseno, logo_de, tema_de
from .seguridad import bloqueado, registrar_fallo, requiere_negocio

bp = Blueprint("cliente", __name__)
OTP_MINUTOS = 10
MAX_REGISTROS_HORA = 60   # por conexión; un local con wifi propio comparte una sola IP, por eso el tope es holgado
MAX_CODIGOS_HORA = 30
MENSAJES = {"intervalo": "Ya registraste una visita hace poco. Podés sumar la próxima a partir de las {hora}.",
            "limite_diario": "Llegaste al máximo de visitas de hoy. Podés volver a sumar mañana.",
            "limite": "Pediste muchos códigos seguidos. Esperá un rato y probá de nuevo."}


def render_cliente(vista, com, t=None, **ctx):
    """Toda pantalla del cliente pasa por acá: recibe el tema del negocio (variables CSS + clases) y usa la plantilla
    propia del negocio (templates/disenos/<slug>/<vista>.html) si existe; si no, la base, que ya toma el tema."""
    t = t or tema_de(com)
    fondo = fondo_de(com)
    return render_template([f"disenos/{com['slug']}/{vista}.html", f"cliente/{vista}.html"], com=com, t=t,
                           tema_css=temas.css_tema(t, fondo), con_fondo=bool(fondo), vista=vista,
                           logo=logo_de(com, t), diseno=info_diseno(com["slug"]), **ctx)


def datos_programa(com):
    """Lo público del programa: reglas y premios activos. Es lo mismo que ve el cliente al registrarse."""
    premios = db().execute("SELECT id, nombre, costo FROM premios WHERE comercio_id=? AND activo=1 ORDER BY costo", (com["id"],)).fetchall()
    cfg = motor.config(com["id"])
    return dict(premios=premios, pts=cfg["puntos"], tarjeta=cfg["sellos"], premio_tarjeta=cfg["premio"])


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


def aviso_de(res):
    """Texto para mostrarle al cliente tras intentar sumar una visita."""
    if res.get("estado") == "ok":
        return "ok", f"+{res['puntos']} puntos y 1 sello por tu visita." + (" ¡Completaste tu tarjeta! Tu premio está más abajo." if res.get("completa") else "")
    if res.get("estado") in ("rechazada", "limite"):
        return "info", MENSAJES.get(res.get("motivo") or "limite", "Esta visita no se pudo registrar.").format(hora=_hora(res.get("proxima")))
    if res.get("estado") == "duplicada":
        return "info", "Esa visita ya estaba registrada."
    return {"canje_ok": ("ok", "¡Canje listo! Mostrá el código en caja para recibir tu premio."),
            "canje_saldo": ("info", "Todavía no te alcanzan los puntos para ese premio."),
            "duplicado": ("info", "Ese canje ya estaba hecho."),
            "no_existe": ("info", "Ese premio ya no está disponible.")}.get(res.get("estado"))


def _hora(ts):
    from .ayudas import hora
    return hora(ts)


@bp.route("/")
def raiz():
    host = request.host.split(":")[0].lower()
    com = db().execute("SELECT slug FROM comercios WHERE dominio=?", (host,)).fetchone()
    if com:
        return redirect(url_for("cliente.local", slug=com["slug"]))
    if session.get("rol") == "plataforma":
        return redirect(url_for("plataforma.resumen"))
    return redirect(url_for("cliente.local", slug="demo"))


@bp.route("/salud")
def salud():
    """Para el control de disponibilidad de Render. No expone nada interno."""
    try:
        db().execute("SELECT 1").fetchone()
    except Exception:
        return {"ok": False}, 503
    return {"ok": True}


@bp.route("/l/<slug>")
def local(slug):
    """Entrada permanente de cada negocio (la del QR impreso). Abrirla NO suma puntos ni visitas."""
    com = motor.comercio(slug)
    if (p := activo_o_pagina(com)):
        return p
    c = cliente_actual(com)
    if not c:
        if session.get("scan_ok") == slug:  # apoyó el celular en el cartel: directo al registro, su visita lo espera
            return render_cliente("registro", com, error=request.args.get("e"), escaneo=True, **datos_programa(com))
        return render_cliente("inicio", com, **datos_programa(com))
    res = session.pop("resultado", None) or {}
    aviso = aviso_de(res) if res else None
    premios = db().execute("SELECT * FROM premios WHERE comercio_id=? AND activo=1 ORDER BY costo", (com["id"],)).fetchall()
    pend = db().execute("""SELECT k.codigo, k.vence_en, p.nombre FROM canjes k JOIN premios p ON p.id=k.premio_id
                           WHERE k.cliente_id=? AND k.estado='pendiente' AND k.vence_en>?""", (c["id"], ahora())).fetchall()
    cfg = motor.config(com["id"])
    n = motor.visitas(c["id"])
    sellos = ((n - 1) % cfg["sellos"]) + 1 if n else 0
    total = motor.saldo(c["id"])
    siguiente = min((p["costo"] for p in premios if p["costo"] > total), default=None)
    return render_cliente("home", com, c=c, aviso=aviso, sumo=res.get("estado") == "ok", ya=bool(res) and res.get("estado") != "ok",
                          pts=cfg["puntos"], total=total, premios=premios, pendientes=pend, sellos=sellos, tarjeta=cfg["sellos"],
                          premio_tarjeta=cfg["premio"], completa=bool(res.get("completa")),
                          falta=(siguiente - total) if siguiente else None)


@bp.route("/registro/<slug>")
def registro_pagina(slug):
    """Página de registro propia de cada negocio. El negocio sale de la dirección y se valida acá, en el servidor."""
    com = motor.comercio(slug)
    if (p := activo_o_pagina(com)):
        return p
    if cliente_actual(com):
        return redirect(url_for("cliente.local", slug=slug))
    return render_cliente("registro", com, error=request.args.get("e"), escaneo=session.get("scan_ok") == slug, **datos_programa(com))


@bp.route("/l/<slug>/registro", methods=["GET", "POST"])
def registro(slug):
    com = motor.comercio(slug)
    if request.method == "GET":
        return redirect(url_for("cliente.registro_pagina", slug=slug))
    if activo_o_pagina(com):
        abort(404)
    def volver(msg):
        return redirect(url_for("cliente.registro_pagina", slug=slug, e=msg))
    if bloqueado("registro", MAX_REGISTROS_HORA, 3600):
        return volver("Demasiados intentos desde esta conexión. Probá de nuevo en un rato.")
    registrar_fallo("registro")  # cuenta cada intento: frena la creación masiva de cuentas
    nombre = request.form.get("nombre", "").strip()[:80]
    tel = re.sub(r"\D", "", request.form.get("telefono", ""))
    email = request.form.get("email", "").strip()[:120]
    acepta = 1 if request.form.get("acepta") else 0
    if not nombre or not (8 <= len(tel) <= 15):
        return volver("Revisá tu nombre y tu teléfono.")
    if db().execute("SELECT 1 FROM clientes WHERE comercio_id=? AND telefono=?", (com["id"], tel)).fetchone():
        return volver("Ese teléfono ya está registrado. Usá «Ya tengo cuenta» para entrar con un código.")
    token = secrets.token_urlsafe(24)
    try:
        with transaccion():
            db().execute("""INSERT INTO clientes(comercio_id,token,nombre,telefono,email,acepta_whatsapp,creado)
                            VALUES(?,?,?,?,?,?,?)""", (com["id"], token, nombre, tel, email, acepta, ahora()))
    except sqlite3.IntegrityError:  # otro pedido registró ese teléfono justo antes
        return volver("Ese teléfono ya está registrado. Usá «Ya tengo cuenta» para entrar con un código.")
    if session.pop("scan_ok", None) == slug:  # llegó con un escaneo válido: su primera visita se suma ahora
        cid = db().execute("SELECT id FROM clientes WHERE token=?", (token,)).fetchone()["id"]
        session["resultado"] = motor.registrar_visita(cid, session.pop("scan_fuente", "nfc"), f"cliente:{cid}", session.pop("scan_idem", None))
    return poner_cookie(make_response(redirect(url_for("cliente.local", slug=slug))), slug, token)


@bp.post("/l/<slug>/canjear/<int:premio_id>")
def canjear(slug, premio_id):
    com = motor.comercio(slug)
    c = cliente_actual(com) or abort(403)
    r = motor.canjear(c["id"], premio_id, request.form.get("nonce", ""))
    session["resultado"] = {"estado": {"ok": "canje_ok", "saldo": "canje_saldo"}.get(r["estado"], r["estado"])}
    return redirect(url_for("cliente.local", slug=slug))


@bp.post("/l/<slug>/baja")
def baja(slug):
    com = motor.comercio(slug)
    c = cliente_actual(com) or abort(403)
    db().execute("UPDATE clientes SET acepta_whatsapp=0 WHERE id=?", (c["id"],))
    db().commit()
    return redirect(url_for("cliente.local", slug=slug))


@bp.post("/l/<slug>/salir")
def salir(slug):
    """Cierra la sesión del cliente en este celular (útil si es un celular compartido)."""
    resp = make_response(redirect(url_for("cliente.local", slug=slug)))
    resp.delete_cookie("ct_" + slug)
    return resp


# ---------- visita pedida desde el celular (QR) y confirmada por el personal ----------
@bp.post("/l/<slug>/visita/pedir")
def visita_pedir(slug):
    com = motor.comercio(slug)
    c = cliente_actual(com) or abort(403)
    if activo_o_pagina(com):
        abort(404)
    r = motor.crear_solicitud(c["id"])
    if r["estado"] != "ok":
        session["resultado"] = r
        return redirect(url_for("cliente.local", slug=slug))
    return redirect(url_for("cliente.visita", slug=slug))


@bp.route("/l/<slug>/visita")
def visita(slug):
    com = motor.comercio(slug)
    c = cliente_actual(com) or abort(403)
    s = motor.solicitud_activa(c["id"])
    if not s:
        return redirect(url_for("cliente.local", slug=slug))
    if s["estado"] == "pendiente" and s["vence_en"] > ahora():
        enlace = f"{qr.base_publica()}/negocio/{slug}/caja?codigo={s['codigo']}"
        return render_cliente("visita", com, c=c, s=s, qr_svg=qr.svg_en_linea(enlace, 5), restante=s["vence_en"] - ahora())
    if s["estado"] == "confirmada" and session.get("sol_vista") != s["id"] and ahora() - (s["resuelto_en"] or 0) < 900:
        session["sol_vista"] = s["id"]
        cfg = motor.config(com["id"])
        session["resultado"] = {"estado": "ok", "puntos": cfg["puntos"], "completa": motor.visitas(c["id"]) % cfg["sellos"] == 0}
    elif s["estado"] == "rechazada" and session.get("sol_vista") != s["id"]:
        session["sol_vista"] = s["id"]
        session["resultado"] = {"estado": "rechazada", "motivo": s["motivo"] or "no_confirmada"}
    return redirect(url_for("cliente.local", slug=slug))


# ---------- ingreso con código por WhatsApp ----------
def hash_otp(codigo, tel):
    return hmac.new(current_app.secret_key.encode(), f"{tel}:{codigo}".encode(), hashlib.sha256).hexdigest()


@bp.route("/l/<slug>/entrar", methods=["GET", "POST"])
def entrar(slug):
    com = motor.comercio(slug)
    if request.method == "GET":
        return render_cliente("entrar", com)
    tel = re.sub(r"\D", "", request.form.get("telefono", ""))
    if bloqueado("entrar", MAX_CODIGOS_HORA, 3600):
        return redirect(url_for("cliente.verificar", slug=slug, tel=tel))  # misma respuesta que si hubiera enviado el código
    registrar_fallo("entrar")
    existe = db().execute("SELECT 1 FROM clientes WHERE comercio_id=? AND telefono=?", (com["id"], tel)).fetchone()
    recientes = motor.un("SELECT COUNT(*) FROM otp WHERE comercio_id=? AND telefono=? AND creado>?", com["id"], tel, ahora() - 3600)
    if existe and recientes < 3:  # máximo 3 códigos por hora
        codigo = f"{secrets.randbelow(10**6):06d}"
        db().execute("INSERT INTO otp(comercio_id,telefono,codigo_hash,expira,creado) VALUES(?,?,?,?,?)",
                     (com["id"], tel, hash_otp(codigo, tel), ahora() + OTP_MINUTOS * 60, ahora()))
        db().commit()
        enviar_whatsapp(tel, f"Tu código de {com['nombre']} es {codigo}. Vence en {OTP_MINUTOS} minutos.")
    return redirect(url_for("cliente.verificar", slug=slug, tel=tel))


@bp.route("/l/<slug>/verificar", methods=["GET", "POST"])
def verificar(slug):
    com = motor.comercio(slug)
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


# ---------- escaneo del cartel NFC ----------
def tras_escaneo_valido(com, fuente, idem=None):
    c = cliente_actual(com)
    if c:
        session["resultado"] = motor.registrar_visita(c["id"], fuente, f"cliente:{c['id']}", idem)
    else:
        session.update(scan_ok=com["slug"], scan_fuente=fuente, scan_idem=idem)  # su primera visita se suma al registrarse
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
        motor.auditar("escaneo_rechazado", "aviso", "anonimo", None, None, str(e))
        abort(403)
    tag = db().execute("SELECT * FROM tags WHERE uid=? AND activo=1", (uid,)).fetchone()
    if not tag:
        motor.auditar("escaneo_cartel_desconocido", "aviso", "anonimo", None, f"uid:{uid}", "cartel sin registrar o desactivado")
        abort(403)
    com = motor.comercio(db().execute("SELECT slug FROM comercios WHERE id=?", (tag["comercio_id"],)).fetchone()["slug"])
    if com["estado"] != "activo":
        return redirect(url_for("cliente.local", slug=com["slug"]))
    cur = db().execute("UPDATE tags SET ultimo_contador=?, ultimo_escaneo=? WHERE id=? AND ultimo_contador<?",
                       (ctr, ahora(), tag["id"], ctr))
    db().commit()
    if not cur.rowcount:  # el contador no avanzó: es un escaneo repetido o copiado, no suma
        motor.auditar("escaneo_repetido", "aviso", "anonimo", com["id"], f"tag:{tag['id']}", f"contador {ctr}")
        session["resultado"] = {"estado": "duplicada"}
        return redirect(url_for("cliente.local", slug=com["slug"]))
    return tras_escaneo_valido(com, "nfc", f"nfc:{tag['id']}:{ctr}")


def _imagen(slug, campo):
    r = db().execute(f"SELECT {campo}_img AS img, {campo}_mime AS mime FROM comercios WHERE slug=?", (slug,)).fetchone()
    if not r or not r["img"]:
        abort(404)
    resp = Response(r["img"], mimetype=r["mime"])
    resp.headers["Cache-Control"] = "public, max-age=86400"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


@bp.route("/l/<slug>/logo")
def logo(slug):
    return _imagen(slug, "logo")


@bp.route("/l/<slug>/fondo")
def fondo(slug):
    return _imagen(slug, "fondo")


# ---------- vista previa del editor de diseño (solo el dueño de ESE negocio o la plataforma) ----------
VISTAS = ("inicio", "registro", "tarjeta", "exito", "entrar")


@bp.route("/l/<slug>/vista/<pagina>")
@requiere_negocio
def vista_previa(slug, pagina):
    """Las pantallas reales del cliente, con datos de ejemplo y el tema que se está editando (sin guardarlo).
    Los valores llegan por la dirección y pasan por la misma validación que al guardar."""
    if pagina not in VISTAS:
        abort(404)
    com = dict(g.com)
    crudo, _ = temas.combinar(request.args, temas.leer(com["tema"]))
    if "tagline" in request.args:
        com["tagline"] = request.args.get("tagline", "").strip()[:80]
    if "nombre" in request.args and request.args.get("nombre", "").strip():
        com["nombre"] = request.args["nombre"].strip()[:60]
    if request.args.get("quitar_logo"):
        com["tiene_logo"] = 0
    if request.args.get("quitar_fondo"):
        com["tiene_fondo"] = 0
    t = temas.normalizar(crudo)
    t["nombre"], t["tagline"] = com["nombre"], com["tagline"] or ""
    ctx = dict(datos_programa(g.com), vista_previa=True, avisos_tema=temas.problemas(t))
    if pagina in ("inicio", "registro", "entrar"):
        return render_cliente(pagina, com, t=t, **ctx)
    premios, pts, tarjeta = ctx["premios"], ctx["pts"], ctx["tarjeta"]
    exito = pagina == "exito"
    sellos = min(tarjeta - 1, 3) + (1 if exito else 0)
    total = pts * sellos
    siguiente = min((p["costo"] for p in premios if p["costo"] > total), default=None)
    pend = [dict(codigo="K7M2QX", vence_en=ahora() + 7 * 86400, nombre=premios[0]["nombre"])] if premios else []
    return render_cliente("home", com, t=t, c=dict(id=1, nombre="Ana", acepta_whatsapp=1), sumo=exito, ya=False,
                          aviso=("ok", f"+{pts} puntos y 1 sello por tu visita.") if exito else None, total=total, pendientes=pend,
                          sellos=sellos, completa=False, falta=(siguiente - total) if siguiente else None, **ctx)


def registrar_modo_dev(app):
    """Simula apoyar el celular en el cartel. Solo existe si arrancás con MODO_DEV=1 (nunca en producción)."""
    @app.route("/dev/escanear/<slug>")
    def dev_escanear(slug):
        return tras_escaneo_valido(motor.comercio(slug), "dev")
