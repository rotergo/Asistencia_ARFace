from flask import Blueprint

# Creamos dos "planos" (Blueprints) para separar las vistas HTML de los datos JSON
web_bp = Blueprint('web_bp', __name__)
api_bp = Blueprint('api_bp', __name__)

from rutas_frontend import web, api