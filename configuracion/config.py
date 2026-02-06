import os
import platform

# --- RUTAS ABSOLUTAS ---
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ARCHIVO_CAMARAS = os.path.join(BASE_DIR, "camaras.json")

# --- CAMBIO AQUÍ: Ponemos un nombre nuevo para forzar creación limpia ---
DB_OFFLINE = os.path.join(BASE_DIR, "buffer_asistencia_v2.db")
# -----------------------------------------------------------------------

# --- DETECCIÓN AUTOMÁTICA DE SISTEMA OPERATIVO ---
if platform.system() == 'Windows':
    # Tu ruta local de desarrollo
    LIB_DIR_ORACLE = r"C:\Users\j.chacana\Documents\instantclient_19_29"
else:
    # La ruta estándar dentro del contenedor Docker
    LIB_DIR_ORACLE = "/opt/oracle/instantclient"

# --- CONEXIÓN ORACLE ---
dsn = os.getenv('DB_HOST')
user = os.getenv('DB_USER')
password = os.getenv('DB_PASSWORD')

# --- SEGURIDAD ---
SECRET_KEY_HASH = "EMPRESA_SCAF_2026_SECRET_KEY_!@#" 

# --- CORREO ---
EMAIL_CFG = {
    "smtp_server": "smtp.gmail.com", 
    "smtp_port": 587,
    "sender": "j.chacana@geminis.cl",      
    "password": "bkzi jtht hdmk evpo"          
}