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
      temas.py             identidad visual de cada negocio: esquema del tema, validación y variables CSS
      templates/cliente    pantallas del cliente (inicio, registro, ingreso, tarjeta); todas toman el tema del negocio
      templates/disenos/   OPCIONAL: disposición a medida de un negocio (una carpeta por negocio)
      static/disenos/      estilos, logos e ilustraciones de esos diseños a medida
    tools/nuevo_diseno.py  crea la carpeta de diseño de un negocio nuevo
    tools/respaldar.py     copia de seguridad verificada
    tools/verificar_integridad.py   control de coherencia de saldos y canjes
    tests/                 117 pruebas automáticas (pytest) y el simulador de carteles NFC
    docs/AUDITORIA.md      informe de seguridad: hallazgos, correcciones, riesgos que quedan
    docs/OPERACION.md      publicación, respaldos, retroceso y qué hacer ante una emergencia

## Direcciones

    /plataforma            panel de RF (contraseña = variable ADMIN_PASSWORD)
    /negocio/<slug>        panel de cada negocio: el dueño ve todo; la caja solo ve «Caja»
    /negocio/<slug>/caja   confirmar visitas pedidas por QR y entregar premios
    /negocio/<slug>/qr     descargar el QR del negocio (PNG, SVG y versión para imprimir)
    /negocio/<slug>/diseno el dueño edita la identidad visual de SU negocio (con vista previa)
    /l/<slug>              entrada permanente del negocio (la del QR impreso). Sin cuenta: página de inicio
                           del negocio; con cuenta: su tarjeta y premios. Abrirla NO suma puntos
    /registro/<slug>       página de registro propia de cada negocio
    /l/<slug>/vista/<p>    vista previa del editor (solo el dueño de ese negocio o la plataforma)
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
2. Te lleva a la pestaña Diseño: logo, colores, tipografía, estilo, botones, tarjetas, cabecera, imagen y textos
   de la página de inicio. La vista previa muestra las pantallas reales (inicio, registro, tarjeta, visita
   sumada, ingreso) en celular y computadora antes de guardar. Al guardar, cambian TODAS sus pantallas.
   El dueño puede hacer lo mismo desde su panel (Diseño). Nadie puede tocar el diseño de otro negocio.
3. No hace falta programar nada. El tema se guarda en comercios.tema y solo admite colores #rrggbb,
   opciones de listas cerradas y textos (que se muestran escapados): no se puede inyectar CSS ni HTML.

Opcional, para una disposición realmente a medida (por ejemplo una tarjeta de sellos ilustrada):
   python tools/nuevo_diseno.py <slug>   y editá rf/templates/disenos/<slug>/ y rf/static/disenos/<slug>/.
   Usá las variables del tema (var(--primario), var(--acento)...) en vez de colores fijos.
   Ejemplos: rf/templates/disenos/cafe-brasa y rf/templates/disenos/helados-copo.

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
