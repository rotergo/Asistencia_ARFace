import sqlite3
import os
from datetime import datetime

DB_NAME = "buffer_asistencia_v2.db"

def conectar():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def inicializar_db():
    conn = conectar()
    # Creamos la tabla si no existe
    conn.execute("""
        CREATE TABLE IF NOT EXISTS marcas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uid TEXT,
            nombre TEXT,
            timestamp TEXT,
            area TEXT,
            enviado INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()

def guardar_en_buffer(uid, nombre, timestamp, area):
    try:
        conn = conectar()
        # Verificar si ya existe (Evitar duplicados exactos en buffer)
        cursor = conn.execute("SELECT id FROM marcas WHERE uid=? AND timestamp=?", (uid, timestamp))
        if cursor.fetchone():
            conn.close()
            return False
            
        conn.execute("INSERT INTO marcas (uid, nombre, timestamp, area, enviado) VALUES (?, ?, ?, ?, 0)", 
                     (uid, nombre, timestamp, area))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"⚠️ Error Buffer: {e}")
        return False

def obtener_pendientes():
    conn = conectar()
    # Traemos TODO lo que haya (FIFO: El más antiguo primero)
    filas = conn.execute("SELECT * FROM marcas ORDER BY id ASC LIMIT 50").fetchall()
    conn.close()
    return [dict(f) for f in filas]

# --- NUEVA FUNCIÓN: ELIMINAR FÍSICAMENTE ---
def eliminar_registro(db_id):
    """ Borra el registro del archivo SQLite para que no ocupe espacio """
    try:
        conn = conectar()
        conn.execute("DELETE FROM marcas WHERE id = ?", (db_id,))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"⚠️ Error limpiando buffer: {e}")

inicializar_db()