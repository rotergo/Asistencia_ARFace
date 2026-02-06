import threading
import time
from flask import Flask, send_from_directory

# Importamos nuestros módulos (Ya blindados y listos)
from configuracion.base_datos import inicializar_db_offline, obtener_conexion_oracle
from rutas_frontend import web_bp, api_bp
import modulos.camaras as camaras
import modulos.turnos as turnos
import modulos.biometria as biometria

# --- CONFIGURACIÓN APP FLASK ---
app = Flask(__name__, 
            template_folder='templates',
            static_folder='statics')

app.secret_key = 'clave_seguridad_dt_2026'

app.register_blueprint(web_bp)
app.register_blueprint(api_bp, url_prefix='/api')

# Ruta para servir fotos (necesaria para verlas en la web si no están encriptadas)
@app.route('/statics/fotos/<path:filename>')
def serve_fotos(filename):
    return send_from_directory('statics/fotos', filename)

# --- EL MOTOR EN SEGUNDO PLANO (CEREBRO DEL SISTEMA) ---
def motor_asistencia_background():
    print("🚀 [Motor] Iniciando ciclo de vigilancia...")
    camaras.cargar_configuracion()

    # --- ZONA DE INICIO (Se ejecuta solo 1 vez al arrancar) ---
    print("⏰ [Inicio] Sincronizando relojes con SHOA...")
    for cam in camaras.LISTA_CAMARAS:
        if cam.get('ip'):
            camaras.sincronizar_reloj_camara(cam)
    # ----------------------------------------------------------

    ciclo_count = 0 

    # --- ZONA DE BUCLE (Se repite infinitamente) ---
    while True:
        try:
            # 1. SINCRONIZACIÓN ORACLE (Buffer -> BD)
            turnos.sincronizar_con_oracle()

            # 2. VIGILANCIA DE FOTOS (Encriptación al vuelo)
            biometria.ejecutar_migracion_automatica()

            # 3. OFFBOARDING AUTOMÁTICO (Cada ~1 minuto / 30 ciclos) 🛡️
            if ciclo_count % 30 == 0:
                try:
                    conn = obtener_conexion_oracle()
                    if conn:
                        cur = conn.cursor()
                        # Busca usuarios inactivos que NO tengan otro contrato activo
                        sql = "SELECT DISTINCT RUT FROM ERPG_VTURNOS_PROGRAMADOS WHERE ISACTIVE = 'N' AND RUT NOT IN (SELECT RUT FROM ERPG_VTURNOS_PROGRAMADOS WHERE ISACTIVE = 'Y')"
                        cur.execute(sql)
                        inactivos = cur.fetchall()
                        conn.close()
                        
                        if inactivos:
                            print(f"🛡️ [Offboarding] Detectados {len(inactivos)} usuarios inactivos. Revocando accesos...")
                            for row in inactivos:
                                rut_baja = row[0]
                                for cam in camaras.LISTA_CAMARAS:
                                    if cam.get('ip'):
                                        camaras.eliminar_usuario_de_camara(cam, rut_baja)
                except Exception as ex_off:
                    print(f"⚠️ Error Offboarding: {ex_off}")

            # 4. SINCRONIZACIÓN RELOJ (Cada ~1 hora / 1800 ciclos) ⏰
            # Mantiene la hora legal del SHOA en los equipos
            if ciclo_count % 1800 == 0 and ciclo_count > 0:
                print("⏰ [Mantenimiento] Resincronizando hora de cámaras...")
                for cam in camaras.LISTA_CAMARAS:
                    if cam.get('ip'):
                        camaras.sincronizar_reloj_camara(cam)

            # 5. LECTURA DE CÁMARAS (Asistencia)
            for cam in camaras.LISTA_CAMARAS:
                if not cam.get('ip'): continue

                registros = camaras.descargar_logs_asistencia(cam)
                if registros:
                    nuevos = turnos.procesar_lecturas_camara(registros, cam)
                    if nuevos > 0:
                        print(f"📷 [Cámara {cam.get('nombre')}] {nuevos} marcas nuevas.")
            
            ciclo_count += 1

        except Exception as e:
            print(f"⚠️ [Motor Error] {e}")
        
        time.sleep(2) # Espera 2 segundos entre ciclos

if __name__ == '__main__':
    print(f"📂 [Cámaras] Cargando configuración...")
    
    # AUTO-PROTECCIÓN AL INICIO
    print("🔒 [Seguridad] Verificando fotos...")
    biometria.ejecutar_migracion_automatica()

    print("--- SISTEMA INICIANDO ---")
    inicializar_db_offline()
    
    # Iniciar el motor en un hilo separado
    hilo_motor = threading.Thread(target=motor_asistencia_background, daemon=True)
    hilo_motor.start()
    
    print("🌐 Servidor Web activo en http://0.0.0.0:5000")
    # Ejecutamos Flask modo clásico (estable)
    app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)