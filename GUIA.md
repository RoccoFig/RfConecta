# RF — guía del proyecto

## Qué hay en cada carpeta

    app.py                 punto de entrada (Render ejecuta: gunicorn app:app)
    automatizaciones.py    mensajes automáticos (bienvenida, clientes inactivos); hoy salen por consola
    rf/                    el programa
      motor.py             reglas de puntos y sellos, canjes y todas las estadísticas (no sabe de pantallas)
      db.py                base de datos y migraciones (solo agregan, nunca borran)
      seguridad.py         sesiones, CSRF, límite de intentos de ingreso
      cliente.py           lo que ve el cliente final (registro, tarjeta, canje, escaneo del cartel)
      negocio.py           panel de cada negocio
      plataforma.py        panel de RF
      sdm.py               verificación de los carteles NTAG 424 DNA
      templates/panel      piezas compartidas de los dos paneles
      templates/cliente    pantalla base del cliente
      templates/disenos/   UN DISEÑO A MEDIDA POR NEGOCIO (una carpeta por negocio)
      static/disenos/      estilos, logos e ilustraciones de cada negocio
    tools/nuevo_diseno.py  crea la carpeta de diseño de un negocio nuevo
    tools/respaldar.py     copia de seguridad verificada
    tools/verificar_integridad.py   control de coherencia de saldos y canjes
    tests/                 102 pruebas automáticas (pytest) y el simulador de carteles NFC
    docs/AUDITORIA.md      informe de seguridad: hallazgos, correcciones, riesgos que quedan
    docs/OPERACION.md      publicación, respaldos, retroceso y qué hacer ante una emergencia

## Direcciones

    /plataforma            panel de RF (contraseña = variable ADMIN_PASSWORD)
    /negocio/<slug>        panel de cada negocio: el dueño ve todo; la caja solo ve «Caja»
    /negocio/<slug>/caja   confirmar visitas pedidas por QR y entregar premios
    /negocio/<slug>/qr     descargar el QR del negocio (PNG, SVG y versión para imprimir)
    /l/<slug>              entrada permanente del negocio (la del QR impreso). Abrirla NO suma puntos
    /t?picc_data=...       link firmado que escribe cada cartel NFC
    /salud                 control de disponibilidad (no expone datos)

## Variables de entorno en Render (Environment)

    ADMIN_PASSWORD     obligatoria. Contraseña del panel de RF.
    SECRET_KEY         obligatoria. Texto largo y al azar. Firma las sesiones.
    PUBLIC_BASE_URL    la dirección definitiva (https://...). Definila ANTES de imprimir QR.
    SDM_META_KEY       claves de los carteles NTAG 424 DNA (32 caracteres hexadecimales cada una)
    SDM_FILE_KEY
    DB_PATH            /var/data/puntos.db con disco persistente
    COOLDOWN_HORAS     horas mínimas entre visitas si el negocio no define las suyas (por defecto 8)
    PYTHON_VERSION     recomendado: una 3.12 (es la versión con la que se probó)
    MODO_DEV           NUNCA en producción: habilita /dev/escanear. Con RENDER la app se niega a arrancar si está.

Si falta ADMIN_PASSWORD o SECRET_KEY en Render, la app se niega a arrancar a propósito.

## Cómo se crea el diseño de un negocio nuevo

1. En /plataforma → Negocios → Nuevo negocio: cargá los datos (nombre, rubro, clave del dueño).
2. En tu compu: python tools/nuevo_diseno.py <slug>
3. Editá rf/templates/disenos/<slug>/_tarjeta.html y rf/static/disenos/<slug>/estilo.css.
   Podés sumar un logo.svg y copiar cualquier plantilla de rf/templates/cliente/ para rehacerla entera
   (registro.html, home.html, entrar.html, verificar.html). Lo que no crees usa la versión base.
4. Probá en tu compu y subí los cambios a GitHub: Render publica solo.

Ejemplos reales: rf/templates/disenos/cafe-brasa y rf/templates/disenos/helados-copo.

## Probar en tu compu

    pip install -r requirements.txt
    ADMIN_PASSWORD=algo MODO_DEV=1 python app.py          (en Windows: set ADMIN_PASSWORD=algo y set MODO_DEV=1)
    pip install -r requirements-dev.txt
    python -m pytest tests                                (corre todas las pruebas con una base temporal)

Con MODO_DEV=1 podés abrir /dev/escanear/<slug> para simular que apoyás el celular en el cartel.

## Pendiente

    Mensajes reales por WhatsApp (hoy salen por consola).
    Cambiar las claves de fábrica de los tags NTAG 424 DNA antes de usarlos en un local real.
    Disco persistente en Render para que la base no se borre al reiniciar.
    Tarjeta en Google Wallet / Apple Wallet.
