# Panda Fusión Desktop

Esta carpeta contiene la carcasa Tauri de Panda Fusión. El lector sigue
ejecutándose localmente en `127.0.0.1:8010`; Tauri lo abre en una ventana de
aplicación sin la barra, pestañas ni controles de Chrome.

Primero iniciar Fusion como siempre:

```bash
fusionctl start
```

Luego, una única vez por máquina:

```bash
cd desktop
npm install
```

Para desarrollo:

```bash
npm run dev
```

Para generar el paquete de la ventana:

```bash
npm run build
```

La distribución actual requiere instalar por separado el paquete Python que
provee `fusionctl`, con ese ejecutable disponible en el `PATH` de la sesión de
escritorio. También requiere disponer de los motores locales de voz y dictado
según la [guía operativa](../docs/OPERATIONS.md) y el [manifiesto de
dependencias](../docs/DEPENDENCIES_V2.md).

Para publicar un instalador autónomo, queda integrar el backend y su entorno
en el paquete, provisionar la voz y el dictado, y verificar arranque, lectura
audible, dictado y liberación de recursos en una instalación limpia.

La ventana no abre contenido remoto: su única URL es el servidor loopback de
Panda Fusión. El backend, voz, documentos y sus rutas API conservan el mismo
contrato que en navegador.

## Uso actual (consolidación 17/11/2026)

El acceso diario usa Chrome/Chromium en modo aplicación. Chromium y Tauri respetan `FUSION_READER_BIND_HOST` y `FUSION_READER_V2_PORT` para la dirección local. Tauri requiere PANDA_FUSION_NATIVE_EXPERIMENTAL=1; permanece experimental por diferencias de captura y renderizado en WebKitGTK. Su permiso automático se limita al micrófono, sin cámara, desde el origen local actual de Panda.
