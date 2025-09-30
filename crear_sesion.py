from telethon.sync import TelegramClient
from dotenv import load_dotenv
import os

# Reemplaza con tus propios valores
api_id = int(os.getenv('TELEGRAM_API_ID'))
api_hash = os.getenv('TELEGRAM_API_HASH')
nombre_de_la_sesion = 'mi_sesion_telegram' # El nombre de tu sesión va aquí

if not api_id or not api_hash:
    raise ValueError("No se encontraron TELEGRAM_API_ID y TELEGRAM_API_HASH en el archivo .env")

# Creamos el cliente de Telethon
# El primer argumento es el nombre que tendrá el archivo de sesión.
client = TelegramClient(nombre_de_la_sesion, api_id, api_hash)

async def main():
    # Conectarse a Telegram
    await client.start()
    print("¡Conexión exitosa! El archivo de sesión ha sido creado.")
    
    me = await client.get_me()
    print(f"Conectado como: {me.first_name}")

    # Es buena práctica desconectarse al final
    await client.disconnect()

# Ejecutar el cliente
with client:
    client.loop.run_until_complete(main())