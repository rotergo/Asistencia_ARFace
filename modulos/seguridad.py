import hashlib

def generar_hash_fila(rut, nombre, fecha, e_am, s_am, e_pm, s_pm, estado, area):
    """
    Genera un Hash SHA-256 basado en TODOS los campos críticos de la fila.
    Si cambia una sola letra o número en cualquiera de estos campos, el hash cambia.
    """
    SECRET_SALT = "SCAF_2026_FULL_INTEGRITY_SALT_V2"
    
    # 1. Normalización estricta: Convertir todo a string, quitar espacios y manejar Nulos (None)
    # El orden aquí es SAGRADO. Debe ser el mismo al guardar y al auditar.
    datos = [
        str(rut or "").strip(),
        str(nombre or "").strip(),
        str(fecha or "").strip(),
        str(e_am or "").strip(),     # Entrada AM
        str(s_am or "").strip(),     # Salida AM
        str(e_pm or "").strip(),     # Entrada PM
        str(s_pm or "").strip(),     # Salida PM
        str(estado or "").strip(),   # Estado (ej: ATRASO)
        str(area or "").strip(),     # Área (ej: Bodega)
        SECRET_SALT                  # Nuestra clave secreta
    ]
    
    # 2. Unimos todo con un separador único (pipe |)
    # Ejemplo: "123-K|Juan|2026-01-26|08:30||||ATRASO|Bodega|SALT"
    raw_string = "|".join(datos)
    
    # 3. Generamos la firma
    return hashlib.sha256(raw_string.encode('utf-8')).hexdigest()