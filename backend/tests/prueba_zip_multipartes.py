"""Comprueba ZIP multipartes .zip.NNN y _zip.NNN, sin servicios externos."""
import io
import os
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.extractor import extract_archive, find_first_archive, is_archive_file
from app.services.storage import _detect_multipart, suggest_folder_name


def crear_partes(directorio, stem, marcador, cantidad=5):
    memoria = io.BytesIO()
    with zipfile.ZipFile(memoria, "w") as archivo:
        archivo.writestr("video/prueba.txt", "contenido del ZIP multipartes")
    datos = memoria.getvalue()
    tamano = (len(datos) + cantidad - 1) // cantidad
    partes = [datos[i:i + tamano] for i in range(0, len(datos), tamano)]
    rutas = []
    for numero, parte in enumerate(partes, start=1):
        ruta = os.path.join(directorio, f"{stem}{marcador}.{numero:03d}")
        with open(ruta, "wb") as salida:
            salida.write(parte)
        rutas.append(ruta)
    return rutas


def comprobar(condicion, mensaje):
    if not condicion:
        raise AssertionError(mensaje)


comprobar(not is_archive_file("archivo.mkv"), "un video directo no debe clasificarse como comprimido")
comprobar(is_archive_file("archivo.zip"), "no reconoce un ZIP normal como comprimido")

with tempfile.TemporaryDirectory() as temporal:
    stem = "Akemi_El_Diablo_Viste_de_Prada_2006_7B9D5366_BDRip_1080p_x264"
    nombre = stem + "_zip.005"
    base, numero, tipo = _detect_multipart(nombre)
    comprobar((base, numero, tipo) == (stem, 5, "zip"), "no detecta el nombre _zip.005")
    comprobar(suggest_folder_name(nombre).endswith("x264"), "el nombre de carpeta conserva _zip")

    for marcador, indice_inicial in (("_zip", 4), (".zip", 0)):
        carpeta = os.path.join(temporal, marcador.replace(".", "dot"))
        os.makedirs(carpeta)
        rutas = crear_partes(carpeta, "pelicula", marcador)
        primer_archivo = rutas[indice_inicial]
        ordenados = [find_first_archive([ruta]) for ruta in [primer_archivo]]
        comprobar(ordenados[0] == primer_archivo, f"no reconoce parte inicial {os.path.basename(primer_archivo)}")

        extraidos, es_archivo = extract_archive(primer_archivo, carpeta)
        video = os.path.join(carpeta, "video", "prueba.txt")
        comprobar(es_archivo, f"no extrae multipartes {marcador}")
        comprobar(os.path.isfile(video), f"no aparece el contenido extraido para {marcador}")
        comprobar(len(extraidos) == 1, f"lista de archivos extraidos incorrecta para {marcador}")
        comprobar(not any(os.path.exists(ruta) for ruta in rutas), f"no limpia las partes {marcador}")

print("ZIP multipartes .zip.NNN y _zip.NNN: correcto")
