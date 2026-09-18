"""
Punto de entrada de siembra para despliegues (Render, Railway, etc.).

Crea el esquema y carga 200 donantes ficticios de Jujuy reutilizando
la lógica de poblar_datos.py, para no mantener dos generadores de datos
distintos.

Uso:
    python seed_db.py
"""
from poblar_datos import poblar_sistema

if __name__ == "__main__":
    poblar_sistema(cantidad=200, semilla=2026, confirmar_borrado=False)
