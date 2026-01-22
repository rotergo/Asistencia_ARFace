from flask import jsonify, request
from rutas_frontend import api_bp
from configuracion.base_datos import obtener_conexion_oracle
import modulos.camaras as camaras_mod
import modulos.turnos as turnos

# --- DASHBOARD EN VIVO ---
@api_bp.route('/dashboard/live')
def dashboard_live():
    area_filter = request.args.get('area', 'todas')
    lista_completa = list(turnos.BUFFER_VISUAL)
    
    if area_filter != 'todas':
        registros_filtrados = [r for r in lista_completa if r.get('area') == area_filter]
    else:
        registros_filtrados = lista_completa

    presentes = 0
    total_plantilla = 0 
    
    conn = obtener_conexion_oracle()
    if conn:
        try:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(DISTINCT RUT) FROM ERPG_VTURNOS_PROGRAMADOS WHERE ISACTIVE='Y' AND FECHA_TERMINO_TURNO >= TRUNC(SYSDATE)")
            res = cur.fetchone()
            if res: total_plantilla = res[0]
            conn.close()
        except: pass

    if area_filter == 'todas':
        presentes = len(turnos.CACHE_UBICACION)
    else:
        presentes = sum(1 for a in turnos.CACHE_UBICACION.values() if a == area_filter)

    ausentes = total_plantilla - presentes
    if ausentes < 0: ausentes = 0

    return jsonify({
        "registros": registros_filtrados,
        "stats": {"presentes": presentes, "total": total_plantilla, "ausentes": ausentes}
    })

# --- TRABAJADORES ---
@api_bp.route('/workers/list')
def list_workers():
    workers = []
    conn = obtener_conexion_oracle()
    if conn:
        try:
            cur = conn.cursor()
            sql = """
            SELECT DISTINCT RUT, NOMBRE, ISACTIVE 
            FROM ERPG_VTURNOS_PROGRAMADOS 
            WHERE FECHA_TERMINO_TURNO >= TRUNC(SYSDATE) - 30 
            ORDER BY NOMBRE
            """
            cur.execute(sql)
            for row in cur:
                workers.append({"id": str(row[0]).strip(), "nombre": str(row[1]).strip(), "activo": str(row[2]).strip()})
            conn.close()
        except: pass
    return jsonify(workers)

# --- NUEVO: SINCRONIZACIÓN (LA RUTA QUE FALTABA) ---
# rutas_frontend/api.py

@api_bp.route('/sync/execute', methods=['POST'])
def sync_execute():
    data = request.json
    usuarios = data.get('users', [])
    camaras_ids = data.get('cameras', []) 

    log_resultados = []

    # --- CORRECCIÓN 1: Convertir todo a String para comparar ---
    # Esto soluciona que el bucle no corra
    ids_seleccionados = [str(id_cam) for id_cam in camaras_ids]
    camaras_destino = [c for c in camaras_mod.LISTA_CAMARAS if str(c['id']) in ids_seleccionados]
    # -----------------------------------------------------------

    if not camaras_destino:
        return jsonify({"status": "error", "logs": ["⚠️ Error: No se encontraron cámaras seleccionadas (ID Mismatch)."]})

    if not usuarios:
        return jsonify({"status": "error", "logs": ["⚠️ Error: No hay usuarios seleccionados."]})

    for cam in camaras_destino:
        for user in usuarios:
            # Enviamos a la cámara
            resultado = camaras_mod.enviar_usuario_a_camara(cam, user['id'], user['name'])
            
            estado_visual = "✅" if "OK" in resultado else "❌"
            log_resultados.append(f"{estado_visual} {user['name']} -> {cam.get('nombre')}: {resultado}")

    return jsonify({"status": "ok", "logs": log_resultados})

# --- GESTIÓN DE DISPOSITIVOS ---
@api_bp.route('/devices/list_simple')
def list_devices_simple():
    return jsonify(camaras_mod.LISTA_CAMARAS)

@api_bp.route('/devices/save', methods=['POST'])
def save_device():
    data = request.json
    if 'port' in data: data['puerto'] = data['port']
    camaras_mod.guardar_camara(data)
    return jsonify({"status": "ok"})

@api_bp.route('/devices/delete/<int:id>', methods=['DELETE'])
def delete_device(id):
    camaras_mod.eliminar_camara(id)
    return jsonify({"status": "ok"})

@api_bp.route('/devices/test', methods=['POST'])
def test_device():
    data = request.json
    puerto = data.get('puerto', data.get('port', 80))
    exito = camaras_mod.login_camara(data['ip'], puerto, data['user'], data['pass'])
    if exito: return jsonify({"status": "ok"})
    return jsonify({"status": "error", "message": "Fallo autenticación"})

# --- REPORTES ---
@api_bp.route('/reports/search', methods=['POST'])
def reports_search():
    data = request.json
    f_inicio = data.get('start')
    f_fin = data.get('end')
    resultados = []
    conn = obtener_conexion_oracle()
    if conn:
        try:
            cur = conn.cursor()
            sql = """
                SELECT ID_TRABAJADOR, NOMBRE_TRABAJADOR, TO_CHAR(FECHA_DIA, 'YYYY-MM-DD'), 
                       DIA_SEMANA, ENTRADA_AM, SALIDA_AM, ENTRADA_PM, SALIDA_PM, ESTADO
                FROM ERPG_PASO_CAMARA
                WHERE FECHA_DIA BETWEEN TO_DATE(:1, 'YYYY-MM-DD') AND TO_DATE(:2, 'YYYY-MM-DD')
                ORDER BY FECHA_DIA DESC, NOMBRE_TRABAJADOR ASC
            """
            cur.execute(sql, [f_inicio, f_fin])
            for row in cur:
                resultados.append({
                    "id": row[0], "nombre": row[1], "fecha": row[2], "dia": row[3],
                    "e_am": row[4] or "-", "s_am": row[5] or "-",
                    "e_pm": row[6] or "-", "s_pm": row[7] or "-", "estado": row[8]
                })
            conn.close()
        except Exception as e: return jsonify({"error": str(e)}), 500
    return jsonify(resultados)

# --- HEALTH CHECK ---
@api_bp.route('/status/health')
def status_health():
    # 1. Verificar Oracle
    oracle_ok = False
    try:
        conn = obtener_conexion_oracle()
        if conn: 
            oracle_ok = True
            conn.close()
    except: pass
        
    # 2. Verificar Cámaras
    active_cams = 0
    
    # --- ¡ESTA ES LA LÍNEA QUE FALTABA! ---
    total_cams = len(camaras_mod.LISTA_CAMARAS) 
    # ---------------------------------------

    for cam in camaras_mod.LISTA_CAMARAS:
        puerto = cam.get('puerto', cam.get('port', 80))
        if camaras_mod.login_camara(cam['ip'], puerto, cam['user'], cam['pass']):
            active_cams += 1

    return jsonify({
        "oracle": oracle_ok, 
        "cameras": (active_cams > 0) if total_cams > 0 else False,
        "active_count": active_cams,
        "total_cams": total_cams 
    })