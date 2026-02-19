# CRH · Búsqueda de Pacientes/Donantes (Demo)

Mini-prototipo en **Python + Flask + SQLite** para tu TFG:
- Base con **100 registros ficticios** (nombre, edad, tipo de sangre).
- Búsqueda por **nombre**, **tipo de sangre** y **rango de edad**.
- Vista tipo "panel" (alineada a la idea de panel administrativo CRH).

> ⚠️ Importante: NO uses datos reales de pacientes/donantes sin consentimiento y sin cumplir normativa de protección de datos.

## Requisitos
- Python 3.10+ recomendado

## Instalación (local)
```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Mac/Linux:
source .venv/bin/activate

pip install -r requirements.txt
python seed_db.py
python app.py
```

Abrí en el navegador:
- http://127.0.0.1:5000

## Deploy rápido (Render)
1. Subí este proyecto a GitHub.
2. En Render: **New > Web Service** y conectá el repo.
3. Build Command: `pip install -r requirements.txt`
4. Start Command: `python seed_db.py && gunicorn app:app`
5. Agregá `gunicorn` a `requirements.txt` si usás este start command.

Si preferís, podés usar Railway / Fly.io / PythonAnywhere.
