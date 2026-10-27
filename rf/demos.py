"""Negocios de ejemplo. Solo se crean si todavía no existen (por su slug)."""
import json

DEMOS = [
    dict(slug="demo", nombre="Local Demo", categoria="General", tagline="", tema={}, logo=None,
         premios=[("Café gratis", 50), ("10% de descuento", 100), ("Postre de la casa", 150)]),
    dict(slug="cafe-brasa", nombre="Café Brasa", categoria="Cafetería", premio="Un café de regalo", tagline="Tostado propio, todos los días",
         tema=dict(fondo="#f4ebdd", tinta="#2b1a10", acento="#c8793a", linea="#dccbb2", sup="#fffaf2", radio="4px",
                   titulo="Georgia,Cambria,serif", icono="☕"), logo=None,
         premios=[("Café de filtro gratis", 50), ("Medialuna con café", 100), ("Bolsa de café 250 g", 300)]),
    dict(slug="helados-copo", nombre="Helados Copo", categoria="Heladería", premio="Un cucurucho de regalo", tagline="Helado artesanal, sabor a feliz",
         tema=dict(fondo="#fff1f6", tinta="#4a1f3d", acento="#ff7aa8", linea="#f3c9da", sup="#ffffff", radio="22px",
                   fuente="Trebuchet MS,Verdana,sans-serif", titulo="Trebuchet MS,Verdana,sans-serif", icono="🍦"),
         logo=None, premios=[("Bochita extra", 40), ("Cucurucho simple", 90), ("Un cuarto para llevar", 200)]),
]


def sembrar_demos(c):
    for d in DEMOS:
        fila = c.execute("SELECT id, categoria FROM comercios WHERE slug=?", (d["slug"],)).fetchone()
        if fila:
            if not fila[1]:
                c.execute("UPDATE comercios SET categoria=? WHERE id=?", (d["categoria"], fila[0]))
            if d.get("premio"):
                c.execute("UPDATE comercios SET premio_tarjeta=? WHERE id=? AND premio_tarjeta IS NULL", (d["premio"], fila[0]))
            continue
        cur = c.execute("INSERT INTO comercios(slug,nombre,tagline,tema,categoria,premio_tarjeta) VALUES(?,?,?,?,?,?)",
                        (d["slug"], d["nombre"], d["tagline"], json.dumps(d["tema"]), d["categoria"], d.get("premio")))
        c.executemany("INSERT INTO premios(comercio_id,nombre,costo) VALUES(?,?,?)",
                      [(cur.lastrowid, n, k) for n, k in d["premios"]])
