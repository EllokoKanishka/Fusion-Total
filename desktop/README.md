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

Para generar el paquete de escritorio:

```bash
npm run build
```

La ventana no abre contenido remoto: su única URL es el servidor loopback de
Panda Fusión. El backend, voz, documentos y sus rutas API conservan el mismo
contrato que en navegador.
