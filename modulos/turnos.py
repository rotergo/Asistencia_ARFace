from configuracion.base_datos import obtener_conexion_oracle
import modulos.offline as offline
from datetime import datetime, timedelta
import modulos.correos as correos
from modulos.seguridad import generar_hash_fila
from modulos.validaciones import validar_rut

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

        es_rut_valido, rut_resultado = validar_rut(uid)
        if es_rut_valido:
            rut_limpio = rut_resultado.replace("-", "")
        else:
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
            "rut": uid,
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
    FASE FINAL: CORRECCIÓN CASO 'ALEJANDRO' + LÓGICA DUAL
    - Detecta turno 00:00-23:59 como DÍA LIBRE.
    - En Día Libre: Fuerza estado 'TURNO EN DESCANSO' y detalle 'EXTRA'.
    - Elimina cálculos de 'ANTICIPADA' en días sin turno real.
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

            # 1. Recuperamos Estado Actual (Lo que ya marcó hoy)
            sql_estado_actual = """
                SELECT ID_SECUENCIA, ENTRADA_AM, SALIDA_AM, ENTRADA_PM, SALIDA_PM, ESTADO, AREA 
                FROM ERPG_PASO_CAMARA 
                WHERE ID_TRABAJADOR=:1 AND FECHA_DIA=TO_DATE(:2, 'YYYY-MM-DD')
            """
            cursor.execute(sql_estado_actual, [rut_insertar, fecha_dia_str])
            fila_est = cursor.fetchone()

            v_e_am = fila_est[1] if fila_est else None
            v_s_am = fila_est[2] if fila_est else None
            v_e_pm = fila_est[3] if fila_est else None
            v_s_pm = fila_est[4] if fila_est else None
            id_secuencia = fila_est[0] if fila_est else None

            # 2. Buscamos el TURNO PROGRAMADO
            sql_turno = """
                SELECT TURNO_INICIO_DESDE, TURNO_INICIO_HASTA, TURNO_FINAL_DESDE, TURNO_FINAL_HASTA, NOMBRE
                FROM ERPG_VTURNOS_PROGRAMADOS
                WHERE RUT = :1 
                  AND TRUNC(FECHA_INICIO_TURNO) <= TO_DATE(:2, 'YYYY-MM-DD')
                  AND TRUNC(FECHA_TERMINO_TURNO) >= TO_DATE(:3, 'YYYY-MM-DD')
            """
            turno = None
            try:
                cursor.execute(sql_turno, [rut_buscar, fecha_dia_str, fecha_dia_str])
                turno = cursor.fetchone()
                if not turno:
                    cursor.execute(sql_turno, [rut_insertar, fecha_dia_str, fecha_dia_str])
                    turno = cursor.fetchone()
            except: pass

            nombre_real = turno[4] if (turno and turno[4]) else item['nombre']
            
            columna_destino = None
            texto_detalle = "MARCAJE" 
            estado_global = "PENDIENTE"
            
            t_in_am, t_out_am, t_in_pm, t_out_pm = None, None, None, None

            if turno:
                t_in_am = parsear_fecha_oracle(turno[0])
                t_out_am = parsear_fecha_oracle(turno[1])
                t_in_pm = parsear_fecha_oracle(turno[2])
                t_out_pm = parsear_fecha_oracle(turno[3])
                
                def normalizar(dt_turno, dt_marca):
                    if not dt_turno: return None
                    return dt_turno.replace(year=dt_marca.year, month=dt_marca.month, day=dt_marca.day)

                t_in_am = normalizar(t_in_am, marca_dt)
                t_out_am = normalizar(t_out_am, marca_dt)
                t_in_pm = normalizar(t_in_pm, marca_dt)
                t_out_pm = normalizar(t_out_pm, marca_dt)

            # ==============================================================================
            # DETECCIÓN INTELIGENTE DE DÍA LIBRE (Aquí estaba el fallo)
            # ==============================================================================
            es_dia_libre = False
            
            if not turno:
                es_dia_libre = True
            
            # Caso "00:00 - 00:00"
            elif t_in_am and t_in_am.hour == 0 and t_in_am.minute == 0 and t_out_pm and t_out_pm.hour == 0:
                es_dia_libre = True
            
            # NUEVO: Caso "00:00 - 23:59" (El turno trampa de Alejandro)
            elif t_in_am and t_in_am.hour == 0 and t_in_am.minute == 0 and t_out_pm and t_out_pm.hour == 23 and t_out_pm.minute == 59:
                es_dia_libre = True

            # ==============================================================================
            # MOTOR DE DECISIÓN DUAL
            # ==============================================================================
            
            if es_dia_libre:
                # --- MODO DÍA LIBRE (Sin cálculos matemáticos) ---
                estado_global = "TURNO EN DESCANSO" # Requisito del usuario
                texto_detalle = "EXTRA"             # Requisito del usuario (Nada de "ANTICIPADA")
                
                if not v_e_am:
                    columna_destino = "ENTRADA_AM"
                elif not v_s_am and not v_e_pm and not v_s_pm:
                    # Regla de las 5 horas para decidir si es Salida Parcial o Final
                    dt_entrada = datetime.strptime(v_e_am, '%H:%M:%S')
                    dt_entrada = dt_entrada.replace(year=marca_dt.year, month=marca_dt.month, day=marca_dt.day)
                    
                    horas_trabajadas = (marca_dt - dt_entrada).total_seconds() / 3600
                    
                    if horas_trabajadas >= 5:
                        columna_destino = "SALIDA_PM" # Cierra el día (Caso Alejandro)
                    else:
                        columna_destino = "SALIDA_AM" # Mantiene abierto
                
                elif v_s_am and not v_e_pm:
                    columna_destino = "ENTRADA_PM"
                else:
                    columna_destino = "SALIDA_PM"

            else:
                # --- MODO DÍA LABORAL (Con cálculos) ---
                opciones = []
                if t_in_am: opciones.append(('ENTRADA_AM', abs((marca_dt - t_in_am).total_seconds())))
                if t_out_pm: opciones.append(('SALIDA_PM', abs((marca_dt - t_out_pm).total_seconds())))

                # Turno Corrido
                es_turno_corrido = False
                if t_out_am and t_out_am.hour == 0 and t_out_am.minute == 0:
                    es_turno_corrido = True
                
                if not es_turno_corrido:
                    if t_out_am: opciones.append(('SALIDA_AM', abs((marca_dt - t_out_am).total_seconds())))
                    if t_in_pm: opciones.append(('ENTRADA_PM', abs((marca_dt - t_in_pm).total_seconds())))

                if opciones:
                    opciones.sort(key=lambda x: x[1])
                    mejor_opcion = opciones[0][0]
                    
                    if mejor_opcion == "ENTRADA_AM" and v_e_am:
                        if not es_turno_corrido: columna_destino = "SALIDA_AM"
                        else: columna_destino = "SALIDA_PM"
                    else:
                        columna_destino = mejor_opcion

            # ==============================================================================
            # FILTRO ANTI-REBOTE
            # ==============================================================================
            ultima_columna_llena = None
            ultima_hora_registrada = None
            if v_s_pm: ultima_columna_llena="SALIDA_PM"; ultima_hora_registrada=v_s_pm
            elif v_e_pm: ultima_columna_llena="ENTRADA_PM"; ultima_hora_registrada=v_e_pm
            elif v_s_am: ultima_columna_llena="SALIDA_AM"; ultima_hora_registrada=v_s_am
            elif v_e_am: ultima_columna_llena="ENTRADA_AM"; ultima_hora_registrada=v_e_am

            if ultima_hora_registrada:
                dt_last = datetime.strptime(ultima_hora_registrada, '%H:%M:%S')
                dt_last = dt_last.replace(year=marca_dt.year, month=marca_dt.month, day=marca_dt.day)
                diff_min = abs((marca_dt - dt_last).total_seconds()) / 60
                
                if diff_min < 20:
                    print(f"🛡️ Anti-Rebote: {diff_min:.1f}m. Manteniendo {ultima_columna_llena}.")
                    columna_destino = ultima_columna_llena

            # ==============================================================================
            # CÁLCULO DE DIFERENCIAS (SOLO EN DÍA LABORAL)
            # ==============================================================================
            if columna_destino and not es_dia_libre and turno:
                horario_pactado = None
                if columna_destino == "ENTRADA_AM": horario_pactado = t_in_am
                elif columna_destino == "SALIDA_AM": horario_pactado = t_out_am
                elif columna_destino == "ENTRADA_PM": horario_pactado = t_in_pm
                elif columna_destino == "SALIDA_PM": horario_pactado = t_out_pm
                
                if horario_pactado and not (horario_pactado.hour == 0 and horario_pactado.minute == 0):
                    delta_min = (marca_dt - horario_pactado).total_seconds() / 60
                    TOLERANCIA = 5 

                    if "ENTRADA" in columna_destino:
                        if delta_min > TOLERANCIA: texto_detalle = f"ATRASO {int(delta_min)}m"
                        elif delta_min < -TOLERANCIA: texto_detalle = f"ADELANTO {int(abs(delta_min))}m"
                        else: texto_detalle = "A TIEMPO"
                    else: # SALIDAS
                        if delta_min > 0: texto_detalle = f"EXTRA {int(delta_min)}m"
                        elif delta_min < -TOLERANCIA: texto_detalle = f"ANTICIPADA {int(abs(delta_min))}m"
                        else: texto_detalle = "A TIEMPO"

            columna_detalle = MAPA_COLUMNAS_DETALLE.get(columna_destino, "ESTADO")
            
            # Cierre de ciclo normal (Solo si NO es día libre, el día libre tiene su propio estado)
            if not es_dia_libre and columna_destino == "SALIDA_PM" and "INCIDENCIA" not in estado_global:
                estado_global = "CERRADO"

            # ==============================================================================
            # GUARDADO
            # ==============================================================================
            if columna_destino:
                should_save = False
                
                def es_mejor_hora(hora_nueva, hora_actual, tipo):
                    if not hora_actual: return True
                    dt_new = datetime.strptime(hora_nueva, '%H:%M:%S')
                    dt_old = datetime.strptime(hora_actual, '%H:%M:%S')
                    if "ENTRADA" in tipo: return dt_new < dt_old 
                    return dt_new > dt_old 

                if columna_destino == "ENTRADA_AM":
                    if es_mejor_hora(hora_str, v_e_am, "ENTRADA"): v_e_am = hora_str; should_save = True
                elif columna_destino == "SALIDA_AM":
                    if es_mejor_hora(hora_str, v_s_am, "SALIDA"): v_s_am = hora_str; should_save = True
                elif columna_destino == "ENTRADA_PM":
                    if es_mejor_hora(hora_str, v_e_pm, "ENTRADA"): v_e_pm = hora_str; should_save = True
                elif columna_destino == "SALIDA_PM":
                    if es_mejor_hora(hora_str, v_s_pm, "SALIDA"): v_s_pm = hora_str; should_save = True

                if v_s_am and v_e_pm:
                    dt_out = datetime.strptime(v_s_am, '%H:%M:%S')
                    dt_in = datetime.strptime(v_e_pm, '%H:%M:%S')
                    if dt_out >= dt_in:
                        v_e_pm = None 
                        should_save = True

                if should_save or not fila_est:
                    nuevo_hash = generar_hash_fila(rut_insertar, nombre_real, fecha_dia_str, v_e_am, v_s_am, v_e_pm, v_s_pm, estado_global, area_evento)
                    
                    if fila_est:
                        sql_upd = f"UPDATE ERPG_PASO_CAMARA SET ENTRADA_AM=:1, SALIDA_AM=:2, ENTRADA_PM=:3, SALIDA_PM=:4, {columna_detalle}=:5, HASH_SHA256=:6, ESTADO=:7 WHERE ID_SECUENCIA=:8"
                        cursor.execute(sql_upd, [v_e_am, v_s_am, v_e_pm, v_s_pm, texto_detalle, nuevo_hash, estado_global, id_secuencia])
                        procesados += 1
                    else:
                        sql_ins = f"INSERT INTO ERPG_PASO_CAMARA (ID_SECUENCIA, ID_TRABAJADOR, NOMBRE_TRABAJADOR, FECHA_DIA, DIA_SEMANA, ENTRADA_AM, SALIDA_AM, ENTRADA_PM, SALIDA_PM, {columna_detalle}, ESTADO, AREA, HASH_SHA256) VALUES (SEQ_ERPG_PASO_CAMARA.NEXTVAL, :1, :2, TO_DATE(:3, 'YYYY-MM-DD'), TRIM(TO_CHAR(TO_DATE(:4, 'YYYY-MM-DD'), 'Day', 'NLS_DATE_LANGUAGE=SPANISH')), :5, :6, :7, :8, :9, :10, :11, :12)"
                        cursor.execute(sql_ins, [rut_insertar, nombre_real, fecha_dia_str, fecha_dia_str, v_e_am, v_s_am, v_e_pm, v_s_pm, texto_detalle, estado_global, area_evento, nuevo_hash])
                        procesados += 1

                    try:
                        sql_email = "SELECT EMAIL_TRABAJADOR FROM ERPG_VTURNOS_PROGRAMADOS WHERE RUT = :1"
                        cursor.execute(sql_email, [rut_buscar])
                        res = cursor.fetchone()
                        if res and res[0]: 
                            correos.enviar_comprobante(nombre_real, res[0], fecha_dia_str, hora_str, f"{columna_destino}: {texto_detalle}", area_evento)
                    except: pass
            
            offline.eliminar_registro(item['id'])

        conn.commit()
        if procesados > 0: print(f"🚀 [Oracle] {procesados} cambios sincronizados.")

    except Exception as e:
        print(f"❌ Error Sincronización: {e}")
        conn.rollback()
    finally:
        conn.close()


def calcular_estado_manual(rut, horas_dict):
    """
    Simula el proceso de decisión para una corrección manual.
    Devuelve el Estado y Detalle recalculados.
    """
    # 1. Obtener Turno desde Oracle para ese RUT y Fecha
    # ... (Logica de query de turno igual a sincronizar_con_oracle) ...
    
    # 2. Aplicar lógica de proximidad y tolerancia
    # ... (Si hora_manual_entrada - turno_entrada > 5 min -> ATRASO) ...
    
    # 3. Retornar diccionario
    return {
        "nombre": "Nombre Desde BD",
        "fecha": "2026-02-04",
        "dia_semana": "Miércoles",
        "estado": "CERRADO",
        "estado": "A TIEMPO" # O el resultado del cálculo real
    }

    # --- AGREGAR ESTO AL FINAL DE modulos/turnos.py --


# --- REEMPLAZAR LA FUNCIÓN AL FINAL DE modulos/turnos.py ---

def calcular_detalles_manuales(rut, fecha_str, horas_manuales):
    """
    Versión Final: Soporta Día Libre, Turno 24h y Debugging.
    """
    #print(f"\n🔍 [DEBUG] Iniciando cálculo manual para RUT: {rut}, Fecha: {fecha_str}")
    
    conn = obtener_conexion_oracle()
    if not conn: 
        print("❌ [DEBUG] Sin conexión Oracle")
        return {}
    
    cursor = conn.cursor()
    detalles = {
        'diff_e_am': None, 'diff_s_am': None, 
        'diff_e_pm': None, 'diff_s_pm': None
    }

    try:
        # 1. Buscar Turno Programado
        rut_limpio = rut.replace(".", "").replace("-", "").strip().upper()
        rut_con_guion = f"{rut_limpio[:-1]}-{rut_limpio[-1]}" if len(rut_limpio) > 1 else rut_limpio

        sql = """
            SELECT TURNO_INICIO_DESDE, TURNO_INICIO_HASTA, TURNO_FINAL_DESDE, TURNO_FINAL_HASTA
            FROM ERPG_VTURNOS_PROGRAMADOS
            WHERE (RUT = :1 OR RUT = :2)
              AND TRUNC(FECHA_INICIO_TURNO) <= TO_DATE(:3, 'YYYY-MM-DD')
              AND TRUNC(FECHA_TERMINO_TURNO) >= TO_DATE(:4, 'YYYY-MM-DD')
        """
        cursor.execute(sql, [rut_limpio, rut_con_guion, fecha_str, fecha_str])
        turno = cursor.fetchone()

        # 2. Detección de DÍA LIBRE / TURNO 24H
        es_dia_libre = False
        t_e_am, t_s_am, t_e_pm, t_s_pm = None, None, None, None

        if not turno:
            print("⚠️ [DEBUG] No se encontró turno (Asumiendo Día Libre)")
            es_dia_libre = True
        else:
            # Parsear horas del turno
            def parse_ora(h):
                if not h: return None
                try: return datetime.strptime(str(h).strip()[:16], '%d/%m/%Y %H:%M')
                except: return None

            t_e_am = parse_ora(turno[0])
            t_s_am = parse_ora(turno[1])
            t_e_pm = parse_ora(turno[2])
            t_s_pm = parse_ora(turno[3])

            #print(f"✅ [DEBUG] Turno encontrado: {t_e_am} - {t_s_pm}")

            # Detectar turno 00:00 - 23:59 (Caso Alejandro)
            if t_e_am and t_s_pm:
                if t_e_am.hour == 0 and t_e_am.minute == 0 and t_s_pm.hour == 23 and t_s_pm.minute == 59:
                    print("✨ [DEBUG] Detectado Turno Especial 24h -> Es Día Libre/Extra")
                    es_dia_libre = True
                # Detectar turno 00:00 - 00:00
                elif t_e_am.hour == 0 and t_e_am.minute == 0 and t_s_pm.hour == 0:
                    es_dia_libre = True

        # 3. Lógica de Cálculo
        def calcular_diff(hora_manual_str, hora_turno_obj, tipo):
            if not hora_manual_str: return None
            
            # Si es DÍA LIBRE, cualquier hora ingresada es EXTRA (o Marcaje)
            if es_dia_libre:
                # Usualmente la "Extra" se marca en la salida, o simplemente se etiqueta la marca
                return "EXTRA" 

            if not hora_turno_obj: return None # No hay contra qué comparar

            # Parseador flexible para la hora manual
            dt_manual = None
            h_clean = str(hora_manual_str).strip()
            try:
                dt_manual = datetime.strptime(f"{fecha_str} {h_clean}", '%Y-%m-%d %H:%M:%S')
            except ValueError:
                try:
                    if len(h_clean) > 5: h_clean = h_clean[:5]
                    dt_manual = datetime.strptime(f"{fecha_str} {h_clean}", '%Y-%m-%d %H:%M')
                except: return None
            
            # Ajustar fecha del turno a hoy
            dt_turno = hora_turno_obj.replace(year=dt_manual.year, month=dt_manual.month, day=dt_manual.day)
            
            delta_min = (dt_manual - dt_turno).total_seconds() / 60
            tolerancia = 5

            print(f"   [DEBUG] Comp {tipo}: Manual={dt_manual.time()} Turno={dt_turno.time()} Diff={delta_min}m")

            if tipo == 'ENTRADA':
                if delta_min > tolerancia: return f"ATRASO {int(delta_min)}m"
                elif delta_min < -tolerancia: return f"ADELANTO {int(abs(delta_min))}m"
                return "A TIEMPO"
            else: # SALIDA
                if delta_min > 0: return f"EXTRA {int(delta_min)}m"
                elif delta_min < -tolerancia: return f"ANTICIPADA {int(abs(delta_min))}m"
                return "A TIEMPO"

        # Aplicamos la matemática
        detalles['diff_e_am'] = calcular_diff(horas_manuales.get('entrada_am'), t_e_am, 'ENTRADA')
        detalles['diff_s_am'] = calcular_diff(horas_manuales.get('salida_am'), t_s_am, 'SALIDA')
        detalles['diff_e_pm'] = calcular_diff(horas_manuales.get('entrada_pm'), t_e_pm, 'ENTRADA')
        detalles['diff_s_pm'] = calcular_diff(horas_manuales.get('salida_pm'), t_s_pm, 'SALIDA')

    except Exception as e:
        print(f"❌ [DEBUG] Error calculando manual: {e}")
    finally:
        conn.close()
    
    #print(f"📤 [DEBUG] Resultado Cálculos: {detalles}")
    return detalles