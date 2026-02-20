from configuracion.base_datos import obtener_conexion_oracle
import modulos.offline as offline
from datetime import datetime, timedelta
import modulos.correos as correos
from modulos.seguridad import generar_hash_fila
from modulos.validaciones import validar_rut

# --- CACHÉS ---
CACHE_NOMBRES_REALES = {}
CACHE_ULTIMO_PASO = {}  
BUFFER_VISUAL = []  
CACHE_PROCESADOS_RAM = set()
CACHE_UBICACION = {} 

# Configuración Anti-Rebote
TIEMPO_ESPERA_SEGUNDOS = 30 

def inicializar_cache_nombres():
    """ Carga nombres para que el log se vea bonito en consola/web """
    print("🧠 [Cache] Cargando matriz de trabajadores...")
    conn = obtener_conexion_oracle()
    if not conn: return
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT RUT, NOMBRE FROM ERPG_VTURNOS_PROGRAMADOS")
        for row in cursor:
            if row[0] and row[1]:
                rut_limpio = str(row[0]).replace(".", "").replace("-", "").strip()
                CACHE_NOMBRES_REALES[rut_limpio] = row[1]
        print(f"🧠 [Cache] {len(CACHE_NOMBRES_REALES)} trabajadores identificados.")
    except Exception as e:
        print(f"⚠️ Error Cache Nombres: {e}")
    finally:
        conn.close()

# Ejecutamos carga inicial
inicializar_cache_nombres()

def procesar_lecturas_camara(registros, cam_info):
    """ 
    FASE 1: FILTRO DE 30 SEGUNDOS Y BUFFER
    """
    area = cam_info.get('area', 'General')
    nuevos_count = 0

    for reg in registros:
        uid = str(reg.get('userid', '')).strip()
        timestamp_str = str(reg.get('timestamp', ''))[:19] 
        nombre_camara = reg.get('name', 'Desconocido')

        if not uid or uid == '0': continue

        # --- CANDADO DE MEMORIA ---
        id_evento = f"{uid}_{timestamp_str}"
        if id_evento in CACHE_PROCESADOS_RAM:
            continue 
        
        CACHE_PROCESADOS_RAM.add(id_evento)
        CACHE_UBICACION[uid] = area 
        # ----------------------------------------------------

        # 1. Validación RUT
        es_rut_valido, rut_resultado = validar_rut(uid)
        if es_rut_valido:
            rut_limpio = rut_resultado.replace("-", "")
        else:
            rut_limpio = uid.replace(".", "").replace("-", "").strip()

        nombre_visual = CACHE_NOMBRES_REALES.get(rut_limpio, nombre_camara)
        
        # --- ALIMENTAR EL DASHBOARD EN VIVO ---
        hora_solo = timestamp_str.split(" ")[1] if " " in timestamp_str else "00:00"
        BUFFER_VISUAL.insert(0, {
            "id": id_evento,
            "hora": hora_solo,
            "nombre": nombre_visual,
            "rut": uid,
            "area": area,
            "dispositivo": cam_info.get('nombre'),
            "estado": "Marcaje"
        })
        if len(BUFFER_VISUAL) > 50: BUFFER_VISUAL.pop()
        # --------------------------------------

        # 2. LÓGICA ANTI-REBOTE (30 SEGUNDOS)
        try:
            fecha_actual = datetime.strptime(timestamp_str, '%Y-%m-%d %H:%M:%S')
            
            if rut_limpio in CACHE_ULTIMO_PASO:
                ultima_vez = CACHE_ULTIMO_PASO[rut_limpio]
                diferencia = (fecha_actual - ultima_vez).total_seconds()
                
                if 0 <= diferencia < TIEMPO_ESPERA_SEGUNDOS:
                    continue 
            
            CACHE_ULTIMO_PASO[rut_limpio] = fecha_actual
            
        except Exception as e:
            print(f"⚠️ Error procesando fecha rebote: {e}")
            continue

        # 3. GUARDAR EN BUFFER (SQLite)
        guardado = offline.guardar_en_buffer(uid, nombre_visual, timestamp_str, area)
        if guardado:
            nuevos_count += 1
            print(f"📥 [Buffer] Recibido: {nombre_visual} @ {timestamp_str}")

    return nuevos_count

def sincronizar_con_oracle():
    """ 
    FASE 2: INYECCIÓN TRANSACCIONAL (LOG PURO)
    """
    pendientes = offline.obtener_pendientes()
    if not pendientes: return

    conn = obtener_conexion_oracle()
    if not conn: return 
    
    cursor = conn.cursor()
    procesados = 0

    try:
        for item in pendientes:
            raw_uid = item['uid']
            ts_str = item['timestamp']
            area_evento = item['area']
            nombre_buffer = item['nombre'] 
            
            # Preparar Datos
            rut_limpio = raw_uid.replace(".", "").replace("-", "").strip().upper()
            rut_insertar = f"{rut_limpio[:-1]}-{rut_limpio[-1]}" if len(rut_limpio) > 1 else rut_limpio
            
            # Separar Fecha y Hora
            dt_obj = datetime.strptime(ts_str, '%Y-%m-%d %H:%M:%S')
            fecha_dia = dt_obj.strftime('%Y-%m-%d')
            hora_marca = dt_obj.strftime('%H:%M:%S')

            hash_seguridad = generar_hash_fila(
                rut=rut_insertar,
                nombre=nombre_buffer,
                fecha=fecha_dia,
                e_am=hora_marca,   
                s_am="LOG",        
                e_pm="-",          
                s_pm="-",          
                estado="OK",
                area=area_evento
            )

            # --- INSERT PURO ---
            sql_ins = """
                INSERT INTO ERPG_PASO_CAMARA (
                    ID_SECUENCIA, 
                    ID_TRABAJADOR, 
                    NOMBRE_TRABAJADOR, 
                    FECHA_DIA, 
                    DIA_SEMANA, 
                    HORA_MARCA, 
                    AREA, 
                    HASH_SHA256
                ) VALUES (
                    SEQ_ERPG_PASO_CAMARA.NEXTVAL, 
                    :1, :2, 
                    TO_DATE(:3, 'YYYY-MM-DD'), 
                    TRIM(TO_CHAR(TO_DATE(:4, 'YYYY-MM-DD'), 'Day', 'NLS_DATE_LANGUAGE=SPANISH')), 
                    :5, :6, :7
                )
            """
            
            cursor.execute(sql_ins, [
                rut_insertar, 
                nombre_buffer, 
                fecha_dia, 
                fecha_dia, 
                hora_marca, 
                area_evento, 
                hash_seguridad
            ])
            
            procesados += 1
            
            # Limpiar Buffer Local
            offline.eliminar_registro(item['id'])

        conn.commit()
        if procesados > 0: print(f"🚀 [Oracle] {procesados} registros insertados.")

    except Exception as e:
        print(f"❌ Error Sincronización Transactional: {e}")
        conn.rollback()
    finally:
        conn.close()

def calcular_detalles_manuales(rut, fecha_str, horas_manuales):
    return {}