# Archivos de descarga

Este directorio contiene información sobre los paquetes binarios distribuidos en las Releases de GitHub.

IMPORTANTE: las URL de descarga apuntan a los *assets* de una Release en GitHub. Sustituye <TAG> por el tag de la release (por ejemplo `v1.0.0`).

Formato de URL de descarga:

https://github.com/Evilchuck666/QuakeBundle/releases/download/<TAG>/<FILENAME>

Ejemplos de ficheros por sistema operativo

- Windows
  - Nombre de fichero sugerido: `QuakeBundle-windows-x64.zip`
  - Ejemplo URL: `https://github.com/Evilchuck666/QuakeBundle/releases/download/<TAG>/QuakeBundle-windows-x64.zip`

- Linux
  - Nombre de fichero sugerido (AppImage): `QuakeBundle-linux-x86_64.AppImage`
  - Alternativa tar.gz: `QuakeBundle-linux-x86_64.tar.gz`
  - Ejemplo URL: `https://github.com/Evilchuck666/QuakeBundle/releases/download/<TAG>/QuakeBundle-linux-x86_64.AppImage`

- macOS
  - Nombre de fichero sugerido (dmg): `QuakeBundle-macos-universal.dmg`
  - Alternativa zip: `QuakeBundle-macos-universal.zip`
  - Ejemplo URL: `https://github.com/Evilchuck666/QuakeBundle/releases/download/<TAG>/QuakeBundle-macos-universal.dmg`

Notas

- Cuando publiques la Release, sube los binarios con exactamente los nombres indicados arriba o actualiza este README con los nombres reales.
- Las Releases de GitHub son el lugar recomendado para subir binarios; evita subir binarios grandes directamente al árbol del repositorio.

Cómo crear el tag, la release y subir los binarios (resumen rápido)

1) Crear un tag localmente y subirlo:

```bash
# desde tu clon local en la rama release (por ejemplo main):
git tag -a v1.0.0 -m "Release v1.0.0"
git push origin v1.0.0
```

2) Crear la release y subir assets con gh (GitHub CLI) — recomendado:

```bash
# crear release y subir varios ficheros a la vez
gh release create v1.0.0 \
  path/to/QuakeBundle-windows-x64.zip \
  path/to/QuakeBundle-linux-x86_64.AppImage \
  path/to/QuakeBundle-macos-universal.dmg \
  --title "v1.0.0" \
  --notes "QuakeBundle: instalador y lanzador para Quake."

# o, si ya tienes la release creada, subir un asset concreto:
gh release upload v1.0.0 path/to/QuakeBundle-windows-x64.zip
```

3) Crear release usando la API (si no usas gh):

- Crear release (obtendrás `upload_url`):

```bash
curl -X POST -H "Authorization: token <PERSONAL_ACCESS_TOKEN>" \
  -d '{"tag_name":"v1.0.0","name":"v1.0.0","body":"Notas de la release","draft":false,"prerelease":false}' \
  https://api.github.com/repos/Evilchuck666/QuakeBundle/releases
```

- Subir un asset (reemplaza `:upload_url` por el upload_url devuelto; incluye ?name=...):

```bash
curl -X POST -H "Authorization: token <PERSONAL_ACCESS_TOKEN>" \
  -H "Content-Type: application/octet-stream" \
  --data-binary @QuakeBundle-windows-x64.zip \
  "https://uploads.github.com/repos/Evilchuck666/QuakeBundle/releases/<RELEASE_ID>/assets?name=QuakeBundle-windows-x64.zip"
```

Si quieres, puedo:

- Preparar el texto de las notas de la Release (changelog/Descripción) y crear un borrador de release (texto listo para pegar) — dime qué versión usar (`v1.0.0` por ejemplo) y qué cambios incluir.
- Subir los binarios a la Release por ti si me proporcionas los ficheros y la instrucción para dónde subir (nota: no puedo aceptar tokens aquí; necesitarías usar la CLI localmente o dar acceso a un runner).

---

(Archivo añadido/actualizado por la automatización: instrucciones y nombres de ficheros para las descargas por SO.)
