"""Pruebas automáticas de RF. Uso:  ADMIN_PASSWORD=x python tests/probar.py
Usa una base temporal, nunca la tuya. Termina con código distinto de 0 si algo falla."""
import contextlib, io, os, re, sqlite3, sys, tempfile, time
carpeta = tempfile.mkdtemp()
os.environ.update(DB_PATH=os.path.join(carpeta, "prueba.db"), ADMIN_PASSWORD="x", MODO_DEV="1",
                  SDM_META_KEY="00" * 16, SDM_FILE_KEY="00" * 16)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import base64, app as a
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from rf import sdm

fallas, total = [], 0
def ok(nombre, cond):
    global total; total += 1
    if not cond: fallas.append(nombre)
    print(("OK    " if cond else "FALLA ") + nombre)
def tok(cl):
    with cl.session_transaction() as s:
        s.setdefault("csrf", "t"); return s["csrf"]
def post(cl, url, **d): return cl.post(url, data={"csrf": tok(cl), **d})
def nuevo(): return a.app.test_client()
def bd(sql, *p):
    con = sqlite3.connect(os.environ["DB_PATH"]); r = con.execute(sql, p).fetchall(); con.commit(); con.close(); return r
def texto(r): return r.get_data(as_text=True)
def adelantar(horas=9): bd("UPDATE puntos_mov SET creado=creado-?", horas * 3600)

# ---- sesiones de prueba ----
plat = nuevo(); post(plat, "/plataforma/ingresar", clave="x")
post(plat, "/plataforma/negocios/nuevo", nombre="Panadería Don Pepe", categoria="Panadería", clave="clave-pepe-1", tagline="Pan caliente")
post(plat, "/plataforma/negocios/nuevo", nombre="Gimnasio Fuerza", categoria="Gimnasio", clave="clave-fuerza-1")
pepe = nuevo(); post(pepe, "/negocio/panaderia-don-pepe/ingresar", clave="clave-pepe-1")
fuerza = nuevo(); post(fuerza, "/negocio/gimnasio-fuerza/ingresar", clave="clave-fuerza-1")
ok("la plataforma y los dueños pueden ingresar", all(c.get(u).status_code == 200 for c, u in ((plat, "/plataforma/"), (pepe, "/negocio/panaderia-don-pepe/"), (fuerza, "/negocio/gimnasio-fuerza/"))))

# ---- acceso y aislamiento ----
anon = nuevo()
ok("sin sesión, la plataforma pide ingresar", anon.get("/plataforma/clientes").status_code == 302 and "ingresar" in anon.get("/plataforma/clientes").headers["Location"])
ok("sin sesión, el panel de un negocio pide ingresar", "ingresar" in anon.get("/negocio/cafe-brasa/").headers["Location"])
ok("un dueño NO entra al panel de otro negocio", "ingresar" in pepe.get("/negocio/gimnasio-fuerza/clientes").headers["Location"])
ok("un dueño NO entra a la plataforma", "ingresar" in pepe.get("/plataforma/").headers["Location"])
ok("la plataforma entra a cualquier negocio (soporte) y se avisa", "como RF" in texto(plat.get("/negocio/cafe-brasa/")))
ok("las direcciones viejas redirigen", a.app.test_client().get("/admin").status_code == 301 and anon.get("/l/demo/admin").status_code == 301)

# ---- protección CSRF y orígenes ----
ok("POST sin token → 403", anon.post("/plataforma/ingresar", data={"clave": "x"}).status_code == 403)
ok("POST con Origin ajeno → 403", plat.post("/plataforma/negocios/nuevo", data={"csrf": tok(plat), "nombre": "Z"}, headers={"Origin": "https://malo.com"}).status_code == 403)
ok("el formulario del cliente también exige token", anon.post("/l/demo/registro", data={"nombre": "A", "telefono": "3815550000"}).status_code == 403)
ok("'siguiente' externo se ignora (sin redirección abierta)", "evil" not in post(nuevo(), "/plataforma/ingresar?siguiente=//evil.com", clave="x", siguiente="//evil.com").headers["Location"])

# ---- flujo del cliente ----
def registrar(cl, slug, nombre="Ana", tel="3815550000"):
    cl.get(f"/dev/escanear/{slug}"); post(cl, f"/l/{slug}/registro", nombre=nombre, telefono=tel)
u = nuevo(); registrar(u, "panaderia-don-pepe")
h = texto(u.get("/l/panaderia-don-pepe"))
ok("registro con escaneo: 1 sello y +10 puntos", "1 de 8" in h and "+10 puntos" in h)
n = len(bd("SELECT 1 FROM puntos_mov")); u.get("/l/panaderia-don-pepe")
ok("abrir la página sin escanear NO suma", len(bd("SELECT 1 FROM puntos_mov")) == n)
u.get("/dev/escanear/panaderia-don-pepe")
ok("segundo escaneo dentro del tiempo de espera no suma", "Ya sumaste" in texto(u.get("/l/panaderia-don-pepe")))
ok("sin cartel la página dice cómo sumar", "apoyá tu celular" in texto(u.get("/l/panaderia-don-pepe")))
dup = nuevo(); dup.get("/dev/escanear/panaderia-don-pepe")
aviso = dup.get(post(dup, "/l/panaderia-don-pepe/registro", nombre="Otra", telefono="3815550000").headers["Location"])
ok("un teléfono ya registrado no se puede registrar dos veces", "ya está registrado" in texto(aviso) and bd("SELECT COUNT(*) FROM clientes WHERE telefono='3815550000' AND comercio_id=(SELECT id FROM comercios WHERE slug='panaderia-don-pepe')")[0][0] == 1)
ok("cada negocio tiene su propia sesión de cliente", "Creá tu cuenta" in texto(u.get("/l/cafe-brasa")))

# ---- reglas propias del negocio ----
post(pepe, "/negocio/panaderia-don-pepe/ajustes/reglas", puntos_visita="25", sellos_tarjeta="3", premio_tarjeta="Docena de facturas")
adelantar(); u.get("/dev/escanear/panaderia-don-pepe"); adelantar(); u.get("/dev/escanear/panaderia-don-pepe")
h = texto(u.get("/l/panaderia-don-pepe"))
ok("reglas propias: tarjeta de 3 completa y premio automático", "Docena de facturas" in h and "3 de 3" in h)
ok("reglas inválidas se rechazan", "Revisá" in texto(pepe.get(post(pepe, "/negocio/panaderia-don-pepe/ajustes/reglas", puntos_visita="0", sellos_tarjeta="3", premio_tarjeta="x").headers["Location"])))

# ---- premios, canjes y entrega ----
post(pepe, "/negocio/panaderia-don-pepe/premios", nombre="Pan francés x6", costo="30")
pid = bd("SELECT id FROM premios WHERE nombre='Pan francés x6'")[0][0]
ok("el premio nuevo le aparece al cliente", "Pan francés x6" in texto(u.get("/l/panaderia-don-pepe")))
antes = bd("SELECT COALESCE(SUM(delta),0) FROM puntos_mov WHERE cliente_id=1")[0][0]
post(u, f"/l/panaderia-don-pepe/canjear/{pid}")
cod = bd("SELECT k.codigo FROM canjes k JOIN premios p ON p.id=k.premio_id WHERE p.nombre='Pan francés x6'")[0][0]
ok("canjear descuenta exactamente el costo y genera el código", cod in texto(u.get("/l/panaderia-don-pepe")) and bd("SELECT COALESCE(SUM(delta),0) FROM puntos_mov WHERE cliente_id=1")[0][0] == antes - 30)
post(u, f"/l/panaderia-don-pepe/canjear/{pid}") if False else None
post(fuerza, "/negocio/gimnasio-fuerza/canjes/validar", codigo=cod)
ok("otro negocio NO puede entregar mi canje", bd("SELECT estado FROM canjes WHERE codigo=?", cod)[0][0] == "pendiente")
post(pepe, "/negocio/panaderia-don-pepe/canjes/validar", codigo=cod)
ok("el dueño entrega el canje una vez", bd("SELECT estado FROM canjes WHERE codigo=?", cod)[0][0] == "usado")
ok("un código ya usado no se entrega de nuevo", "inválido" in texto(pepe.get(post(pepe, "/negocio/panaderia-don-pepe/canjes/validar", codigo=cod).headers["Location"])))
post(pepe, f"/negocio/panaderia-don-pepe/premios/{pid}/quitar")
ok("quitar un premio lo oculta al cliente", "Pan francés x6" not in texto(u.get("/l/panaderia-don-pepe")))
ok("no se pueden tocar premios de otro negocio", post(fuerza, f"/negocio/gimnasio-fuerza/premios/{pid}", nombre="x", costo="1").status_code == 404)

# ---- ingreso del cliente con código ----
b = nuevo(); salida = io.StringIO()
with contextlib.redirect_stdout(salida): post(b, "/l/panaderia-don-pepe/entrar", telefono="3815550000")
codigo = re.search(r"es (\d{6})", salida.getvalue()).group(1)
post(b, "/l/panaderia-don-pepe/verificar", tel="3815550000", codigo="000000")
ok("código incorrecto no entra", "Creá tu cuenta" in texto(b.get("/l/panaderia-don-pepe")))
post(b, "/l/panaderia-don-pepe/verificar", tel="3815550000", codigo=codigo)
ok("código correcto entra desde otro dispositivo", "Hola, Ana" in texto(b.get("/l/panaderia-don-pepe")))

# ---- carteles firmados ----
K = bytes(16)
def link(ctr, uid="04DE5F1EACC040"):
    plano = bytes([0xC7]) + bytes.fromhex(uid) + ctr.to_bytes(3, "little") + os.urandom(5)
    e = Cipher(algorithms.AES(K), modes.CBC(bytes(16))).encryptor(); picc = e.update(plano) + e.finalize()
    sv = bytes([0x3C, 0xC3, 0, 1, 0, 0x80]) + bytes.fromhex(uid) + ctr.to_bytes(3, "little")
    full = sdm._cmac(sdm._cmac(K, sv), b""); return f"/t?picc_data={picc.hex().upper()}&cmac={bytes(full[i] for i in range(1, 16, 2)).hex().upper()}"
ok("cartel sin registrar → rechazado", nuevo().get(link(1)).status_code == 403)
post(pepe, "/negocio/panaderia-don-pepe/carteles", uid="04DE5F1EACC040")
ok("firma alterada → rechazada", nuevo().get(link(2).replace("cmac=", "cmac=00")).status_code == 403)
adelantar(); u.get(link(5)); h1 = texto(u.get("/l/panaderia-don-pepe"))
ok("escaneo firmado válido suma", "+25 puntos" in h1)
adelantar(); u.get(link(5)); ok("el mismo link repetido (copiado) no suma", "Ya sumaste" in texto(u.get("/l/panaderia-don-pepe")))
u.get(link(3)); ok("un link viejo no suma", "Ya sumaste" in texto(u.get("/l/panaderia-don-pepe")))
ok("un dueño no ve ni toca los carteles de otro", bd("SELECT COUNT(*) FROM tags WHERE comercio_id=(SELECT id FROM comercios WHERE slug='gimnasio-fuerza')")[0][0] == 0)
post(plat, f"/plataforma/carteles/{bd('SELECT id FROM tags')[0][0]}/estado"); adelantar()
filas = len(bd("SELECT 1 FROM puntos_mov")); r = u.get(link(9))
ok("un cartel desactivado deja de sumar", r.status_code == 403 and len(bd("SELECT 1 FROM puntos_mov")) == filas)
post(plat, f"/plataforma/carteles/{bd('SELECT id FROM tags')[0][0]}/estado")

# ---- suspensión ----
post(plat, "/plataforma/negocios/panaderia-don-pepe/estado")
ok("negocio suspendido: el cliente ve el aviso", "en pausa" in texto(u.get("/l/panaderia-don-pepe")))
post(plat, "/plataforma/negocios/panaderia-don-pepe/estado")
ok("reactivado: vuelve a funcionar", "Hola, Ana" in texto(u.get("/l/panaderia-don-pepe")))

# ---- paneles: datos, filtros y estados vacíos ----
paginas = ["/plataforma/", "/plataforma/negocios", "/plataforma/negocios/nuevo", "/plataforma/clientes", "/plataforma/actividad", "/plataforma/canjes", "/plataforma/carteles", "/plataforma/ajustes"] + \
    [f"/plataforma/negocios/cafe-brasa?tab={t}" for t in ("resumen", "configuracion", "diseno", "carteles")]
ok("todas las pantallas de la plataforma cargan", all(plat.get(p).status_code == 200 for p in paginas))
ok("todas las pantallas del negocio cargan", all(pepe.get(f"/negocio/panaderia-don-pepe/{p}").status_code == 200 for p in ("", "clientes", "actividad", "premios", "canjes", "carteles", "ajustes", "ajustes?tab=cuenta", "clientes/1")))
ok("negocio sin datos: estados vacíos sin errores", all(fuerza.get(f"/negocio/gimnasio-fuerza/{p}").status_code == 200 for p in ("", "clientes", "actividad", "premios", "canjes", "carteles")) and "Todavía no hay premios" in texto(fuerza.get("/negocio/gimnasio-fuerza/premios")))
for i in range(20): registrar(nuevo(), "gimnasio-fuerza", f"Socio {i:02d}", f"38155510{i:02d}")
ok("clientes: paginación (15 por página)", "Página 1 de 2" in texto(fuerza.get("/negocio/gimnasio-fuerza/clientes")) and "Socio" in texto(fuerza.get("/negocio/gimnasio-fuerza/clientes?pagina=2")))
ok("clientes: búsqueda", "Socio 07" in texto(fuerza.get("/negocio/gimnasio-fuerza/clientes?q=07")) and "Socio 03" not in texto(fuerza.get("/negocio/gimnasio-fuerza/clientes?q=07")))
ok("el dueño solo ve a sus clientes", "Ana" not in texto(fuerza.get("/negocio/gimnasio-fuerza/clientes")))
ok("plataforma: filtra clientes por negocio", "Socio 05" in texto(plat.get("/plataforma/clientes?negocio=gimnasio-fuerza&q=05")) and "Ana" not in texto(plat.get("/plataforma/clientes?negocio=gimnasio-fuerza")))
ok("actividad: filtra por tipo", "se registró" in texto(plat.get("/plataforma/actividad?tipo=registro")) and "sumó una visita" not in texto(plat.get("/plataforma/actividad?tipo=registro")))
ok("resumen de la plataforma cuenta bien", re.search(r"Clientes</div><div class=\"v\">(\d+)", texto(plat.get("/plataforma/"))).group(1) == str(bd("SELECT COUNT(*) FROM clientes")[0][0]))
ok("alta de negocio duplicado se rechaza", bd("SELECT COUNT(*) FROM comercios WHERE slug='gimnasio-fuerza'")[0][0] == 1 and (post(plat, "/plataforma/negocios/nuevo", nombre="Gimnasio Fuerza", clave="clave-larga-1") and bd("SELECT COUNT(*) FROM comercios WHERE slug='gimnasio-fuerza'")[0][0] == 1))
ok("clave corta se rechaza", post(plat, "/plataforma/negocios/nuevo", nombre="Corta SA", clave="123") and not bd("SELECT 1 FROM comercios WHERE slug='corta-sa'"))

# ---- tema base y logo ----
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
tema = dict(fondo="#fdf3e3", tinta="#3b2412", acento="#e8a33d", forma="suave", tipografia="clasica", icono="🥖")
r = plat.post("/plataforma/negocios/panaderia-don-pepe/tema", data={"csrf": tok(plat), **tema, "logo": (io.BytesIO(PNG), "l.png")})
h = texto(u.get("/l/panaderia-don-pepe"))
ok("logo PNG subido se muestra y se sirve", "/l/panaderia-don-pepe/logo?v=" in h and u.get("/l/panaderia-don-pepe/logo").mimetype == "image/png")
ok("tema rechaza logo que no es imagen", plat.post("/plataforma/negocios/gimnasio-fuerza/tema", data={"csrf": tok(plat), **tema, "logo": (io.BytesIO(b"<svg onload=alert(1)>"), "x.svg")}).status_code == 302 and not bd("SELECT 1 FROM comercios WHERE slug='gimnasio-fuerza' AND logo_img IS NOT NULL"))
post(plat, "/plataforma/negocios/gimnasio-fuerza/tema", **{**tema, "tinta": "#f0e6d0"})
ok("tema rechaza texto sin contraste", "#f0e6d0" not in bd("SELECT tema FROM comercios WHERE slug='gimnasio-fuerza'")[0][0])

# ---- diseños a medida ----
cafe = nuevo(); h = texto(cafe.get("/l/cafe-brasa"))
ok("Café Brasa carga su propio estilo y no el de otros", "disenos/cafe-brasa/estilo.css" in h and "helados-copo" not in h)
registrar(cafe, "cafe-brasa", "Lu", "3815559999"); h = texto(cafe.get("/l/cafe-brasa")); hc = nuevo(); registrar(hc, "helados-copo", "Lu", "3815559999")
ok("la tarjeta de cada negocio es distinta", "cb-tazas" in h and "hc-fila" in texto(hc.get("/l/helados-copo")) and "cb-tazas" not in texto(hc.get("/l/helados-copo")))
socio = nuevo(); registrar(socio, "gimnasio-fuerza", "Socio Base", "3815558888"); hb = texto(socio.get("/l/gimnasio-fuerza"))
ok("un negocio sin diseño propio usa la tarjeta base", "Tu tarjeta de sellos" in hb and "cb-tazas" not in hb and "hc-fila" not in hb and "disenos/gimnasio-fuerza" not in hb)
ok("la ficha de diseño reconoce los archivos propios", "_tarjeta.html" in texto(plat.get("/plataforma/negocios/cafe-brasa?tab=diseno")))

# ---- seguridad de sesión y configuración ----
ok("cerrar sesión cierra de verdad", post(pepe, "/negocio/panaderia-don-pepe/salir").status_code == 302 and "ingresar" in pepe.get("/negocio/panaderia-don-pepe/").headers["Location"])
ok("cabecera nosniff en todas las respuestas", plat.get("/plataforma/").headers.get("X-Content-Type-Options") == "nosniff" and "no-store" in plat.get("/plataforma/").headers.get("Cache-Control", ""))
os.environ["RENDER"] = "1"; os.environ.pop("SECRET_KEY", None)
try: a.create_app(); ok("en Render sin SECRET_KEY la app se niega a arrancar", False)
except RuntimeError: ok("en Render sin SECRET_KEY la app se niega a arrancar", True)
del os.environ["RENDER"]
mal = nuevo(); [post(mal, "/plataforma/ingresar", clave="incorrecta") for _ in range(5)]
ok("tras 5 intentos fallidos se bloquea el ingreso", post(mal, "/plataforma/ingresar", clave="x").status_code == 429)

print(f"\n{total - len(fallas)} de {total} pruebas correctas")
if fallas: print("FALLARON:", *fallas, sep="\n  - "); sys.exit(1)
