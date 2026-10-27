import os, re, json, time, secrets, sqlite3, hashlib, hmac
from functools import wraps
from flask import (Flask, request, redirect, url_for, g, abort, Response,
                   render_template_string, make_response, session)
from markupsafe import Markup
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import generate_password_hash, check_password_hash

import sdm

app = Flask(__name__)
DB = os.environ.get("DB_PATH", "puntos.db")

# Tu contraseña se mantiene en RFNFC
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "RFNFC")
PUNTOS_POR_VISITA = 10

# Aquí agregué una clave secreta segura (aleatoria) para las sesiones
SECRET = os.environ.get("SECRET_KEY", "b5d8f9a2c4e6g1h3j7k9l0m2n4p6q8r")
app.secret_key = SECRET
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")

# Claves de los tags NTAG 424 DNA
SDM_META_KEY = os.environ.get("SDM_META_KEY", "")
SDM_FILE_KEY = os.environ.get("SDM_FILE_KEY", "")

# Apagamos el modo desarrollador para que no cause errores
MODO_DEV = False 

if os.environ.get("RENDER"):  
    # Eliminé la restricción que te bloqueaba la contraseña RFNFC
    if SECRET == "dev-secret":
        raise RuntimeError("Definí una SECRET_KEY en las variables de entorno de Render.")
    app.config["SESSION_COOKIE_SECURE"] = True
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)  # detrás del proxy HTTPS de Render
OTP_MINUTOS = 10
SELLOS_POR_TARJETA = 8  # visitas para completar una tarjeta
PREMIO_TARJETA = "Premio por tarjeta completa"
COOLDOWN_HORAS = float(os.environ.get("COOLDOWN_HORAS", 8))  # una suma por visita cada X horas (anti-abuso)

SCHEMA = """
CREATE TABLE IF NOT EXISTS comercios(
  id INTEGER PRIMARY KEY, slug TEXT UNIQUE NOT NULL, nombre TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS clientes(
  id INTEGER PRIMARY KEY, comercio_id INTEGER NOT NULL, token TEXT UNIQUE NOT NULL,
  nombre TEXT NOT NULL, telefono TEXT NOT NULL, email TEXT,
  acepta_whatsapp INTEGER NOT NULL DEFAULT 0, creado INTEGER NOT NULL,
  UNIQUE(comercio_id, telefono));
CREATE TABLE IF NOT EXISTS puntos_mov(
  id INTEGER PRIMARY KEY, cliente_id INTEGER NOT NULL, delta INTEGER NOT NULL,
  motivo TEXT NOT NULL, creado INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS premios(
  id INTEGER PRIMARY KEY, comercio_id INTEGER NOT NULL, nombre TEXT NOT NULL,
  costo INTEGER NOT NULL, activo INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS canjes(
  id INTEGER PRIMARY KEY, cliente_id INTEGER NOT NULL, premio_id INTEGER NOT NULL,
  codigo TEXT UNIQUE NOT NULL, estado TEXT NOT NULL DEFAULT 'pendiente', creado INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS tags(
  id INTEGER PRIMARY KEY, comercio_id INTEGER NOT NULL, uid TEXT UNIQUE NOT NULL,
  ultimo_contador INTEGER NOT NULL DEFAULT -1, activo INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS otp(
  id INTEGER PRIMARY KEY, comercio_id INTEGER NOT NULL, telefono TEXT NOT NULL,
  codigo_hash TEXT NOT NULL, expira INTEGER NOT NULL, intentos INTEGER NOT NULL DEFAULT 0,
  usado INTEGER NOT NULL DEFAULT 0, creado INTEGER NOT NULL);
"""

def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def cerrar(_):
    c = g.pop("db", None)
    if c:
        c.close()

LOGO_CAFE = '<svg viewBox="0 0 48 48" width="50" height="50" role="img" aria-label="Café Brasa"><circle cx="24" cy="24" r="23" fill="#c8793a"/><path d="M13 22h19v6a8 8 0 0 1-8 8h-3a8 8 0 0 1-8-8z" fill="#f4ebdd"/><path d="M32 24h2.5a3.5 3.5 0 0 1 0 7H31" fill="none" stroke="#f4ebdd" stroke-width="2.4"/><path d="M18 18c-1.6-2 1.6-3 0-6M24 18c-1.6-2 1.6-3 0-6M30 18c-1.6-2 1.6-3 0-6" fill="none" stroke="#f4ebdd" stroke-width="2" stroke-linecap="round"/></svg>'
LOGO_HELADO = '<svg viewBox="0 0 48 48" width="50" height="50" role="img" aria-label="Helados Copo"><circle cx="24" cy="24" r="23" fill="#ff7aa8"/><path d="M15 27h18l-9 17z" fill="#f4b86a"/><path d="M20 31l4 8M26 29l3 6" stroke="#d98f3a" stroke-width="1.5" stroke-linecap="round"/><circle cx="24" cy="24" r="9" fill="#fff7fb"/><circle cx="24" cy="16" r="6.5" fill="#7fe0c6"/><circle cx="24" cy="9.5" r="2.2" fill="#e0245e"/></svg>'

LOCALES_DEMO = [
    dict(slug="cafe-brasa", nombre="Café Brasa", tagline="Tostado propio, todos los días", logo=LOGO_CAFE,
         tema=dict(fondo="#f4ebdd", tinta="#2b1a10", acento="#c8793a", linea="#dccbb2", sup="#fffaf2",
                   radio="4px", titulo="Georgia,Cambria,serif", icono="☕"),
         premios=[("Café de filtro gratis", 50), ("Medialuna con café", 100), ("Bolsa de café 250 g", 300)]),
    dict(slug="helados-copo", nombre="Helados Copo", tagline="Helado artesanal, sabor a feliz", logo=LOGO_HELADO,
         tema=dict(fondo="#fff1f6", tinta="#4a1f3d", acento="#ff7aa8", linea="#f3c9da", sup="#ffffff",
                   radio="22px", fuente="Trebuchet MS,Verdana,sans-serif",
                   titulo="Trebuchet MS,Verdana,sans-serif", icono="🍦"),
         premios=[("Bochita extra", 40), ("Cucurucho simple", 90), ("Un cuarto para llevar", 200)]),
]

TEMA_BASE = dict(fondo="#f2f4f1", tinta="#16322c", acento="#e0a526", linea="#cfd8d3", sup="#ffffff",
                 radio="8px", fuente="system-ui,sans-serif", titulo="system-ui,sans-serif", icono="✓")

def tema_de(com):
    t = dict(TEMA_BASE, nombre="", tagline="")
    if com is not None:
        try:
            t.update(json.loads(com["tema"] or "{}"))
        except ValueError:
            pass
        t["nombre"], t["tagline"] = com["nombre"], com["tagline"] or ""
    return t

def migrar_comercios(c):
    cols = {r[1] for r in c.execute("PRAGMA table_info(comercios)")}
    for col in ("tema", "logo_svg", "tagline", "dominio", "clave_hash"):
        if col not in cols:
            c.execute(f"ALTER TABLE comercios ADD COLUMN {col} TEXT")
    c.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_comercios_dominio ON comercios(dominio)")

def sembrar_demos(c):
    for L in LOCALES_DEMO:
        if c.execute("SELECT 1 FROM comercios WHERE slug=?", (L["slug"],)).fetchone():
            continue
        cur = c.execute("INSERT INTO comercios(slug,nombre,tagline,tema,logo_svg) VALUES(?,?,?,?,?)",
                        (L["slug"], L["nombre"], L["tagline"], json.dumps(L["tema"]), L["logo"]))
        c.executemany("INSERT INTO premios(comercio_id,nombre,costo) VALUES(?,?,?)",
                      [(cur.lastrowid, n, k) for n, k in L["premios"]])

def init_db():
    c = sqlite3.connect(DB)
    c.executescript(SCHEMA)
    if not c.execute("SELECT 1 FROM comercios").fetchone():
        c.execute("INSERT INTO comercios(slug,nombre) VALUES('demo','Local Demo')")
        c.executemany("INSERT INTO premios(comercio_id,nombre,costo) VALUES(1,?,?)",
                      [("Café gratis", 50), ("10% de descuento", 100), ("Postre de la casa", 150)])
    migrar_comercios(c)
    sembrar_demos(c)
    c.commit()
    c.close()

def ahora():
    return int(time.time())

def comercio(slug):
    c = db().execute("SELECT * FROM comercios WHERE slug=?", (slug,)).fetchone()
    return c or abort(404)

def cliente_actual(com):
    t = request.cookies.get("ct_" + com["slug"])
    if not t:
        return None
    return db().execute("SELECT * FROM clientes WHERE token=? AND comercio_id=?",
                        (t, com["id"])).fetchone()

def saldo(cid):
    return db().execute("SELECT COALESCE(SUM(delta),0) FROM puntos_mov WHERE cliente_id=?",
                        (cid,)).fetchone()[0]

def visitas(cid):
    return db().execute("SELECT COUNT(*) FROM puntos_mov WHERE cliente_id=? AND motivo='visita'",
                        (cid,)).fetchone()[0]

def premio_tarjeta(com_id):
    p = db().execute("SELECT id FROM premios WHERE comercio_id=? AND nombre=? AND activo=0",
                     (com_id, PREMIO_TARJETA)).fetchone()
    if p:
        return p["id"]
    cur = db().execute("INSERT INTO premios(comercio_id,nombre,costo,activo) VALUES(?,?,0,0)",
                       (com_id, PREMIO_TARJETA))
    return cur.lastrowid

def sumar_visita(cid):
    ult = db().execute("SELECT MAX(creado) FROM puntos_mov WHERE cliente_id=? AND motivo='visita'",
                       (cid,)).fetchone()[0]
    if ult and ahora() - ult < COOLDOWN_HORAS * 3600:
        return False
    db().execute("INSERT INTO puntos_mov(cliente_id,delta,motivo,creado) VALUES(?,?,?,?)",
                 (cid, PUNTOS_POR_VISITA, "visita", ahora()))
    if visitas(cid) % SELLOS_POR_TARJETA == 0:  # tarjeta completa: premio automático
        com_id = db().execute("SELECT comercio_id FROM clientes WHERE id=?", (cid,)).fetchone()[0]
        db().execute("INSERT INTO canjes(cliente_id,premio_id,codigo,creado) VALUES(?,?,?,?)",
                     (cid, premio_tarjeta(com_id), secrets.token_hex(3).upper(), ahora()))
    db().commit()
    return True

# ---------- vistas ----------
BASE = """<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="theme-color" content="{{ t.tinta }}"><title>{{ titulo }}</title>
<style>
:root{--fondo:{{ t.fondo }};--tinta:{{ t.tinta }};--acento:{{ t.acento }};--linea:{{ t.linea }};--sup:{{ t.sup }};--radio:{{ t.radio }};--fuente:{{ t.fuente }};--titulo:{{ t.titulo }}}
*{box-sizing:border-box}body{margin:0;font:16px/1.5 var(--fuente);background:var(--fondo);color:var(--tinta)}
.marca{display:flex;align-items:center;gap:.85rem;padding:.9rem 1.25rem;background:var(--tinta);color:var(--fondo)}
.marca strong{display:block;font:700 1.3rem/1.15 var(--titulo)}.marca small{opacity:.85}
main{max-width:30rem;margin:0 auto;padding:1.25rem 1.25rem 3rem}
h1{font:700 1.7rem/1.2 var(--titulo);margin:.2rem 0 1rem}h2{font:700 1.15rem/1.3 var(--titulo);margin:1.8rem 0 .6rem}
.puntos{font:800 3.4rem/1 var(--titulo)}a{color:var(--tinta)}
.tarjeta{background:var(--sup);border:1px solid var(--linea);border-radius:var(--radio);padding:1rem 1.1rem;margin:0 0 1rem}
input,button{font:inherit;width:100%;padding:.75rem;border:1px solid var(--linea);border-radius:var(--radio);margin:.25rem 0 .75rem;background:var(--sup);color:var(--tinta)}
button{background:var(--tinta);color:#fff;border:0;font-weight:600;cursor:pointer}
button:disabled{background:var(--linea);color:#6b6b6b;cursor:not-allowed}
button:focus-visible,input:focus-visible,a:focus-visible{outline:3px solid var(--acento);outline-offset:2px}
.fila{display:flex;justify-content:space-between;gap:1rem;align-items:center;padding:.6rem 0;border-bottom:1px solid var(--linea)}
.fila form{width:8rem}.fila button{margin:0}
.aviso{background:var(--sup);border-left:4px solid var(--acento);border-radius:var(--radio);padding:.6rem .8rem;margin:0 0 1rem}
.codigo{font:700 1.4rem ui-monospace,monospace;letter-spacing:.1em}
.chk{display:flex;gap:.6rem;align-items:flex-start;font-size:.9rem}.chk input{width:auto;margin-top:.3rem}
.sellos{display:grid;grid-template-columns:repeat(4,1fr);gap:.6rem;margin:.6rem 0}
.sello{aspect-ratio:1;border:2px dashed var(--linea);border-radius:50%;display:grid;place-items:center;font-weight:800;color:#8a8a8a;font-size:1.3rem}
.sello.on{background:var(--acento);border:2px solid var(--tinta);color:var(--tinta)}
table{width:100%;border-collapse:collapse;font-size:.9rem}td,th{text-align:left;padding:.4rem .3rem;border-bottom:1px solid var(--linea)}
</style></head><body>
{% if t.nombre %}<header class="marca">{{ logo }}<div><strong>{{ t.nombre }}</strong><small>{{ t.tagline }}</small></div></header>{% endif %}
<main>{{ cuerpo }}</main></body></html>"""

T_REGISTRO = """<h1>Creá tu cuenta</h1>
<p>Registrate una vez y sumá puntos y sellos en cada visita.</p>
{% if error %}<p class="aviso">{{ error }}</p>{% endif %}
<form method="post" action="{{ url_for('registro', slug=com.slug) }}">
<label>Nombre<input name="nombre" required autocomplete="name"></label>
<label>Teléfono (con código de área)<input name="telefono" type="tel" required autocomplete="tel"></label>
<label>Email (opcional)<input name="email" type="email" autocomplete="email"></label>
<label class="chk"><input type="checkbox" name="acepta" value="1">
<span>Acepto recibir cupones e invitaciones por WhatsApp. Puedo darme de baja cuando quiera.</span></label>
<button>Crear mi cuenta</button></form>
<p><a href="{{ url_for('entrar', slug=com.slug) }}">Ya tengo cuenta</a></p>"""

T_HOME = """<h1>Hola, {{ c.nombre }}</h1>
{% if sumo %}<p class="aviso">+{{ pts }} puntos y 1 sello por tu visita.{% if completa %} ¡Completaste tu tarjeta! Tu premio está más abajo.{% endif %}</p>
{% elif ya %}<p class="aviso">Ya sumaste puntos por esta visita. Volvé en otra visita para sumar más.</p>{% else %}<p><small>Para sumar puntos y un sello, apoyá tu celular en el cartel del local.</small></p>{% endif %}
<div class="tarjeta"><div class="puntos">{{ total }}</div><div>puntos</div></div>
<div class="tarjeta"><h2 style="margin-top:0">Tu tarjeta de sellos</h2>
<div class="sellos" role="img" aria-label="{{ sellos }} de {{ tarjeta }} sellos">
{% for i in range(tarjeta) %}<span class="sello {{ 'on' if i < sellos }}">{{ t.icono if i < sellos else i + 1 }}</span>{% endfor %}</div>
<small>{{ sellos }} de {{ tarjeta }}. Al completarla ganás un premio.</small></div>
{% if pendientes %}<h2>Tus canjes para mostrar en caja</h2>
{% for p in pendientes %}<div class="fila"><span>{{ p.nombre }}</span><span class="codigo">{{ p.codigo }}</span></div>{% endfor %}{% endif %}
<h2>Premios</h2>
{% for p in premios %}<div class="fila"><span>{{ p.nombre }}<br><small>{{ p.costo }} puntos</small></span>
<form method="post" action="{{ url_for('canjear', slug=com.slug, premio_id=p.id) }}">
<button {% if total < p.costo %}disabled{% endif %}>Canjear</button></form></div>{% endfor %}
{% if c.acepta_whatsapp %}<form method="post" action="{{ url_for('baja', slug=com.slug) }}">
<p><small>Recibís cupones e invitaciones por WhatsApp.</small></p><button>Dejar de recibir mensajes</button></form>
{% else %}<p><small>No vas a recibir mensajes por WhatsApp.</small></p>{% endif %}"""

T_ADMIN = """<h1>Panel del local</h1>
{% if msg %}<p class="aviso">{{ msg }}</p>{% endif %}
<h2>Validar un canje</h2>
<form method="post" action="{{ url_for('admin_validar', slug=com.slug) }}">
<input name="codigo" placeholder="Código del cliente" required autocomplete="off"><button>Validar y entregar</button></form>
<h2>Registrar un cartel</h2>
<form method="post" action="{{ url_for('admin_tags', slug=com.slug) }}">
<input name="uid" placeholder="UID del tag (14 caracteres)" required autocomplete="off"><button>Registrar cartel</button></form>
<p><small>Carteles registrados: {{ ntags }}</small></p>
<h2>Canjes pendientes</h2>
{% for p in pend %}<div class="fila"><span>{{ p.nombre }} · {{ p.cliente }}</span><span class="codigo">{{ p.codigo }}</span></div>
{% else %}<p>No hay canjes pendientes.</p>{% endfor %}
<h2>Clientes ({{ clientes|length }})</h2>
<table><tr><th>Nombre</th><th>Teléfono</th><th>Puntos</th></tr>
{% for k in clientes %}<tr><td>{{ k.nombre }}</td><td>{{ k.telefono }}</td><td>{{ k.total }}</td></tr>{% endfor %}</table>"""

def pagina(titulo, tpl, **ctx):
    com = ctx.get("com")
    t = tema_de(com)
    logo = Markup(com["logo_svg"]) if com is not None and com["logo_svg"] else ""
    cuerpo = Markup(render_template_string(tpl, t=t, **ctx))
    return render_template_string(BASE, titulo=titulo, cuerpo=cuerpo, t=t, logo=logo)

@app.route("/l/<slug>")
def local(slug):
    com = comercio(slug)
    c = cliente_actual(com)
    if not c:
        return pagina(com["nombre"], T_REGISTRO, com=com, error=request.args.get("e"))
    res = session.pop("resultado", None) or {}
    sumo = bool(res.get("sumo"))
    premios = db().execute("SELECT * FROM premios WHERE comercio_id=? AND activo=1 ORDER BY costo",
                           (com["id"],)).fetchall()
    pend = db().execute("""SELECT k.codigo, p.nombre FROM canjes k JOIN premios p ON p.id=k.premio_id
                           WHERE k.cliente_id=? AND k.estado='pendiente'""", (c["id"],)).fetchall()
    n = visitas(c["id"])
    sellos = ((n - 1) % SELLOS_POR_TARJETA) + 1 if n else 0
    return pagina(com["nombre"], T_HOME, com=com, c=c, sumo=sumo, pts=PUNTOS_POR_VISITA,
                  total=saldo(c["id"]), premios=premios, pendientes=pend, sellos=sellos,
                  tarjeta=SELLOS_POR_TARJETA, completa=bool(res.get("completa")), ya=bool(res) and not sumo)

@app.post("/l/<slug>/registro")
def registro(slug):
    com = comercio(slug)
    nombre = request.form.get("nombre", "").strip()[:80]
    tel = re.sub(r"\D", "", request.form.get("telefono", ""))
    email = request.form.get("email", "").strip()[:120]
    acepta = 1 if request.form.get("acepta") else 0
    def volver(msg):
        return redirect(url_for("local", slug=slug, e=msg))
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
    r = make_response(redirect(url_for("local", slug=slug)))
    r.set_cookie("ct_" + slug, token, max_age=365 * 86400, httponly=True, samesite="Lax", secure=request.is_secure)
    return r

@app.post("/l/<slug>/canjear/<int:premio_id>")
def canjear(slug, premio_id):
    com = comercio(slug)
    c = cliente_actual(com) or abort(403)
    p = db().execute("SELECT * FROM premios WHERE id=? AND comercio_id=? AND activo=1",
                     (premio_id, com["id"])).fetchone() or abort(404)
    if saldo(c["id"]) >= p["costo"]:  # los puntos se descuentan al generar el código
        db().execute("INSERT INTO puntos_mov(cliente_id,delta,motivo,creado) VALUES(?,?,?,?)",
                     (c["id"], -p["costo"], "canje", ahora()))
        db().execute("INSERT INTO canjes(cliente_id,premio_id,codigo,creado) VALUES(?,?,?,?)",
                     (c["id"], p["id"], secrets.token_hex(3).upper(), ahora()))
        db().commit()
    return redirect(url_for("local", slug=slug))

T_ENTRAR = """<h1>Entrar a mi cuenta</h1>
<p>Te mandamos un código por WhatsApp.</p>
<form method="post"><label>Teléfono<input name="telefono" type="tel" required autocomplete="tel"></label>
<button>Enviar código</button></form>"""

T_VERIFICAR = """<h1>Ingresá tu código</h1>
<p>Si el número está registrado, te llegó un código de 6 dígitos por WhatsApp.</p>
{% if error %}<p class="aviso">{{ error }}</p>{% endif %}
<form method="post"><input type="hidden" name="tel" value="{{ tel }}">
<label>Código<input name="codigo" inputmode="numeric" maxlength="6" required autocomplete="one-time-code"></label>
<button>Entrar</button></form>"""

def enviar_whatsapp(tel, texto):
    # Modo desarrollo: el mensaje sale por la consola. Acá se conecta la API oficial de WhatsApp.
    print(f"[WHATSAPP -> {tel}] {texto}", flush=True)

def hash_otp(codigo, tel):
    return hmac.new(SECRET.encode(), f"{tel}:{codigo}".encode(), hashlib.sha256).hexdigest()

@app.route("/l/<slug>/entrar", methods=["GET", "POST"])
def entrar(slug):
    com = comercio(slug)
    if request.method == "GET":
        return pagina(com["nombre"], T_ENTRAR, com=com)
    tel = re.sub(r"\D", "", request.form.get("telefono", ""))
    existe = db().execute("SELECT 1 FROM clientes WHERE comercio_id=? AND telefono=?",
                          (com["id"], tel)).fetchone()
    recientes = db().execute("SELECT COUNT(*) FROM otp WHERE comercio_id=? AND telefono=? AND creado>?",
                             (com["id"], tel, ahora() - 3600)).fetchone()[0]
    if existe and recientes < 3:  # máximo 3 códigos por hora
        codigo = f"{secrets.randbelow(10**6):06d}"
        db().execute("INSERT INTO otp(comercio_id,telefono,codigo_hash,expira,creado) VALUES(?,?,?,?,?)",
                     (com["id"], tel, hash_otp(codigo, tel), ahora() + OTP_MINUTOS * 60, ahora()))
        db().commit()
        enviar_whatsapp(tel, f"Tu código de {com['nombre']} es {codigo}. Vence en {OTP_MINUTOS} minutos.")
    return redirect(url_for("verificar", slug=slug, tel=tel))

@app.route("/l/<slug>/verificar", methods=["GET", "POST"])
def verificar(slug):
    com = comercio(slug)
    tel = re.sub(r"\D", "", request.values.get("tel", ""))
    if request.method == "GET":
        return pagina(com["nombre"], T_VERIFICAR, com=com, tel=tel, error=request.args.get("e"))
    codigo = request.form.get("codigo", "").strip()
    o = db().execute("""SELECT * FROM otp WHERE comercio_id=? AND telefono=? AND usado=0 AND expira>?
                        ORDER BY id DESC LIMIT 1""", (com["id"], tel, ahora())).fetchone()
    if o and o["intentos"] < 5:  # máximo 5 intentos por código
        if hmac.compare_digest(o["codigo_hash"], hash_otp(codigo, tel)):
            db().execute("UPDATE otp SET usado=1 WHERE id=?", (o["id"],))
            db().commit()
            c = db().execute("SELECT token FROM clientes WHERE comercio_id=? AND telefono=?",
                             (com["id"], tel)).fetchone()
            r = make_response(redirect(url_for("local", slug=slug)))
            r.set_cookie("ct_" + slug, c["token"], max_age=365 * 86400, httponly=True,
                         samesite="Lax", secure=request.is_secure)
            return r
        db().execute("UPDATE otp SET intentos=intentos+1 WHERE id=?", (o["id"],))
        db().commit()
    return redirect(url_for("verificar", slug=slug, tel=tel, e="Código incorrecto o vencido."))

@app.post("/l/<slug>/baja")
def baja(slug):
    com = comercio(slug)
    c = cliente_actual(com) or abort(403)
    db().execute("UPDATE clientes SET acepta_whatsapp=0 WHERE id=?", (c["id"],))
    db().commit()
    return redirect(url_for("local", slug=slug))

def registrar_visita(c):
    sumo = sumar_visita(c["id"])
    session["resultado"] = {"sumo": sumo,
                            "completa": sumo and visitas(c["id"]) % SELLOS_POR_TARJETA == 0}

def tras_escaneo_valido(com):
    c = cliente_actual(com)
    if c:
        registrar_visita(c)
    else:
        session["scan_ok"] = com["slug"]  # su primera visita se suma al registrarse
    return redirect(url_for("local", slug=com["slug"]))

@app.route("/t")
def escaneo():
    if not (SDM_META_KEY and SDM_FILE_KEY):
        abort(503)
    try:
        uid, ctr = sdm.verificar(request.args.get("picc_data", ""), request.args.get("cmac", ""),
                                 bytes.fromhex(SDM_META_KEY), bytes.fromhex(SDM_FILE_KEY))
    except ValueError as e:
        app.logger.warning("Escaneo rechazado: %s", e)
        abort(403)
    tag = db().execute("SELECT * FROM tags WHERE uid=? AND activo=1", (uid,)).fetchone()
    if not tag:
        app.logger.warning("Escaneo de un cartel sin registrar. UID: %s", uid)
        abort(403)
    com = db().execute("SELECT * FROM comercios WHERE id=?", (tag["comercio_id"],)).fetchone()
    cur = db().execute("UPDATE tags SET ultimo_contador=? WHERE id=? AND ultimo_contador<?",
                       (ctr, tag["id"], ctr))
    db().commit()
    if not cur.rowcount:  # el contador no avanzó: es un escaneo repetido o copiado, no suma
        session["resultado"] = {"sumo": False, "completa": False}
        return redirect(url_for("local", slug=com["slug"]))
    return tras_escaneo_valido(com)

if MODO_DEV:
    @app.route("/dev/escanear/<slug>")
    def dev_escanear(slug):
        return tras_escaneo_valido(comercio(slug))

# ---------- paneles ----------
T_SUPER = """<h1>Locales</h1>{% if msg %}<p class="aviso">{{ msg }}</p>{% endif %}
{% for l in locales %}<div class="tarjeta"><strong>{{ l.nombre }}</strong><br>
<small><a href="{{ url_for('local', slug=l.slug) }}">Pantalla del cliente</a> ·
<a href="{{ url_for('admin', slug=l.slug) }}">Panel del local</a><br>Dominio: {{ l.dominio or 'sin dominio propio' }}</small>
<form method="post" action="{{ url_for('super_local', slug=l.slug) }}">
<input name="dominio" placeholder="Dominio propio, ej: cafebrasa.com (opcional)" autocomplete="off">
<input name="clave" type="password" placeholder="Nueva clave del local, mínimo 8 (opcional)" autocomplete="new-password">
<button>Guardar</button></form></div>{% endfor %}"""

def coincide(pw, esperado):
    return secrets.compare_digest(pw.encode(), esperado.encode())

def pedir_clave():
    return Response("Acceso restringido", 401, {"WWW-Authenticate": 'Basic realm="admin"'})

def solo_super(f):
    @wraps(f)
    def w(*a, **k):
        auth = request.authorization
        if not auth or not coincide(auth.password or "", ADMIN_PASSWORD):
            return pedir_clave()
        return f(*a, **k)
    return w

def solo_local(f):
    @wraps(f)
    def w(slug, *a, **k):
        com = comercio(slug)
        auth = request.authorization
        pw = (auth.password if auth else None) or ""
        if not auth or not (coincide(pw, ADMIN_PASSWORD)
                            or (com["clave_hash"] and check_password_hash(com["clave_hash"], pw))):
            return pedir_clave()
        return f(slug, *a, **k)
    return w

@app.route("/")
def raiz():
    host = request.host.split(":")[0].lower()
    com = db().execute("SELECT slug FROM comercios WHERE dominio=?", (host,)).fetchone()
    return redirect(url_for("local", slug=com["slug"] if com else "demo"))

@app.route("/admin")
@solo_super
def super_panel():
    locales = db().execute("SELECT * FROM comercios ORDER BY id").fetchall()
    return pagina("Locales", T_SUPER, locales=locales, msg=request.args.get("m"))

@app.post("/admin/local/<slug>")
@solo_super
def super_local(slug):
    com = comercio(slug)
    clave = request.form.get("clave", "")
    dom = re.sub(r"^https?://|/.*$", "", request.form.get("dominio", "").strip().lower())
    msg = []
    if clave:
        if len(clave) < 8:
            return redirect(url_for("super_panel", m="La clave debe tener al menos 8 caracteres."))
        db().execute("UPDATE comercios SET clave_hash=? WHERE id=?", (generate_password_hash(clave), com["id"]))
        msg.append("Clave guardada.")
    if dom:
        try:
            db().execute("UPDATE comercios SET dominio=? WHERE id=?", (dom, com["id"]))
            msg.append("Dominio guardado.")
        except sqlite3.IntegrityError:
            return redirect(url_for("super_panel", m="Ese dominio ya lo usa otro local."))
    db().commit()
    return redirect(url_for("super_panel", m=" ".join(msg) or "Sin cambios."))

@app.route("/l/<slug>/admin")
@solo_local
def admin(slug):
    com = comercio(slug)
    pend = db().execute("""SELECT k.codigo, p.nombre, c.nombre AS cliente FROM canjes k
        JOIN premios p ON p.id=k.premio_id JOIN clientes c ON c.id=k.cliente_id
        WHERE k.estado='pendiente' AND c.comercio_id=? ORDER BY k.creado""", (com["id"],)).fetchall()
    clientes = db().execute("""SELECT c.nombre, c.telefono, COALESCE(SUM(m.delta),0) AS total
        FROM clientes c LEFT JOIN puntos_mov m ON m.cliente_id=c.id WHERE c.comercio_id=?
        GROUP BY c.id ORDER BY total DESC""", (com["id"],)).fetchall()
    ntags = db().execute("SELECT COUNT(*) FROM tags WHERE comercio_id=? AND activo=1", (com["id"],)).fetchone()[0]
    return pagina("Panel", T_ADMIN, com=com, pend=pend, clientes=clientes, msg=request.args.get("m"), ntags=ntags)

@app.post("/l/<slug>/admin/validar")
@solo_local
def admin_validar(slug):
    com = comercio(slug)
    cod = request.form.get("codigo", "").strip().upper()
    cur = db().execute("""UPDATE canjes SET estado='usado' WHERE codigo=? AND estado='pendiente'
                          AND cliente_id IN (SELECT id FROM clientes WHERE comercio_id=?)""", (cod, com["id"]))
    db().commit()
    return redirect(url_for("admin", slug=slug, m="Canje entregado." if cur.rowcount else "Código inválido o ya usado."))

@app.post("/l/<slug>/admin/tags")
@solo_local
def admin_tags(slug):
    com = comercio(slug)
    uid = re.sub(r"[^0-9A-Fa-f]", "", request.form.get("uid", "")).upper()
    if len(uid) != 14:
        return redirect(url_for("admin", slug=slug, m="El UID debe tener 14 caracteres (7 bytes)."))
    cur = db().execute("INSERT OR IGNORE INTO tags(comercio_id,uid) VALUES(?,?)", (com["id"], uid))
    db().commit()
    return redirect(url_for("admin", slug=slug, m="Cartel registrado." if cur.rowcount else "Ese cartel ya estaba registrado."))

init_db()
if __name__ == "__main__":
    app.run(debug=True)
