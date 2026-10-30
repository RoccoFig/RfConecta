# RF — informe de seguridad y preparación para el lanzamiento

Fecha: 27/10/2026. Alcance: todo el código de `rf/`, la base de datos, los paneles y los flujos de cliente, NFC y QR.
Método: primero se atacó la versión anterior con pruebas reales, después se corrigió, y cada corrección quedó cubierta por una prueba automática.

## 1. Lo que se encontró antes de corregir

Confirmado con una prueba de ataque (no es teoría):
- GRAVE. Canjes simultáneos con saldo para uno solo: 8 pedidos en paralelo crearon 2 canjes y dejaron el saldo en -50.
  Hoy no se explota con un solo proceso de servidor, pero aparece apenas se usen varios procesos o hilos.
- ALTA. El historial de puntos se podía modificar o borrar directamente en la base, y la base aceptaba saldos negativos.
- ALTA. Los códigos de ingreso del cliente se escribían en los registros del servidor: quien viera los registros podía tomar cuentas.
- ALTA. No existía el QR como alternativa sin NFC; el único camino alternativo era un atajo de desarrollo.
- MEDIA. El dueño y el personal de caja compartían una clave: quien entregaba premios veía todos los clientes y ajustes.
- MEDIA. Códigos de canje de 6 caracteres (24 bits), sin vencimiento, sin cancelación y sin freno a los intentos.
- MEDIA. Sin registro de auditoría: nada quedaba anotado (intentos rechazados, entregas, cambios de claves, ajustes).
- MEDIA. 30 registros seguidos desde el mismo origen sin ningún freno; el registro revelaba si un teléfono ya existía.
- BAJA. Sin política de seguridad de contenido (CSP). Sin HSTS.

Revisado y NO explotable (lo dejo asentado para no sembrar alarmas falsas):
- Redirección abierta con barra invertida: Flask la neutraliza al codificarla. Igual se endureció la validación.
- Doble toque secuencial en «Canjear»: solo duplicaba si el saldo alcanzaba para dos premios. Igual se agregó un identificador único por envío.
- Inyección SQL: todas las consultas usan parámetros; los nombres de columnas y de orden salen de listas fijas.
- XSS: las plantillas escapan todo; se probó con nombres que contienen scripts.

Errores míos encontrados por las nuevas pruebas y ya corregidos:
- Registro simultáneo del mismo teléfono: la base lo impedía pero la app devolvía error 500.
- La pantalla Ajustes de la plataforma fallaba por un nombre de variable renombrado.
- El filtro de Actividad por tipo fallaba cuando se elegía un solo tipo.

## 2. Qué se implementó

Reglas que hace cumplir la propia base (aunque el programa tuviera un error):
- El historial de puntos no se puede modificar ni borrar. No se aceptan saldos negativos ni movimientos de un negocio que no es el del cliente.
- Un canje ya resuelto no se puede reabrir. Las claves de idempotencia son únicas.

Motor único (`rf/motor.py`): toda suma de puntos, canje, entrega, cancelación y ajuste pasa por ahí, en una transacción que toma el candado de escritura al empezar.
- Visitas: una sola puerta (`_visita`) para NFC, QR en caja y pruebas. Misma elegibilidad, mismo cálculo, misma clave idempotente, misma auditoría.
- Reglas por negocio: horas mínimas entre visitas (1 a 720, por defecto 8), tope diario opcional (0 = sin tope), vencimiento de premios, puntos, sellos y premio de la tarjeta.
  Los puntos los calcula siempre el servidor con las reglas del negocio; el navegador no manda ningún valor.
- Canjes: código de 8 caracteres sin letras confundibles (40 bits, generado con `secrets`), vence (30 días por defecto), se entrega una sola vez, solo por el negocio dueño, y se puede cancelar con devolución auditada.
  Tras 10 códigos incorrectos se frena la validación 10 minutos. Un doble envío del mismo formulario canjea una sola vez.
- Ajustes manuales: solo el dueño o la plataforma, con motivo obligatorio, tope de 1000 puntos, sin dejar saldo negativo, con registro en el historial y en la auditoría.
- Historial (ledger): cada movimiento guarda negocio, cliente, tipo, puntos, origen (nfc, qr_caja, cliente, manual), quién lo hizo, clave idempotente, referencia y nota. El saldo siempre se deriva de la suma: no hay un saldo guardado que pueda desincronizarse.
- Roles: dueño (todo) y caja (solo confirmar visitas y entregar premios), con claves distintas.
- Auditoría y señales: tabla `auditoria` + una línea JSON por evento en los registros. Pantalla «Seguridad» en la plataforma con indicadores (no acusaciones).
- Sesiones nuevas en cada ingreso, token CSRF en todos los formularios, límite de intentos de ingreso, CSP, HSTS bajo HTTPS, `nosniff`, sin consola de depuración, los códigos de ingreso no se escriben en los registros en producción.
- Datos: migración automática que solo agrega (probada con una base vieja), respaldo descargable y por línea de comandos, control de integridad en Ajustes, `/salud` para el control de disponibilidad.

## 3. QR: qué se eligió y por qué

Se evaluaron dos caminos:
- A) El cliente muestra un código corto de 5 minutos y el personal lo confirma.
- B) El negocio muestra un QR rotativo y el cliente lo escanea.
En B cualquiera puede fotografiar la pantalla y compartirla mientras vale; un vencimiento corto no prueba que la persona esté en el local.
Se implementó A, porque la confirmación la hace una persona que ve al cliente. El motor está separado de la pantalla (`registrar_visita`), así que B se puede agregar sin tocar las reglas.

Flujo: el QR impreso (fijo, solo contiene `/l/<negocio>`) abre la tarjeta del negocio. Abrirla NO suma nada. El cliente toca «Registrar mi visita en caja», ve un código de 6 caracteres de 5 minutos (también como QR hacia la pantalla de caja), y el personal lo confirma. Recién ahí se suma, una sola vez.

NFC y QR comparten: negocio, reglas de elegibilidad, cálculo de puntos, clave idempotente, auditoría y pantalla del cliente. El cartel NFC firmado suma directamente porque el cartel está en el mostrador y la firma cambia en cada toque; el QR suma solo con confirmación humana.
Aclaración honesta: ningún QR prueba por sí mismo que alguien está en el local. Por eso el QR nunca suma solo.

## 4. Pruebas

102 pruebas automáticas (pytest): 102 pasan, 0 fallan. Más una corrida con 4 procesos de servidor reales (24 canjes simultáneos → 1 canje, saldo 0; 24 entregas simultáneas → 1 entrega; sin saldos negativos, sin errores).
Cubren: visitas, NFC simulado (válido, firma falsa, repetido, viejo, cartel desconocido o desactivado, de otro negocio), QR decodificado con OpenCV (apunta al negocio correcto, sin secretos, con zona de silencio), flujo de caja, canjes, cancelación, vencimiento, concurrencia con hilos, aislamiento entre negocios, CSRF, fuerza bruta, redirecciones, subidas de archivos, inyecciones, encabezados, configuración de producción, migración, respaldo e integridad.
Cómo correrlas: `pip install -r requirements-dev.txt` y luego `python -m pytest tests`.

Probado a mano con un navegador real (Chromium sin interfaz, sin errores de consola ni de CSP): paneles de plataforma y negocio, caja, QR, versión imprimible, tarjetas de cliente de Café Brasa y Helados Copo. No probado: tablet, Safari, Firefox, teléfonos reales.

## 5. Riesgos que quedan

- El teléfono no se verifica al registrarse: alguien puede registrar el número de otra persona. Se resuelve con el código por WhatsApp real (pendiente).
- Las claves de fábrica de los tags (todo ceros) permiten fabricar links válidos a quien las conozca. Hay que cambiarlas antes de usar carteles reales.
- Fraude interno: el personal puede confirmar visitas de amigos. Las reglas de intervalo, el registro y los indicadores lo limitan y lo hacen visible, no lo eliminan.
- Render gratis borra la base al reiniciar. Sin disco persistente no hay datos confiables.
- SQLite admite un solo servidor con un disco: sirve para decenas de negocios, no para escalar a varios servidores.
- Las claves foráneas no se pueden agregar a tablas existentes en SQLite; la integridad del historial se hace cumplir con reglas de la base y el resto con el programa.
- La sesión del cliente es una cookie de un año: si le roban el celular desbloqueado, tienen su cuenta (puede cerrar sesión desde la tarjeta).
- Los frenos por IP pueden afectar a una red compartida; los topes son holgados (60 registros por hora y conexión) a propósito.
- No hay borrado ni exportación de datos del cliente (privacidad). Como el historial no se puede borrar, corresponde ANONIMIZAR (reemplazar nombre y teléfono). Pendiente. No se evaluó el cumplimiento legal (Ley 25.326): requiere revisión profesional.
- La auditoría guarda la IP: falta definir cuánto tiempo se conserva.
- Probado con Python 3.12; Render usa 3.14 por defecto. Recomendado: fijar `PYTHON_VERSION` a una 3.12 en Render.

## 6. Qué cambia en el despliegue

- Variables nuevas u obligatorias en Render: `ADMIN_PASSWORD`, `SECRET_KEY` (sin ellas la app no arranca), `PUBLIC_BASE_URL` (la dirección definitiva, ANTES de imprimir QR), `SDM_META_KEY` y `SDM_FILE_KEY`.
- Dependencia nueva: `segno`. Ruta de control de disponibilidad: `/salud`.
- La migración de base de datos es automática y solo agrega. Hacé un respaldo antes de publicar (ver docs/OPERACION.md).
- ATENCIÓN con el retroceso: después de migrar, el código anterior ya no puede escribir puntos (la base exige el negocio en cada movimiento). Volver atrás significa restaurar el respaldo previo.

## 7. Clasificación final

Implementado y probado: todo lo de la sección 2, salvo lo indicado abajo.
Implementado pero no verificado del todo: visualización en navegadores distintos de Chromium; conducta bajo Python 3.14; carga con miles de clientes (se probó con decenas).
Recomendado y no implementado: anonimizar clientes, alertas por correo o mensaje, retención de la auditoría, QR rotativo del negocio (opción B), verificación del teléfono con WhatsApp real, claves propias por cartel (hoy comparten clave), migración a una base con varios servidores si el negocio crece.
No se pudo probar: lectura real del chip NFC con un celular, programación del chip NTAG 424 DNA (claves incluidas), escaneo del QR con cámaras de celulares reales, WhatsApp real, la publicación real en Render con estos cambios.

## 8. Cuando lleguen los chips

1. Programar un chip con el link firmado y las claves reales (no las de fábrica) y cargar las mismas en `SDM_META_KEY` y `SDM_FILE_KEY`.
2. Registrar su UID en el negocio y apoyar un iPhone y un Android: debe sumar una vez y mostrar el aviso.
3. Probar el mismo toque dos veces y un link copiado: no debe sumar de nuevo.
4. Probar con el celular bloqueado y en el navegador de cada marca (el NFC abre el navegador predeterminado).
5. Probar con el cartel pegado en el mostrador (algunos materiales metálicos bloquean la lectura).
