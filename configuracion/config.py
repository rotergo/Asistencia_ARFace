import os
import platform

# --- RUTAS ---
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARCHIVO_CAMARAS = os.path.join(BASE_DIR, "camaras.json")
DB_OFFLINE = os.path.join(BASE_DIR, "buffer_asistencia_v2.db")

# --- SISTEMA OPERATIVO ---
if platform.system() == 'Windows':
    LIB_DIR_ORACLE = r"C:\Users\j.chacana\Documents\instantclient_19_29"
else:
    LIB_DIR_ORACLE = "/opt/oracle/instantclient"

# --- CONFIGURACIÓN ORACLE (Aquí está la variable que faltaba) ---
ORACLE_CFG = {
    "host": "192.168.16.15",
    "port": "1521",
    "sid": "testq",
    "user": "adempiere",
    "pass": "test123",
    "lib_dir": LIB_DIR_ORACLE
}

# --- SEGURIDAD ---
SECRET_KEY_HASH = "EMPRESA_SCAF_2026_SECRET_KEY_!@#" 

# --- CORREO ---
EMAIL_CFG = {
    "smtp_server": "smtp.gmail.com", 
    "smtp_port": 587,
    "sender": "j.chacana@geminis.cl",      
    "password": "bkzi jtht hdmk evpo"          
}