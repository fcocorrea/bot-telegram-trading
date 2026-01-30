import MetaTrader5 as mt5
from collections import defaultdict
import numpy as np
import re
import asyncio

class Strategy:
    """ Filtramos por ciertos criterios definidos por el usuario.
    En la presente versión, los filtros son:
    - cobertura: Es el objeto de cobertura, que nos será util para extraer balance, cálculo de stop out, entre otros.
    - order: Es un diccionario con las características de la orden que viene de Telegram. 
    - asset_regex: Qué activo quiere operar el usuario. Por defecto es [A-Z0-9]+
    - distance: Distancia en precio del activo a otra ya creada. Si es menor a x número, lo desechamos la operación.
        * Es un dict en la forma de {"asset":"distancia_minima}
        * Si esta resistencia pesimista tiene valores cero , se toma el valor del stop loss como resistencia pesimista.
        * Si la resistencia pesimista es None, no se considera como filtro.
    - pessimistic_resistance: Es el precio minimo que queremos aguantar con las operaciones abiertas de diferentes activos.
    Si el valor mínimo es cero, tomamos como valor el stop loss de la operación.
        * Es un dict en la forma de {"asset":resistencia_pesimista}
    - risk: Es un diccionario cuya llave es el activo de la orden y el valor del riesgo que el usuario quiere tomar
    """

    def __init__(self, cover, order, distance:dict, pessimistic_resistance:dict, risk:dict, asset_regex=r"[A-Z0-9]+"):
        self.cover = cover
        self.distance = distance
        self.pessimistic_resistance = pessimistic_resistance
        self.risk = risk # Riesgo por operación
        self.asset_regex = asset_regex

        numeric_data = ["price", "stop_loss"]
        
        for order_feature, order_value in order.items():
            # {"order_type": "Orden del movimiento", "asset": "Nemo", "price": "Precio de apertura", "stop_loss": "Stop Loss"}
            if order_feature in numeric_data:
                try:
                    order_value = float(order_value)
                except (ValueError, TypeError):
                    order_value = 0.0
            
            setattr(self, order_feature, order_value)
    
    async def filter_order(self)->bool:
        """ 
        Vemos si una orden está lo suficientemente alejada de las otras ordenes activas y pendientes.
        Si está suficientemente alejada bajo el criterio del usuario, retornará True. De lo contrario, False
        """

        all_orders = await self.cover.get_all_orders()
        trailing_stop_order = self.order_type == "Trailing Stop" # No filtraremos los trailing stop, dado que no son ordenes persé
        not_a_asset_match   = not re.search(self.asset_regex, self.asset) # Si el activo de la orden no es el que queremos, no la ejecutaremos

        if not_a_asset_match: 
            print(f"Activo {self.asset} no considerado por el usuario para realizar ordenes.")
            return False
        if trailing_stop_order: return True

        for asset, orders_list in all_orders.items():
            if self.asset == asset:

                proper_distance = self.__check_proper_distance(orders_list)
                if proper_distance == False: return proper_distance
                proper_risk_exposure = await self.__check_risk_exposure(orders_list, self.volume)
                return proper_risk_exposure
        return True # Si no hay ordenes pendientes o activas, retornamos True

    def __check_proper_distance(self, orders_list: dict)->bool:
        """
        Verifica si la distancia entre el precio de la orden y sus colindantes en la lista 
        ordenada es mayor a 'min_distance' en ambos casos. (Síncrona - solo cálculos)
        """
        prices = [price for price, _ in orders_list]
        min_distance = self.distance[self.asset]
        proper_distance = all([abs(price - self.price) > min_distance for price in prices])

        if proper_distance == False:
            print(f"El precio {self.price} del activo {self.asset} está a menos de {min_distance} USD entre sus colindantes. No se realiza la operación.")
        return proper_distance
    
    async def __check_risk_exposure(self, orders_list: dict, op_volume:float)->bool:
        """
        Verifica si con la operación que estamos por ejecutar, aguantamos hasta la resistencia pesimista.
        (Asíncrona porque consulta el balance de la cuenta)
        """

        if self.pessimistic_resistance == None and self.cover == None: return True # El usuario quiere ir con todo
        new_operation = (self.price, op_volume)
        orders_list.append(new_operation)
        pessimistic_resistance = self.pessimistic_resistance[self.asset]
        if pessimistic_resistance == 0: pessimistic_resistance = self.stop_loss
        
        info = mt5.symbol_info(self.asset)
        vol_min = info.volume_min
        
        account_info = await asyncio.to_thread(mt5.account_info)
        balance = account_info.balance

        if self.cover == None: # El usuario no quiere utilizar una estrategia de cobertura, si no de stop loss.        
            for price, volume in orders_list:
                balance -= (price - pessimistic_resistance) * volume
                if balance <= 0:
                    if op_volume != vol_min:
                        # Si la operación nos deja con un riesgo de SO, probamos con lotaje mínimo
                        print("Probamos con lotaje mínimo para menor exposición de riesgo...")
                        self.__check_risk_exposure(orders_list[:-1], vol_min)
                    else:
                        print(f"Orden rechazada: La orden \"{self.order_type}\" del activo {self.asset} con precio {self.price} deja una exposición mayor a la permitida.")
                        return False
        else:
            precio_cobertura = self.cover.calcular_cobertura(orders_list, balance)
            # Si la distancia entre el precio de la orden y el precio de cobertura es mayor al margen de la cobertura, se acepta la orden
            es_valida = self.price - precio_cobertura > self.cover.margen_cobertura
            if not es_valida:
                print(f"Orden rechazada: El precio de la orden ({self.price}) está por debajo o muy cerca de la cobertura actualizada ({precio_cobertura:.2f}).")
                return False
        return True # Todos los filtros pasaron exitosamente

class Coverage:

    """ La estrategia de cobertura tiene como principal objetivo evitar que una cuenta de trading quede en
      stop out. Esto es posible gracias a operaciones que van en sentido contrario de las que ya están abiertas. 
      Por ejemplo, si tengo una compra de 1 lote, y veo que el precio ha bajado mucho y me está comiendo toda mi liquidez, 
      puedo hacer una venta por un lote, Esto hace que mi capital restante quedé estable, dado que estoy haciendo una operación 
      que va en una dirección a cierta magnitud y otra que va en la dirección contraria con la misma magnitud, 
      haciendo que pierda y gane en igual proporción.
      
      1. Por el momento, se asume que las operaciones son solo compras y de un activo. Esto para que el cálculo del stop out sea el correcto.
      2. Para evitar quedar en stop out, SIEMPRE debe haber una orden pendiente que funcione como cobertura.
      3. La idea no es ganar dinero con la cobertura, si no que protegerse del stop out. En consecuencia, la cobertura tendrá por defecto solo break even.
    
      Argumentos:
      - asset: Es el activo que estamos evaluando para crear la cobertura. Se asume que el usuario tiene en su cuenta un solo activo para que funcione bien la cobertura.
      - margen_cobertura: Es un float que corresponda a la distancia entre el stop out y la cobertura, multiplicada por la cantidad de ordenes activas y pendientes
      - balance: Es el balance de la cuenta. Por defecto es cero y se irá actualizando.
      - break_even: Corresponde a qué distancia con el precio de apertura activamos el break-even. Si es cero no lo activamos.
      - trailing_stop: Corresponde a qué distancia con el precio de apertura comenzamos a seguir el precio. Si es cero no lo activamos (su valor por defecto).

      """

    def __init__(self, asset:str, margen_cobertura:float, balance:float=0, break_even:float=0, trailing_stop:float=0):
        self.asset = asset # para que funcione, debo tener un solo asset en cartera.
        self.balance = balance
        self.trailing_stop = trailing_stop
        self.break_even = break_even
        self.margen_cobertura = margen_cobertura
        self.ticket_cobertura = 0
        self.crypto_symbols = ['BTCUSD', 'ETHUSD']
        self.ultimo_precio_cobertura = 0

        # --- Variables de control de cobertura ---
        self.volumen_cobertura = 0
        self.volumen_total = 0
        self.cantidad_de_ordenes = 0
        self.cobertura_activa = False

    async def get_active_orders(self):
        group_active_orders = defaultdict(list)
        positions = await asyncio.to_thread(mt5.positions_get)
        
        if positions is not None:
            for position in positions:
                if position.comment == "cobertura":
                    self.cobertura_activa = True
                    self.ticket_cobertura = position.ticket
                    self.volumen_cobertura = position.volume
                else:
                    volume = position.volume
                    stop_loss = position.sl
                    price = position.price_open
                    if stop_loss >= price: continue # Ignoramos esta posición al no tener riesgos asociados.
                    self.volumen_total += volume
                    self.cantidad_de_ordenes += 1
                    group_active_orders[position.symbol].append((position.price_open, volume))
        return group_active_orders

    async def get_pending_orders(self):
        group_pending_orders = defaultdict(list)
        orders = await asyncio.to_thread(mt5.orders_get)
        
        if orders is not None:
            for order in orders:
                if order.comment == "cobertura":
                    self.cobertura_activa = False
                    self.ticket_cobertura = order.ticket
                    self.volumen_cobertura = order.volume_initial
                else:
                    volume = order.volume_initial
                    stop_loss = order.sl
                    price = order.price_open
                    if stop_loss >= price: continue # Ignoramos esta posición al no tener riesgos asociados.
                    self.volumen_total += volume
                    self.cantidad_de_ordenes += 1
                    group_pending_orders[order.symbol].append((order.price_open, volume))
        return group_pending_orders     

    async def get_all_orders(self)->dict:
        """
        Recupera todas las posiciones activas y órdenes pendientes de forma no bloqueante.

        La estructura es:

            {'Activo': [(Precio de apertura, Lotaje)], }

        """
        all_orders = defaultdict(list)
        active_orders = await self.get_active_orders()
        pending_orders = await self.get_pending_orders()
        
        for key, value_list in active_orders.items():
            all_orders[key].extend(value_list)
        for key, value_list in pending_orders.items():
            all_orders[key].extend(value_list)
        return dict(all_orders)

    async def gestionar_cobertura(self):

        # Reseteamos los valores antes de recalcular
        self.volumen_total = 0
        self.cantidad_de_ordenes = 0
        self.volumen_cobertura = 0

        account_info = await asyncio.to_thread(mt5.account_info)
        balance_actual = account_info.balance
        orders_list = await self.get_all_orders()
        try:
            orders_list = orders_list[self.asset]
        except KeyError:
            # print(f"No hay ordenes de {self.asset} activas o pendientes. No se crea una cobertura.")
            return None
        
        if self.cobertura_activa:
            await self.gestionar_cobertura_activa()

        else:
            if self.volumen_total == 0 and self.volumen_cobertura > 0:
                # Hay una cobertura, pero no hay posiciones activas ni pendientes, por lo tanto, debemos eliminar la cobertura.
                order_info_tuple = await asyncio.to_thread(mt5.orders_get, ticket=self.ticket_cobertura)
                await self.eliminar_cobertura_pendiente(order_info_tuple[0])

            elif self.balance != balance_actual and self.volumen_total == self.volumen_cobertura:
                await self.modificar_cobertura_pendiente(orders_list, balance_actual)

            elif self.volumen_total > 0 and self.volumen_cobertura:
                await self.crear_cobertura(orders_list, balance_actual)
        
        self.balance = balance_actual

    async def crear_cobertura(self, orders_list, balance):
        precio_cobertura = self.calcular_cobertura(orders_list, balance)
        precio_bid = await self.obtener_precio_bid()
        if precio_bid == 0: return None

        if precio_bid - precio_cobertura < self.margen_cobertura:
            precio_cobertura = precio_bid - self.margen_cobertura

        filling_mode = mt5.ORDER_FILLING_IOC if self.asset in self.crypto_symbols else mt5.ORDER_FILLING_FOK
        request = {
            "action": mt5.TRADE_ACTION_PENDING,
            "symbol": self.asset,
            "volume": self.volumen_total,
            "type": mt5.ORDER_TYPE_SELL_STOP,
            "price": precio_cobertura,
            "sl": 0.0, "tp": 0.0, "magic": 1234,
            "comment": "cobertura",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": filling_mode,
        }
        
        result = await asyncio.to_thread(mt5.order_send, request)

        if result == None or result.retcode != mt5.TRADE_RETCODE_DONE:
            last_error = await asyncio.to_thread(mt5.last_error)
            print(f"Detalle del último error de MT5: {last_error}")
        else:
            self.ticket_cobertura = result.order
            print("¡Cobertura creada con éxito!")

    async def eliminar_cobertura_pendiente(self, order_info):
        request = {
                "action": mt5.TRADE_ACTION_REMOVE,
                "order": self.ticket_cobertura,
                "symbol": order_info.symbol,
            }
        result = await asyncio.to_thread(mt5.order_send, request)
        
        if result == None or result.retcode != mt5.TRADE_RETCODE_DONE:
            print("Error al intentar eliminar la cobertura para crear otra nueva")
            print(f"Detalle del último error de MT5: {await asyncio.to_thread(mt5.last_error)}")
            return False
        else:
            return True

    async def obtener_precio_bid(self):
        tick = await asyncio.to_thread(mt5.symbol_info_tick, self.asset)
        if tick is None:
            print(f"No se pudo obtener el tick para {self.asset}")
            return 0
        return tick.bid

    async def obtener_precio_cobertura_activa(self):
        tick = await asyncio.to_thread(mt5.symbol_info_tick, self.asset)
        if tick is None:
            print(f"No se pudo obtener el tick para {self.asset}")
            return 0
        return tick.bid  

    def calcular_stop_out(self, orders_list, balance):
        # Esta función es solo matemática, no necesita ser async

        if not orders_list: return 0 # Evitar división por cero si la lista está vacía
        
        weighted_prices = sum(price * volume for price, volume in orders_list)
        suma_volumen = sum(volume for _, volume in orders_list)
        
        if suma_volumen == 0: return 0 # Evitar división por cero
        
        weighted_price = weighted_prices / suma_volumen
        stop_out = round(weighted_price - (balance / suma_volumen), 2)
        return stop_out

    def calcular_cobertura(self, orders_list, balance):
        # Esta función es solo matemática, no necesita ser async
        stop_out = self.calcular_stop_out(orders_list, balance)
        margen_total = self.cantidad_de_ordenes * self.margen_cobertura
        cobertura = stop_out + margen_total
        return cobertura    

    async def modificar_cobertura_pendiente(self, orders_list, balance):
        order_info_tuple = await asyncio.to_thread(mt5.orders_get, ticket=self.ticket_cobertura)
        
        if order_info_tuple is None or len(order_info_tuple) == 0: 
            print(f"No se reconoció el ticket {self.ticket_cobertura} para modificar su precio. No se realiza ninguna acción.")
            return False
            
        order_info = order_info_tuple[0]
        nuevo_precio_cobertura = self.calcular_cobertura(orders_list, balance)
        if nuevo_precio_cobertura != self.ultimo_precio_cobertura:
            self.ultimo_precio_cobertura = nuevo_precio_cobertura
            request = {
                "action": mt5.TRADE_ACTION_MODIFY,
                "order": self.ticket_cobertura,
                "symbol": self.asset,
                "price": nuevo_precio_cobertura,
                "sl": order_info.sl,
                "tp": order_info.tp,
                "type_filling": order_info.type_filling,
                "type_time": order_info.type_time,
            }
            result = await asyncio.to_thread(mt5.order_send, request)
            return True if result.retcode == mt5.TRADE_RETCODE_DONE else False


    async def gestionar_cobertura_activa(self):
        precio_bid = await self.obtener_precio_bid()
        position_info_tuple = await asyncio.to_thread(mt5.positions_get, ticket=self.ticket_cobertura)
        
        if not position_info_tuple:
            print("No se pudo obtener información de la cobertura activa con el ticket proporcionado. No podemos gestionarla.")
            return
            
        info_cobertura = position_info_tuple[0]
        precio_cobertura = info_cobertura.price_open
        stop_loss_cobertura = info_cobertura.sl
        diferencia_apertura = precio_cobertura - precio_bid
        
        if diferencia_apertura > self.break_even > 0 and precio_cobertura > stop_loss_cobertura:
            await self.implementar_break_even(info_cobertura, precio_cobertura)
        elif self.trailing_stop > 0 and precio_bid - self.trailing_stop > precio_cobertura:
            await self.implementar_take_profit(info_cobertura)
        
    async def implementar_break_even(self, info_cobertura, precio_apertura):
        request = {
                "action": mt5.TRADE_ACTION_SLTP,
                "position": self.ticket_cobertura,
                "symbol": info_cobertura.symbol,
                "sl": precio_apertura,
                "tp": info_cobertura.tp,
            }
        result = await asyncio.to_thread(mt5.order_send, request)
        
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            print(f"Error al modificar el SL para el break even.\nCódigo del error: {result.retcode}\nMensaje: {result.comment}")
        else:
            print("Break Even de la cobertura implementado exitosamente!")

    async def implementar_take_profit(self, info_cobertura):
        precio_cobertura = info_cobertura.price_open
        nuevo_stop_loss = precio_cobertura + self.trailing_stop
        request = {
                "action": mt5.TRADE_ACTION_SLTP,
                "position": self.ticket_cobertura,
                "symbol": info_cobertura.symbol,
                "sl": nuevo_stop_loss,
                "tp": info_cobertura.tp,
            }
        result = await asyncio.to_thread(mt5.order_send, request)
        
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            print(f"Error al modificar el SL para el trailing stop\nCódigo del error: {result.retcode}\nMensaje: {result.comment}")
        else:
            print("Trailing Stop de la cobertura implementado exitosamente!")
