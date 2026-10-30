"""Revisa que saldos, canjes e historial sean coherentes. Termina con código 1 si encuentra problemas.

    python tools/verificar_integridad.py
"""
import os, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
os.environ.setdefault("ADMIN_PASSWORD", "solo-lectura")
from rf import create_app, motor  # noqa: E402

app = create_app()
with app.app_context():
    problemas = motor.verificar_integridad()
print("Integridad correcta." if not problemas else "PROBLEMAS:\n- " + "\n- ".join(problemas))
sys.exit(1 if problemas else 0)
