import oracledb
import sqlite3
import os
import sys
from configuracion.config import ORACLE_CFG, DB_OFFLINE

# --- CONEXIÓN ORACLE (Principal) ---
def obtener_conexion_oracle():
    """
    Intenta conectar a Oracle forzando el modo THICK (Cliente Instantáneo).
    Necesario para Oracle 11g.
    """
    # 1. Intentar inicializar las librerías de Oracle (Thick Mode)
    try:
        oracledb.init_oracle_client(lib_dir=ORACLE_CFG['lib_dir'])
    except oracledb.DatabaseError as e:
        err, = e.args
        # Si el error es "Oracle Client library has already been initialized", lo ignoramos.
        # Cualquier otro error (ej. falta libaio) lo mostramos.
        if err.code != 1005: 
            print(f"❌ [ERROR CRÍTICO] Falló carga de Drivers Oracle: {e}")
            print(f"   Ruta configurada: {ORACLE_CFG['lib_dir']}")
    except Exception as e:
        print(f"❌ [ERROR CRÍTICO] Falló carga de Drivers Oracle: {e}")

    # 2. Conectar
    try:
        dsn = oracledb.makedsn(ORACLE_CFG['host'], ORACLE_CFG['port'], sid=ORACLE_CFG['sid'])
        return oracledb.connect(user=ORACLE_CFG['user'], password=ORACLE_CFG['pass'], dsn=dsn)
    except Exception as e:
        print(f"⚠️ Oracle no disponible: {e}")
        return None

# --- CONEXIÓN SQLITE (Respaldo) ---
def inicializar_db_offline():
    try:
        conn = sqlite3.connect(DB_OFFLINE)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS BUFFER_ASISTENCIA (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                uid TEXT,
                nombre TEXT,
                timestamp TEXT,
                area TEXT,
                enviado INTEGER DEFAULT 0 
            )
        ''')
        conn.commit()
        conn.close()
        print("✅ Base de Datos Offline (SQLite) lista.")
    except Exception as e:
        print(f"❌ Error creando DB Local: {e}")

def obtener_conexion_local():
    return sqlite3.connect(DB_OFFLINE)