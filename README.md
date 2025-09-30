# Bot de Trading: Sincronizador de Señales de Telegram a MetaTrader 5

Este proyecto es un bot de trading que actúa como un puente inteligente entre un proveedor de señales en Telegram y tu cuenta de trading. Su función principal es escuchar en tiempo real los mensajes de un canal, interpretarlos y ejecutar las operaciones correspondientes sin intervención manual.

La característica más potente es su capacidad para **sincronizar carteras de órdenes pendientes**. Cuando el proveedor de señales envía una lista actualizada, el bot primero cancela las órdenes antiguas y luego crea las nuevas, asegurando que tu cuenta refleje siempre la estrategia más reciente.

-----

## ✨ Características Principales

  * **Conexión en Tiempo Real:** Utiliza una arquitectura asíncrona para escuchar y procesar mensajes de Telegram de forma instantánea y eficiente.
  * **Análisis Inteligente de Señales:** Emplea expresiones regulares para extraer con precisión los parámetros de cada operación: activo, tipo de orden, precio de entrada y stop loss.
  * **Ejecución Automatizada:** Se integra directamente con la terminal de MetaTrader 5 para colocar órdenes a mercado y pendientes.
  * **Sincronización de Órdenes:** Mantiene la cartera de órdenes pendientes siempre actualizada, eliminando las antiguas y creando las nuevas en cada señal masiva.
  * **Soporte para Múltiples Activos:** Diseñado para manejar una amplia gama de símbolos, incluyendo criptomonedas (`BTCUSD`), índices (`UK100`, `US500`) y más.
  * **Configuración Segura:** Gestiona las credenciales de forma segura a través de variables de entorno, sin exponer datos sensibles en el código.

-----

## ⚙️ Tecnologías Utilizadas

  * **Python 3.8+**
  * **Telethon:** Para interactuar con la API de Telegram.
  * **MetaTrader5:** Para la conexión y ejecución de órdenes en la plataforma MT5.
  * **Asyncio:** Para el manejo de operaciones asíncronas.
  * **python-dotenv:** Para la gestión de variables de entorno.

-----

## 🚀 Instalación y Puesta en Marcha

Sigue estos pasos para poner en funcionamiento el bot en tu propio sistema.

### **Pre-requisitos**

1.  Tener **Python 3.8** o superior instalado.
2.  Tener la terminal de **MetaTrader 5** instalada y abierta en tu ordenador.
3.  Tener una cuenta de **Telegram**.

### **Pasos de Instalación**

1.  **Clona el repositorio:**

    ```bash
    git clone https://github.com/tu-usuario/tu-repositorio.git
    cd tu-repositorio
    ```

2.  **Crea un entorno virtual y actívalo:** (Recomendado)

      * En Windows:
        ```bash
        python -m venv venv
        .\venv\Scripts\activate
        ```
      * En macOS / Linux:
        ```bash
        python3 -m venv venv
        source venv/bin/activate
        ```

3.  **Instala las dependencias:**
    Crea un archivo llamado `requirements.txt` con el siguiente contenido:

    ```
    telethon
    MetaTrader5
    python-dotenv
    ```

    Luego, instálalo con pip:

    ```bash
    pip install -r requirements.txt
    ```

4.  **Obtén tus credenciales de la API de Telegram:**

      * Inicia sesión en [my.telegram.org](https://my.telegram.org) con tu número de teléfono.
      * Ve a la sección "API development tools" y crea una nueva aplicación.
      * Copia los valores de `api_id` y `api_hash`. **¡No los compartas con nadie\!**

5.  **Configura tus variables de entorno:**

      * Crea un archivo llamado `.env` en la raíz del proyecto.
      * Añade tus credenciales de Telegram. No uses comillas.

    ```
    TELEGRAM_API_ID=12345678
    TELEGRAM_API_HASH=abcdef1234567890abcdef1234567890
    ```

6.  **Crea tu sesión**
    Asegúrate de tener la terminal de MetaTrader 5 abierta y de haber iniciado sesión en tu cuenta. Luego, ejecuta el script `crear_sesion.py`:

    ```bash
    python crear_sesion.py.py
    ```

    La primera vez que lo ejecutes, Telethon te pedirá tu número de teléfono, un código de verificación y, si la tienes, tu contraseña de doble factor para iniciar sesión.

7.  **Descarga Meta Trader 5**
    Descarga MT5 desde tu broker e ingresa a tu cuenta con tu usuario, clave y servidor. 

    * En `observación de mercado` asegúrate de tener habilitados los símbolos con los que vas a trabajar. Da clic derecho en la ventana de observación de mercado y has clic en "Símbolos". Luego, busca el símbolo que quieres operar y das clic en "Mostrar símbolo". De esta manera, MT5 podrá observar el precio en cada tick.
    * Asegurate que el `trading algorítmico` esté activado.

8.  **Ejecuta tu bot\!**
    Asegúrate de tener la terminal de MetaTrader 5 abierta y de haber iniciado sesión en tu cuenta. Luego, ejecuta el script `telegram.py`:

    ```bash
    python telegram.py.py
    ```

    ¡Y listo! Tu bot estará escuchando tus mensajes de telegram y podrá operar sin que tu estés pendiente.

-----

## 💬 Formato de Señales Soportado

El bot está diseñado para interpretar los siguientes formatos de mensaje:

### **Órdenes a Mercado (Compra/Venta)**

Se ejecutan inmediatamente al precio de mercado actual.

```
Compra BTCUSD $4180.49, Sl: 1280
Venta TSLA $315.00, Sl: 330
```

### **Órdenes Pendientes Individuales**

Se crea una orden `Buy Limit` que se activará si el precio alcanza el nivel especificado.

```
Buy limit Creada BTCUSD $113553.93, Sl: 73700
```

### **Sincronización de Órdenes Pendientes**

Cuando se recibe un mensaje que contiene `ORDENES PENDIENTES`, el bot realiza un proceso de sincronización completo, añadiendo las ordenes pendientes que no han sido creadas en tu cuenta de trading e ignorando las que sí están creadas para evitar duplicidad de ordenes.
<!-- end list -->

```
ORDENES PENDIENTES

Buy Limit BTCUSD 107549.71 SL: 73700
Buy Limit BTCUSD 108526.54 SL: 73700

Buy Limit ETHUSD 3827.38 SL: 1280

Buy Limit HK50 18500 SL: 18000
Buy Limit US500 4500 SL: 4450
```

### **Modificación de Stop Loss (Trailing Stop)**

El bot puede modificar el Stop Loss de una posición ya abierta y en ganancia.

```
SL BTCUSD 115000
```

-----

## ⚖️ Aviso Legal

Este software se proporciona "tal cual", sin garantía de ningún tipo. El trading de instrumentos financieros implica un riesgo significativo y puede resultar en la pérdida de tu capital invertido. El autor no se hace responsable de ninguna pérdida financiera que pueda ocurrir como resultado del uso de este bot. **Úsalo bajo tu propio riesgo.**
