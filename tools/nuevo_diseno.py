"""Crea la carpeta de diseño a medida de un negocio con una tarjeta de lealtad y un estilo de partida.

    python tools/nuevo_diseno.py mi-negocio

Después editás los archivos creados y los subís a GitHub. Lo que no crees (registro, home, entrar...) usa la versión base.
"""
import os, re, sys

raiz = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "rf")
if len(sys.argv) != 2 or not re.fullmatch(r"[a-z0-9-]+", sys.argv[1]):
    sys.exit("Uso: python tools/nuevo_diseno.py <slug>   (el slug es el que ves en /l/<slug>, en minúsculas)")
slug = sys.argv[1]
plantillas = os.path.join(raiz, "templates", "disenos", slug)
estaticos = os.path.join(raiz, "static", "disenos", slug)
if os.path.exists(plantillas) or os.path.exists(estaticos):
    sys.exit(f"Ya existe un diseño para «{slug}». Editalo directamente.")
os.makedirs(plantillas); os.makedirs(estaticos)

TARJETA = '''{# __SLUG__ · tarjeta de lealtad. Variables: sellos (los que tiene), tarjeta (los que lleva completar), sumo (¿recién sumó?),
   premio_tarjeta, c (cliente), t (tema), com (negocio). Cambiá el HTML y el CSS por algo que sea de este negocio. #}
<section class="tarjeta __SLUG__-tarjeta" aria-labelledby="__SLUG__-t">
  <h2 id="__SLUG__-t">Tu tarjeta</h2>
  <ol class="__SLUG__-sellos" role="img" aria-label="{{ sellos }} de {{ tarjeta }} sellos">
    {% for i in range(tarjeta) %}<li class="{{ 'on' if i < sellos }}{{ ' nuevo' if i == sellos - 1 and sumo }}">{{ t.icono if i < sellos else i + 1 }}</li>{% endfor %}
  </ol>
  <p>{% if sellos >= tarjeta %}¡Completa! Pedí tu premio: {{ premio_tarjeta }}{% else %}Faltan {{ tarjeta - sellos }} para {{ premio_tarjeta }}{% endif %}</p>
</section>
'''
ESTILO = '''/* __SLUG__ — estilo propio. Las variables --fondo, --tinta, --acento, --linea, --sup y --radio vienen del tema base. */
.__SLUG__-sellos{list-style:none;margin:.5rem 0;padding:0;display:grid;grid-template-columns:repeat(4,1fr);gap:.6rem}
.__SLUG__-sellos li{aspect-ratio:1;display:grid;place-items:center;border:2px dashed var(--linea);border-radius:var(--radio);font-weight:700}
.__SLUG__-sellos li.on{background:var(--acento);border:2px solid var(--tinta);color:var(--tinta)}
.__SLUG__-sellos li.nuevo{animation:__SLUG__-pop .45s ease-out}
@keyframes __SLUG__-pop{from{transform:scale(.6);opacity:0}to{transform:none;opacity:1}}
'''
open(os.path.join(plantillas, "_tarjeta.html"), "w", encoding="utf-8").write(TARJETA.replace("__SLUG__", slug))
open(os.path.join(estaticos, "estilo.css"), "w", encoding="utf-8").write(ESTILO.replace("__SLUG__", slug))
print(f"Listo. Se crearon:\n  rf/templates/disenos/{slug}/_tarjeta.html\n  rf/static/disenos/{slug}/estilo.css\n"
      f"Opcional: poné el logo en rf/static/disenos/{slug}/logo.svg (o .png/.jpg/.webp) y copiá rf/templates/cliente/home.html para rehacer toda la pantalla.")
