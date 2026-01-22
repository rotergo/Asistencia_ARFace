from configuracion.base_datos import obtener_conexion_oracle
import modulos.offline as offline
from datetime import datetime, timedelta
import modulos.correos as correos
from modulos.seguridad import generar_hash_asistencia

# --- CACHÉS ---
CACHE_PROCESADOS_RAM = set()
CACHE_NOMBRES_REALES = {}
BUFFER_VISUAL = []      
CACHE_UBICACION = {}    

def inicializar_cache_nombres():
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
        print(f"⚠️ Error Cache: {e}")
    finally:
        conn.close()

inicializar_cache_nombres()

def procesar_lecturas_camara(registros, cam_info):
    """ FASE 1: INGESTIÓN """
    area = cam_info.get('area', 'General')
    nuevos_count = 0

    for reg in registros:
        uid = str(reg.get('userid', '')).strip()
        timestamp = str(reg.get('timestamp', ''))[:19]
        nombre_camara = reg.get('name', 'Desconocido')

        if not uid or uid == '0': continue

        # Normalización
        rut_limpio = uid.replace(".", "").replace("-", "").strip()
        nombre_visual = CACHE_NOMBRES_REALES.get(rut_limpio, nombre_camara)

        id_evento = f"{uid}_{timestamp}"
        if id_evento in CACHE_PROCESADOS_RAM: continue
        CACHE_PROCESADOS_RAM.add(id_evento)
        
        CACHE_UBICACION[uid] = area
        
        hora_solo = timestamp.split(" ")[1] if " " in timestamp else "00:00"
        BUFFER_VISUAL.insert(0, {
            "id": id_evento,
            "hora": hora_solo,
            "nombre": nombre_visual,
            "area": area,
            "dispositivo": cam_info.get('nombre'),
            "estado": "Marcaje"
        })
        if len(BUFFER_VISUAL) > 50: BUFFER_VISUAL.pop()
        
        guardado = offline.guardar_en_buffer(uid, nombre_visual, timestamp, area)
        if guardado:
            nuevos_count += 1
            print(f"📥 [Buffer] Recibido: {nombre_visual} @ {timestamp}")

    return nuevos_count

def parsear_fecha_oracle(fecha_str):
    if not fecha_str: return None
    try:
        clean_str = fecha_str.strip()[:16] 
        return datetime.strptime(clean_str, '%d/%m/%Y %H:%M')
    except:
        return None

def sincronizar_con_oracle():
    """ 
    FASE 2: LÓGICA RESOLUCIÓN 38 (CORRECCIÓN ORA-01400)
    Se inserta explícitamente el ID usando la secuencia.
    """
    pendientes = offline.obtener_pendientes()
    if not pendientes: return

    conn = obtener_conexion_oracle()
    if not conn: return 
    cursor = conn.cursor()
    procesados = 0

    MAPA_COLUMNAS_DETALLE = {
        "ENTRADA_AM": "DIFF_ENT_AM",
        "SALIDA_AM":  "DIFF_SAL_AM",
        "ENTRADA_PM": "DIFF_ENT_PM",
        "SALIDA_PM":  "DIFF_SAL_PM"
    }

    try:
        for item in pendientes:
            raw_uid = item['uid']
            ts_str = item['timestamp']
            area_evento = item['area']
            
            # Preparar Datos
            rut_limpio = raw_uid.replace(".", "").replace("-", "").strip().upper()
            rut_insertar = f"{rut_limpio[:-1]}-{rut_limpio[-1]}" if len(rut_limpio) > 1 else rut_limpio
            rut_buscar = rut_limpio 

            marca_dt = datetime.strptime(ts_str, '%Y-%m-%d %H:%M:%S')
            fecha_dia_str = marca_dt.strftime('%Y-%m-%d')
            hora_str = marca_dt.strftime('%H:%M:%S')

            # Buscar Turno
            sql_turno = """
                SELECT 
                    TURNO_INICIO_DESDE, TURNO_INICIO_HASTA,
                    TURNO_FINAL_DESDE, TURNO_FINAL_HASTA,
                    NOMBRE
                FROM ERPG_VTURNOS_PROGRAMADOS
                WHERE RUT = :1 AND FECHA_INICIO_TURNO = TO_DATE(:2, 'YYYY-MM-DD')
            """
            
            turno = None
            try:
                cursor.execute(sql_turno, [rut_buscar, fecha_dia_str])
                turno = cursor.fetchone()
                if not turno:
                    cursor.execute(sql_turno, [rut_insertar, fecha_dia_str])
                    turno = cursor.fetchone()
            except Exception as e:
                print(f"⚠️ Error SQL Turno: {e}")

            nombre_real = turno[4] if (turno and turno[4]) else item['nombre']
            
            columna_destino = None
            columna_detalle = None
            texto_detalle = ""
            estado_global = "PENDIENTE"
            
            # --- MOTOR DE DECISIÓN ---
            if turno:
                t_entrada_am = parsear_fecha_oracle(turno[0])
                t_salida_am  = parsear_fecha_oracle(turno[1])
                t_entrada_pm = parsear_fecha_oracle(turno[2])
                t_salida_pm  = parsear_fecha_oracle(turno[3])

                # DÍA LIBRE
                es_dia_libre = False
                if t_entrada_am and t_entrada_am.hour == 0 and t_entrada_am.minute == 0:
                     if t_salida_pm and t_salida_pm.hour == 0 and t_salida_pm.minute == 0:
                         es_dia_libre = True

                if es_dia_libre:
                    print(f"⚠️ {nombre_real}: DÍA LIBRE TRABAJADO.")
                    h = marca_dt.hour
                    columna_destino = "ENTRADA_AM" if h < 12 else "ENTRADA_PM"
                    texto_detalle = "DIA LIBRE TRAB"
                    estado_global = "INCIDENCIA"
                
                else:
                    diferencias = []
                    if t_entrada_am: diferencias.append(('ENTRADA_AM', abs((marca_dt - t_entrada_am).total_seconds()), t_entrada_am))
                    if t_salida_am:  diferencias.append(('SALIDA_AM',  abs((marca_dt - t_salida_am).total_seconds()), t_salida_am))
                    if t_entrada_pm: diferencias.append(('ENTRADA_PM', abs((marca_dt - t_entrada_pm).total_seconds()), t_entrada_pm))
                    if t_salida_pm:  diferencias.append(('SALIDA_PM',  abs((marca_dt - t_salida_pm).total_seconds()), t_salida_pm))

                    if diferencias:
                        diferencias.sort(key=lambda x: x[1])
                        mejor_opcion = diferencias[0] 
                        
                        tipo_marca = mejor_opcion[0]     
                        diff_segundos = mejor_opcion[1]  
                        horario_programado = mejor_opcion[2] 

                        if diff_segundos < 14400: # 4 horas max diff
                            columna_destino = tipo_marca
                            
                            delta_real = (marca_dt - horario_programado).total_seconds() / 60 
                            TOLERANCIA = 5 

                            if "ENTRADA" in tipo_marca:
                                if delta_real > TOLERANCIA:
                                    texto_detalle = f"ATRASO {int(delta_real)}m"
                                elif delta_real < -TOLERANCIA: 
                                    texto_detalle = f"ADELANTO {int(abs(delta_real))}m"
                                else:
                                    texto_detalle = "A TIEMPO" 
                            
                            elif "SALIDA" in tipo_marca:
                                if delta_real > 0:
                                    texto_detalle = f"EXTRA {int(delta_real)}m"
                                elif delta_real < -TOLERANCIA:
                                    texto_detalle = f"ANTICIPADA {int(abs(delta_real))}m"
                                else:
                                    texto_detalle = "A TIEMPO"

            # FALLBACK
            if not columna_destino:
                h = marca_dt.hour
                if h < 12: columna_destino = "ENTRADA_AM"
                elif 12 <= h < 15: columna_destino = "SALIDA_AM" if marca_dt.minute < 30 else "ENTRADA_PM"
                else: columna_destino = "SALIDA_PM"
                
                texto_detalle = "FUERA HORARIO" if turno else "SIN TURNO"
                estado_global = "INCIDENCIA"

            columna_detalle = MAPA_COLUMNAS_DETALLE.get(columna_destino, "ESTADO")
            
            if columna_destino == "SALIDA_PM" and estado_global != "INCIDENCIA":
                estado_global = "CERRADO"

            # --- 4. GUARDADO (CON CORRECCIÓN DE ID) ---
            if columna_destino:
                sql_check = f"SELECT COUNT(*) FROM ERPG_PASO_CAMARA WHERE ID_TRABAJADOR=:1 AND FECHA_DIA=TO_DATE(:2,'YYYY-MM-DD') AND {columna_destino} IS NOT NULL"
                cursor.execute(sql_check, [rut_insertar, fecha_dia_str])
                
                if cursor.fetchone()[0] == 0:
                    firma_sha256 = generar_hash_asistencia(rut_insertar, fecha_dia_str, hora_str, columna_destino, area_evento)
                    
                    # ¡AQUÍ ESTÁ LA SOLUCIÓN AL ERROR ORA-01400!
                    # Agregamos ID_SECUENCIA y usamos SEQ_ERPG_PASO_CAMARA.NEXTVAL
                    sql_ins = f"""
                        INSERT INTO ERPG_PASO_CAMARA 
                        (ID_SECUENCIA, ID_TRABAJADOR, NOMBRE_TRABAJADOR, FECHA_DIA, DIA_SEMANA, ESTADO, AREA, 
                         {columna_destino}, {columna_detalle}, HASH_SHA256)
                        VALUES (SEQ_ERPG_PASO_CAMARA.NEXTVAL, :1, :2, TO_DATE(:3, 'YYYY-MM-DD'), 
                                TRIM(TO_CHAR(TO_DATE(:4, 'YYYY-MM-DD'), 'Day', 'NLS_DATE_LANGUAGE=SPANISH')), 
                                :5, :6, :7, :8, :9)
                    """
                    cursor.execute(sql_ins, [
                        rut_insertar,   # :1
                        nombre_real,    # :2
                        fecha_dia_str,  # :3
                        fecha_dia_str,  # :4
                        estado_global,  # :5
                        area_evento,    # :6
                        hora_str,       # :7
                        texto_detalle,  # :8
                        firma_sha256    # :9
                    ])
                    
                    try:
                        sql_email = "SELECT EMAIL_TRABAJADOR FROM ERPG_VTURNOS_PROGRAMADOS WHERE RUT = :1"
                        cursor.execute(sql_email, [rut_buscar])
                        res = cursor.fetchone()
                        if res and res[0]: 
                            correos.enviar_comprobante(nombre_real, res[0], fecha_dia_str, hora_str, f"{columna_destino}: {texto_detalle}", area_evento)
                    except: pass
                    
                    print(f"✅ [Oracle] {nombre_real}: {columna_destino} ({texto_detalle})")
                    procesados += 1
                else:
                    print(f"🛡️ [Oracle] Omitido: Ya existe {columna_destino}")
            
            offline.eliminar_registro(item['id'])

        conn.commit()
        if procesados > 0: print(f"🚀 [Oracle] {procesados} registros detallados guardados.")

    except Exception as e:
        print(f"❌ Error Sincronización: {e}")
        conn.rollback()
    finally:
        conn.close()