# CRH Jujuy — versión 2.6, feedback 17 de octubre de 2026

## Cambios incluidos

- Ver ficha: administración puede editar peso, embarazo, medicación, fecha de último tatuaje o piercing, grupo sanguíneo y factor Rh.
- Fecha única de tatuaje/piercing. En bases anteriores se conserva la más reciente de ambas fechas. Se mantiene el plazo de un año calendario solicitado; 29 de febrero pasa al 28 de febrero siguiente.
- Estados visibles: Apto Temporal, No Apto Temporal, Apto Clínico, No Apto Clínico y En espera. La opción «Sin decisión clínica» significa usar la preselección temporal y no es un sexto estado.
- Apto Clínico y No Apto Clínico reemplazan las antiguas decisiones manuales. Una aprobación clínica no elimina la espera mínima de 60 días ni el máximo anual que ya tenía el proyecto.
- Medicación se conserva como antecedente que requiere evaluación profesional. No se aprueba automáticamente por declarar medicación: el feedback proponía alternativas y esta versión conserva el criterio anterior hasta su definición por el CRH.
- Cambiar antecedentes retira una aprobación clínica anterior para revisión. No Apto Clínico se mantiene hasta una nueva decisión del profesional.
- Registro con apellido y nombres separados, solo letras y espacios; DNI de 7 u 8 cifras; peso entero entre 50 y 140 kg; teléfono de 10 cifras (código de área sin 0 y número sin 15). Validación en servidor y controles de formulario. Los 50 kg se admiten al registrar, aunque la regla anterior de preselección exige más de 50: el registro no confirma aptitud.
- DNI y correo duplicados muestran aviso. Los correos se comparan sin distinguir mayúsculas, también en el ingreso.
- Los nombres anteriores no se separan automáticamente para evitar inferir mal los apellidos. El nombre completo existente se conserva; al editar el perfil por primera vez, la persona debe separar sus campos.
- Extracciones: totales por mes (año-mes), grupo y factor, campaña/lugar y localidad del donante. Cada extracción nueva conserva grupo, factor y localidad en ese momento. Historial antiguo sin esos campos usa la ficha actual y el lugar original; no se inventan asociaciones a campañas.
- Campañas con horarios a elegir: 08:00 a 12:00 y 14:00 a 18:00.
- WhatsApp: campaña activa filtra por su localidad; «Ninguna» muestra aptos temporales y clínicos de todas las localidades, respetando los demás filtros. No aptos y En espera quedan excluidos. Seleccionar una campaña inactiva o inexistente devuelve error, sin ampliar destinatarios.
- Portal del donante: cartel de atención de 7 a 14 hs sin turno y dirección provista en el feedback.
- Registrar extracción: Centro Regional, campañas activas u Otro con detalle obligatorio. Si se elige campaña, su fecha debe coincidir con la de extracción.
- Correo informativo: al crear publicada o pasar de borrador a publicada se encolan avisos para la misma localidad con estados Apto Temporal, No Apto Temporal o Apto Clínico, conforme al feedback. El mensaje aclara que no confirma aptitud. No se envía a No Apto Clínico ni En espera.

## Prioridad: evitar que desaparezcan registros

No podemos confirmar la causa en el servicio alojado sin revisar su configuración. Los síntomas son compatibles con SQLite en almacenamiento temporal o una siembra repetida. Esta versión:

1. No genera los 200 donantes al iniciar ni incluye una base precargada.
2. Migra el esquema sin borrar donantes, contraseñas, campañas ni donaciones.
3. Deshabilita los generadores de datos ficticios en producción, incluso con la opción de reemplazo.
4. En Render exige que /var/data sea un punto de montaje y que DB_PATH esté dentro de ese disco. Si falta, detiene el inicio con un error claro en vez de aceptar registros en almacenamiento temporal.
5. Incluye render.yaml con disco persistente y zona horaria de Jujuy.

El archivo render.yaml no configura por sí solo un servicio que ya fue creado manualmente. En ese caso hay que asociar el disco en Render y revisar las variables y comandos existentes. Un disco persistente puede requerir plan pago. No ejecutar seed_db.py ni poblar_datos.py en Build Command ni Start Command.

Los registros que ya desaparecieron no se reconstruyen con esta actualización. Requieren recuperar la base o un respaldo anterior. No corresponde cambiar contraseñas para solucionar una cuenta cuya fila fue eliminada.

## Actualizar un servicio existente

1. Antes de desplegar, conservar una copia consistente de la base actual con `python backup_db.py /ruta/segura/respaldo-antes-2-6.db`. Usar la misma variable DB_PATH del servicio. Descargar esa copia fuera del servidor.
2. Conservar FLASK_SECRET_KEY y las credenciales administrativas actuales. Cambiar la clave de sesión invalida sesiones y enlaces de recuperación, aunque no cambia contraseñas de donantes.
3. Verificar disco montado en `/var/data` y `DB_PATH=/var/data/patients.db`. Si la base actual está en otra ruta, copiarla consistentemente con backup_db.py al destino persistente antes de iniciar esta versión; no crear una demo encima. La herramienta no sobrescribe destinos existentes.
4. Instalar con `pip install -r requirements.txt`.
5. Iniciar con `gunicorn --workers 2 --threads 4 --timeout 60 app:app`.
6. Probar alta de una cuenta de prueba, ingreso, edición y persistencia después de reiniciar el servicio y después de un despliegue. Comprobar que sigue el mismo número de donantes y su historial. Esta comprobación del servicio es indispensable y no se ha ejecutado desde aquí.
7. Mantener respaldos periódicos externos al disco del servidor.

## Activar correos automáticos

Configurar SMTP_HOST, SMTP_PORT (587), SMTP_USER, SMTP_PASSWORD y SMTP_FROM con el proveedor institucional. La contraseña se configura como secreto en el servidor, nunca en GitHub.

Programar `flask --app app enviar-avisos` cada pocos minutos en el mismo servicio/host, con acceso al mismo DB_PATH y sus variables. El trabajador requiere Linux/Unix y usa un bloqueo de archivo para no solapar ejecuciones. En Render, un servicio separado no comparte automáticamente el disco del servicio web: hay que definir el mecanismo de ejecución dentro del host existente o adaptar el trabajador a una base compartida. Sin SMTP o sin la tarea programada, los avisos quedan en cola y no se envían.

Los destinatarios se vuelven a revisar al enviar. La cola no duplica campañas/donantes al republicar y los fallos SMTP quedan pendientes para reintento. Si el proceso se interrumpe justo después de que SMTP acepta un correo y antes de guardarlo como enviado, un reintento puede duplicarlo. Para grandes volúmenes o garantías más fuertes, integrar un proveedor con idempotencia.

No se han enviado correos ni WhatsApp durante esta actualización.

## Validación realizada y pendiente

Ejecutadas 10 pruebas sin dependencias web sobre funciones reales extraídas del código: migración repetible con conservación de contraseñas/antecedentes, validaciones, espera de 60 días con aprobación clínica, bloqueo clínico, antecedentes, horarios, cola local sin duplicación, filtros WhatsApp, estadísticas por extracción y conservación al reabrir SQLite. Todas pasan. También se comprobó compilación de Python.

Las dependencias Flask/Jinja2 no están disponibles en el entorno de elaboración y su instalación quedó bloqueada por el acceso a PyPI. Por ello no se ejecutaron las pruebas HTTP de `test_app.py`, el render visual ni una prueba completa del servidor. Antes de publicar ejecutar `python -m unittest -v test_app.py test_feedback_offline.py` con las dependencias instaladas y revisar registro, edición y paneles en navegador.

Los criterios de preselección conservan las reglas del proyecto anterior y el plazo solicitado en el feedback; esta actualización no constituye validación sanitaria. La decisión de retirar medicación o cambiar criterios debe definirla el equipo profesional del CRH.
