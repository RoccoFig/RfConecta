"""Punto de entrada. Render ejecuta `gunicorn app:app`."""
from rf import create_app
from rf.db import DB_PATH as DB          # automatizaciones.py usa estos dos nombres
from rf.mensajes import enviar_whatsapp  # noqa: F401

app = create_app()

if __name__ == "__main__":
    app.run(debug=True)
