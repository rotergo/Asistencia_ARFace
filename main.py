import threading
import time
from flask import Flask

# Importamos nuestros módulos
from configuracion.base_datos import inicializar_db_offline
from rutas_frontend import web_bp, api_bp
import modulos.camaras as camaras
import modulos.turnos as turnos
import modulos.biometria as biometria

# --- CONFIGURACIÓN APP FLASK ---
app = Flask(__name__, 
            template_folder='templates',
            static_folder='statics')

app.register_blueprint(web_bp)
app.register_blueprint(api_bp, url_prefix='/api')

# --- EL MOTOR EN SEGUNDO PLANO ---
def motor_asistencia_background():
    print("🚀 [Motor] Iniciando ciclo de vigilancia...")
    camaras.cargar_configuracion()

    # --- ZONA DE INICIO (Se ejecuta solo 1 vez) ---
    print("⏰ Intentando sincronizar hora de cámaras...")
    for cam in camaras.LISTA_CAMARAS:
        if cam.get('ip'):
            camaras.sincronizar_reloj_camara(cam)
    # ----------------------------------------------

    # --- ZONA DE BUCLE (Se repite infinitamente) ---
    while True:
        try:
            # 1. SINCRONIZACIÓN ORACLE
            turnos.sincronizar_con_oracle()

            # 2. VIGILANCIA DE FOTOS (NUEVO: Aquí revisa siempre) 
            # Esto detectará cualquier .jpg nuevo y lo encriptará al instante
            biometria.ejecutar_migracion_automatica()

            # 2. LECTURA DE CÁMARAS
            for cam in camaras.LISTA_CAMARAS:
                if not cam.get('ip'): continue

                # (Aquí NO debe haber ninguna llamada a sincronizar_reloj_camara)

                # Descargar logs
                registros = camaras.descargar_logs_asistencia(cam)
                if registros:
                    nuevos = turnos.procesar_lecturas_camara(registros, cam)
                    if nuevos > 0:
                        print(f"📷 [Cámara {cam.get('nombre')}] {nuevos} marcas nuevas.")
            
        except Exception as e:
            print(f"⚠️ [Motor Error] {e}")
        
        time.sleep(5)

if __name__ == '__main__':
    print(f"📂 [Cámaras] {len(camaras.LISTA_CAMARAS)} dispositivos cargados.")
    
    # AUTO-PROTECCIÓN DE FOTOS AL INICIO
    print("🔒 [Seguridad] Verificando nuevas fotos...")
    biometria.ejecutar_migracion_automatica()

    print("--- SISTEMA INICIANDO ---")
    inicializar_db_offline()
    
    hilo_motor = threading.Thread(target=motor_asistencia_background, daemon=True)
    hilo_motor.start()
    
    print("🌐 Servidor Web activo en http://0.0.0.0:5000")
    app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)