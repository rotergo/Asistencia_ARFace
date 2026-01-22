# 1. Usamos una imagen base de Python ligera (Linux Debian)
FROM python:3.10-slim

# 2. Evitamos que Python genere archivos .pyc y buffer de salida
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# 3. Instalamos dependencias del sistema necesarias para Oracle (libaio)
RUN apt-get update && apt-get install -y \
    libaio1 \
    unzip \
    wget \
    && rm -rf /var/lib/apt/lists/*

# 4. Configurar Zona Horaria (Crítico para SHOA y Logs)
ENV TZ="America/Santiago"

# 5. Preparar el Oracle Instant Client
WORKDIR /opt/oracle
# Copiamos el zip que descargaste en el Paso 1 (asegúrate que el nombre coincida)
COPY instantclient-basic-linux.x64-*.zip .
# Descomprimimos y renombramos la carpeta para que sea fácil de encontrar
RUN unzip instantclient-basic-linux.x64-*.zip && \
    mv instantclient_19_* instantclient && \
    rm instantclient-basic-linux.x64-*.zip

# 6. Configurar variables de entorno para que Python encuentre a Oracle
ENV LD_LIBRARY_PATH=/opt/oracle/instantclient:$LD_LIBRARY_PATH
ENV ORACLE_HOME=/opt/oracle/instantclient

# 7. Preparar la Aplicación
WORKDIR /app
COPY requerimientos.txt .
RUN pip install --no-cache-dir -r requerimientos.txt

# 8. Copiar el código fuente
COPY . .

# 9. Crear volumen para datos persistentes (BD Local y Config)
VOLUME ["/app/config_data"]

# 10. Exponer el puerto web
EXPOSE 5000

# 11. Ejecutar el orquestador
CMD ["python", "main.py"]