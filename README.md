# CRH Jujuy · Sistema de Gestión de Donantes

Aplicación **Python + Flask + SQLite** para el Centro Regional de Hemoteratoria
de Jujuy: portal de autogestión para donantes y panel administrativo para el
personal del centro.

> ⚠️ Todos los datos de esta demo son **ficticios**, generados con Faker.
> No cargues datos reales de pacientes/donantes sin consentimiento y sin
> cumplir la normativa de protección de datos vigente.

## Qué trae esta versión

- CRUD administrativo de campañas con publicación/borrador, fecha, horario,
  dirección y localidad.
- Estadísticas de donantes por localidad y grupo/factor sanguíneo.
- Constancias PDF descargables desde cada donación registrada.
- Preparación de mensajes individuales de WhatsApp filtrados por grupo y Rh.
- Ayuda contextual para estados del donante y sección pública informativa.
- Galería institucional con las tres fotografías proporcionadas.
- Reserva de turnos retirada; las consultas se canalizan directamente al CRH.
- Filtro administrativo por estado del donante.
- Constancia PDF con nombre, DNI, fecha y lugar de donación.
- Consultas públicas por WhatsApp dirigidas al +54 9 388 755-7004.
- Filtros de WhatsApp por nombre/DNI, zona, localidad, grupo y factor Rh.
- Preselección automática por edad, peso, embarazo, medicación declarada,
  tatuajes/piercings, intervalo mínimo y máximo anual de donaciones.

- **Diseño propio de punta a punta**: se crearon todas las plantillas
  (`templates/`) y la hoja de estilos (`static/css/estilo.css`), ya que el
  proyecto original no incluía ninguna vista. Paleta cálida vino/crema,
  tipografía Fraunces + Work Sans, tarjetas de estado clínico, insignias de
  color por estado (Apto / En espera / No apto) y diseño responsive.
- **200 donantes ficticios de Jujuy** generados combinando nombres,
  apellidos y localidades típicas de la zona (sin depender de Faker, que en
  la versión usada trae un bug en su generador de nombres para el locale
  argentino), distribuidos por localidad con pesos realistas (San Salvador de Jujuy,
  Palpalá, Perico, El Carmen, Libertador Gral. San Martín, Humahuaca,
  Tilcara, La Quiaca), con casos variados a propósito: donantes fuera de
  rango etario, con tatuajes/piercings recientes, con medicación, con
  decisiones médicas manuales y con historial de donaciones (algunos dentro
  de la ventana sanitaria y otros ya habilitados) para poder ver los tres
  estados clínicos funcionando en la demo.
- **Backend optimizado** (`app.py`):
  - Una sola conexión SQLite por request (vía `flask.g`) en lugar de abrir
    y cerrar la conexión en cada función.
  - Búsqueda y filtros reales en el panel administrativo (por nombre/DNI,
    grupo sanguíneo, localidad y rango de edad) con paginación — la
    funcionalidad que el README original mencionaba pero no estaba
    implementada.
  - Manejo de errores específico (ya no hay `except:` genéricos que ocultan
    fallas).
  - Credenciales de administrador configurables por variable de entorno en
    lugar de estar hardcodeadas.
  - Lógica de edad y elegibilidad clínica separada en funciones reutilizables.
- **Un solo generador de datos**: `seed_db.py` ahora reutiliza la lógica de
  `poblar_datos.py` en vez de duplicar el esquema y los datos de prueba.
- `requirements.txt` sin dependencias que no se usaban en el código
  (se quitó `psycopg`, ya que el proyecto usa SQLite) ni que daban problemas
  (se quitó `Faker`, ver punto anterior).

## Requisitos

- Python 3.10+ (probado con 3.11, ver `runtime.txt`)

## Instalación y uso local

```bash
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate

pip install -r requirements.txt

# Crea el esquema y carga 200 donantes ficticios + campañas de ejemplo
python seed_db.py

python app.py
```

Abrí en el navegador: http://127.0.0.1:5000

Para ejecutar las pruebas: `python -m unittest -v test_app.py`.

**Contraseña de demo** para cualquiera de los 200 donantes generados:
`donante123` (el usuario/email de cada uno está en la base — podés
consultarlos desde el panel administrativo).

### Generar una cantidad distinta de donantes

```bash
python poblar_datos.py --cantidad 300 --semilla 7
```

## Panel administrativo

URL: `/admin/login`

En desarrollo existe una cuenta de demostración. En producción la aplicación
no inicia si detecta las credenciales predeterminadas o una clave de sesión
insegura. Configurá las variables de entorno antes de desplegar:

```bash
export ADMIN_USER="tu_usuario"
export ADMIN_PASSWORD="una_clave_segura"
export FLASK_SECRET_KEY="una_clave_larga_y_aleatoria"
export APP_ENV="production"
```

## Deploy (Render / Railway / Fly.io)

### Persistencia obligatoria en Render

SQLite debe ubicarse en un disco persistente. El proyecto incluye `render.yaml`
con un disco montado en `/var/data` y `DB_PATH=/var/data/patients.db`. Si el
servicio ya fue creado manualmente, configurá esos dos elementos en Render o
los registros y campañas se perderán al reiniciar o desplegar. Un disco
persistente puede requerir un plan pago; como alternativa de producción debe
migrarse la base a PostgreSQL.

1. Subí el proyecto a GitHub.
2. **Build Command:** `pip install -r requirements.txt`
3. Para una demo nueva y vacía, ejecutá `python seed_db.py` una sola vez.
   Nunca agregues la siembra de datos al comando de inicio de producción.
4. **Start Command:** `gunicorn --workers 2 --threads 4 --timeout 60 app:app`
5. Configurá las variables de entorno `ADMIN_USER`, `ADMIN_PASSWORD` y
   `FLASK_SECRET_KEY` desde el panel del proveedor.

Si la base ya existe, el generador se detiene para proteger los datos. Solo en
una demo descartable puede forzarse el reemplazo con `--confirmar-borrado`.

> Antes de usar datos reales, validá las reglas clínicas, la política de
> privacidad, los permisos por rol y la infraestructura con las autoridades
> sanitarias y legales correspondientes.

## Estructura del proyecto

```
app.py              # Rutas, lógica clínica y conexión a la base
main.py             # Entrypoint WSGI (from app import app)
poblar_datos.py     # Genera 200 donantes ficticios + historial + campañas
seed_db.py          # Punto de entrada de siembra para despliegues
templates/          # Vistas Jinja2 (landing, registro, login, portal, admin)
static/css/estilo.css  # Sistema de diseño (paleta, tipografía, componentes)
patients.db         # Base SQLite (se genera/actualiza al poblar datos)
```
