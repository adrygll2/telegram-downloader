# Despliegue en Raspberry Pi 4 B

Objetivo: Raspberry Pi OS de 64 bits, Docker Compose y `linux/arm64`. El Compose de prueba publica la web en el puerto 8080 para convivir con el servicio `systemd` actual durante la validacion. El backend y PostgreSQL solo son accesibles dentro de la red de Compose.

## Preparar almacenamiento

Antes de arrancar Compose, comprobar que el disco USB esta montado. No arrancar con los valores de ejemplo si `/mnt/usb1` no existe: Docker podria crear un directorio local y llenar la tarjeta SD.

Crear directorios persistentes en el USB, manteniendo intacta la biblioteca actual:

```bash
sudo mkdir -p /mnt/usb1/tmd-downloads /mnt/usb1/tmd-state
sudo chown -R "$(id -u USUARIO):$(id -g USUARIO)" /mnt/usb1/tmd-downloads /mnt/usb1/tmd-state
```

Configurar el `.env` con rutas absolutas:

```dotenv
DOCKER_PLATFORM=linux/arm64
WEB_PORT=8080
HOST_SESSION_DIR=/home/USUARIO/telegram-movie-downloader/session
HOST_DOWNLOAD_DIR=/mnt/usb1/tmd-downloads
HOST_MEDIA_DIR=/mnt/usb1/torrent-complete
HOST_STATE_DIR=/mnt/usb1/tmd-state
PUID=1000
PGID=1000
TMD_TRANSMISSION_HOST_PATH=/mnt/usb1/torrent-complete
TMD_TRANSMISSION_DOWNLOAD_DIR=/mnt/usb1/torrent-complete
```

`HOST_MEDIA_DIR` debe apuntar a la biblioteca existente. `HOST_SESSION_DIR` debe apuntar a la sesion Telethon persistente. No usar la misma ruta de sesion simultaneamente desde el servicio antiguo y el contenedor.

El directorio de sesión contiene un archivo SQLite por cuenta (`user.session` para Principal y otro archivo por cada cuenta secundaria). Respaldar el directorio completo; nunca copiar una sesión en uso ni arrancar dos procesos Telethon con el mismo archivo.

Definir `PUID` y `PGID` con los valores de `id -u USUARIO` e `id -g USUARIO`. El backend corre como ese usuario para conservar propiedad y permisos de los archivos montados. La base PostgreSQL queda en un volumen Docker persistente independiente; respaldarla con `pg_dump`.

## Secretos

Copiar `.env.example` a `.env`, completar credenciales y generar JWT y contrasenas unicas:

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(48))'
```

Usar un resultado para `TMD_JWT_SECRET` y contrasenas distintas para `TMD_ADMIN_PASSWORD` y `POSTGRES_PASSWORD`. Proteger el archivo:

```bash
chmod 600 .env
```

`TMD_API_ID`, `TMD_API_HASH` y `TMD_PHONE` son obligatorios. Los canales pueden cargarse inicialmente con `TMD_CHANNELS` o desde la administracion web.

## Transmission del host

Si Transmission sigue ejecutandose directamente en la Raspberry, usar `TMD_TRANSMISSION_HOST=host.docker.internal`; Compose lo resuelve hacia el host mediante `host-gateway`. Transmission debe aceptar conexiones desde la red Docker y la lista blanca RPC debe permitir la subred de Docker. Mantener RPC autenticado y configurar `TMD_TRANSMISSION_USER` y `TMD_TRANSMISSION_PASS` cuando corresponda. `TMD_TRANSMISSION_HOST_PATH` es la ruta que usa el daemon en el host; `TMD_TRANSMISSION_DOWNLOAD_DIR` es la misma ruta visible dentro del contenedor. El montaje de Compose conserva esa ruta para que el backend pueda publicar en la biblioteca los archivos terminados sin sacarlos de Transmission (y sin romper el seeding).

## Arranque de prueba

```bash
docker compose up --build -d
docker compose ps
docker compose logs -f backend
```

Abrir `http://IP-DE-LA-RASPBERRY:8080`. Verificar login, canales, una descarga pequeña, extraccion, reproduccion y los servicios de torrents antes del cambio definitivo. La primera indexacion de Telegram y TMDB puede continuar en segundo plano.

## Cambio desde systemd

No detener el servicio actual hasta completar la prueba. Para el corte, respaldar sesion, estado de pausas, `.env` y la base de datos; detener el backend antiguo para liberar la sesion y, cuando se haya validado la ruta de produccion, cambiar `WEB_PORT` a `80`.

```bash
sudo systemctl stop telegram-movie
sudo systemctl disable telegram-movie
docker compose up --build -d
```

Docker tiene `restart: unless-stopped`; con Docker habilitado al arranque, Compose mantiene la aplicacion tras reiniciar la Pi. No borrar el servicio ni sus archivos antiguos hasta verificar el nuevo stack y su restauracion desde respaldo. Para revertir, parar Compose antes de reactivar systemd y no permitir que dos instancias usen la misma sesion.

## Conversion de video

La Pi 4 B tiene RAM suficiente para los servicios, pero la transcodificacion puede saturar sus cuatro nucleos y provocar throttling termico. Empezar con `TMD_CONVERT_DTS=false`, probar la conversion con un archivo real y medir uso de CPU, temperatura y tiempo antes de decidir si dejarla activa.

## Copias de seguridad

La biblioteca y la sesion estan en bind mounts. Respaldar PostgreSQL con `pg_dump` y conservar una copia del `.env` en un medio cifrado. Probar la restauracion, no solo la creacion del archivo de copia.
