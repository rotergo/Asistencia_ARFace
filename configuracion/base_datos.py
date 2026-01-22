import oracledb
import sqlite3
from configuracion.config import ORACLE_CFG, DB_OFFLINE

# --- CONEXIÓN ORACLE (Principal) ---
def obtener_conexion_oracle():
    """Intenta conectar a Oracle. Retorna None si falla (modo offline)."""
    try:
        oracledb.init_oracle_client(lib_dir=ORACLE_CFG['lib_dir'])
    except:
        pass # Ya estaba iniciado
        
    try:
        dsn = oracledb.makedsn(ORACLE_CFG['host'], ORACLE_CFG['port'], sid=ORACLE_CFG['sid'])
        return oracledb.connect(user=ORACLE_CFG['user'], password=ORACLE_CFG['pass'], dsn=dsn)
    except Exception as e:
        print(f"⚠️ Oracle no disponible: {e}")
        return None

# --- CONEXIÓN SQLITE (Respaldo Legal) ---
def inicializar_db_offline():
    """
    Crea la tabla local si no existe. 
    CORREGIDO: Ahora coincide con la estructura de modulos/offline.py
    """
    try:
        conn = sqlite3.connect(DB_OFFLINE)
        cursor = conn.cursor()
        
        # Tabla idéntica a la que espera turnos.py (con columna 'enviado')
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
        print("✅ Base de Datos Offline (SQLite) lista y actualizada.")
    except Exception as e:
        print(f"❌ Error creando DB Local: {e}")

# (Opcional) Helper para conexiones rápidas si se requiere en este archivo
def obtener_conexion_local():
    return sqlite3.connect(DB_OFFLINE)