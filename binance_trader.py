# binance_trader.py
from binance.client import Client
from binance.exceptions import BinanceAPIException
import logging

logger = logging.getLogger(__name__)

class BinanceTrader:
    def __init__(self, api_key, secret_key, testnet=True):
        """
        Inicializa el cliente de Binance.
        :param api_key: Tu API Key de Binance.
        :param secret_key: Tu Secret Key de Binance.
        :param testnet: True para usar la Testnet (recomendado para pruebas), False para real.
        """
        if testnet:
            # URLs para la Testnet de Binance Futures
            self.client = Client(api_key, secret_key, tld='com', testnet=True)
            logger.info("🧪 BinanceTrader inicializado en MODO TESTNET.")
        else:
            # URLs para la cuenta real de Binance Futures
            self.client = Client(api_key, secret_key, tld='com')
            logger.warning("🚨 BinanceTrader inicializado en MODO REAL. 🚨")

    def check_connection(self):
        """Verifica si la conexión con la API es válida."""
        try:
            # El método ping_server es una forma ligera de verificar la conectividad.
            self.client.ping()
            # Obtener info del servidor también valida las claves API
            server_status = self.client.get_system_status()
            if server_status['status'] == 0:
                logger.info("✅ Conexión con la API de Binance exitosa y sistema operativo.")
                return True
            else:
                logger.error(f"❌ El sistema de Binance no está operativo: {server_status['msg']}")
                return False
        except BinanceAPIException as e:
            logger.error(f"❌ Error de API de Binance al verificar conexión: {e}")
            return False
        except Exception as e:
            logger.error(f"❌ Error inesperado al conectar con Binance: {e}")
            return False

    def set_leverage(self, symbol, leverage):
        """
        Establece el apalancamiento para un símbolo específico.
        :param symbol: Ej. 'BTCUSDT'
        :param leverage: El apalancamiento deseado (ej. 5).
        :return: True si tiene éxito, False si falla.
        """
        try:
            self.client.futures_change_leverage(symbol=symbol, leverage=leverage)
            logger.info(f"✅ Apalancamiento establecido en {leverage}x para {symbol}.")
            return True
        except BinanceAPIException as e:
            logger.error(f"❌ Error de API de Binance al establecer apalancamiento para {symbol}: {e}")
            return False
        except Exception as e:
            logger.error(f"❌ Error inesperado al establecer apalancamiento para {symbol}: {e}")
            return False

    def get_account_info(self):
        """Obtiene la información de la cuenta de futuros."""
        try:
            account_info = self.client.futures_account()
            return account_info
        except BinanceAPIException as e:
            logger.error(f"❌ Error al obtener información de la cuenta: {e}")
            return None

    def place_market_order(self, symbol, side, quantity):
        """
        Coloca una orden de mercado en el mercado de Futuros de Binance.
        :param symbol: Ej. 'BTCUSDT'
        :param side: 'BUY' o 'SELL'
        :param quantity: La cantidad a comprar/vender.
        :return: La respuesta de la API si tiene éxito, None si falla.
        """
        try:
            logger.info(f"📈 Enviando orden de mercado a Binance: {side} {quantity} {symbol}")
            order = self.client.futures_create_order(
                symbol=symbol,
                side=side,
                type='MARKET',
                quantity=quantity
            )
            logger.info(f"✅ Orden enviada con éxito. ID de Orden: {order['orderId']}")
            return order
        except BinanceAPIException as e:
            logger.error(f"❌ Error de API de Binance al colocar orden: {e}")
            return None
        except Exception as e:
            logger.error(f"❌ Error inesperado al colocar orden: {e}")
            return None

    def place_stop_loss_order(self, symbol, side, quantity, stop_price):
        """
        Coloca una orden de stop-loss para una posición abierta.
        :param symbol: Ej. 'BTCUSDT'
        :param side: 'BUY' (para cerrar SHORT) o 'SELL' (para cerrar LONG)
        :param quantity: La cantidad de la posición a cerrar.
        :param stop_price: El precio de activación del stop-loss.
        :return: La respuesta de la API si tiene éxito, None si falla.
        """
        try:
            logger.info(f"🛑 Colocando orden de Stop-Loss: {side} {quantity} {symbol} a {stop_price}")
            order = self.client.futures_create_order(
                symbol=symbol,
                side=side,
                type='STOP_MARKET',
                stopPrice=stop_price,
                closePosition='true' # Cierra la posición automáticamente
            )
            logger.info(f"✅ Stop-Loss colocado con éxito. ID de Orden: {order['orderId']}")
            return order
        except BinanceAPIException as e:
            logger.error(f"❌ Error de API de Binance al colocar Stop-Loss: {e}")
            return None
        except Exception as e:
            logger.error(f"❌ Error inesperado al colocar Stop-Loss: {e}")
            return None
