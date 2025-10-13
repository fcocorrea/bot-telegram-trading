import os
import asyncio
import re
from dotenv import load_dotenv
from telethon import TelegramClient, events
import MetaTrader5 as mt5

class TelegramInput:
    """Clase que lee los mensajes de mi Telegram y los expone a través de una cola."""

    def __init__(self, api_id: int, api_hash: str):
        self.api_id = api_id
        self.api_hash = api_hash
        self.session_name = "mi_sesion_trading"
        self.queue = asyncio.Queue()  # Cola para compartir mensajes

    async def handle_new_message(self, event: events.NewMessage.Event):
        """Maneja nuevos mensajes recibidos."""
        sender = await event.get_sender()
        message_text = event.raw_text
        mensaje = {
            "username": sender.username,
            "text": message_text,
            "chat_id": event.chat_id,
        }
        # Metemos el mensaje a la cola
        await self.queue.put(mensaje)

    async def start_listening(self):
        """Inicia la escucha de mensajes."""
        client = TelegramClient(self.session_name, self.api_id, self.api_hash)

        @client.on(events.NewMessage)
        async def new_message_listener(event):
            await self.handle_new_message(event)

        print("Bot escuchando tus chats de Telegram...")
        async with client:
            await client.run_until_disconnected()

    async def get_message(self):
        """Obtiene el siguiente mensaje de la cola (espera hasta que haya uno)."""
        return await self.queue.get()

class TradingOrder:
    """ Clase que toma los mensajes de mi telegram y los 
    convierte en ordenes para Meta Trader 5.
    
    """
    
    def __init__(self, my_trading_account:object):
        self.my_trading_account = my_trading_account
        
    def catch_order(self, telegram_message:str, order_type:str, order_match:str)->dict:
        """ Capturamos la orden de compra desde Telegram.
        El resultado es un diccionario.

        {
        "order_type": "Orden del movimiento",
        "asset": "Nemo",
        "price": "Precio de apertura",
        "stop_loss": "Stop Loss"
        }
        """
        price_match = r"\$?(\d+(\.\d+)?)"
        stop_loss_match = r"Sl:\s?(\d+(\.\d+)?)"
        take_profit_match = r"Tp:\s?(\d+(\.\d+)?)"
        trailing_stop_match = r"SL [A-Z0-9]+ \$(\d+(\.\d+)?)"         
        

        order_instruction = {}
        
        # Buscamos la coincidencia completa de la orden
        order_search = re.search(order_match, telegram_message, re.IGNORECASE)
        # Si encontramos una coincidencia para la orden (ej. "Buy Limit Creada BTCUSD...")
        if order_search:
            # Extraemos el activo del grupo de captura (el paréntesis en la regex)
            asset = order_search.group(1)
            price_search = re.search(price_match, telegram_message)
            stop_loss_search = re.search(stop_loss_match, telegram_message, re.IGNORECASE)
            take_profit_search = re.search(take_profit_match, telegram_message, re.IGNORECASE)
            trailing_stop_search = re.search(trailing_stop_match, telegram_message, re.IGNORECASE)            
            
            order_instruction["order_type"] = order_type.strip()
            order_instruction["asset"] = asset
            
            if order_type != "Trailing Stop" and price_search: 
                order_instruction["price"] = price_search.group(1)
                order_instruction["stop_loss"] = "0.0" if not stop_loss_search else stop_loss_search.group(1)
                order_instruction["take_profit"] = "0.0" if not take_profit_search else take_profit_search.group(1)
            elif order_type == "Trailing Stop" and trailing_stop_search or order_type == "Cierre": 
                 order_instruction["price"] = "0" # Precio no aplica para trailing stop
                 order_instruction["stop_loss"] = trailing_stop_search.group(1)

        return order_instruction
    
    def catch_orders(self, telegram_message):
        """ Función que puede capturar varias ordenes distintas """
        asset_pattern = r"[A-Z0-9]+"
        orders = {
            "Buy Limit": rf"Buy limit Creada ({asset_pattern})",
            "Buy Limit ": rf"Buy Limit ({asset_pattern})", # Buy Limit para operaciones pendientes
            "Compra": rf"Compra\s({asset_pattern})",
            "Venta": rf"Venta\s({asset_pattern})",
            "Trailing Stop": rf"SL\s({asset_pattern})",
            "Cierre": rf"Cierre\s({asset_pattern})"
        }
        for order_type, order_match in orders.items():
            order_call = self.catch_order(telegram_message, order_type, order_match)
            if order_call: 
                return order_call
        # Si no encuentra ninguna orden, retorna un diccionario vacío para evitar errores
        return {}
    
    def execute_pending_orders(self, telegram_message):
        """ 
        Función que captura las ordenes pendientes y las ejecuta.
        Si el mercado está cerrado no podrá ejecutar las ordenes pendientes. En ese caso, 
        agregarlas manualmente cuando el mercado abra.
        """
        # 1. Eliminamos todas las ordenes pendientes
        self.my_trading_account.delete_all_pending_orders()
        # 2. Separa cada línea que representa una orden
        split_orders = [line.strip() for line in telegram_message.splitlines() if line.strip().startswith("Buy Limit")]
        
        # 3. Itera sobre cada orden individual
        
        for message_order in split_orders:
            # 3. Ejecutamos la orden
            self.execute_order(message_order)


    def execute_order(self, telegram_message):
        order = self.catch_orders(telegram_message)
        # Verificamos que la orden no esté vacía antes de proceder
        if not order:
            print("No se detectó una orden válida en el mensaje.")
            return
        print("Orden de trading: ", order)
        order_type = order.get("order_type")
        asset = order.get("asset")
        # Usamos .get() con un valor por defecto para más seguridad
        try:
            price = float(order.get("price", 0.0))
            stop_loss = float(order.get("stop_loss", 0.0))
            take_profit = float(order.get("take_profit", 0.0))
        except ValueError:
            print("Error al intentar convertir un precio, stop loss o take profit a número:")
            print(f"Precio: {price}\nStop Loss: {stop_loss}\nTake Profit: {take_profit}")
        
        if not order_type or not asset:
            print("La orden detectada no tiene tipo o activo. Omitiendo.")
            return

        if order_type == "Buy Limit":
            self.my_trading_account.execute_buy_limit(asset, price, stop_loss, take_profit)
        elif order_type == "Compra":
            self.my_trading_account.execute_buy(asset, stop_loss, take_profit)
        elif order_type == "Venta":
            self.my_trading_account.execute_sell(asset, stop_loss, take_profit)
        elif order_type == "Trailing Stop":
            self.my_trading_account.execute_trailing_stop(asset, stop_loss)
        elif order_type == "Cierre":
            self.my_trading_account.close_profit_trades(asset)
        
class PendingOperations(TradingOrder):
    """ Clase que maneja el mensaje de las operaciones pendientes """

    def __init__(self, my_trading_account):
        self.pending_orders = mt5.orders_get()
        super().__init__(my_trading_account)
        
    def get_pending_operations_in_message(self, telegram_message)->set:
        """ Retorna un set de las operaciones pendientes desde el mensaje de señales
        concadenando el activo con el precio. """
        assets_in_message = set()
        split_orders = [line.strip() for line in telegram_message.splitlines() if line.strip().startswith("Buy Limit")]
        for line in split_orders:
            match = re.search(r"Buy Limit\s+([A-Z0-9]+)\s(\d+(\.\d+)?)", line)
            if not match: continue # en caso que no se haya encontrado match
            groups = match.groups()
            if len(groups): # tengo el activo y el precio
                concat = groups[0] + ">" + groups[1]
                assets_in_message.add((line, concat))
        return assets_in_message
    
    def get_pending_operations_in_trading_account(self):
        """ Retorna un set de las operaciones pendientes desde una cuenta de trading
        concadenando el activo con el precio. """
        assets_in_account = set()
        for order in self.pending_orders:
            info_order = f"{order.symbol}>{order.price_open}"
            assets_in_account.add(info_order)
        return assets_in_account
    
    def add_new_pending_orders(self, telegram_message):
        """ Creamos nuevas ordenes pendientes en una cuenta de trading. Estas corresponden
        a las ordenes pendientes que están en los mensajes de señales, pero no en nuestra cuenta. """
        message_orders_and_lines = self.get_pending_operations_in_message(telegram_message)
        message_orders = {i[1] for i in message_orders_and_lines}
        account_orders = self.get_pending_operations_in_trading_account()
        new_orders = message_orders - account_orders
        if not new_orders: print("No hay ordenes pendientes nuevas para agregar.")
        for new_order in new_orders:
            asset, price = new_order.split(">")
            message_lines = [l for l, o in message_orders_and_lines if asset in o and price in o]
            for message in message_lines:
                self.execute_order(message)

    def delete_old_pending_orders(self, telegram_message):
        """ Buscamos ordenes que están en pendientes en nuestra cuenta de trading, pero
        que no están en los mensajes enviados por telegram """
        message_orders_and_lines = self.get_pending_operations_in_message(telegram_message)
        message_orders = {i[1] for i in message_orders_and_lines}
        account_orders = self.get_pending_operations_in_trading_account()
        delete_orders = account_orders-message_orders
        for order in self.pending_orders:
            info_order = f"{order.symbol}>{order.price_open}"
            if info_order in delete_orders:
                request = {
                    "action": mt5.TRADE_ACTION_REMOVE,  # cancelar orden pendiente
                    "order": order.ticket,
                }
                result = mt5.order_send(request)
                if result.retcode != mt5.TRADE_RETCODE_DONE:
                    print(f"Error al eliminar orden {order.ticket}: {result.retcode}")
                else:
                    print(f"Orden {order.ticket} eliminada con éxito")

    def manage_pending_orders(self, telegram_message):
        self.delete_old_pending_orders(telegram_message)
        self.add_new_pending_orders(telegram_message)


class TradingAccount:
    """ Clase para conectarse y operar en una cuenta de trading. """

    def __init__(self):
        if not mt5.initialize():
            print("initialize() falló, error code =", mt5.last_error())
            quit()
        else:
            print("¡Conexión con MetaTrader 5 establecida con éxito!")
        self.crypto_symbols = ['BTCUSD', 'ETHUSD']

    def _get_trade_request(self, asset, order_type, sl=0.0, tp=0.0, price=0.0):
        """
        Construye un diccionario de solicitud de trade válido y adaptado al símbolo.
        """
        symbol_info = mt5.symbol_info(asset)
        if symbol_info is None:
            print(f"No se pudo obtener información para el símbolo {asset}")
            return None

        # 1. Determinar el VOLUMEN correcto (usamos el mínimo permitido por el broker)
        volume = symbol_info.volume_min

        # 2. Determinar la POLÍTICA DE EJECUCIÓN correcta        
        filling_mode = 0 # Valor por defecto
        if asset in self.crypto_symbols:
            filling_mode = mt5.ORDER_FILLING_IOC
        else:
            filling_mode = mt5.ORDER_FILLING_FOK
        
        # 3. Construir la solicitud base
        request = {
            "symbol": asset,
            "volume": volume,
            "sl": sl,
            "tp": tp,
            "magic": 1234,
            "deviation": 20,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": filling_mode,
        }

        # 4. Ajustar parámetros según el tipo de orden
        request["type"] = order_type
        if order_type == mt5.ORDER_TYPE_BUY or order_type == mt5.ORDER_TYPE_SELL:
            request["action"] = mt5.TRADE_ACTION_DEAL
            request["comment"] = f"Orden a mercado {asset}"
        elif order_type == mt5.ORDER_TYPE_BUY_LIMIT:
            request["action"] = mt5.TRADE_ACTION_PENDING            
            request["price"] = price
            request["comment"] = f"Orden Buy Limit {asset}"
        else:
            print(f"Tipo de orden no soportado: {order_type}")
            return None

        return request

    def execute_buy_limit(self, asset, price, stop_loss, take_profit):
        if not self._check_and_enable_symbol(asset): return
        request = self._get_trade_request(asset, mt5.ORDER_TYPE_BUY_LIMIT, sl=stop_loss, tp=take_profit, price=price)
        if request is None: return
        
        result = mt5.order_send(request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            print("Error al enviar la orden Buy Limit.")
            self.print_failed_operation(result)
        else:
            print("¡Orden Buy Limit enviada exitosamente!")
            print("Posición ticket: {}".format(result.order))

    def execute_buy(self, asset, stop_loss, take_profit):
        if not self._check_and_enable_symbol(asset): return
        request = self._get_trade_request(asset, mt5.ORDER_TYPE_BUY, sl=stop_loss, tp=take_profit,)
        if request is None: return
        
        result = mt5.order_send(request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            print("Error al enviar la orden de Compra.")
            self.print_failed_operation(result)
        else:
            print("¡Orden de Compra enviada exitosamente!")
            print("Posición ticket: {}".format(result.order))

    def execute_sell(self, asset, stop_loss, take_profit):
        if not self._check_and_enable_symbol(asset): return
        request = self._get_trade_request(asset, mt5.ORDER_TYPE_SELL, sl=stop_loss, tp=take_profit,)
        if request is None: return
        
        result = mt5.order_send(request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            print("Error al enviar la orden de Venta.")
            self.print_failed_operation(result)
        else:
            print("¡Orden de Venta enviada exitosamente!")
            print("Posición ticket: {}".format(result.order))

    def execute_trailing_stop(self, asset, stop_loss):
        """
        Implementación de trailing stop
        """
        min_profit = 0.2 # minima ganancia esperada para cerrar una posición
        positions = mt5.positions_get()
        if positions is None:
            print("No se encontraron posiciones, código de error =", mt5.last_error())
            return
                
        position_found = False
        for position in positions:
            if position.symbol == asset and position.profit > min_profit:
                position_found = True
                print(f"Posición encontrada para {asset} con ticket {position.ticket} y ganancia de {position.profit:.2f}")
                modificar_sl_compra = (position.type == mt5.ORDER_TYPE_BUY and 
                                    position.sl < stop_loss and 
                                    stop_loss > position.price_open)
                modificar_sl_venta = (position.type == mt5.ORDER_TYPE_SELL and
                                    (position.sl > stop_loss or position.sl == 0) and 
                                    stop_loss < position.price_open)
                
                if modificar_sl_compra or modificar_sl_venta:
                    print(f"  Modificando SL. Actual: {position.sl}, Nuevo: {stop_loss}")
                    current_tp = position.tp
                    request = {
                        "action": mt5.TRADE_ACTION_SLTP,
                        "position": position.ticket,
                        "sl": stop_loss,
                        "tp": current_tp, # especificamos el take profit actual para no configurarlo a 0.
                        "comment": "Trailing Stop Update",
                    }
                    result = mt5.order_send(request)
                    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
                        print(f"Error al modificar el Stop Loss al precio {stop_loss}.")
                        print(f"Intentaremos cerrar las posiciones abiertas de {asset} en positivo.")
                        self.close_profit_trades(asset)
                    else:
                        print("  ¡Stop Loss modificado exitosamente!")
                else:
                    print(f"  No se requiere modificación de SL. Actual: {position.sl}, Propuesto: {stop_loss}")
        
        if not position_found:
            print(f"No se encontró una posición abierta y rentable para {asset}.")

    def close_profit_trades(self, asset):
            """
            Cierra todas las operaciones abiertas para un activo específico que tengan ganancias.
            """
            min_profit = 0.2 # minima ganancia esperada para cerrar una posición
            if not self._check_and_enable_symbol(asset): # Asegurarse de que el símbolo esté disponible
                return
            positions = mt5.positions_get(symbol=asset) # Obtener solo las posiciones para el activo de interés
            if positions is None:
                print(f"No se pudieron obtener posiciones para {asset}. Error: {mt5.last_error()}")
                return

            if not positions:
                print(f"No se encontraron posiciones abiertas para {asset}.")
                return
            
            closed_any = False

            for position in positions:
                if position.profit > min_profit:
                    print(f"Posición rentable encontrada para {asset} (Ticket: {position.ticket}, Ganancia: {position.profit:.2f}). Intentando cerrar...")
                    
                    # Si la posición es de compra (BUY), debemos vender (SELL) para cerrarla, y viceversa.
                    order_type_close = mt5.ORDER_TYPE_SELL if position.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
                    
                    # Determinar la política de ejecución (filling mode)
                    filling_mode = mt5.ORDER_FILLING_IOC if asset in self.crypto_symbols else mt5.ORDER_FILLING_FOK

                    request = {
                        "action": mt5.TRADE_ACTION_DEAL,
                        "position": position.ticket,
                        "symbol": asset,
                        "volume": position.volume,
                        "type": order_type_close,
                        "deviation": 20,
                        "magic": 1234, # Mismo magic number para consistencia
                        "comment": "Cierre con ganancia",
                        "type_time": mt5.ORDER_TIME_GTC,
                        "type_filling": filling_mode,
                    }

                    result = mt5.order_send(request)

                    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
                        print(f"Error al cerrar la posición {position.ticket}.")
                        self.print_failed_operation(result)
                    else:
                        print(f"¡Posición {position.ticket} para {asset} cerrada exitosamente!")
                        closed_any = True
                        
            if not closed_any:
                print(f"No se encontraron posiciones rentables para cerrar en {asset}.")
    

    def _check_and_enable_symbol(self, asset):
        symbol_info = mt5.symbol_info(asset)
        if symbol_info is None:
            print(f"El símbolo {asset} no fue encontrado.")
            return False
        if not symbol_info.visible:
            if not mt5.symbol_select(asset, True): return False
        return True

    def print_failed_operation(self, result):
        if result is None:
            print("La operación falló antes de enviar la solicitud a MT5. Error:", mt5.last_error())
            return
        print("Falló el envío de la orden, retcode={}".format(result.retcode))
        result_dict = result._asdict()
        for field, value in result_dict.items():
            print(f"  {field}={value}")
            if field == "request":
                traderequest_dict = value._asdict()
                for req_field, req_value in traderequest_dict.items():
                    print(f"    traderequest: {req_field}={req_value}")


async def main():
    """Función principal para iniciar el bot de Telegram y consumir mensajes."""
    load_dotenv()
    api_id = int(os.getenv("TELEGRAM_API_ID"))
    api_hash = os.getenv("TELEGRAM_API_HASH")

    if not api_id or not api_hash:
        raise ValueError(
            "Por favor, asegúrate de que TELEGRAM_API_ID y TELEGRAM_API_HASH "
            "estén configurados en el archivo .env"
        )

    telegram_input = TelegramInput(api_id, api_hash)

    # Lanza la escucha en segundo plano
    listener_task = asyncio.create_task(telegram_input.start_listening())
    my_trading_account = TradingAccount() # Nos conectamos a mi cuenta de trading
    order_obj = TradingOrder(my_trading_account)
    # Ejemplo: consumir mensajes que llegan
    while True:
        message = await telegram_input.get_message()
        print("Mensaje recibido:", message)
        telegram_message = message["text"]
        # Aquí podrías pasarlo a otro objeto, guardarlo en BD, etc.

        # Dejaremos afuera la gestión de ordenes pendientes por ahora.

        if "ORDENES PENDIENTES" in telegram_message:
            pending_orders = PendingOperations(my_trading_account)
            pending_orders.manage_pending_orders(telegram_message)
        else:         
            order_obj.execute_order(telegram_message)



if __name__ == "__main__":
    asyncio.run(main())