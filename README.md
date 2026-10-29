# Telegram Movie Downloader v2

Para desplegar en Raspberry Pi 4 B de 64 bits con Docker Compose, consulta [`DEPLOY-RASPBERRY.md`](DEPLOY-RASPBERRY.md). El stack publica la web en el puerto 8080 por defecto para permitir validarlo junto al servicio existente.

Un servidor web autoalojado para buscar y descargar películas y series desde canales de Telegram. Diseñado para Raspberry Pi, funciona en cualquier Linux.

## Dónde se usa

| Cliente | Ruta / paquete | Para qué |
|---|---|---|
| Web de escritorio | `http://servidor/` | Todo, incluido el **panel de administración** (cuentas y cuotas, canales, ajustes, registros) |
| PWA de móvil | `http://servidor/m/` (los teléfonos se redirigen solos) | Buscar, descargar, ver y gestionar; instalable en iPhone y Android; también con panel de administración |
| Samsung TV (Tizen) | `tizen/` → `MoviesChill.wgt` | Ver desde el sofá con el mando |
| LG TV (webOS) | `webos/` → `MoviesChill.ipk` | Lo mismo, mismo código |

**Cuentas**: el administrador da de alta cuentas con una **cuota de disco**. Lo que descarga una cuenta lo ven todas, pero **solo su dueño (o un admin) puede borrarlo**, y nadie puede volver a descargar lo mismo. Las descargas se convierten a **MP4** para que se vean en cualquier dispositivo, iPhone incluido.

## Características

- **Catálogo multiusuario** — cuentas, cuotas, favoritos, progreso de reproducción y permisos por propietario
- **Biblioteca enriquecida** — índice de canales, fichas TMDB, búsqueda y vistas de películas/series
- **Clientes web y TV** — web de escritorio, PWA móvil, Tizen, webOS, Fire TV y PlayStation
- **Fuentes torrent** — Nyaa, 1337x, AnimeTosho y control de Transmission para administradores
- **Varias cuentas Telegram** — cada canal se asigna a una sesión Telegram; el catálogo sigue siendo compartido por la aplicación
- **Búsqueda multicanal** — busca en varios canales de Telegram a la vez
- **Varias cuentas Telegram** — asocia cada canal a la cuenta de servicio que tiene acceso
- **Descarga paralela** — hasta 5 descargas simultáneas configurables
- **Extracción automática** — RAR, ZIP, 7z, TAR (incluyendo `.part1.rar`, `.zip.001`, `.7z.001` y `_zip.###`)
- **Conversión de audio** — Detecta DTS y convierte a AC3 automáticamente (requiere `ffmpeg` + `mediainfo`)
- **Nombrado inteligente** — Las carpetas se nombran por serie y temporada (`Cape Fear S1`, `Rick y Morty S2`)
- **Pausa y reanudación** — Pausa descargas y reanúdalas más tarde, incluso tras reiniciar el servidor
- **Streaming de vídeo** — Reproduce archivos `.mkv`, `.mp4` directamente desde el navegador o VLC en iPhone
- **Interfaz web** — Panel responsive con tema oscuro, búsqueda, progreso en tiempo real, gestión de archivos
- **Modo desarrollador** — Página de logs en tiempo real vía `journalctl`
- **Configuración web** — Página `/settings` para editar toda la configuración desde el navegador
- **Soporte `.env`** — Variables de entorno para secretos y overrides
- **Paginación real** — Búsqueda paginada contra Telegram, sin límite de resultados
- **Filtro por canal** — Chips de selección de canal en los resultados de búsqueda
- **Gestión visual de canales** — Página `/channels` que lista todos tus canales/grupos de Telegram y permite seleccionarlos
- **Tailscale Funnel** — Acceso remoto desde internet sin abrir puertos

---

## Requisitos e instalación

- Raspberry Pi 4 B de 64 bits (ARM64 recomendado), Docker Engine y Docker Compose v2.24 o posterior.
- API ID y API Hash de [my.telegram.org/apps](https://my.telegram.org/apps).
- Disco USB montado de forma estable para la biblioteca y el estado persistente.
- Transmission RPC en el host si se van a usar las fuentes torrent.

El despliegue de Raspberry Pi y la migración desde `systemd` están documentados en [`DEPLOY-RASPBERRY.md`](DEPLOY-RASPBERRY.md). Resumen:

```bash
cp .env.example .env
# Editar secretos, usuario de host y rutas persistentes en .env.
docker compose up --build -d
```

La web se publica en `http://<ip-raspberry>:8080` por defecto para permitir pruebas junto al servicio antiguo. El backend y PostgreSQL no publican puertos al host. Para iniciar sesión de Telegram, usar la pestaña Telegram del panel de administración; el contenedor no necesita consola interactiva.

La configuración vive en `.env` y en PostgreSQL. `TMD_JWT_SECRET`, `TMD_ADMIN_PASSWORD` y `POSTGRES_PASSWORD` deben completarse antes de iniciar Compose; el Compose falla si faltan. No se configura mediante `config.json` ni el asistente antiguo.

`TMD_API_ID` y `TMD_API_HASH` son comunes a las sesiones Telegram. `TMD_PHONE` identifica la cuenta Principal; las cuentas secundarias se añaden en **Administración → Telegram** y sus canales se asocian en **Administración → Canales**. Todas las cuentas web comparten el catálogo y la biblioteca; favoritos, progreso y permisos de descarga siguen siendo propios de cada usuario web.

---

## Uso

Inicia sesión en la web. La portada, Películas, Series, Buscar, Favoritos y Descargas requieren una cuenta. El administrador gestiona cuentas, canales, configuración, sesión de Telegram, conversión y torrents desde el menú de administración. El panel Torrents ofrece búsquedas externas y control de Transmission.

En pruebas la dirección es `http://<ip-raspberry>:8080`; en el corte final se puede cambiar `WEB_PORT` a `80`. Para logs y operación del stack, usar `docker compose logs -f backend` y `docker compose ps`.

---

## Uso de la interfaz web

La interfaz autenticada incluye Inicio, Películas, Series, Buscar, Favoritos y Descargas. El menú de administración gestiona cuentas, canales, Telegram, cuotas y ajustes. La página Torrents está disponible para administradores y controla las fuentes externas y Transmission.

Los clientes móviles y de TV comparten el catálogo, la biblioteca y la reproducción. Las descargas guardan progreso en tiempo real; los medios se organizan bajo la biblioteca montada en `TMD_EXTRACT_PATH`.

---

## Flujo de descarga

```
Búsqueda → Selección → Descarga paralela
    │
    ├── Archivo comprimido (.rar/.zip/.7z)
    │   └── Extracción automática → Conversión compatible (si se habilita)
    │
    └── Archivo de vídeo (.mkv/.mp4)
        └── Conversión compatible (si se habilita)
             └── Incorporación a la biblioteca organizada
```

---

## API REST

| Método | Endpoint | Descripción |
|---|---|---|
| `POST` | `/api/auth/login` | Iniciar sesión |
| `GET` | `/api/browse/home` | Catálogo principal |
| `GET` | `/api/search` | Buscar catálogo y Telegram |
| `POST` | `/api/download` | Descargar desde Telegram |
| `POST` | `/api/pause`, `/api/resume`, `/api/cancel` | Controlar descargas Telegram |
| `GET` | `/api/status` | Progreso de descargas |
| `GET` | `/api/files` | Biblioteca local |
| `GET` | `/api/stream` | Streaming con soporte de rangos |
| `GET` | `/api/nyaa/search`, `/api/leetx/search`, `/api/animetosho/search` | Buscar fuentes torrent |
| `GET` | `/api/torrents` | Consultar Transmission |
| `POST` | `/api/torrents` | Añadir magnet a Transmission (administrador) |
| `POST` | `/api/torrent/pause`, `/api/torrent/resume` | Controlar torrent (administrador) |
| `DELETE` | `/api/torrent/{id}` | Eliminar torrent (administrador) |

---

## Docker Compose en Raspberry Pi

Docker Compose es el despliegue recomendado para este fork. Para rutas USB, UID/GID, Transmission del host, secretos, pruebas junto a `systemd` y corte con reversión, seguir [`DEPLOY-RASPBERRY.md`](DEPLOY-RASPBERRY.md). No usar el comando `docker run` heredado de la aplicación anterior.

---

## Solución de problemas

### "No se encontró el mensaje"
El mensaje fue eliminado del canal o el ID cambió. Busca de nuevo.

### Error Bad7zFile en archivos 7z
Asegúrate de tener `p7zip-full` instalado: `sudo apt install p7zip-full`

### Consultar logs
Usa `docker compose logs -f backend` o `docker compose logs -f frontend` desde el directorio del Compose.

### Transmission no conecta desde Docker
Comprueba `TMD_TRANSMISSION_HOST`, que la ruta host `TMD_TRANSMISSION_HOST_PATH` esté montada en `TMD_TRANSMISSION_DOWNLOAD_DIR`, y que el RPC de Transmission admita conexiones desde la red Docker.

### La Pi se satura con streams
El semáforo de streams (configurable vía `TMD_STREAM_MAX`) limita las reproducciones simultáneas a 3 por defecto.

---

## Licencia

MIT
