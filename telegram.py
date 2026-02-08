import os
import asyncio
import re
from dotenv import load_dotenv
from telethon import TelegramClient, events
import MetaTrader5 as mt5
import strategy

class TelegramInput:
    """Clase que lee los mensajes de mi Telegram y los expone a través de una cola."""

    def __init__(self, api_id: int, api_hash: str):
        self.api_id = api_id
        self.api_hash = api_hash
        self.session_name = "mi_sesion_trading"
        self.queue = asyncio.Queue()  # Cola para compartir mensajes
        self.client = TelegramClient(self.session_name, self.api_id, self.api_hash)

    async def handle_new_message(self, event: events.NewMessage.Event):
        """Maneja nuevos mensajes recibidos."""
        sender = await event.get_sender()
        message_text = event.raw_text
        username = sender.username if sender else None
        mensaje = {
            "username": username,
            "text": message_text,
            "chat_id": event.chat_id,
        }
        await self.queue.put(mensaje)

    async def start_listening(self):
        """Inicia la escucha de mensajes."""
        
        @self.client.on(events.NewMessage(chats=[-1003169821641, 6685390587]))
        async def new_message_listener(event):
            await self.handle_new_message(event)

        print("Bot escuchando mensajes del grupo \"VIP Trading\"...")
        # Usamos async with para manejar la conexión y desconexión
        try:
            async with self.client:
                await self.client.run_until_disconnected()
        except asyncio.CancelledError:
            print("Deteniendo la escucha de Telegram...")
            if self.client.is_connected():
                await self.client.disconnect()
            print("Cliente de Telegram desconectado.")
        except Exception as e:
            print(f"Error en start_listening: {e}")


    async def get_message(self):
        """Obtiene el siguiente mensaje de la cola (espera hasta que haya uno)."""
        return await self.queue.get()

class TradingOrder:
    """ Clase que toma los mensajes de mi telegram y los 
    convierte en ordenes para Meta Trader 5.
    """
    
    def __init__(self, my_trading_account:object, cobertura:object, estrategia:dict):
        self.my_trading_account = my_trading_account
        self.cobertura = cobertura
        self.estrategia = estrategia
        
        
    def catch_order(self, telegram_message:str, order_type:str, order_match:str)->dict:
        # Esta función es síncrona (solo regex)
        price_match = r"\$?(\d+(\.\d+)?)"
        stop_loss_match = r"Sl:\s?(\d+(\.\d+)?)"
        take_profit_match = r"Tp:\s?(\d+(\.\d+)?)"
        trailing_stop_match = r"SL [A-Z0-9]+ \$(\d+(\.\d+)?)"

        # Calculamos el volumen mínimo por operación
            
        
        order_instruction = {}
        order_search = re.search(order_match, telegram_message, re.IGNORECASE)
        
        if order_search:
            asset_base = order_search.group(1)
            asset = asset_base + "c" if self.my_trading_account.account_type == "USC" else asset_base
            price_search = re.search(price_match, telegram_message)
            stop_loss_search = re.search(stop_loss_match, telegram_message, re.IGNORECASE)
            take_profit_search = re.search(take_profit_match, telegram_message, re.IGNORECASE)
            trailing_stop_search = re.search(trailing_stop_match, telegram_message, re.IGNORECASE)     
            
            order_instruction["order_type"] = order_type.strip()
            order_instruction["asset"] = asset         
            if order_type != "Trailing Stop" and price_search:
                # Si el precio es a mercado, debo calcularlo yo (puede ser muy distinto al de telegram por lag)
                order_instruction["price"] = self.get_market_price(asset, price_search, order_type)
                order_instruction["stop_loss"] = "0.0" if not stop_loss_search else stop_loss_search.group(1)
                order_instruction["take_profit"] = "0.0" if not take_profit_search else take_profit_search.group(1)
                order_instruction["volume"] = self.calculate_volume(asset, order_instruction["price"], order_instruction["stop_loss"])
            elif order_type == "Trailing Stop" and trailing_stop_search or order_type == "Cierre": 
                 order_instruction["price"] = "0"
                 order_instruction["stop_loss"] = "0.0" if not trailing_stop_search else trailing_stop_search.group(1)

        return order_instruction
    
    def catch_orders(self, telegram_message):
        # Esta función es síncrona (solo regex)
        try:
            asset_pattern = self.estrategia["asset_regex"]
        except KeyError:
            asset_pattern = r"[A-Z0-9]+" # Si el usuario no especifica un activo, asumimos que los quiere todos.
        orders = {
            "Buy Limit": rf"Buy limit Creada ({asset_pattern})",
            "Buy Limit ": rf"Buy Limit ({asset_pattern})",
            "Compra": rf"Compra\s({asset_pattern})",
            "Venta": rf"Venta\s({asset_pattern})",
            "Trailing Stop": rf"SL\s({asset_pattern})",
            "Cierre": rf"Cierre\s({asset_pattern})"
        }
        for order_type, order_match in orders.items():
            order_call = self.catch_order(telegram_message, order_type, order_match)
            if order_call: 
                return order_call
        return {}

    async def execute_order(self, telegram_message):
        order = self.catch_orders(telegram_message)
        if not order:
            print("No se detectó una orden válida en el mensaje.")
            return
        
        accept_order = strategy.Strategy(self.cobertura, order, **self.estrategia)

        if not await accept_order.filter_order():
            print(f"Orden para {order.get('asset')} rechazada por filtros de riesgo/distancia.")
            return

        print("Orden de trading: ", order)
        order_type = order.get("order_type")
        asset = order.get("asset")
        
        try:
            price = float(order.get("price", 0.0))
            stop_loss = float(order.get("stop_loss", 0.0))
            take_profit = float(order.get("take_profit", 0.0))
            volume = float(order.get("volume", 0.0))
        except ValueError:
            print("Error al convertir datos numéricos.")
            return
        
        if accept_order.try_with_min_vol:
            info_symbol = mt5.symbol_info(asset)
            volume  = info_symbol.volume_min # Se puede refactorizar para que el volumen mínimo sea obtenido una sola vez.

        if not order_type or not asset:
            print("La orden detectada no tiene tipo o activo. Omitiendo.")
            return
        
        if order_type == "Buy Limit":
            await self.my_trading_account.execute_buy_limit(asset, price, stop_loss, take_profit, volume)
        elif order_type == "Compra":
            await self.my_trading_account.execute_buy(asset, stop_loss, take_profit, volume)
        elif order_type == "Venta":
            await self.my_trading_account.execute_sell(asset, stop_loss, take_profit, volume)
        elif order_type == "Trailing Stop":
            await self.my_trading_account.execute_trailing_stop(asset, stop_loss)
        elif order_type == "Cierre":
            await self.my_trading_account.close_profit_trades(asset, stop_loss)
        
        # Gestionamos la cobertura después de realizar la orden
        if self.cobertura:
            await self.cobertura.gestionar_cobertura()

    async def filter_order_by_strategy(self, order):        
        my_strategy = strategy.Strategy(self.cobertura, order, **self.estrategia)
        return await my_strategy.filter_order()
    
    def calculate_volume(self, asset, price, stop_loss):
        try:
            risk = self.estrategia["risk"][asset]
        except (KeyError, TypeError) as e:
            print(f"Error detectado ({type(e).__name__}). Usando riesgo por defecto: 0.01")
            risk = 0.01
        try:
            price = float(price)
            stop_loss = float(stop_loss)
        except ValueError:
            print("No pudimos convertir el precio o stop loss a un valor numérico.")
            return 0.0 # No podemos utilizar price o stop loss, por lo que no ejecutamos la orden

        account_info = mt5.account_info()
        balance = account_info.balance if account_info else 0
        volume = round((risk * balance) / abs(price - stop_loss), 2)
        info_symbol = mt5.symbol_info(asset)
        if info_symbol is None:
                    print(f"Error: El símbolo {asset} no existe en el Market Watch de MT5.")
                    return 0.0
        min_volume  = info_symbol.volume_min
        if volume < min_volume:
            print("El riesgo de esta operación es mayor al esperado.")
            return 0.0
        else:
            return volume
        
    def get_market_price(self, symbol, price_search:str, order:str):
        """ Obtenemos el precio as, bid o el precio desde telegram según sea el caso. 
        
        Ask: Precio cuando es una compra a mercado
        Bid: Precio cuando es una venta a mercado
        price_search: Match de precio realizado con regex
        """
        last_tick = mt5.symbol_info_tick(symbol)
        if order == "Compra":
            return last_tick.ask
        elif order == "Venta":
            return last_tick.bid
        else:
            return price_search.group(1)




        
class PendingOperations(TradingOrder):
    """ Clase que maneja el mensaje de las operaciones pendientes """

    def __init__(self, my_trading_account, cobertura, estrategia):
        # __init__ debe ser ligero. No hacer llamadas MT5 aquí.
        self.pending_orders = None # Se cargará bajo demanda
        super().__init__(my_trading_account, cobertura, estrategia)
        
    def get_pending_operations_in_message(self, telegram_message)->set:
        """
        Devuelve un conjunto de strings. Cada string viene en la forma de activo>precio 
        """
        assets_in_message = set()
        split_orders = [line.strip() for line in telegram_message.splitlines() if line.strip().startswith("Buy Limit")]
        for line in split_orders:
            match = re.search(r"Buy Limit\s+([A-Z0-9]+)\s(\d+(\.\d+)?)", line)
            if not match: continue
            groups = match.groups()
            if len(groups):
                concat = groups[0] + ">" + groups[1]
                assets_in_message.add((line, concat))
        return assets_in_message
    
    async def get_pending_operations_in_trading_account(self):
        # Asíncrono, carga las órdenes solo cuando se necesita
        assets_in_account = set()
        self.pending_orders = await asyncio.to_thread(mt5.orders_get)
        
        if self.pending_orders is None:
            print("No se pudieron obtener órdenes pendientes de la cuenta.")
            self.pending_orders = [] # Asegurar que sea iterable
            return assets_in_account
            
        for order in self.pending_orders:
            info_order = f"{order.symbol}>{order.price_open}"
            assets_in_account.add(info_order)
        return assets_in_account
    
    async def add_new_pending_orders(self, telegram_message):
        message_orders_and_lines = self.get_pending_operations_in_message(telegram_message)
        message_orders = {i[1] for i in message_orders_and_lines}
        account_orders = await self.get_pending_operations_in_trading_account()
        
        new_orders = message_orders - account_orders
        if not new_orders: 
            print("No hay ordenes pendientes nuevas para agregar.")
            return
            
        for new_order in new_orders:
            asset, price = new_order.split(">")
            message_lines = [l for l, o in message_orders_and_lines if asset in o and price in o]
            for message in message_lines:
                await self.execute_order(message) # execute_order ya es async

    async def delete_old_pending_orders(self, telegram_message):
        message_orders_and_lines = self.get_pending_operations_in_message(telegram_message)
        message_orders = {i[1] for i in message_orders_and_lines}
        account_orders = await self.get_pending_operations_in_trading_account()
        
        delete_orders = account_orders - message_orders
        
        # self.pending_orders fue cargado por get_pending_operations_in_trading_account()
        if self.pending_orders is None: return 
        
        for order in self.pending_orders:
            info_order = f"{order.symbol}>{order.price_open}"
            if info_order in delete_orders:
                request = {
                    "action": mt5.TRADE_ACTION_REMOVE,
                    "order": order.ticket,
                }
                result = await asyncio.to_thread(mt5.order_send, request)
                if result.retcode != mt5.TRADE_RETCODE_DONE:
                    print(f"Error al eliminar orden {order.ticket}: {result.retcode}")
                else:
                    print(f"Orden {order.ticket} eliminada con éxito")

    async def manage_pending_orders(self, telegram_message):
        await self.delete_old_pending_orders(telegram_message)
        await self.add_new_pending_orders(telegram_message)


class TradingAccount:
    """ Clase para conectarse y operar en una cuenta de trading. """

    def __init__(self, account_type):
        # __init__ es síncrono. La inicialización de MT5 es bloqueante
        # pero se hace una sola vez al inicio, ANTES del bucle async.
        if not mt5.initialize():
            print("initialize() falló, error code =", mt5.last_error())
            quit()
        else:
            print("¡Conexión con MetaTrader 5 establecida con éxito!")
        self.account_type = account_type # Puede ser USD o USC
        self.crypto_symbols = ['BTCUSD', 'ETHUSD'] if account_type == "USC" else ['BTCUSD', 'ETHUSD']

    async def _get_trade_request(self, asset, order_type, volume, sl=0.0, tp=0.0, price=0.0):
        """
        Construye un diccionario de solicitud de trade (asíncrono).
        """
        if not await self._check_and_enable_symbol(asset):
            print(f"No se pudo obtener información para el símbolo {asset}")
            return None
        
        filling_mode = mt5.ORDER_FILLING_IOC if asset in self.crypto_symbols else mt5.ORDER_FILLING_FOK
        
        request = {
            "symbol": asset, "volume": volume, "sl": sl, "tp": tp,
            "magic": 1234, "deviation": 20,
            "type_time": mt5.ORDER_TIME_GTC, "type_filling": filling_mode,
        }

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

    async def execute_buy_limit(self, asset, price, stop_loss, take_profit, volume):
        if not await self._check_and_enable_symbol(asset): return
        request = await self._get_trade_request(asset, mt5.ORDER_TYPE_BUY_LIMIT, volume, sl=stop_loss, tp=take_profit, price=price)
        if request is None: return
        
        result = await asyncio.to_thread(mt5.order_send, request)
        
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            print("Error al enviar la orden Buy Limit.")
            await self.print_failed_operation(result)
        else:
            print("¡Orden Buy Limit enviada exitosamente!")
            print("Posición ticket: {}".format(result.order))

    async def execute_buy(self, asset, stop_loss, take_profit, volume):
        if not await self._check_and_enable_symbol(asset): return
        request = await self._get_trade_request(asset, mt5.ORDER_TYPE_BUY, volume, sl=stop_loss, tp=take_profit)
        if request is None: return
        
        result = await asyncio.to_thread(mt5.order_send, request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            print("Error al enviar la orden de Compra.")
            await self.print_failed_operation(result)
        else:
            print("¡Orden de Compra enviada exitosamente!")
            print("Posición ticket: {}".format(result.order))

    async def execute_sell(self, asset, stop_loss, take_profit, volume):
        if not await self._check_and_enable_symbol(asset): return
        request = await self._get_trade_request(asset, mt5.ORDER_TYPE_SELL, volume, sl=stop_loss, tp=take_profit)
        if request is None: return
        
        result = await asyncio.to_thread(mt5.order_send, request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            print("Error al enviar la orden de Venta.")
            await self.print_failed_operation(result)
        else:
            print("¡Orden de Venta enviada exitosamente!")
            print("Posición ticket: {}".format(result.order))

    async def execute_trailing_stop(self, asset, stop_loss):
        positions = await asyncio.to_thread(mt5.positions_get)
        position_found = False
        if positions is None:
            print("No se encontraron posiciones, código de error =", await asyncio.to_thread(mt5.last_error))
            return
        for position in positions:
            
            position_profit, min_profit = self.check_position_profit(asset, position, stop_loss)
            if position.symbol != asset:
                continue
            elif position_profit > 0 and position_profit < min_profit:
                print(f"{asset}:  ganancia mínima ({min_profit}) > ganancia esperada ({position_profit})")

            elif position_profit > min_profit:
                position_found = True
                print(f"Posición encontrada para {asset} con ticket {position.ticket} y ganancia de {position_profit:.2f}")
                position_type = position.type
                modificar_sl_compra = (position_type == 0 and 
                                    position.sl < stop_loss and 
                                    stop_loss > position.price_open)
                modificar_sl_venta = (position_type == 1 and
                                    (position.sl > stop_loss or position.sl == 0) and 
                                    stop_loss < position.price_open)
                
                if modificar_sl_compra or modificar_sl_venta:
                    print(f"  Modificando SL. Actual: {position.sl}, Nuevo: {stop_loss}")
                    request = {
                        "action": mt5.TRADE_ACTION_SLTP,
                        "position": position.ticket,
                        "sl": stop_loss,
                        "tp": position.tp,
                        "comment": "Trailing Stop Update",
                    }
                    result = await asyncio.to_thread(mt5.order_send, request)
                    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
                        print(f"Error al modificar el Stop Loss al precio {stop_loss}. Cerramos posiciones de {asset} en positivo.")
                        await self.close_order_with_profit(asset, position)
                    else:
                        print("  ¡Stop Loss modificado exitosamente!")
                else:
                    print(f"  No se requiere modificación de SL. Actual: {position.sl}, Propuesto: {stop_loss}")
        
        if not position_found:
            print(f"No se encontró una posición abierta y rentable para {asset}.")

    async def close_profit_trades(self, asset, stop_loss):
        positions = await asyncio.to_thread(mt5.positions_get)
        position_with_profit = False
        if positions is None:
            print("No se encontraron posiciones, código de error =", await asyncio.to_thread(mt5.last_error))
            return
                
        for position in positions:
            position_profit, min_profit = self.check_position_profit(asset, position, stop_loss)
            if position_profit > min_profit:
                position_with_profit = True
                await self.close_order_with_profit(asset, position)
        if not position_with_profit:
            print("No hay posiciones con la ganancia suficiente para cerrarla.")


    async def close_order_with_profit(self, asset, position):
        order_type_close = mt5.ORDER_TYPE_SELL if position.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
        filling_mode = mt5.ORDER_FILLING_IOC if asset in self.crypto_symbols else mt5.ORDER_FILLING_FOK

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "position": position.ticket,
            "symbol": asset,
            "volume": position.volume,
            "type": order_type_close,
            "deviation": 20, "magic": 1234,
            "comment": "Cierre con ganancia",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": filling_mode,
        }
        result = await asyncio.to_thread(mt5.order_send, request)

        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            print(f"Error al cerrar la posición {position.ticket}.")
            await self.print_failed_operation(result)
        else:
            print(f"¡Posición {position.ticket} para {asset} cerrada exitosamente!")
            closed_any = True
                        
            if not closed_any:
                print(f"No se encontraron posiciones con la rentabilidad deseada para {asset}.")

    def check_position_profit(self, asset, position, stop_loss)-> tuple:
        """ Calculamos la ganancia mínima que estamos dispuestos a aceptar para cerrar la operación.
         La función devuelve una tupla, donde el primer valor es el profit de la posición y el
         segundo valor es la ganancia mínima que estoy dispuesto a aceptar.
        """
        min_profit = 0.2
        position_type = position.type
        position_volume = position.volume
        position_price = position.price_open

        if position_type == 0: # Compra
            position_profit = round((stop_loss - position_price) * position_volume, 2)
        else:
            position_profit = round((position_price - stop_loss) * position_volume, 2)

        info_symbol = mt5.symbol_info(asset)
        min_volume  = info_symbol.volume_min
        min_profit = min_profit * (position_volume / min_volume)
        return position_profit, min_profit

    
    async def _check_and_enable_symbol(self, asset):
        symbol_info = await asyncio.to_thread(mt5.symbol_info, asset)
        if symbol_info is None:
            print(f"El símbolo {asset} no fue encontrado.")
            return False
        if not symbol_info.visible:
            if not await asyncio.to_thread(mt5.symbol_select, asset, True):
                return False
        return True

    async def print_failed_operation(self, result):
        if result is None:
            print("La operación falló antes de enviar la solicitud a MT5. Error:", await asyncio.to_thread(mt5.last_error))
            return
        print("Falló el envío de la orden, retcode={}".format(result.retcode))
        result_dict = result._asdict()
        for field, value in result_dict.items():
            print(f"  {field}={value}")
            if field == "request":
                traderequest_dict = value._asdict()
                for req_field, req_value in traderequest_dict.items():
                    print(f"    traderequest: {req_field}={req_value}")

# --- BUCLES ASÍNCRONOS Y LÓGICA PRINCIPAL ---

async def exit_gracefully():
    """
    Cancela todas las tareas de asyncio excepto la actual,
    permitiendo un apagado limpio.
    """
    print("Iniciando apagado elegante...")
    current_task = asyncio.current_task()
    tasks = [task for task in asyncio.all_tasks() if task is not current_task]
    
    if not tasks:
        print("No hay otras tareas que cancelar.")
        return

    print(f"Cancelando {len(tasks)} tareas pendientes...")
    for task in tasks:
        task.cancel()
    
    await asyncio.sleep(0.1) # Dar tiempo a que se procesen las cancelaciones
    print("Todas las tareas han sido señaladas para cancelación.")

def limpiar_terminal():
    # 'cls' para Windows, 'clear' para Linux/macOS
    os.system('cls' if os.name == 'nt' else 'clear')
    print("--- Terminal limpiado automáticamente ---")

async def daily_cleanup_loop(frequency_seconds=86400):
    """
    Bucle que limpia el terminal periódicamente.
    Por defecto, cada 24 horas.
    """
    try:
        while True:
            await asyncio.sleep(frequency_seconds)
            limpiar_terminal()
    except asyncio.CancelledError:
        print("Bucle de limpieza detenido.")

async def process_messages_loop(telegram_input, order_obj):
    """
    Espera y procesa mensajes de Telegram.
    """
    my_trading_account = order_obj.my_trading_account
    
    try:
        while True:
            message = await telegram_input.get_message()
            print("\nMensaje recibido:", message)
            telegram_message = message["text"]
            
            if telegram_message.lower() == "quit":
                print("Saliendo del programa. ¡Adiós! 👋")
                await exit_gracefully()
                break # Salir del bucle de mensajes

            if "ORDENES PENDIENTES" in telegram_message:
                pending_orders = PendingOperations(my_trading_account, order_obj.cobertura, order_obj.estrategia)
                await pending_orders.manage_pending_orders(telegram_message)
            else:
                await order_obj.execute_order(telegram_message)

    except asyncio.CancelledError:
        print("Bucle de mensajes detenido limpiamente.")
    except Exception as e:
        print(f"Error fatal en process_messages_loop: {e}")

async def monitor_coverage_loop(utilizar_cobertura, cobertura, frequency_seconds=15):
    """
    Bucle independiente que gestiona la cobertura periódicamente.
    """
    try:
        while utilizar_cobertura:
            try:
                await asyncio.sleep(frequency_seconds) 
                await cobertura.gestionar_cobertura()

            except asyncio.CancelledError:
                print("Monitor de cobertura detenido limpiamente.")
                break 
            except Exception as e:
                print(f"Error en el bucle de monitor_coverage_loop: {e}")
                await asyncio.sleep(60) 

    except asyncio.CancelledError:
        print("Monitor de cobertura detenido limpiamente.")


async def main():
    """Función principal para iniciar el bot y los monitores."""
    load_dotenv()
    api_id = int(os.getenv("TELEGRAM_API_ID"))
    api_hash = os.getenv("TELEGRAM_API_HASH")

    if not api_id or not api_hash:
        raise ValueError(
            "Asegúrate de que TELEGRAM_API_ID y TELEGRAM_API_HASH estén en .env"
        )

    telegram_input = TelegramInput(api_id, api_hash)
    
    # Iniciar la escucha de Telegram
    listener_task = asyncio.create_task(telegram_input.start_listening())
    

    # IMPORTANTE: En este punto, debemos descargar las preferencias del usuario para la estrategia
    # Puede que el usuario no quiera una cobertura, si no un stop-loss!
    # Estos parámetros están aquí mientras tanto. La idea es que se descarguen desde un campo rellenado por el usuario.

    utilizar_cobertura = False
    account_type = "USD"

    # Cambiar con sufijo "c" si estoy en cuenta Cent
    parametros_cobertura = {"asset": "BTCUSD", "account_type": account_type, "margen_cobertura": 400, "balance": 0, 
                            "break_even": 200, "trailing_stop": 400}
    parametros_estrategia = {"distance":{"BTCUSD": 0}, "pessimistic_resistance":{"BTCUSD": 0}, 
                             "risk": {"BTCUSD": 0.5}, "asset_regex": r"BTCUSD"}
    
    # Conectarse a la cuenta (esto es síncrono, se hace una vez)
    my_trading_account = TradingAccount(account_type)

    # Configurar la cobertura (síncrono, se hace una vez)
    cobertura = strategy.Coverage(**parametros_cobertura) if utilizar_cobertura else None
    order_obj = TradingOrder(my_trading_account, cobertura, parametros_estrategia)

    # --- Lanzamos las tareas concurrentes ---
    message_processor_task = asyncio.create_task(
        process_messages_loop(telegram_input, order_obj)
    )
    
    coverage_monitor_task = asyncio.create_task(
        monitor_coverage_loop(utilizar_cobertura, cobertura, frequency_seconds=15) 
    )

    cleanup_task = asyncio.create_task(daily_cleanup_loop())

    # Esperar a que todas las tareas se completen (o sean canceladas)
    try:
        await asyncio.gather(listener_task, message_processor_task, coverage_monitor_task, cleanup_task)
    except asyncio.CancelledError:
        print("El programa principal fue cancelado.")
    finally:
        # Asegurarse de que MT5 se apague limpiamente al final
        mt5.shutdown()
        print("Conexión con MetaTrader 5 cerrada. Apagado completado.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nApagado solicitado por el usuario (Ctrl+C).")