# bot_web_service.py
# Adaptación para Render + Binance Testnet + sin gráficos + Diagnóstico de Telegram
# ✅ CORREGIDO: órdenes huérfanas, cierres reales, error -2021, notificaciones
import requests
import time
import json
import os
import sys
from datetime import datetime, timedelta
import numpy as np
import math
import csv
import itertools
import statistics
import random
from flask import Flask, request, jsonify
import threading
import logging

# --- MÓDULO BINANCE TRADER (EMBEDDED) ---
from binance.client import Client
from binance.exceptions import BinanceAPIException

logger_binance = logging.getLogger("BinanceTrader")

class BinanceTrader:
    def __init__(self, api_key, secret_key, testnet=True):
        if testnet:
            self.client = Client(api_key, secret_key, tld='com', testnet=True)
            logger_binance.info("🧪 BinanceTrader inicializado en MODO TESTNET.")
        else:
            self.client = Client(api_key, secret_key, tld='com')
            logger_binance.warning("🚨 BinanceTrader inicializado en MODO REAL. 🚨")

    def check_connection(self):
        try:
            self.client.ping()
            server_status = self.client.get_system_status()
            if server_status['status'] == 0:
                logger_binance.info("✅ Conexión con la API de Binance exitosa.")
                return True
            else:
                logger_binance.error(f"❌ Sistema de Binance no operativo: {server_status['msg']}")
                return False
        except Exception as e:
            logger_binance.error(f"❌ Error al conectar con Binance: {e}")
            return False

    def set_leverage(self, symbol, leverage):
        try:
            self.client.futures_change_leverage(symbol=symbol, leverage=leverage)
            logger_binance.info(f"✅ Apalancamiento {leverage}x establecido para {symbol}.")
            return True
        except Exception as e:
            logger_binance.error(f"❌ Error al establecer apalancamiento: {e}")
            return False

    def get_account_info(self):
        try:
            return self.client.futures_account()
        except Exception as e:
            logger_binance.error(f"❌ Error al obtener info de cuenta: {e}")
            return None

    def get_symbol_info(self, symbol):
        try:
            exchange_info = self.client.futures_exchange_info()
            for s in exchange_info['symbols']:
                if s['symbol'] == symbol:
                    return s
            return None
        except Exception as e:
            logger_binance.error(f"❌ Error al obtener info del símbolo {symbol}: {e}")
            return None

    def get_price_precision(self, symbol):
        try:
            info = self.client.futures_exchange_info()
            for s in info['symbols']:
                if s['symbol'] == symbol:
                    for f in s['filters']:
                        if f['filterType'] == 'PRICE_FILTER':
                            min_price = float(f['minPrice'])
                            return int(-math.log10(min_price))
            return 8
        except Exception as e:
            logger_binance.error(f"Error obteniendo precisión para {symbol}: {e}")
            return 8

    def place_market_order(self, symbol, side, quantity):
        try:
            logger_binance.info(f"📈 Enviando orden MARKET: {side} {quantity} {symbol}")
            order = self.client.futures_create_order(
                symbol=symbol,
                side=side,
                type='MARKET',
                quantity=quantity
            )
            logger_binance.info(f"✅ Orden enviada. ID: {order['orderId']}")
            return order
        except Exception as e:
            logger_binance.error(f"❌ Error al colocar orden: {e}")
            return None

    def place_stop_loss_order(self, symbol, side, stop_price):
        try:
            precision = self.get_price_precision(symbol)
            stop_price = round(stop_price, precision)
            logger_binance.info(f"🛑 Colocando STOP_MARKET: {side} en {symbol} a {stop_price} (precisión: {precision})")
            order = self.client.futures_create_order(
                symbol=symbol,
                side=side,
                type='STOP_MARKET',
                stopPrice=stop_price,
                closePosition=True
            )
            logger_binance.info(f"✅ Stop-Loss colocado. ID: {order['orderId']}")
            return order
        except Exception as e:
            logger_binance.error(f"❌ Error al colocar Stop-Loss: {e}")
            return None

    def place_take_profit_order(self, symbol, side, take_profit_price):
        try:
            precision = self.get_price_precision(symbol)
            take_profit_price = round(take_profit_price, precision)
            logger_binance.info(f"🎯 Colocando TAKE_PROFIT_MARKET: {side} en {symbol} a {take_profit_price} (precisión: {precision})")
            order = self.client.futures_create_order(
                symbol=symbol,
                side=side,
                type='TAKE_PROFIT_MARKET',
                stopPrice=take_profit_price,
                closePosition=True
            )
            logger_binance.info(f"✅ Take-Profit colocado. ID: {order['orderId']}")
            return order
        except Exception as e:
            logger_binance.error(f"❌ Error al colocar Take-Profit: {e}")
            return None

    # === NUEVA FUNCIÓN: Validar y ajustar SL/TP para evitar error -2021 ===
    def validar_niveles_sl_tp(self, symbol, side, sl_price, tp_price):
        try:
            ticker = self.client.futures_symbol_ticker(symbol=symbol)
            precio_actual = float(ticker['price'])
            if side == 'BUY':  # SHORT → SL arriba, TP abajo
                if sl_price <= precio_actual:
                    sl_price = precio_actual * 1.005
                if tp_price >= precio_actual:
                    tp_price = precio_actual * 0.995
            else:  # LONG → SL abajo, TP arriba
                if sl_price >= precio_actual:
                    sl_price = precio_actual * 0.995
                if tp_price <= precio_actual:
                    tp_price = precio_actual * 1.005
            return sl_price, tp_price
        except Exception as e:
            logger_binance.error(f"Error validando SL/TP para {symbol}: {e}")
            return sl_price, tp_price

    # === NUEVA FUNCIÓN: Cancelar órdenes huérfanas ===
    def cancelar_ordenes_cierre(self, symbol):
        try:
            open_orders = self.client.futures_get_open_orders(symbol=symbol)
            for order in open_orders:
                if order['type'] in ['STOP_MARKET', 'TAKE_PROFIT_MARKET']:
                    self.client.futures_cancel_order(symbol=symbol, orderId=order['orderId'])
                    logger_binance.info(f"🧹 Orden cancelada: {order['type']} ID {order['orderId']} en {symbol}")
        except Exception as e:
            logger_binance.error(f"❌ Error al cancelar órdenes de cierre en {symbol}: {e}")

# --- FIN MÓDULO BINANCE TRADER ---

logging.basicConfig(level=logging.INFO, stream=sys.stdout, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ---------------------------
# Optimizador IA
# ---------------------------
class OptimizadorIA:
    def __init__(self, log_path="operaciones_log.csv", min_samples=15):
        self.log_path = log_path
        self.min_samples = min_samples
        self.datos = self.cargar_datos()

    def cargar_datos(self):
        datos = []
        try:
            with open(self.log_path, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    try:
                        pnl = float(row.get('pnl_percent', 0))
                        angulo = float(row.get('angulo_tendencia', 0))
                        pearson = float(row.get('pearson', 0))
                        r2 = float(row.get('r2_score', 0))
                        ancho_relativo = float(row.get('ancho_canal_relativo', 0))
                        nivel_fuerza = int(row.get('nivel_fuerza', 1))
                        datos.append({
                            'pnl': pnl, 
                            'angulo': angulo, 
                            'pearson': pearson, 
                            'r2': r2,
                            'ancho_relativo': ancho_relativo,
                            'nivel_fuerza': nivel_fuerza
                        })
                    except Exception:
                        continue
        except FileNotFoundError:
            print("⚠ No se encontró operaciones_log.csv (optimizador)")
        return datos

    def evaluar_configuracion(self, trend_threshold, min_strength, entry_margin):
        if not self.datos:
            return -99999
        filtradas = [
            op for op in self.datos
            if abs(op['angulo']) >= trend_threshold
            and abs(op['angulo']) >= min_strength
            and abs(op['pearson']) >= 0.4
            and op.get('nivel_fuerza', 1) >= 2
            and op.get('r2', 0) >= 0.4
        ]
        n = len(filtradas)
        if n < max(8, int(0.15 * len(self.datos))):
            return -10000 - n
        pnls = [op['pnl'] for op in filtradas]
        pnl_mean = statistics.mean(pnls) if filtradas else 0
        pnl_std = statistics.stdev(pnls) if len(pnls) > 1 else 0
        winrate = sum(1 for op in filtradas if op['pnl'] > 0) / n if n > 0 else 0
        score = (pnl_mean - 0.5 * pnl_std) * winrate * math.sqrt(n)
        ops_calidad = [op for op in filtradas if op.get('r2', 0) >= 0.6 and op.get('nivel_fuerza', 1) >= 3]
        if ops_calidad:
            score *= 1.2
        return score

    def buscar_mejores_parametros(self):
        if not self.datos or len(self.datos) < self.min_samples:
            print(f"ℹ️ No hay suficientes datos para optimizar (se requieren {self.min_samples}, hay {len(self.datos)})")
            return None
        mejor_score = -1e9
        mejores_param = None
        trend_values = [3, 5, 8, 10, 12, 15, 18, 20, 25, 30, 35, 40]
        strength_values = [3, 5, 8, 10, 12, 15, 18, 20, 25, 30]
        margin_values = [0.0005, 0.001, 0.0015, 0.002, 0.0025, 0.003, 0.004, 0.005, 0.008, 0.01]
        combos = list(itertools.product(trend_values, strength_values, margin_values))
        total = len(combos)
        print(f"🔎 Optimizador: probando {total} combinaciones...")
        for idx, (t, s, m) in enumerate(combos, start=1):
            score = self.evaluar_configuracion(t, s, m)
            if score > mejor_score:
                mejor_score = score
                mejores_param = {
                    'trend_threshold_degrees': t,
                    'min_trend_strength_degrees': s,
                    'entry_margin': m,
                    'score': score,
                    'evaluated_samples': len(self.datos),
                    'total_combinations': total
                }
        if mejores_param:
            print("✅ Optimizador: mejores parámetros encontrados:", mejores_param)
            try:
                with open("mejores_parametros.json", "w", encoding='utf-8') as f:
                    json.dump(mejores_param, f, indent=2)
            except Exception as e:
                print("⚠ Error guardando mejores_parametros.json:", e)
        else:
            print("⚠ No se encontró una configuración mejor")
        return mejores_param

# ---------------------------
# BOT PRINCIPAL - BREAKOUT + REENTRY (sin gráficos)
# ---------------------------
class TradingBot:
    def __init__(self, config):
        self.config = config
        self.log_path = config.get('log_path', 'operaciones_log.csv')
        self.auto_optimize = config.get('auto_optimize', True)
        self.ultima_optimizacion = datetime.now()
        self.operaciones_desde_optimizacion = 0
        self.total_operaciones = 0
        self.breakout_history = {}
        self.config_optima_por_simbolo = {}
        self.ultima_busqueda_config = {}
        self.breakouts_detectados = {}
        self.esperando_reentry = {}
        self.estado_file = config.get('estado_file', 'estado_bot.json')
        self.cargar_estado()
        self.trader = BinanceTrader(
            api_key=config['binance_api_key'],
            secret_key=config['binance_secret_key'],
            testnet=config.get('binance_testnet', True)
        )
        if not self.trader.check_connection():
            print("❌ No se pudo conectar a Binance. El bot no operará.")
            self.trader = None
        if self.auto_optimize:
            try:
                ia = OptimizadorIA(log_path=self.log_path, min_samples=config.get('min_samples_optimizacion', 15))
                parametros_optimizados = ia.buscar_mejores_parametros()
                if parametros_optimizados:
                    self.config['trend_threshold_degrees'] = parametros_optimizados.get('trend_threshold_degrees', self.config.get('trend_threshold_degrees', 13))
                    self.config['min_trend_strength_degrees'] = parametros_optimizados.get('min_trend_strength_degrees', self.config.get('min_trend_strength_degrees', 16))
                    self.config['entry_margin'] = parametros_optimizados.get('entry_margin', self.config.get('entry_margin', 0.001))
            except Exception as e:
                print("⚠ Error en optimización automática:", e)
        self.ultimos_datos = {}
        self.operaciones_activas = {}
        self.senales_enviadas = set()
        self.archivo_log = self.log_path
        self.inicializar_log()

    def cargar_estado(self):
        try:
            if os.path.exists(self.estado_file):
                with open(self.estado_file, 'r', encoding='utf-8') as f:
                    estado = json.load(f)
                if 'ultima_optimizacion' in estado:
                    estado['ultima_optimizacion'] = datetime.fromisoformat(estado['ultima_optimizacion'])
                if 'ultima_busqueda_config' in estado:
                    for simbolo, fecha_str in estado['ultima_busqueda_config'].items():
                        estado['ultima_busqueda_config'][simbolo] = datetime.fromisoformat(fecha_str)
                if 'breakout_history' in estado:
                    for simbolo, fecha_str in estado['breakout_history'].items():
                        estado['breakout_history'][simbolo] = datetime.fromisoformat(fecha_str)
                if 'esperando_reentry' in estado:
                    for simbolo, info in estado['esperando_reentry'].items():
                        info['timestamp'] = datetime.fromisoformat(info['timestamp'])
                    self.esperando_reentry = estado['esperando_reentry']
                if 'breakouts_detectados' in estado:
                    for simbolo, info in estado['breakouts_detectados'].items():
                        info['timestamp'] = datetime.fromisoformat(info['timestamp'])
                    self.breakouts_detectados = estado['breakouts_detectados']
                self.ultima_optimizacion = estado.get('ultima_optimizacion', datetime.now())
                self.operaciones_desde_optimizacion = estado.get('operaciones_desde_optimizacion', 0)
                self.total_operaciones = estado.get('total_operaciones', 0)
                self.breakout_history = estado.get('breakout_history', {})
                self.config_optima_por_simbolo = estado.get('config_optima_por_simbolo', {})
                self.ultima_busqueda_config = estado.get('ultima_busqueda_config', {})
                self.operaciones_activas = estado.get('operaciones_activas', {})
                self.senales_enviadas = set(estado.get('senales_enviadas', []))
                print("✅ Estado anterior cargado correctamente")
        except Exception as e:
            print(f"⚠ Error cargando estado previo: {e}")

    def guardar_estado(self):
        try:
            estado = {
                'ultima_optimizacion': self.ultima_optimizacion.isoformat(),
                'operaciones_desde_optimizacion': self.operaciones_desde_optimizacion,
                'total_operaciones': self.total_operaciones,
                'breakout_history': {k: v.isoformat() for k, v in self.breakout_history.items()},
                'config_optima_por_simbolo': self.config_optima_por_simbolo,
                'ultima_busqueda_config': {k: v.isoformat() for k, v in self.ultima_busqueda_config.items()},
                'operaciones_activas': self.operaciones_activas,
                'senales_enviadas': list(self.senales_enviadas),
                'esperando_reentry': {
                    k: {
                        'tipo': v['tipo'],
                        'timestamp': v['timestamp'].isoformat(),
                        'precio_breakout': v['precio_breakout'],
                        'config': v.get('config', {})
                    } for k, v in self.esperando_reentry.items()
                },
                'breakouts_detectados': {
                    k: {
                        'tipo': v['tipo'],
                        'timestamp': v['timestamp'].isoformat(),
                        'precio_breakout': v.get('precio_breakout', 0)
                    } for k, v in self.breakouts_detectados.items()
                },
                'timestamp_guardado': datetime.now().isoformat()
            }
            with open(self.estado_file, 'w', encoding='utf-8') as f:
                json.dump(estado, f, indent=2, ensure_ascii=False)
            print("💾 Estado guardado correctamente")
        except Exception as e:
            print(f"⚠ Error guardando estado: {e}")

    def buscar_configuracion_optima_simbolo(self, simbolo):
        if simbolo in self.config_optima_por_simbolo:
            ultima_busqueda = self.ultima_busqueda_config.get(simbolo)
            if ultima_busqueda and (datetime.now() - ultima_busqueda).total_seconds() < 7200:
                return self.config_optima_por_simbolo[simbolo]
        print(f"   🔍 Buscando configuración óptima para {simbolo}...")
        timeframes = self.config.get('timeframes', ['1m', '3m', '5m', '15m', '30m'])
        velas_options = self.config.get('velas_options', [80, 100, 120, 150, 200])
        mejor_config = None
        mejor_puntaje = -999999
        prioridad_timeframe = {'1m': 200, '3m': 150, '5m': 120, '15m': 100, '30m': 80}
        for timeframe in timeframes:
            for num_velas in velas_options:
                try:
                    datos = self.obtener_datos_mercado_config(simbolo, timeframe, num_velas)
                    if not datos: continue
                    canal_info = self.calcular_canal_regresion_config(datos, num_velas)
                    if not canal_info: continue
                    if (canal_info['nivel_fuerza'] >= 2 and 
                        abs(canal_info['coeficiente_pearson']) >= 0.4 and 
                        canal_info['r2_score'] >= 0.4):
                        ancho_actual = canal_info['ancho_canal_porcentual']
                        if ancho_actual >= self.config.get('min_channel_width_percent', 4.0):
                            puntaje_ancho = ancho_actual * 10
                            puntaje_timeframe = prioridad_timeframe.get(timeframe, 50) * 100
                            puntaje_total = puntaje_timeframe + puntaje_ancho
                            if puntaje_total > mejor_puntaje:
                                mejor_puntaje = puntaje_total
                                mejor_config = {
                                    'timeframe': timeframe,
                                    'num_velas': num_velas,
                                    'ancho_canal': ancho_actual,
                                    'puntaje_total': puntaje_total
                                }
                except Exception:
                    continue
        if mejor_config:
            self.config_optima_por_simbolo[simbolo] = mejor_config
            self.ultima_busqueda_config[simbolo] = datetime.now()
            print(f"   ✅ Config óptima: {mejor_config['timeframe']} - {mejor_config['num_velas']} velas - Ancho: {mejor_config['ancho_canal']:.1f}%")
        return mejor_config

    def obtener_datos_mercado_config(self, simbolo, timeframe, num_velas):
        url = "https://api.binance.com/api/v3/klines"
        params = {'symbol': simbolo, 'interval': timeframe, 'limit': num_velas + 14}
        try:
            respuesta = requests.get(url, params=params, timeout=10)
            datos = respuesta.json()
            if not isinstance(datos, list) or len(datos) == 0:
                return None
            maximos = [float(vela[2]) for vela in datos]
            minimos = [float(vela[3]) for vela in datos]
            cierres = [float(vela[4]) for vela in datos]
            tiempos = list(range(len(datos)))
            return {
                'maximos': maximos,
                'minimos': minimos,
                'cierres': cierres,
                'tiempos': tiempos,
                'precio_actual': cierres[-1] if cierres else 0,
                'timeframe': timeframe,
                'num_velas': num_velas
            }
        except Exception:
            return None

    def calcular_canal_regresion_config(self, datos_mercado, candle_period):
        if not datos_mercado or len(datos_mercado['maximos']) < candle_period:
            return None
        start_idx = -candle_period
        tiempos = datos_mercado['tiempos'][start_idx:]
        maximos = datos_mercado['maximos'][start_idx:]
        minimos = datos_mercado['minimos'][start_idx:]
        cierres = datos_mercado['cierres'][start_idx:]
        tiempos_reg = list(range(len(tiempos)))
        reg_max = self.calcular_regresion_lineal(tiempos_reg, maximos)
        reg_min = self.calcular_regresion_lineal(tiempos_reg, minimos)
        reg_close = self.calcular_regresion_lineal(tiempos_reg, cierres)
        if not all([reg_max, reg_min, reg_close]):
            return None
        pendiente_max, intercepto_max = reg_max
        pendiente_min, intercepto_min = reg_min
        pendiente_cierre, intercepto_cierre = reg_close
        tiempo_actual = tiempos_reg[-1]
        resistencia_media = pendiente_max * tiempo_actual + intercepto_max
        soporte_media = pendiente_min * tiempo_actual + intercepto_min
        diferencias_max = [maximos[i] - (pendiente_max * tiempos_reg[i] + intercepto_max) for i in range(len(tiempos_reg))]
        diferencias_min = [minimos[i] - (pendiente_min * tiempos_reg[i] + intercepto_min) for i in range(len(tiempos_reg))]
        desviacion_max = np.std(diferencias_max) if diferencias_max else 0
        desviacion_min = np.std(diferencias_min) if diferencias_min else 0
        resistencia_superior = resistencia_media + desviacion_max
        soporte_inferior = soporte_media - desviacion_min
        precio_actual = datos_mercado['precio_actual']
        pearson, angulo_tendencia = self.calcular_pearson_y_angulo(tiempos_reg, cierres)
        fuerza_texto, nivel_fuerza = self.clasificar_fuerza_tendencia(angulo_tendencia)
        direccion = self.determinar_direccion_tendencia(angulo_tendencia, 1)
        stoch_k, stoch_d = self.calcular_stochastic(datos_mercado)
        precio_medio = (resistencia_superior + soporte_inferior) / 2
        ancho_canal_absoluto = resistencia_superior - soporte_inferior
        ancho_canal_porcentual = (ancho_canal_absoluto / precio_medio) * 100
        return {
            'resistencia': resistencia_superior,
            'soporte': soporte_inferior,
            'resistencia_media': resistencia_media,
            'soporte_media': soporte_media,
            'linea_tendencia': pendiente_cierre * tiempo_actual + intercepto_cierre,
            'pendiente_tendencia': pendiente_cierre,
            'precio_actual': precio_actual,
            'ancho_canal': ancho_canal_absoluto,
            'ancho_canal_porcentual': ancho_canal_porcentual,
            'angulo_tendencia': angulo_tendencia,
            'coeficiente_pearson': pearson,
            'fuerza_texto': fuerza_texto,
            'nivel_fuerza': nivel_fuerza,
            'direccion': direccion,
            'r2_score': self.calcular_r2(cierres, tiempos_reg, pendiente_cierre, intercepto_cierre),
            'pendiente_resistencia': pendiente_max,
            'pendiente_soporte': pendiente_min,
            'stoch_k': stoch_k,
            'stoch_d': stoch_d,
            'timeframe': datos_mercado.get('timeframe', 'N/A'),
            'num_velas': candle_period
        }

    def enviar_alerta_breakout(self, simbolo, tipo_breakout, info_canal, datos_mercado, config_optima):
        precio_cierre = datos_mercado['cierres'][-1]
        resistencia = info_canal['resistencia']
        soporte = info_canal['soporte']
        direccion_canal = info_canal['direccion']
        if tipo_breakout == "BREAKOUT_LONG":
            emoji_principal = "🚀"
            tipo_texto = "RUPTURA de SOPORTE"
            expectativa = "posible entrada en long si el precio reingresa al canal"
        else:
            emoji_principal = "📉"
            tipo_texto = "RUPTURA BAJISTA de RESISTENCIA"
            expectativa = "posible entrada en short si el precio reingresa al canal"
        mensaje = f"""
{emoji_principal} <b>¡BREAKOUT DETECTADO! - {simbolo}</b>
⚠️ <b>{tipo_texto}</b>
⏰ <b>Hora:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
⏳ <b>ESPERANDO REINGRESO...</b>
👁️ Máximo 30 minutos para confirmación
📍 {expectativa}
        """
        token = self.config.get('telegram_token')
        chat_ids = self.config.get('telegram_chat_ids', [])
        if token and chat_ids:
            try:
                self._enviar_telegram_simple(mensaje, token, chat_ids)
                print(f"     ✅ Alerta de breakout enviada para {simbolo}")
            except Exception as e:
                print(f"     ❌ Error enviando alerta de breakout: {e}")

    def detectar_breakout(self, simbolo, info_canal, datos_mercado):
        if not info_canal:
            return None
        if info_canal['ancho_canal_porcentual'] < self.config.get('min_channel_width_percent', 4.0):
            return None
        precio_cierre = datos_mercado['cierres'][-1]
        resistencia = info_canal['resistencia']
        soporte = info_canal['soporte']
        angulo = info_canal['angulo_tendencia']
        direccion = info_canal['direccion']
        nivel_fuerza = info_canal['nivel_fuerza']
        r2 = info_canal['r2_score']
        pearson = info_canal['coeficiente_pearson']
        if abs(angulo) < self.config.get('min_trend_strength_degrees', 16):
            return None
        if abs(pearson) < 0.4 or r2 < 0.4:
            return None
        if simbolo in self.breakouts_detectados:
            ultimo_breakout = self.breakouts_detectados[simbolo]
            tiempo_desde_ultimo = (datetime.now() - ultimo_breakout['timestamp']).total_seconds() / 60
            if tiempo_desde_ultimo < 115:
                return None
        if direccion == "🟢 ALCISTA" and nivel_fuerza >= 2:
            if precio_cierre < soporte:
                return "BREAKOUT_LONG"
        elif direccion == "🔴 BAJISTA" and nivel_fuerza >= 2:
            if precio_cierre > resistencia:
                return "BREAKOUT_SHORT"
        return None

    def detectar_reentry(self, simbolo, info_canal, datos_mercado):
        if simbolo not in self.esperando_reentry:
            return None
        breakout_info = self.esperando_reentry[simbolo]
        timestamp_breakout = breakout_info['timestamp']
        tiempo_desde_breakout = (datetime.now() - timestamp_breakout).total_seconds() / 60
        if tiempo_desde_breakout > 120:
            print(f"     ⏰ {simbolo} - Timeout de reentry (>30 min), cancelando espera")
            del self.esperando_reentry[simbolo]
            if simbolo in self.breakouts_detectados:
                del self.breakouts_detectados[simbolo]
            return None
        precio_actual = datos_mercado['precio_actual']
        resistencia = info_canal['resistencia']
        soporte = info_canal['soporte']
        stoch_k = info_canal['stoch_k']
        stoch_d = info_canal['stoch_d']
        tolerancia = 0.001 * precio_actual
        if breakout_info['tipo'] == "BREAKOUT_LONG":
            if soporte <= precio_actual <= resistencia:
                distancia_soporte = abs(precio_actual - soporte)
                if distancia_soporte <= tolerancia and stoch_k <= 30 and stoch_d <= 30:
                    if simbolo in self.breakouts_detectados:
                        del self.breakouts_detectados[simbolo]
                    return "LONG"
        elif breakout_info['tipo'] == "BREAKOUT_SHORT":
            if soporte <= precio_actual <= resistencia:
                distancia_resistencia = abs(precio_actual - resistencia)
                if distancia_resistencia <= tolerancia and stoch_k >= 70 and stoch_d >= 70:
                    if simbolo in self.breakouts_detectados:
                        del self.breakouts_detectados[simbolo]
                    return "SHORT"
        return None

    def calcular_niveles_entrada(self, tipo_operacion, info_canal, precio_actual):
        if not info_canal:
            return None, None, None
        resistencia = info_canal['resistencia']
        soporte = info_canal['soporte']
        ancho_canal = resistencia - soporte
        sl_porcentaje = 0.02
        if tipo_operacion == "LONG":
            precio_entrada = precio_actual
            stop_loss = precio_entrada * (1 - sl_porcentaje)
            take_profit = precio_entrada + ancho_canal 
        else:
            precio_entrada = precio_actual
            stop_loss = resistencia * (1 + sl_porcentaje)
            take_profit = precio_entrada - ancho_canal
        riesgo = abs(precio_entrada - stop_loss)
        beneficio = abs(take_profit - precio_entrada)
        ratio_rr = beneficio / riesgo if riesgo > 0 else 0
        if ratio_rr < self.config.get('min_rr_ratio', 1.2):
            if tipo_operacion == "LONG":
                take_profit = precio_entrada + (riesgo * self.config['min_rr_ratio'])
            else:
                take_profit = precio_entrada - (riesgo * self.config['min_rr_ratio'])
        return precio_entrada, take_profit, stop_loss

    def calcular_tamaño_posicion(self, symbol, precio_entrada):
        if not self.trader:
            return None
        info_cuenta = self.trader.get_account_info()
        if not info_cuenta:
            return None
        balance = float(info_cuenta['availableBalance'])
        monto_usdt = balance * 0.03
        cantidad_base = monto_usdt / precio_entrada
        symbol_info = self.trader.get_symbol_info(symbol)
        if not symbol_info:
            return None
        step_size = None
        for f in symbol_info['filters']:
            if f['filterType'] == 'LOT_SIZE':
                step_size = float(f['stepSize'])
                break
        if step_size is None:
            return None
        precision = int(round(-math.log(step_size, 10), 0))
        cantidad_ajustada = round(cantidad_base, precision)
        if cantidad_ajustada < step_size:
            print(f"⚠️ Cantidad ajustada {cantidad_ajustada} es menor que stepSize {step_size} para {symbol}. Operación omitida.")
            return None
        return cantidad_ajustada

    def ejecutar_operacion_binance(self, simbolo, tipo_operacion, precio_entrada, sl, tp):
        if not self.trader:
            print("❌ Trader no disponible. Operación omitida.")
            return False
        if not self.trader.set_leverage(simbolo, 5):
            print(f"❌ Falló al establecer apalancamiento 5x para {simbolo}")
            return False
        cantidad = self.calcular_tamaño_posicion(simbolo, precio_entrada)
        if not cantidad:
            print(f"❌ No se pudo calcular cantidad para {simbolo}")
            return False
        side = 'BUY' if tipo_operacion == 'LONG' else 'SELL'
        orden = self.trader.place_market_order(simbolo, side, cantidad)
        if not orden:
            print(f"❌ Falló al abrir posición {tipo_operacion} en {simbolo}")
            return False

        # 🔁 Validar y ajustar SL/TP para evitar error -2021
        sl_side = 'SELL' if tipo_operacion == 'LONG' else 'BUY'
        tp_side = sl_side
        sl_ajustado, tp_ajustado = self.trader.validar_niveles_sl_tp(simbolo, sl_side, sl, tp)

        # Colocar órdenes
        sl_order = self.trader.place_stop_loss_order(simbolo, sl_side, sl_ajustado)
        tp_order = self.trader.place_take_profit_order(simbolo, tp_side, tp_ajustado)

        if not sl_order or not tp_order:
            print(f"⚠️ Al menos una orden de cierre falló en {simbolo}. Verificando estado...")
            ordenes_activas = self.trader.client.futures_get_open_orders(symbol=simbolo)
            sl_existe = any(o['type'] == 'STOP_MARKET' for o in ordenes_activas)
            tp_existe = any(o['type'] == 'TAKE_PROFIT_MARKET' for o in ordenes_activas)
            if not sl_existe or not tp_existe:
                print(f"❌ Operación en {simbolo} NO protegida. Cerrando posición inmediatamente.")
                self.trader.client.futures_create_order(
                    symbol=simbolo,
                    side='SELL' if tipo_operacion == 'LONG' else 'BUY',
                    type='MARKET',
                    reduceOnly=True
                )
                return False

        return True

    # === FUNCIÓN REEMPLAZADA: verificar_cierre_operaciones mejorada ===
    def verificar_cierre_operaciones(self):
        if not self.operaciones_activas:
            return []
        
        operaciones_cerradas = []
        
        # Obtener posiciones reales en Binance
        try:
            posiciones = self.trader.client.futures_position_information()
            posiciones_dict = {p['symbol']: float(p['positionAmt']) for p in posiciones if float(p['positionAmt']) != 0}
        except Exception as e:
            print(f"⚠️ Error obteniendo posiciones reales: {e}")
            posiciones_dict = {}

        for simbolo, operacion in list(self.operaciones_activas.items()):
            posicion_actual = posiciones_dict.get(simbolo, 0.0)
            
            if posicion_actual == 0.0:
                # La posición ya se cerró en Binance → procesar cierre
                datos_mercado = self.obtener_datos_mercado_config(
                    simbolo, 
                    operacion.get('timeframe_utilizado', '5m'), 
                    operacion.get('velas_utilizadas', 100)
                )
                if not datos_mercado:
                    continue
                precio_salida = datos_mercado['precio_actual']

                if operacion['tipo'] == "LONG":
                    pnl_percent = ((precio_salida - operacion['precio_entrada']) / operacion['precio_entrada']) * 100
                else:
                    pnl_percent = ((operacion['precio_entrada'] - precio_salida) / operacion['precio_entrada']) * 100

                tp = operacion['take_profit']
                sl = operacion['stop_loss']
                resultado = "TP" if (
                    (operacion['tipo'] == "LONG" and precio_salida >= tp * 0.995) or
                    (operacion['tipo'] == "SHORT" and precio_salida <= tp * 1.005)
                ) else "SL"

                duracion_minutos = (datetime.now() - datetime.fromisoformat(operacion['timestamp_entrada'])).total_seconds() / 60

                datos_operacion = {
                    'timestamp': datetime.now().isoformat(),
                    'symbol': simbolo,
                    'tipo': operacion['tipo'],
                    'precio_entrada': operacion['precio_entrada'],
                    'take_profit': tp,
                    'stop_loss': sl,
                    'precio_salida': precio_salida,
                    'resultado': resultado,
                    'pnl_percent': pnl_percent,
                    'duracion_minutos': duracion_minutos,
                    'angulo_tendencia': operacion.get('angulo_tendencia', 0),
                    'pearson': operacion.get('pearson', 0),
                    'r2_score': operacion.get('r2_score', 0),
                    'ancho_canal_relativo': operacion.get('ancho_canal_relativo', 0),
                    'ancho_canal_porcentual': operacion.get('ancho_canal_porcentual', 0),
                    'nivel_fuerza': operacion.get('nivel_fuerza', 1),
                    'timeframe_utilizado': operacion.get('timeframe_utilizado', 'N/A'),
                    'velas_utilizadas': operacion.get('velas_utilizadas', 0),
                    'stoch_k': operacion.get('stoch_k', 0),
                    'stoch_d': operacion.get('stoch_d', 0),
                    'breakout_usado': operacion.get('breakout_usado', False)
                }

                mensaje_cierre = self.generar_mensaje_cierre(datos_operacion)
                token = self.config.get('telegram_token')
                chats = self.config.get('telegram_chat_ids', [])
                if token and chats:
                    try:
                        self._enviar_telegram_simple(mensaje_cierre, token, chats)
                    except Exception:
                        pass

                self.registrar_operacion(datos_operacion)

                # ✅ Cancelar órdenes huérfanas
                if self.trader:
                    self.trader.cancelar_ordenes_cierre(simbolo)

                operaciones_cerradas.append(simbolo)
                del self.operaciones_activas[simbolo]
                if simbolo in self.senales_enviadas:
                    self.senales_enviadas.remove(simbolo)
                self.operaciones_desde_optimizacion += 1

                print(f"     📊 {simbolo} Cierre detectado (posición cerrada en Binance) - PnL: {pnl_percent:.2f}%")

        return operaciones_cerradas

    def escanear_mercado(self):
        print(f"\n🔍 Escaneando {len(self.config.get('symbols', []))} símbolos (Estrategia: Breakout + Reentry)...")
        senales_encontradas = 0
        for simbolo in self.config.get('symbols', []):
            try:
                if simbolo in self.operaciones_activas:
                    continue
                config_optima = self.buscar_configuracion_optima_simbolo(simbolo)
                if not config_optima:
                    continue
                datos_mercado = self.obtener_datos_mercado_config(
                    simbolo, config_optima['timeframe'], config_optima['num_velas']
                )
                if not datos_mercado:
                    continue
                info_canal = self.calcular_canal_regresion_config(datos_mercado, config_optima['num_velas'])
                if not info_canal:
                    continue
                if (info_canal['nivel_fuerza'] < 2 or 
                    abs(info_canal['coeficiente_pearson']) < 0.4 or 
                    info_canal['r2_score'] < 0.4):
                    continue
                if simbolo not in self.esperando_reentry:
                    tipo_breakout = self.detectar_breakout(simbolo, info_canal, datos_mercado)
                    if tipo_breakout:
                        self.esperando_reentry[simbolo] = {
                            'tipo': tipo_breakout,
                            'timestamp': datetime.now(),
                            'precio_breakout': datos_mercado['precio_actual'],
                            'config': config_optima
                        }
                        self.breakouts_detectados[simbolo] = {
                            'tipo': tipo_breakout,
                            'timestamp': datetime.now(),
                            'precio_breakout': datos_mercado['precio_actual']
                        }
                        self.enviar_alerta_breakout(simbolo, tipo_breakout, info_canal, datos_mercado, config_optima)
                        continue
                tipo_operacion = self.detectar_reentry(simbolo, info_canal, datos_mercado)
                if not tipo_operacion:
                    continue
                precio_entrada, tp, sl = self.calcular_niveles_entrada(
                    tipo_operacion, info_canal, datos_mercado['precio_actual']
                )
                if not precio_entrada or not tp or not sl:
                    continue
                if simbolo in self.breakout_history:
                    ultimo_breakout = self.breakout_history[simbolo]
                    tiempo_desde_ultimo = (datetime.now() - ultimo_breakout).total_seconds() / 3600
                    if tiempo_desde_ultimo < 2:
                        continue
                if self.ejecutar_operacion_binance(simbolo, tipo_operacion, precio_entrada, sl, tp):
                    self.generar_senal_operacion(
                        simbolo, tipo_operacion, precio_entrada, tp, sl, 
                        info_canal, datos_mercado, config_optima, self.esperando_reentry[simbolo]
                    )
                    senales_encontradas += 1
                    self.breakout_history[simbolo] = datetime.now()
                else:
                    print(f"❌ Operación en {simbolo} no ejecutada en Binance")
                del self.esperando_reentry[simbolo]
            except Exception as e:
                print(f"⚠️ Error analizando {simbolo}: {e}")
                continue
        if senales_encontradas > 0:
            print(f"✅ Se encontraron {senales_encontradas} señales de trading")
        else:
            print("❌ No se encontraron señales en este ciclo")
        return senales_encontradas

    def generar_senal_operacion(self, simbolo, tipo_operacion, precio_entrada, tp, sl,
                            info_canal, datos_mercado, config_optima, breakout_info=None):
        if simbolo in self.senales_enviadas:
            return
        riesgo = abs(precio_entrada - sl)
        beneficio = abs(tp - precio_entrada)
        ratio_rr = beneficio / riesgo if riesgo > 0 else 0
        sl_percent = abs((sl - precio_entrada) / precio_entrada) * 100
        tp_percent = abs((tp - precio_entrada) / precio_entrada) * 100
        stoch_estado = "📉 SOBREVENTA" if tipo_operacion == "LONG" else "📈 SOBRECOMPRA"
        breakout_texto = ""
        if breakout_info:
            tiempo_breakout = (datetime.now() - breakout_info['timestamp']).total_seconds() / 60
            breakout_texto = f"""
🚀 <b>BREAKOUT + REENTRY DETECTADO:</b>
⏰ Tiempo desde breakout: {tiempo_breakout:.1f} minutos
💰 Precio breakout: {breakout_info['precio_breakout']:.8f}
"""
        mensaje = f"""
🎯 <b>SEÑAL DE {tipo_operacion} - {simbolo} (TESTNET)</b>
{breakout_texto}
⏱️ <b>Configuración óptima:</b>
📊 Timeframe: {config_optima['timeframe']}
🕯️ Velas: {config_optima['num_velas']}
📏 Ancho Canal: {info_canal['ancho_canal_porcentual']:.1f}% ⭐
💰 <b>Precio Actual:</b> {datos_mercado['precio_actual']:.8f}
🎯 <b>Entrada:</b> {precio_entrada:.8f}
🛑 <b>Stop Loss:</b> {sl:.8f}
🎯 <b>Take Profit:</b> {tp:.8f}
📊 <b>Ratio R/B:</b> {ratio_rr:.2f}:1
🎯 <b>SL:</b> {sl_percent:.2f}%
🎯 <b>TP:</b> {tp_percent:.2f}%
📈 <b>Tendencia:</b> {info_canal['direccion']}
💪 <b>Fuerza:</b> {info_canal['fuerza_texto']}
📏 <b>Ángulo:</b> {info_canal['angulo_tendencia']:.1f}°
📊 <b>Pearson:</b> {info_canal['coeficiente_pearson']:.3f}
🎯 <b>R² Score:</b> {info_canal['r2_score']:.3f}
🎰 <b>Stochástico:</b> {stoch_estado}
⏰ <b>Hora:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
💡 <b>Estrategia:</b> BREAKOUT + REENTRY con confirmación Stochastic
🤖 <b>Operación ejecutada en Binance Testnet con 5x apalancamiento</b>
        """
        token = self.config.get('telegram_token')
        chat_ids = self.config.get('telegram_chat_ids', [])
        if token and chat_ids:
            try:
                self._enviar_telegram_simple(mensaje, token, chat_ids)
                print(f"     ✅ Señal {tipo_operacion} para {simbolo} enviada")
            except Exception as e:
                print(f"     ❌ Error enviando señal: {e}")
        self.operaciones_activas[simbolo] = {
            'tipo': tipo_operacion,
            'precio_entrada': precio_entrada,
            'take_profit': tp,
            'stop_loss': sl,
            'timestamp_entrada': datetime.now().isoformat(),
            'angulo_tendencia': info_canal['angulo_tendencia'],
            'pearson': info_canal['coeficiente_pearson'],
            'r2_score': info_canal['r2_score'],
            'ancho_canal_relativo': info_canal['ancho_canal'] / precio_entrada,
            'ancho_canal_porcentual': info_canal['ancho_canal_porcentual'],
            'nivel_fuerza': info_canal['nivel_fuerza'],
            'timeframe_utilizado': config_optima['timeframe'],
            'velas_utilizadas': config_optima['num_velas'],
            'stoch_k': info_canal['stoch_k'],
            'stoch_d': info_canal['stoch_d'],
            'breakout_usado': breakout_info is not None
        }
        self.senales_enviadas.add(simbolo)
        self.total_operaciones += 1

    def inicializar_log(self):
        if not os.path.exists(self.archivo_log):
            with open(self.archivo_log, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow([
                    'timestamp', 'symbol', 'tipo', 'precio_entrada',
                    'take_profit', 'stop_loss', 'precio_salida',
                    'resultado', 'pnl_percent', 'duracion_minutos',
                    'angulo_tendencia', 'pearson', 'r2_score',
                    'ancho_canal_relativo', 'ancho_canal_porcentual',
                    'nivel_fuerza', 'timeframe_utilizado', 'velas_utilizadas',
                    'stoch_k', 'stoch_d', 'breakout_usado'
                ])

    def registrar_operacion(self, datos_operacion):
        with open(self.archivo_log, 'a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow([
                datos_operacion['timestamp'],
                datos_operacion['symbol'],
                datos_operacion['tipo'],
                datos_operacion['precio_entrada'],
                datos_operacion['take_profit'],
                datos_operacion['stop_loss'],
                datos_operacion['precio_salida'],
                datos_operacion['resultado'],
                datos_operacion['pnl_percent'],
                datos_operacion['duracion_minutos'],
                datos_operacion['angulo_tendencia'],
                datos_operacion['pearson'],
                datos_operacion['r2_score'],
                datos_operacion.get('ancho_canal_relativo', 0),
                datos_operacion.get('ancho_canal_porcentual', 0),
                datos_operacion.get('nivel_fuerza', 1),
                datos_operacion.get('timeframe_utilizado', 'N/A'),
                datos_operacion.get('velas_utilizadas', 0),
                datos_operacion.get('stoch_k', 0),
                datos_operacion.get('stoch_d', 0),
                datos_operacion.get('breakout_usado', False)
            ])

    def filtrar_operaciones_ultima_semana(self):
        if not os.path.exists(self.archivo_log):
            return []
        try:
            ops_recientes = []
            fecha_limite = datetime.now() - timedelta(days=7)
            with open(self.archivo_log, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    try:
                        timestamp = datetime.fromisoformat(row['timestamp'])
                        if timestamp >= fecha_limite:
                            ops_recientes.append({
                                'timestamp': timestamp,
                                'symbol': row['symbol'],
                                'resultado': row['resultado'],
                                'pnl_percent': float(row['pnl_percent']),
                                'tipo': row['tipo'],
                                'breakout_usado': row.get('breakout_usado', 'False') == 'True'
                            })
                    except Exception:
                        continue
            return ops_recientes
        except Exception as e:
            print(f"⚠️ Error filtrando operaciones: {e}")
            return []

    def generar_reporte_semanal(self):
        ops_ultima_semana = self.filtrar_operaciones_ultima_semana()
        if not ops_ultima_semana:
            return None
        total_ops = len(ops_ultima_semana)
        wins = sum(1 for op in ops_ultima_semana if op['resultado'] == 'TP')
        losses = sum(1 for op in ops_ultima_semana if op['resultado'] == 'SL')
        winrate = (wins/total_ops*100) if total_ops > 0 else 0
        pnl_total = sum(op['pnl_percent'] for op in ops_ultima_semana)
        mejor_op = max(ops_ultima_semana, key=lambda x: x['pnl_percent'])
        peor_op = min(ops_ultima_semana, key=lambda x: x['pnl_percent'])
        ganancias = [op['pnl_percent'] for op in ops_ultima_semana if op['pnl_percent'] > 0]
        perdidas = [abs(op['pnl_percent']) for op in ops_ultima_semana if op['pnl_percent'] < 0]
        avg_ganancia = sum(ganancias)/len(ganancias) if ganancias else 0
        avg_perdida = sum(perdidas)/len(perdidas) if perdidas else 0
        racha_actual = 0
        for op in reversed(ops_ultima_semana):
            if op['resultado'] == 'TP':
                racha_actual += 1
            else:
                break
        emoji_resultado = "🟢" if pnl_total > 0 else "🔴" if pnl_total < 0 else "⚪"
        mensaje = f"""
━━━━━━━━━━━━━━━━━━━━
📊 <b>REPORTE SEMANAL</b>
━━━━━━━━━━━━━━━━━━━━
📅 {datetime.now().strftime('%d/%m/%Y')} | Últimos 7 días
<b>RENDIMIENTO GENERAL</b>
{emoji_resultado} PnL Total: <b>{pnl_total:+.2f}%</b>
📈 Win Rate: <b>{winrate:.1f}%</b>
✅ Ganadas: {wins} | ❌ Perdidas: {losses}
<b>ESTADÍSTICAS</b>
📊 Operaciones: {total_ops}
💰 Ganancia Promedio: +{avg_ganancia:.2f}%
📉 Pérdida Promedio: -{avg_perdida:.2f}%
🔥 Racha actual: {racha_actual} wins
<b>DESTACADOS</b>
🏆 Mejor: {mejor_op['symbol']} ({mejor_op['tipo']})
   → {mejor_op['pnl_percent']:+.2f}%
⚠️ Peor: {peor_op['symbol']} ({peor_op['tipo']})
   → {peor_op['pnl_percent']:+.2f}%
━━━━━━━━━━━━━━━━━━━━
🤖 Bot automático 24/7
⚡ Estrategia: Breakout + Reentry
    """
        return mensaje

    def enviar_reporte_semanal(self):
        mensaje = self.generar_reporte_semanal()
        if not mensaje:
            return False
        token = self.config.get('telegram_token')
        chat_ids = self.config.get('telegram_chat_ids', [])
        if token and chat_ids:
            try:
                self._enviar_telegram_simple(mensaje, token, chat_ids)
                print("✅ Reporte semanal enviado correctamente")
                return True
            except Exception as e:
                print(f"❌ Error enviando reporte: {e}")
                return False
        return False

    def verificar_envio_reporte_automatico(self):
        ahora = datetime.now()
        if ahora.weekday() == 0 and 9 <= ahora.hour < 10:
            archivo_control = "ultimo_reporte.txt"
            try:
                if os.path.exists(archivo_control):
                    with open(archivo_control, 'r') as f:
                        ultima_fecha = f.read().strip()
                        if ultima_fecha == ahora.strftime('%Y-%m-%d'):
                            return False
                if self.enviar_reporte_semanal():
                    with open(archivo_control, 'w') as f:
                        f.write(ahora.strftime('%Y-%m-%d'))
                    return True
            except Exception as e:
                print(f"⚠️ Error en envío automático: {e}")
        return False

    def generar_mensaje_cierre(self, datos_operacion):
        emoji = "🟢" if datos_operacion['resultado'] == "TP" else "🔴"
        color_emoji = "✅" if datos_operacion['resultado'] == "TP" else "❌"
        if datos_operacion['tipo'] == 'LONG':
            pnl_absoluto = datos_operacion['precio_salida'] - datos_operacion['precio_entrada']
        else:
            pnl_absoluto = datos_operacion['precio_entrada'] - datos_operacion['precio_salida']
        breakout_usado = "🚀 Sí" if datos_operacion.get('breakout_usado', False) else "❌ No"
        mensaje = f"""
{emoji} <b>OPERACIÓN CERRADA - {datos_operacion['symbol']}</b>
{color_emoji} <b>RESULTADO: {datos_operacion['resultado']}</b>
📊 Tipo: {datos_operacion['tipo']}
💰 Entrada: {datos_operacion['precio_entrada']:.8f}
🎯 Salida: {datos_operacion['precio_salida']:.8f}
💵 PnL Absoluto: {pnl_absoluto:.8f}
📈 PnL %: {datos_operacion['pnl_percent']:.2f}%
⏰ Duración: {datos_operacion['duracion_minutos']:.1f} minutos
🚀 Breakout+Reentry: {breakout_usado}
📏 Ángulo: {datos_operacion['angulo_tendencia']:.1f}°
📊 Pearson: {datos_operacion['pearson']:.3f}
🎯 R²: {datos_operacion['r2_score']:.3f}
📏 Ancho: {datos_operacion.get('ancho_canal_porcentual', 0):.1f}%
⏱️ TF: {datos_operacion.get('timeframe_utilizado', 'N/A')}
🕯️ Velas: {datos_operacion.get('velas_utilizadas', 0)}
🕒 {datos_operacion['timestamp']}
        """
        return mensaje

    def calcular_stochastic(self, datos_mercado, period=14, k_period=3, d_period=3):
        if len(datos_mercado['cierres']) < period:
            return 50, 50
        cierres = datos_mercado['cierres']
        maximos = datos_mercado['maximos']
        minimos = datos_mercado['minimos']
        k_values = []
        for i in range(period-1, len(cierres)):
            highest_high = max(maximos[i-period+1:i+1])
            lowest_low = min(minimos[i-period+1:i+1])
            if highest_high == lowest_low:
                k = 50
            else:
                k = 100 * (cierres[i] - lowest_low) / (highest_high - lowest_low)
            k_values.append(k)
        if len(k_values) >= k_period:
            k_smoothed = []
            for i in range(k_period-1, len(k_values)):
                k_avg = sum(k_values[i-k_period+1:i+1]) / k_period
                k_smoothed.append(k_avg)
            if len(k_smoothed) >= d_period:
                d = sum(k_smoothed[-d_period:]) / d_period
                k_final = k_smoothed[-1]
                return k_final, d
        return 50, 50

    def calcular_regresion_lineal(self, x, y):
        if len(x) != len(y) or len(x) == 0:
            return None
        x = np.array(x)
        y = np.array(y)
        n = len(x)
        sum_x = np.sum(x)
        sum_y = np.sum(y)
        sum_xy = np.sum(x * y)
        sum_x2 = np.sum(x * x)
        denom = (n * sum_x2 - sum_x * sum_x)
        if denom == 0:
            pendiente = 0
        else:
            pendiente = (n * sum_xy - sum_x * sum_y) / denom
        intercepto = (sum_y - pendiente * sum_x) / n if n else 0
        return pendiente, intercepto

    def calcular_pearson_y_angulo(self, x, y):
        if len(x) != len(y) or len(x) < 2:
            return 0, 0
        x = np.array(x)
        y = np.array(y)
        n = len(x)
        sum_x = np.sum(x)
        sum_y = np.sum(y)
        sum_xy = np.sum(x * y)
        sum_x2 = np.sum(x * x)
        sum_y2 = np.sum(y * y)
        numerator = n * sum_xy - sum_x * sum_y
        denominator = math.sqrt((n * sum_x2 - sum_x * sum_x) * (n * sum_y2 - sum_y * sum_y))
        if denominator == 0:
            return 0, 0
        pearson = numerator / denominator
        denom_pend = (n * sum_x2 - sum_x * sum_x)
        pendiente = (n * sum_xy - sum_x * sum_y) / denom_pend if denom_pend != 0 else 0
        angulo_radianes = math.atan(pendiente * len(x) / (max(y) - min(y)) if (max(y) - min(y)) != 0 else 0)
        angulo_grados = math.degrees(angulo_radianes)
        return pearson, angulo_grados

    def clasificar_fuerza_tendencia(self, angulo_grados):
        angulo_abs = abs(angulo_grados)
        if angulo_abs < 3:
            return "💔 Muy Débil", 1
        elif angulo_abs < 13:
            return "❤️‍🩹 Débil", 2
        elif angulo_abs < 27:
            return "💛 Moderada", 3
        elif angulo_abs < 45:
            return "💚 Fuerte", 4
        else:
            return "💙 Muy Fuerte", 5

    def determinar_direccion_tendencia(self, angulo_grados, umbral_minimo=1):
        if abs(angulo_grados) < umbral_minimo:
            return "⚪ RANGO"
        elif angulo_grados > 0:
            return "🟢 ALCISTA"
        else:
            return "🔴 BAJISTA"

    def calcular_r2(self, y_real, x, pendiente, intercepto):
        if len(y_real) != len(x):
            return 0
        y_real = np.array(y_real)
        y_pred = pendiente * np.array(x) + intercepto
        ss_res = np.sum((y_real - y_pred) ** 2)
        ss_tot = np.sum((y_real - np.mean(y_real)) ** 2)
        if ss_tot == 0:
            return 0
        return 1 - (ss_res / ss_tot)

    def _enviar_telegram_simple(self, mensaje, token, chat_ids):
        if not token:
            print("⚠️ TELEGRAM_TOKEN no está definido en las variables de entorno.")
            return False
        if not chat_ids:
            print("⚠️ TELEGRAM_CHAT_ID no está definido en las variables de entorno.")
            return False
        print(f"📡 Enviando mensaje a Telegram (Chat IDs: {chat_ids})...")
        resultados = []
        for chat_id in chat_ids:
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            payload = {'chat_id': chat_id, 'text': mensaje, 'parse_mode': 'HTML'}
            try:
                r = requests.post(url, json=payload, timeout=10)
                if r.status_code == 200:
                    print(f"✅ Mensaje enviado exitosamente al chat {chat_id}.")
                    resultados.append(True)
                else:
                    print(f"❌ Error al enviar a {chat_id}: {r.status_code} - {r.text}")
                    resultados.append(False)
            except Exception as e:
                print(f"❌ Excepción al enviar a {chat_id}: {e}")
                resultados.append(False)
        return any(resultados)

    def reoptimizar_periodicamente(self):
        try:
            horas_desde_opt = (datetime.now() - self.ultima_optimizacion).total_seconds() / 7200
            if self.operaciones_desde_optimizacion >= 8 or horas_desde_opt >= self.config.get('reevaluacion_horas', 24):
                print("🔄 Iniciando re-optimización automática...")
                ia = OptimizadorIA(log_path=self.log_path, min_samples=self.config.get('min_samples_optimizacion', 30))
                nuevos_parametros = ia.buscar_mejores_parametros()
                if nuevos_parametros:
                    self.actualizar_parametros(nuevos_parametros)
                    self.ultima_optimizacion = datetime.now()
                    self.operaciones_desde_optimizacion = 0
                    print("✅ Parámetros actualizados en tiempo real")
        except Exception as e:
            print(f"⚠ Error en re-optimización automática: {e}")

    def actualizar_parametros(self, nuevos_parametros):
        self.config['trend_threshold_degrees'] = nuevos_parametros.get('trend_threshold_degrees', 
                                                                        self.config.get('trend_threshold_degrees', 16))
        self.config['min_trend_strength_degrees'] = nuevos_parametros.get('min_trend_strength_degrees', 
                                                                           self.config.get('min_trend_strength_degrees', 16))
        self.config['entry_margin'] = nuevos_parametros.get('entry_margin', 
                                                             self.config.get('entry_margin', 0.001))

    def ejecutar_analisis(self):
        if random.random() < 0.1:
            self.reoptimizar_periodicamente()
            self.verificar_envio_reporte_automatico()    
        cierres = self.verificar_cierre_operaciones()
        if cierres:
            print(f"     📊 Operaciones cerradas: {', '.join(cierres)}")
        self.guardar_estado()
        return self.escanear_mercado()

    def mostrar_resumen_operaciones(self):
        print(f"\n📊 RESUMEN OPERACIONES:")
        print(f"   Activas: {len(self.operaciones_activas)}")
        print(f"   Esperando reentry: {len(self.esperando_reentry)}")
        print(f"   Total ejecutadas: {self.total_operaciones}")

    def iniciar(self):
        print("\n" + "=" * 70)
        print("🤖 BOT DE TRADING - ESTRATEGIA BREAKOUT + REENTRY")
        print("🎯 PRIORIDAD: TIMEFRAMES CORTOS")
        print("💾 PERSISTENCIA: ACTIVADA")
        print("🔄 REEVALUACIÓN: CADA 2 HORAS")
        print("=" * 70)
        print(f"💱 Símbolos: {len(self.config.get('symbols', []))} monedas")
        print(f"📏 ANCHO MÍNIMO: {self.config.get('min_channel_width_percent', 4)}%")
        print(f"🚀 Estrategia: 1) Detectar Breakout → 2) Esperar Reentry → 3) Confirmar con Stoch")
        print("=" * 70)
        print("\n🚀 INICIANDO BOT...")
        try:
            while True:
                nuevas_senales = self.ejecutar_analisis()
                self.mostrar_resumen_operaciones()
                minutos_espera = self.config.get('scan_interval_minutes', 1)
                print(f"\n✅ Análisis completado. Señales nuevas: {nuevas_senales}")
                print(f"⏳ Próximo análisis en {minutos_espera} minutos...")
                print("-" * 60)
                for minuto in range(minutos_espera):
                    time.sleep(60)
                    restantes = minutos_espera - (minuto + 1)
                    if restantes > 0 and restantes % 5 == 0:
                        print(f"   ⏰ {restantes} minutos restantes...")
        except KeyboardInterrupt:
            print("\n🛑 Bot detenido por el usuario")
            print("💾 Guardando estado final...")
            self.guardar_estado()
            print("👋 ¡Hasta pronto!")
        except Exception as e:
            print(f"\n❌ Error en el bot: {e}")
            print("💾 Intentando guardar estado...")
            try:
                self.guardar_estado()
            except:
                pass

# ---------------------------
# CONFIGURACIÓN SIMPLE
# ---------------------------
def crear_config_desde_entorno():
    directorio_actual = os.path.dirname(os.path.abspath(__file__))
    telegram_chat_ids_str = os.environ.get('TELEGRAM_CHAT_ID', '-1002272872445')
    telegram_chat_ids = [cid.strip() for cid in telegram_chat_ids_str.split(',') if cid.strip()]
    return {
        'min_channel_width_percent': 4.0,
        'trend_threshold_degrees': 16.0,
        'min_trend_strength_degrees': 16.0,
        'entry_margin': 0.001,
        'min_rr_ratio': 1.2,
        'scan_interval_minutes': 1,
        'timeframes': ['5m', '15m', '30m', '1h', '4h'],
        'velas_options': [80, 100, 120, 150, 200],
        'symbols': [
            'BTCUSDT','ETHUSDT','DOTUSDT','LINKUSDT','BNBUSDT','XRPUSDT','SOLUSDT','AVAXUSDT',
            'DOGEUSDT','LTCUSDT','ATOMUSDT','XLMUSDT','ALGOUSDT','VETUSDT','ICPUSDT','FILUSDT',
            'BCHUSDT','EOSUSDT','TRXUSDT','XTZUSDT','SUSHIUSDT','COMPUSDT','YFIUSDT','ETCUSDT',
            'SNXUSDT','RENUSDT','1INCHUSDT','NEOUSDT','ZILUSDT','HOTUSDT','ENJUSDT','ZECUSDT'
        ],
        'telegram_token': os.environ.get('TELEGRAM_TOKEN'),
        'telegram_chat_ids': telegram_chat_ids,
        'auto_optimize': True,
        'min_samples_optimizacion': 30,
        'reevaluacion_horas': 24,
        'log_path': os.path.join(directorio_actual, 'operaciones_log_v23.csv'),
        'estado_file': os.path.join(directorio_actual, 'estado_bot_v23.json'),
        'binance_api_key': os.environ.get('BINANCE_API_KEY'),
        'binance_secret_key': os.environ.get('BINANCE_SECRET_KEY'),
        'binance_testnet': os.environ.get('BINANCE_TESTNET', 'true').lower() == 'true'
    }

# ---------------------------
# FLASK APP Y RENDER
# ---------------------------
app = Flask(__name__)
config = crear_config_desde_entorno()
bot = TradingBot(config)

def run_bot_loop():
    while True:
        try:
            bot.ejecutar_analisis()
            time.sleep(bot.config.get('scan_interval_minutes', 1) * 60)
        except Exception as e:
            print(f"Error en el hilo del bot: {e}", file=sys.stderr)
            time.sleep(60)

bot_thread = threading.Thread(target=run_bot_loop, daemon=True)
bot_thread.start()

@app.route('/')
def index():
    return "Bot Breakout + Reentry está en línea.", 200

@app.route('/webhook', methods=['POST'])
def telegram_webhook():
    if request.is_json:
        update = request.get_json()
        print(f"Update recibido: {json.dumps(update)}", file=sys.stdout)
        return jsonify({"status": "ok"}), 200
    return jsonify({"error": "Request must be JSON"}), 400

def setup_telegram_webhook():
    token = os.environ.get('TELEGRAM_TOKEN')
    if not token:
        return
    webhook_url = os.environ.get('WEBHOOK_URL')
    if not webhook_url:
        render_url = os.environ.get('RENDER_EXTERNAL_URL')
        if render_url:
            webhook_url = f"{render_url}/webhook"
        else:
            return
    try:
        requests.get(f"https://api.telegram.org/bot{token}/deleteWebhook")
        requests.get(f"https://api.telegram.org/bot{token}/setWebhook?url={webhook_url}")
    except Exception as e:
        print(f"Error configurando webhook: {e}", file=sys.stderr)

if __name__ == '__main__':
    setup_telegram_webhook()
    app.run(debug=True, port=5000)
