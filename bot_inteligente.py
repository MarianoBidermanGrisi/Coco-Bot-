import ccxt
import pandas as pd
import numpy as np
import time
import logging
import json
import requests
from datetime import datetime, timedelta

# Configuración logging
logging.basicConfig(level=logging.INFO, format='> %(message)s')

class BinanceCanalRegresionBot:
    def __init__(self, config):
        self.config = config
        self.operaciones_activas = {}  # Diccionario para seguir operaciones activas
        self.operaciones_cerradas = []  # Historial de operaciones
        
        self.exchange = ccxt.binance({
            'apiKey': config.get('api_key', ''),
            'secret': config.get('api_secret', ''),
            'enableRateLimit': True,
            'options': {'defaultType': 'future'},
        })
        
        # Formatear símbolos correctamente para CCXT
        self.pairs = self.formatear_simbolos(config['symbols'])
        
        logging.info(f"🤖 BOT CANAL REGRESIÓN INICIADO")
        logging.info(f"📈 Estrategia: LONG/SHORT en toques de canal")
        logging.info(f"⏰ Scan cada: {config['scan_interval_minutes']}min | Símbolos: {len(self.pairs)}")

    def enviar_telegram(self, mensaje):
        """Envía mensaje a Telegram"""
        token = self.config.get('telegram_token')
        chat_id = self.config.get('telegram_chat_id')
        
        if not token or not chat_id:
            return False
            
        try:
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            payload = {
                'chat_id': chat_id,
                'text': mensaje,
                'parse_mode': 'HTML'
            }
            response = requests.post(url, data=payload, timeout=10)
            return response.status_code == 200
        except Exception as e:
            logging.error(f"❌ Error enviando Telegram: {e}")
            return False

    def formatear_simbolos(self, symbols):
        """Formatea correctamente los símbolos para CCXT"""
        pairs_formateados = []
        for symbol in symbols:
            # Limpiar y formatear símbolo
            symbol_clean = symbol.upper().replace(' ', '').replace('/', '')
            
            # Corregir MATIC y otros símbolos comunes
            if 'MATIC' in symbol_clean and 'USDT' not in symbol_clean:
                symbol_clean = 'MATICUSDT'
            elif 'BTC' in symbol_clean and 'USDT' not in symbol_clean:
                symbol_clean = 'BTCUSDT'
            elif 'ETH' in symbol_clean and 'USDT' not in symbol_clean:
                symbol_clean = 'ETHUSDT'
                
            # Formato CCXT para futuros: "ADA/USDT:USDT"
            if symbol_clean.endswith('USDT'):
                base = symbol_clean.replace('USDT', '')
                pairs_formateados.append(f"{base}/USDT:USDT")
            else:
                pairs_formateados.append(f"{symbol_clean}/USDT:USDT")
                
        return pairs_formateados

    def obtener_precio_actual(self, symbol):
        """Obtiene el precio actual de un símbolo"""
        try:
            ticker = self.exchange.fetch_ticker(symbol)
            return ticker['last']
        except Exception as e:
            logging.error(f"Error obteniendo precio {symbol}: {e}")
            return None

    def verificar_sl_tp(self):
        """Verifica si alguna operación activa alcanzó SL o TP"""
        if not self.operaciones_activas:
            return
        
        operaciones_cerrar = []
        
        for op_id, operacion in self.operaciones_activas.items():
            symbol = operacion['symbol']
            precio_actual = self.obtener_precio_actual(symbol)
            
            if not precio_actual:
                continue
            
            sl = operacion['sl']
            tp = operacion['tp']
            señal = operacion['señal']
            precio_entrada = operacion['precio_entrada']
            
            # Verificar si se alcanzó SL o TP
            if señal == 'LONG':
                if precio_actual <= sl:
                    resultado = 'SL'
                    pnl_percent = ((precio_actual - precio_entrada) / precio_entrada) * 100
                elif precio_actual >= tp:
                    resultado = 'TP'
                    pnl_percent = ((precio_actual - precio_entrada) / precio_entrada) * 100
                else:
                    continue
                    
            else:  # SHORT
                if precio_actual >= sl:
                    resultado = 'SL'
                    pnl_percent = ((precio_entrada - precio_actual) / precio_entrada) * 100
                elif precio_actual <= tp:
                    resultado = 'TP'
                    pnl_percent = ((precio_entrada - precio_actual) / precio_entrada) * 100
                else:
                    continue
            
            # Operación a cerrar
            operacion['precio_salida'] = precio_actual
            operacion['resultado'] = resultado
            operacion['pnl_percent'] = pnl_percent
            operacion['fecha_salida'] = datetime.now().isoformat()
            
            operaciones_cerrar.append(op_id)
            
            # Enviar notificación
            emoji = "🔴" if resultado == 'SL' else "🟢"
            mensaje = f"""{emoji} <b>OPERACIÓN CERRADA - {resultado}</b>

📊 Par: {symbol}
🎯 Dirección: {señal}
💰 Entrada: {precio_entrada:.4f}
💸 Salida: {precio_actual:.4f}
🛡️ SL: {sl:.4f}
🎯 TP: {tp:.4f}

📈 PnL: {pnl_percent:+.2f}%"""

            if self.enviar_telegram(mensaje):
                logging.info(f"✅ Notificación {resultado} enviada a Telegram")
            else:
                logging.info(f"📢 Operación cerrada por {resultado} | PnL: {pnl_percent:+.2f}%")
        
        # Cerrar operaciones
        for op_id in operaciones_cerrar:
            operacion_cerrada = self.operaciones_activas.pop(op_id)
            self.operaciones_cerradas.append(operacion_cerrada)

    def calcular_canal_regresion(self, df):
        """Calcula el canal de regresión lineal"""
        length = self.config['regression_length']
        
        if len(df) < length:
            return None, None, None, 0, 0
        
        # Tomar los últimos precios
        prices = df['close'].tail(length).values
        
        # Array de tiempo
        x = np.arange(len(prices))
        
        # Regresión lineal del cierre
        slope, intercept = np.polyfit(x, prices, 1)
        
        # Calcular canal: línea central + desviación estándar
        regression_line = slope * x + intercept
        residuals = prices - regression_line
        std_dev = np.std(residuals)
        
        # Líneas del canal
        upper_band = regression_line + std_dev
        lower_band = regression_line - std_dev
        
        # Ángulo de tendencia
        angle = np.degrees(np.arctan(slope / np.mean(prices)))
        
        return upper_band, regression_line, lower_band, angle, slope

    def precio_toca_canal(self, precio_actual, upper_band, lower_band):
        """Verifica si el precio toca el canal"""
        threshold_percent = self.config['touch_threshold'] / 100
        current_upper = upper_band[-1]
        current_lower = lower_band[-1]
        
        # Verificar toque en parte superior
        touch_upper = abs(precio_actual - current_upper) / current_upper <= threshold_percent
        
        # Verificar toque en parte inferior  
        touch_lower = abs(precio_actual - current_lower) / current_lower <= threshold_percent
        
        return touch_upper, touch_lower

    def calcular_sl_tp_canal(self, señal, precio_entrada, upper_band, lower_band):
        """Calcula SL y TP basado en el canal"""
        current_upper = upper_band[-1]
        current_lower = lower_band[-1]
        sl_percentage = self.config['sl_percentage'] / 100
        
        if señal == 'LONG':
            # SL: 1% por debajo del canal inferior
            sl_price = current_lower * (1 - sl_percentage)
            # TP: Parte superior del canal
            tp_price = current_upper
            
        else:  # SHORT
            # SL: 1% por encima del canal superior
            sl_price = current_upper * (1 + sl_percentage)
            # TP: Parte inferior del canal  
            tp_price = current_lower
        
        return sl_price, tp_price

    def obtener_datos_futuros(self, symbol):
        """Obtiene datos de futuros"""
        try:
            total_bars = self.config['regression_length'] + 50
            
            ohlcv = self.exchange.fetch_ohlcv(
                symbol, 
                self.config['timeframe'], 
                limit=total_bars
            )
            
            if len(ohlcv) == 0:
                return None
                
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            return df
            
        except Exception as e:
            logging.error(f"    {symbol}...    ERROR: {str(e)}")
            return None

    def analizar_par(self, symbol):
        """Analiza si hay señal en el canal de regresión"""
        # Verificar si ya tenemos una operación activa en este par
        for op in self.operaciones_activas.values():
            if op['symbol'] == symbol:
                return None  # Ya hay operación activa en este par
        
        df = self.obtener_datos_futuros(symbol)
        
        if df is None or len(df) < self.config['regression_length']:
            return None
        
        # Calcular canal de regresión
        upper_band, midline, lower_band, angle, slope = self.calcular_canal_regresion(df)
        
        if upper_band is None:
            return None
        
        # Verificar tendencia significativa
        if abs(angle) < self.config['min_trend_angle']:
            return None  # Canal muy plano, ignorar
        
        # Filtrar por volumen
        avg_volume = df['volume'].tail(20).mean()
        if avg_volume < self.config['min_volume']:
            return None
        
        # Precio actual
        precio_actual = df['close'].iloc[-1]
        
        # Verificar toques del canal
        touch_upper, touch_lower = self.precio_toca_canal(precio_actual, upper_band, lower_band)
        
        señal = None
        
        # SEÑAL LONG: Canal ALCISTA + Toca parte INFERIOR
        if slope > 0 and touch_lower:
            señal = 'LONG'
            
        # SEÑAL SHORT: Canal BAJISTA + Toca parte SUPERIOR  
        elif slope < 0 and touch_upper:
            señal = 'SHORT'
        
        if señal:
            # Calcular SL y TP
            sl_price, tp_price = self.calcular_sl_tp_canal(señal, precio_actual, upper_band, lower_band)
            
            # Calcular riesgo y reward
            if señal == 'LONG':
                riesgo = precio_actual - sl_price
                reward = tp_price - precio_actual
            else:
                riesgo = sl_price - precio_actual  
                reward = precio_actual - tp_price
            
            risk_reward = reward / riesgo if riesgo > 0 else 0
            
            # Crear operación
            operacion_id = f"{symbol}_{int(time.time())}"
            operacion = {
                'id': operacion_id,
                'symbol': symbol,
                'señal': señal,
                'precio_entrada': precio_actual,
                'sl': sl_price,
                'tp': tp_price,
                'risk_reward': risk_reward,
                'angulo_canal': angle,
                'pendiente': slope,
                'volumen': avg_volume,
                'tipo_canal': 'ALCISTA' if slope > 0 else 'BAJISTA',
                'fecha_entrada': datetime.now().isoformat()
            }
            
            # Enviar notificación de entrada
            mensaje_entrada = f"""🎯 <b>NUEVA SEÑAL - {señal}</b>

📊 Par: {symbol}
💰 Precio: {precio_actual:.4f}
🛡️ SL: {sl_price:.4f}
🎯 TP: {tp_price:.4f}
📊 R/R: {risk_reward:.2f}
📈 Ángulo: {angle:.2f}°
🔢 Canal: {self.config['regression_length']} velas"""

            if self.enviar_telegram(mensaje_entrada):
                logging.info(f"✅ Notificación entrada enviada a Telegram")
            
            return operacion
        
        return None

    def obtener_estadisticas(self):
        """Calcula estadísticas de las operaciones"""
        if not self.operaciones_cerradas:
            return "Sin operaciones cerradas"
        
        total_ops = len(self.operaciones_cerradas)
        ops_ganadoras = sum(1 for op in self.operaciones_cerradas if op['resultado'] == 'TP')
        ops_perdedoras = sum(1 for op in self.operaciones_cerradas if op['resultado'] == 'SL')
        win_rate = (ops_ganadoras / total_ops) * 100 if total_ops > 0 else 0
        
        pnl_total = sum(op['pnl_percent'] for op in self.operaciones_cerradas)
        
        return f"Win Rate: {win_rate:.1f}% | Ops: {total_ops} | PnL: {pnl_total:+.2f}%"

    def ejecutar_analisis(self):
        """Ejecuta análisis completo"""
        # Primero verificar SL/TP de operaciones activas
        self.verificar_sl_tp()
        
        logging.info(f"ANALISIS CANAL REGRESIÓN - {self.config['modalidad'].upper()}")
        logging.info(f"Config: {self.config['regression_length']} velas | SL {self.config['sl_percentage']}% | Scan: {self.config['scan_interval_minutes']}min")
        logging.info(f"Ops activas: {len(self.operaciones_activas)} | {self.obtener_estadisticas()}")
        logging.info("=" * 70)
        
        señales = 0
        symbols_scanned = 0
        
        for pair in self.pairs[:self.config['max_symbols_to_scan']]:
            operacion = self.analizar_par(pair)
            symbols_scanned += 1
            
            if operacion:
                # Agregar operación a activas
                self.operaciones_activas[operacion['id']] = operacion
                
                logging.info(f"    {pair}...    SEÑAL {operacion['señal']} | Canal {operacion['tipo_canal']}")
                logging.info(f"        💰 Entrada: {operacion['precio_entrada']:.4f}")
                logging.info(f"        🛡️  SL: {operacion['sl']:.4f} | 🎯 TP: {operacion['tp']:.4f}")
                logging.info(f"        📊 R/R: {operacion['risk_reward']:.2f} | Ángulo: {operacion['angulo_canal']:.2f}°")
                señales += 1
            else:
                logging.info(f"    {pair}...    Sin señal")
        
        logging.info(f"ANALISIS COMPLETADO. Escaneados: {symbols_scanned} | Nuevas señales: {señales}")
        logging.info(f"Ops activas total: {len(self.operaciones_activas)}")
        logging.info(f"Próximo análisis en {self.config['scan_interval_minutes']} minutos")
        logging.info("")

def cargar_configuracion():
    """Carga la configuración desde archivo"""
    try:
        with open('config_binance_canal.json', 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        print("❌ No se encontró config_binance_canal.json - Ejecuta el configurador primero")
        return None

def main():
    config = cargar_configuracion()
    if not config:
        return
    
    bot = BinanceCanalRegresionBot(config)
    
    while True:
        try:
            bot.ejecutar_analisis()
            time.sleep(config['scan_interval_minutes'] * 60)
            
        except KeyboardInterrupt:
            logging.info("Deteniendo bot...")
            break
        except Exception as e:
            logging.error(f"Error general: {e}")
            time.sleep(60)

if __name__ == "__main__":
    main()
    
