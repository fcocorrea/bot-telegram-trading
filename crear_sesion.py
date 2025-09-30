from telethon.sync import TelegramClient

# Reemplaza con tus propios valores
api_id = 19795451
api_hash = 'aacb56b1f96f0ecdbd0214390f440c04'
nombre_de_la_sesion = 'mi_sesion_telegram' # Puedes poner el nombre que quieras

# Creamos el cliente de Telethon
# El primer argumento es el nombre que tendrá el archivo de sesión.
client = TelegramClient(nombre_de_la_sesion, api_id, api_hash)

async def main():
    # Conectarse a Telegram
    await client.start()
    print("¡Conexión exitosa! El archivo de sesión ha sido creado.")
    
    # Aquí va el resto de tu lógica para leer mensajes, etc.
    # Por ejemplo, obtener información sobre ti mismo:
    me = await client.get_me()
    print(f"Conectado como: {me.first_name}")

    # Es buena práctica desconectarse al final
    await client.disconnect()

# Ejecutar el cliente
with client:
    client.loop.run_until_complete(main())