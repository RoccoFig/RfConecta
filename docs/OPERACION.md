# RF — operación: respaldos, publicación, retroceso y emergencias

## Antes de publicar cualquier cambio
1. Descargar un respaldo: /plataforma/ajustes → «Descargar respaldo» (o `python tools/respaldar.py`). Guardarlo fuera de Render.
2. Correr las pruebas en tu compu: `python -m pytest tests`. Si algo falla, no publicar.
3. En Render → Environment: confirmar ADMIN_PASSWORD, SECRET_KEY, PUBLIC_BASE_URL, SDM_META_KEY, SDM_FILE_KEY, DB_PATH=/var/data/puntos.db.
4. Render → Settings → Health Check Path: /salud.
5. Plan de pago con disco persistente montado en /var/data. Sin eso, los datos se pierden al reiniciar.
6. Publicar y mirar los registros del arranque: no debe haber errores.
7. Verificar: /salud responde {"ok": true}; /plataforma/ajustes no muestra puntos en «Revisar» que no esperes; /l/<negocio> abre.

## Lista de control para el primer negocio real
- Datos de prueba separados: probar en un negocio con nombre de prueba, no en el real.
- Clave de dueño y clave de caja distintas y largas.
- PUBLIC_BASE_URL definitiva ANTES de imprimir los QR.
- Reglas del negocio revisadas en Ajustes (intervalo, tope diario, vencimiento).
- Claves de los chips cambiadas respecto de las de fábrica.
- Un respaldo descargado el mismo día.

## Respaldos y recuperación
- Copia: `python tools/respaldar.py respaldos 14` (conserva las últimas 14, verifica cada copia).
- Restaurar: 1) pausar el servicio en Render, 2) reemplazar el archivo de DB_PATH por la copia, 3) iniciar el servicio, 4) correr `python tools/verificar_integridad.py`.
- Un respaldo contiene datos personales: guardalo en un lugar privado y borralo cuando ya no haga falta.

## Retroceso (rollback) de una publicación
- Si el problema es solo de código y la base no cambió: en Render, volver a publicar el commit anterior.
- Si la publicación migró la base (agregó columnas o reglas): el código viejo ya no puede escribir puntos. Hay que volver al commit anterior Y restaurar el respaldo hecho antes de publicar.
- Por eso el respaldo previo es obligatorio.

## Si un cartel NFC se pierde, se copia o se sospecha comprometido
1. /plataforma/carteles → «Desactivar» ese cartel: deja de sumar de inmediato.
2. Registrar el cartel de reemplazo con su UID.
3. Si hay sospecha de que se conocen las claves de los chips: generar claves nuevas, reprogramar todos los chips y cambiar SDM_META_KEY y SDM_FILE_KEY en Render (los links viejos dejan de valer).
4. Revisar /plataforma/seguridad: escaneos inválidos, repetidos o de cartel desconocido.

## Si se sospecha que se filtró una clave de dueño, de caja o de la plataforma
1. Cambiar la clave en /plataforma/negocios/<negocio>?tab=configuracion (o ADMIN_PASSWORD y SECRET_KEY en Render; cambiar SECRET_KEY cierra todas las sesiones).
2. Revisar /plataforma/seguridad: ingresos fallidos, ajustes manuales y canjes entregados recientes.
3. Si hubo ajustes indebidos: corregirlos con un ajuste inverso (queda en el historial; nada se borra).

## Si se sospecha de fraude de un cliente o de un empleado
- /plataforma/seguridad muestra señales (clientes con muchas visitas rechazadas, ajustes manuales). Son indicios para mirar, no pruebas.
- Para anular un premio: Canjes → Cancelar (devuelve los puntos y queda auditado).
- Para pausar un negocio entero: ficha del negocio → Configuración → Suspender programa.

## Controles periódicos (sugerido: semanal)
- Ajustes → «Integridad de los datos» debe decir Correcto.
- Seguridad → revisar los indicadores de la última semana.
- Descargar un respaldo.
- Actualizar dependencias (`pip list --outdated`) y volver a correr las pruebas antes de publicar.
