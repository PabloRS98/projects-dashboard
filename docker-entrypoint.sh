#!/bin/sh
# Baja privilegios antes de arrancar la app.
#
# Hace falta un entrypoint y no basta un `USER app` en el Dockerfile: el volumen
# de /data puede existir ya de un despliegue anterior con sus ficheros
# pertenecientes a root, y entonces la app arrancaría sin poder escribir la base.
# Aquí se corrige la propiedad primero, todavía como root, y solo después se
# cambia de usuario.
#
# Es la única de las tres aplicaciones que ejecutaba subprocesos (`git`) sobre
# rutas que el usuario controla, así que correr como root era justo lo que menos
# convenía.
set -eu

if [ "$(id -u)" = "0" ]; then
    mkdir -p /data
    chown -R app:app /data
    # setpriv y no gosu: viene en util-linux, que ya está en la imagen base, así
    # que no hay que instalar nada. --init-groups para que los grupos
    # suplementarios sean los del usuario y no los heredados de root.
    exec setpriv --reuid=app --regid=app --init-groups "$@"
fi

exec "$@"
