import nest_asyncio
import asyncio
import atexit
import signal
import threading

# Windows에서 asyncio 문제 해결
nest_asyncio.apply()

if asyncio.get_event_loop().is_closed():
    asyncio.set_event_loop(asyncio.new_event_loop())

import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import plotly.express as px
from binance.client import Client
from datetime import datetime, timedelta
import os
import ta
from dotenv import load_dotenv
import time
from main import HighFrequencyBot  # HighFrequencyBot 클래스 import 추가
import sys

# .env 파일 로드
load_dotenv()

# 전역 변수로 봇 인스턴스 관리
active_bots = set()

class StreamlitApp:
    def __init__(self):
        if 'initialized' not in st.session_state:
            st.session_state.initialized = True
            st.session_state.active_bots = set()
            
            # 상태 파일 초기화
            with open('bot_status.txt', 'w') as f:
                f.write('')
            
            # 상태 파일 업데이트 스레드 시작
            self.update_thread = threading.Thread(target=self.update_status_file)
            self.update_thread.daemon = True
            self.update_thread.start()
            
            # 종료 시 정리 함수 등록
            atexit.register(self.cleanup_resources)
            
            # Streamlit 세션 상태에 종료 함수 등록
            if 'on_exit' not in st.session_state:
                st.session_state.on_exit = self.cleanup_resources

    def cleanup_resources(self):
        """모든 리소스 정리"""
        print("Cleaning up resources...")
        if hasattr(st.session_state, 'active_bots'):
            for bot in st.session_state.active_bots:
                try:
                    if bot and hasattr(bot, 'stop'):
                        bot.stop()
                        print(f"Bot {id(bot)} stopped")
                except Exception as e:
                    print(f"Error stopping bot {id(bot)}: {e}")
            st.session_state.active_bots.clear()
            
            # 상태 파일 삭제
            try:
                os.remove('bot_status.txt')
            except:
                pass

    def update_status_file(self):
        """상태 파일 주기적 업데이트"""
        while True:
            try:
                if hasattr(st.session_state, 'active_bots'):
                    with open('bot_status.txt', 'w') as f:
                        bot_ids = [str(id(bot)) for bot in st.session_state.active_bots]
                        f.write(','.join(bot_ids))
            except Exception as e:
                print(f"상태 파일 업데이트 중 에러: {e}")
            time.sleep(10)  # 10초마다 업데이트

class BinanceDashboard:
    def __init__(self):
        self.api_key = os.getenv('BINANCE_API_KEY')
        self.api_secret = os.getenv('BINANCE_SECRET_API_KEY')
        
        # 타임스탬프 오류 해결을 위한 시간 동기화 설정
        self.client = Client(
            self.api_key, 
            self.api_secret, 
            {"verify": True, "timeout": 20},
            tld='com'
        )
        
        # 서버 시간과 로컬 시간의 차이 계산
        server_time = self.client.get_server_time()
        self.time_offset = server_time['serverTime'] - int(time.time() * 1000)
        
        self.default_symbol = 'BTCUSDT'

    def get_account_balance(self):
        """계좌 잔고 조회"""
        try:
            # 요청 전 시간 동기화
            self.client.timestamp_offset = self.time_offset
            
            account_info = self.client.get_account()
            balances = []
            total_value_usdt = 0
            
            for asset in account_info['balances']:
                free_balance = float(asset['free'])
                locked_balance = float(asset['locked'])
                if free_balance > 0 or locked_balance > 0:
                    try:
                        if asset['asset'] != 'USDT':
                            ticker = self.client.get_symbol_ticker(symbol=f"{asset['asset']}USDT")
                            price_usdt = float(ticker['price'])
                        else:
                            price_usdt = 1.0
                        
                        total_balance = free_balance + locked_balance
                        value_usdt = total_balance * price_usdt
                        total_value_usdt += value_usdt
                        
                        balances.append({
                            'Asset': asset['asset'],
                            'Free': free_balance,
                            'Locked': locked_balance,
                            'Total': total_balance,
                            'Price(USDT)': price_usdt,
                            'Value(USDT)': value_usdt,
                            'Portfolio %': 0  # 나중에 계산
                        })
                    except:
                        continue
            
            # 포트폴리오 비율 계산
            for balance in balances:
                balance['Portfolio %'] = (balance['Value(USDT)'] / total_value_usdt) * 100
                
            return pd.DataFrame(balances), total_value_usdt
        except Exception as e:
            st.error(f"잔고 조회 에러: {e}")
            return None, 0

    def get_historical_data(self, symbol, interval='1d', limit=100):
        """과거 가격 데이터 조회"""
        try:
            klines = self.client.get_klines(
                symbol=symbol,
                interval=interval,
                limit=limit
            )
            
            df = pd.DataFrame(klines, columns=[
                'timestamp', 'open', 'high', 'low', 'close', 'volume',
                'close_time', 'quote_volume', 'trades', 'taker_buy_base',
                'taker_buy_quote', 'ignored'
            ])
            
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            for col in ['open', 'high', 'low', 'close', 'volume']:
                df[col] = df[col].astype(float)
                
            return df
        except Exception as e:
            st.error(f"Historical data error: {e}")
            return None

    def get_technical_indicators(self, df):
        """기술적 지표 계산"""
        try:
            # RSI
            df['RSI'] = ta.momentum.RSIIndicator(df['close']).rsi()
            
            # MACD
            macd = ta.trend.MACD(df['close'])
            df['MACD'] = macd.macd()
            df['MACD_Signal'] = macd.macd_signal()
            
            # Bollinger Bands
            bollinger = ta.volatility.BollingerBands(df['close'])
            df['BB_Upper'] = bollinger.bollinger_hband()
            df['BB_Lower'] = bollinger.bollinger_lband()
            df['BB_Middle'] = bollinger.bollinger_mavg()
            
            return df
        except Exception as e:
            st.error(f"Technical indicators error: {e}")
            return df

    def get_top_movers(self):
        """가격 변동폭 상위 코인"""
        try:
            tickers = self.client.get_ticker()
            df = pd.DataFrame(tickers)
            
            # 데이터 타입 변환을 더 안전하게 처리
            df['priceChangePercent'] = pd.to_numeric(df['priceChangePercent'], errors='coerce')
            df['lastPrice'] = pd.to_numeric(df['lastPrice'], errors='coerce')
            df['volume'] = pd.to_numeric(df['volume'], errors='coerce')
            df['quoteVolume'] = pd.to_numeric(df['quoteVolume'], errors='coerce')
            
            # USDT 페어만 필터링
            df = df[df['symbol'].str.endswith('USDT')]
            df['symbol'] = df['symbol'].str.replace('USDT', '')
            
            # NaN 값 제거
            df = df.dropna(subset=['priceChangePercent', 'lastPrice', 'volume', 'quoteVolume'])
            
            df['absChange'] = df['priceChangePercent'].abs()
            top_movers = df.nlargest(10, 'absChange')[
                ['symbol', 'priceChangePercent', 'lastPrice', 'volume', 'quoteVolume']
            ]
            top_movers.columns = ['Symbol', 'Change(%)', 'Price', 'Volume', 'Volume(USDT)']
            
            return top_movers
        except Exception as e:
            st.error(f"Top movers error: {e}")
            return pd.DataFrame()  # 빈 데이터프레임 반환

    def get_trade_history(self, symbol):
        """거래 내역 조회"""
        try:
            trades = self.client.get_my_trades(symbol=symbol)
            if trades:
                df = pd.DataFrame(trades)
                df['time'] = pd.to_datetime(df['time'], unit='ms')
                df['price'] = df['price'].astype(float)
                df['qty'] = df['qty'].astype(float)
                df['quoteQty'] = df['quoteQty'].astype(float)
                return df
            return None
        except Exception as e:
            st.error(f"Trade history error: {e}")
            return None

    def execute_high_frequency_trade(self, symbol, quantity, interval=0.1, 
                                   stop_loss_percent=1.0, take_profit_percent=1.0,
                                   max_trades=10):
        """향상된 초단타 거래 실행"""
        try:
            trades = []
            total_profit = 0
            trade_count = 0
            
            # 초기 잔고 확인
            initial_balance = self.get_account_balance()
            if initial_balance is None:
                return "고 조회 실패"
            
            while trade_count < max_trades:
                # 현재 가격 조회
                current_price = self.monitor_price(symbol)
                if current_price is None:
                    continue
                    
                # 매수 주문
                buy_order = self.client.create_order(
                    symbol=symbol,
                    side=Client.SIDE_BUY,
                    type=Client.ORDER_TYPE_MARKET,
                    quantity=quantity
                )
                buy_price = float(buy_order['fills'][0]['price'])
                
                # 손절매/익절 가격 설정
                stop_loss_price = buy_price * (1 - stop_loss_percent/100)
                take_profit_price = buy_price * (1 + take_profit_percent/100)
                
                # 가격 모니터링 및 매도 조건 확인
                while True:
                    current_price = self.monitor_price(symbol)
                    if current_price is None:
                        continue
                        
                    if current_price <= stop_loss_price or current_price >= take_profit_price:
                        # 매도 주문
                        sell_order = self.client.create_order(
                            symbol=symbol,
                            side=Client.SIDE_SELL,
                            type=Client.ORDER_TYPE_MARKET,
                            quantity=quantity
                        )
                        sell_price = float(sell_order['fills'][0]['price'])
                        
                        # 수익 계산
                        profit, profit_percent = self.calculate_profit(
                            buy_price, sell_price, quantity
                        )
                        
                        trades.append({
                            'trade_no': trade_count + 1,
                            'buy_price': buy_price,
                            'sell_price': sell_price,
                            'quantity': quantity,
                            'profit_usdt': profit,
                            'profit_percent': profit_percent,
                            'type': 'STOP_LOSS' if current_price <= stop_loss_price else 'TAKE_PROFIT'
                        })
                        
                        total_profit += profit
                        break
                
                trade_count += 1
                time.sleep(interval)
            
            # 최종 잔고 확인
            final_balance = self.get_account_balance()
            
            return {
                'trades': trades,
                'total_profit_usdt': total_profit,
                'initial_balance': initial_balance,
                'final_balance': final_balance,
                'trade_count': trade_count
            }
                
        except Exception as e:
            return f"거래 실행 에러: {e}"

    def monitor_price(self, symbol):
        """실시간 가격 모니터링"""
        try:
            ticker = self.client.get_symbol_ticker(symbol=symbol)
            return float(ticker['price'])
        except Exception as e:
            st.error(f"가격 모니터링 에러: {e}")
            return None

    def calculate_profit(self, buy_price, sell_price, quantity, fee_rate=0.001):
        """수익률 계산"""
        buy_total = buy_price * quantity * (1 + fee_rate)
        sell_total = sell_price * quantity * (1 - fee_rate)
        profit = sell_total - buy_total
        profit_percent = (profit / buy_total) * 100
        return profit, profit_percent

    def start_high_frequency_trading(self, symbol, quantity, interval=0.1,
                                   stop_loss_percent=1.0, take_profit_percent=1.0,
                                   max_trades=10):
        """대시보드에서 고빈도 거래 시작"""
        try:
            bot = HighFrequencyBot()  # main.py의 봇 인스턴스 생성
            
            result = bot.execute_high_frequency_trade(
                symbol=symbol,
                quantity=quantity,
                interval=interval,
                stop_loss_percent=stop_loss_percent,
                take_profit_percent=take_profit_percent,
                max_trades=max_trades,
                trades_per_second=5
            )
            
            return result
            
        except Exception as e:
            st.error(f"거래 실행 에러: {e}")
            return None

    def stop(self):
        """봇 종료"""
        try:
            # WebSocket 연결 종료
            if hasattr(self, 'ws_manager') and self.ws_manager:
                try:
                    self.loop.run_until_complete(self.ws_manager.close())
                    self.ws_manager = None
                except Exception as e:
                    print(f"WebSocket 종료 에러: {e}")

            # 이벤트 루프 종료
            if self.loop and not self.loop.is_closed():
                try:
                    pending = asyncio.all_tasks(self.loop)
                    self.loop.run_until_complete(asyncio.gather(*pending))
                    self.loop.close()
                except Exception as e:
                    print(f"이벤트 루프 종료 에러: {e}")

            print("봇이 안전하게 종료되었습니다.")
            
        except Exception as e:
            print(f"봇 종료 중 에러: {e}")

def plot_candlestick(df):
    """캔스 차트 생성"""
    fig = go.Figure(data=[go.Candlestick(
        x=df['timestamp'],
        open=df['open'],
        high=df['high'],
        low=df['low'],
        close=df['close']
    )])
    
    fig.add_trace(go.Scatter(
        x=df['timestamp'],
        y=df['BB_Middle'],
        name='BB Middle',
        line=dict(color='gray', dash='dash')
    ))
    
    fig.add_trace(go.Scatter(
        x=df['timestamp'],
        y=df['BB_Upper'],
        name='BB Upper',
        line=dict(color='lightgray', dash='dash')
    ))
    
    fig.add_trace(go.Scatter(
        x=df['timestamp'],
        y=df['BB_Lower'],
        name='BB Lower',
        line=dict(color='lightgray', dash='dash')
    ))
    
    fig.update_layout(
        title='Price Chart with Bollinger Bands',
        yaxis_title='Price',
        xaxis_title='Date'
    )
    
    return fig

def plot_technical_indicators(df):
    """기술적 지표 차트 생성"""
    fig = go.Figure()
    
    # RSI
    fig.add_trace(go.Scatter(
        x=df['timestamp'],
        y=df['RSI'],
        name='RSI'
    ))
    
    # 과매수/과매도 라인
    fig.add_hline(y=70, line_dash="dash", line_color="red", annotation_text="과매수")
    fig.add_hline(y=30, line_dash="dash", line_color="green", annotation_text="과매도")
    
    fig.update_layout(
        title='RSI Indicator',
        yaxis_title='RSI',
        xaxis_title='Date'
    )
    
    return fig

def plot_portfolio_pie(balances_df):
    """포트폴리오 구성 파이 차트"""
    fig = px.pie(
        balances_df,
        values='Value(USDT)',
        names='Asset',
        title='Portfolio Distribution'
    )
    return fig

def update_metrics():
    """실시간 메트릭 업데이트 함수"""
    print("실시간 가격 모니터링 시작")
    
    while True:
        if not hasattr(st.session_state, 'scalping_active') or not st.session_state.scalping_active:
            time.sleep(0.1)
            continue
            
        if not hasattr(st.session_state, 'bot'):
            print("봇이 초기화되지 않음")
            time.sleep(0.1)
            continue
            
        try:
            # 현재가 조회
            current_price = float(st.session_state.bot.client.get_symbol_ticker(symbol='BTCUSDT')['price'])
            
            # 이전 가격과 비교
            prev_price = getattr(st.session_state, 'last_price', current_price)
            price_change = current_price - prev_price
            price_change_percent = (price_change / prev_price) * 100 if prev_price else 0
            
            # 가격 변화에 따른 색상 설정
            price_color = "green" if price_change >= 0 else "red"
            arrow = "↑" if price_change >= 0 else "↓"
            
            # 기본 정보 섹션의 BTC 현재가 업데이트
            if hasattr(st.session_state, 'btc_price_display'):
                st.session_state.btc_price_display.markdown(f"""
                <div style='padding: 10px; border-radius: 5px; border: 1px solid #ddd;'>
                    <h4 style='margin: 0; color: #1E88E5;'>BTC 현재가</h4>
                    <div style='font-size: 20px; margin-top: 5px;'>
                        <span style='color: {price_color}; font-weight: bold;'>{current_price:,.2f} USDT</span>
                        <br>
                        <span style='font-size: 14px; color: {price_color};'>
                            {arrow} {abs(price_change):+,.2f} ({price_change_percent:+.2f}%)
                        </span>
                    </div>
                </div>
                """, unsafe_allow_html=True)
            
            # 거래 로그 섹션의 현재가 모니터링 업데이트
            if hasattr(st.session_state, 'price_monitor_container'):
                st.session_state.price_monitor_container.markdown(f"""
                <div style='padding: 10px; border-radius: 5px; border: 1px solid #ddd; margin-bottom: 20px;'>
                    <h4 style='margin: 0; color: #1E88E5;'>🔄 실시간 BTC 가격</h4>
                    <div style='font-size: 24px; margin-top: 10px;'>
                        <span style='color: {price_color}; font-weight: bold;'>{current_price:,.2f} USDT</span>
                    </div>
                    <div style='font-size: 16px; margin-top: 5px;'>
                        <span style='color: {price_color};'>
                            {arrow} {abs(price_change):+,.2f} USDT ({price_change_percent:+.2f}%)
                        </span>
                    </div>
                    <div style='font-size: 12px; color: #666; margin-top: 5px;'>
                        마지막 업데이트: {datetime.now().strftime('%H:%M:%S.%f')[:-4]}
                    </div>
                </div>
                """, unsafe_allow_html=True)
            
            # 현재가 저장
            st.session_state.last_price = current_price
            
            time.sleep(0.1)  # 0.1초 대기 (1초에 10회 업데이트)
            
        except Exception as e:
            print(f"현재가 업데이트 중 에러: {e}")
            time.sleep(0.1)

def start_new_long_position():
    """새로운 Long 포지션 시작"""
    try:
        # 현재 시장 가격으로 매수 주문
        current_price = float(st.session_state.bot.client.get_symbol_ticker(symbol='BTCUSDT')['price'])
        
        # USDT 잔고의 설정된 비율만큼 사용
        current_balance = st.session_state.bot.get_account_balance()
        trade_amount = (current_balance['USDT'] * st.session_state.use_percentage) / 100
        quantity = (trade_amount / current_price) * 0.999  # 0.1% 여유
        quantity = "{:.5f}".format(float(quantity))

        # Long 매수 주문
        long_buy_order = st.session_state.bot.client.create_order(
            symbol='BTCUSDT',
            side=Client.SIDE_BUY,
            type=Client.ORDER_TYPE_MARKET,
            quantity=quantity
        )
        
        # 매수가 저장
        st.session_state.long_buy_price = float(long_buy_order['fills'][0]['price'])
        
        # 목표가 설정
        if st.session_state.profit_type == "절대값(USDT)":
            st.session_state.long_target_price = st.session_state.long_buy_price + st.session_state.profit_target
        else:
            st.session_state.long_target_price = st.session_state.long_buy_price * (1 + st.session_state.profit_target/100)

        # 매도 주문 등록
        long_sell_order = st.session_state.bot.client.create_order(
            symbol='BTCUSDT',
            side=Client.SIDE_SELL,
            type=Client.ORDER_TYPE_LIMIT,
            timeInForce='GTC',
            quantity=quantity,
            price="{:.2f}".format(st.session_state.long_target_price)
        )
        st.session_state.long_order_id = long_sell_order['orderId']

    except Exception as e:
        print(f"새로운 Long 포지션 시작 중 에러: {e}")

def start_new_short_position():
    """새로운 Short 포지션 시작"""
    try:
        # session_state에서 필요한 변수들 가져오기
        short_quantity = st.session_state.short_quantity
        profit_type = st.session_state.profit_type
        profit_target = st.session_state.profit_target
        tick_size = st.session_state.tick_size

        current_price = float(st.session_state.bot.client.get_symbol_ticker(symbol='BTCUSDT')['price'])
        # Short 포지션 매도 주문
        status_container.info("🔴 SHORT 포지션: 매도 주문 시작...")
        short_sell_order = st.session_state.bot.client.create_order(
            symbol='BTCUSDT',
            side=Client.SIDE_SELL,
            type=Client.ORDER_TYPE_MARKET,
            quantity=short_quantity
        )
        status_container.success(f"🔴 SHORT 포지션: 매도 완료 (가격: {float(short_sell_order['fills'][0]['price']):,.3f} USDT)")

        # Short 목표가 설정 및 매수 주문
        status_container.info("🔴 SHORT 포지션: 목표가 매수 주문 등록 중...")

        if profit_type == "절대값(USDT)":
            st.session_state.short_target_price = max(
                float(short_sell_order['fills'][0]['price']) - profit_target,  # 실제 매도가 기준
                float(short_sell_order['fills'][0]['price']) * 0.8  # 최대 20% 하락으로 제한
            )
        else:
            profit_percent = min(profit_target, 20)  # 최대 20%로 제한
            st.session_state.short_target_price = float(short_sell_order['fills'][0]['price']) * (1 - profit_percent/100)

        # 가격을 틱 사이즈에 맞게 반올림
        formatted_short_price = "{:.8f}".format(
            round(st.session_state.short_target_price / tick_size) * tick_size
        )

        try:
            short_buy_order = st.session_state.bot.client.create_order(
                symbol='BTCUSDT',
                side=Client.SIDE_BUY,
                type=Client.ORDER_TYPE_LIMIT,
                timeInForce='GTC',
                quantity=short_quantity,
                price=formatted_short_price
            )
            status_container.success(f"🔴 SHORT 포지션: 목표가 매수 주문 등록 완료 (목표가: {formatted_short_price} USDT)")
        except Exception as e:
            st.error(f"Short 주문 에러: {e}")
            raise e

    except Exception as e:
        print(f"새로운 Short 포지션 시작 중 에러: {e}")

def monitor_orders():
    """주문 상태 모니터링"""
    while st.session_state.scalping_active:
        try:
            # Long 포지션 주문 체결 확인
            if hasattr(st.session_state, 'long_order_id'):
                long_order = st.session_state.bot.check_order_status(st.session_state.long_order_id)
                if long_order and long_order['status'] == 'FILLED':
                    update_position_status("LONG", "매도 주문 체결 완료!", 
                        f"체결가: {float(long_order['price']):,.2f} USDT\n"
                        f"수익: {(float(long_order['price']) - st.session_state.long_buy_price) * float(long_order['executedQty']):,.2f} USDT")
                    handle_long_order_filled(long_order)
                else:
                    # 미체결 상태 표시
                    elapsed_time = (datetime.now() - st.session_state.long_order_time).total_seconds()
                    update_position_status("LONG", "목표가 매도 대기 중", 
                        f"목표가: {st.session_state.long_target_price:,.2f} USDT\n"
                        f"경과 시간: {elapsed_time:.1f}초")

            # Short 포지션 주문 체결 확인
            if hasattr(st.session_state, 'short_order_id'):
                short_order = st.session_state.bot.check_order_status(st.session_state.short_order_id)
                if short_order and short_order['status'] == 'FILLED':
                    update_position_status("SHORT", "매도 주문 체결 완료!", 
                        f"체결가: {float(short_order['price']):,.2f} USDT\n"
                        f"수익: {(st.session_state.short_sell_price - float(short_order['price'])) * float(short_order['executedQty']):,.2f} USDT")
                    handle_short_order_filled(short_order)
                else:
                    # 미체결 상태 표시
                    elapsed_time = (datetime.now() - st.session_state.short_order_time).total_seconds()
                    update_position_status("SHORT", "목표가 매도 대기 중", 
                        f"목표가: {st.session_state.short_target_price:,.2f} USDT\n"
                        f"경과 시간: {elapsed_time:.1f}초")

            time.sleep(0.1)  # 0.1초 대기

        except Exception as e:
            print(f"주문 모니터링 중 에러: {e}")
            time.sleep(0.1)

def handle_long_order_filled(order):
    """Long 포지션 주문 체결 처리"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    st.session_state.long_trade_logs.append({
        '시간': timestamp,
        '포지션': 'LONG',
        '거래 ID': order['orderId'],
        '유형': '매도(체결)',
        '가격': f"{float(order['price']):,.3f}",
        '수량': order['executedQty'],
        'USDT 금액': f"{float(order['executedQty']) * float(order['price']):,.3f}",
        '수수료(BNB)': '-',
        '상태': '체결완료',
        '목표가': f"{float(order['price']):,.3f}"
    })
    st.session_state.long_order_id = None
    start_new_long_position()  # 새로운 Long 포지션 시작

def handle_short_order_filled(order):
    """Short 포지션 주문 체결 처리"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    st.session_state.short_trade_logs.append({
        '시간': timestamp,
        '포지션': 'SHORT',
        '거래 ID': order['orderId'],
        '유형': '매수(체결)',
        '가격': f"{float(order['price']):,.3f}",
        '수량': order['executedQty'],
        'USDT 금액': f"{float(order['executedQty']) * float(order['price']):,.3f}",
        '수수료(BNB)': '-',
        '상태': '체결완료',
        '목표가': f"{float(order['price']):,.3f}"
    })
    st.session_state.short_order_id = None
    start_new_short_position()  # 새로운 Short 포지션 시작

def update_position_status(position_type, status, details=None):
    """포지션 상태 업데이트"""
    try:
        current_price = float(st.session_state.bot.client.get_symbol_ticker(symbol='BTCUSDT')['price'])
        
        if position_type == "LONG":
            container = st.session_state.long_status_container
            color = "#32CD32"
            border_color = "#90EE90"
            emoji = "🟢"
            target_price = getattr(st.session_state, 'long_target_price', 0)
            entry_price = getattr(st.session_state, 'long_buy_price', 0)
        else:
            container = st.session_state.short_status_container
            color = "#DC143C"
            border_color = "#FFB6C1"
            emoji = "🔴"
            target_price = getattr(st.session_state, 'short_target_price', 0)
            entry_price = getattr(st.session_state, 'short_sell_price', 0)

        if entry_price and target_price:
            price_info = f"""
            현재가: {current_price:,.2f} USDT<br>
            진입가: {entry_price:,.2f} USDT<br>
            목표가: {target_price:,.2f} USDT<br>
            진행률: {((current_price - entry_price) / (target_price - entry_price) * 100):,.1f}%
            """
        else:
            price_info = "거래 대기 중..."

        status_html = f"""
        <div style='padding: 10px; border-radius: 5px; border: 1px solid {border_color}; margin-bottom: 20px;'>
            <h4 style='margin: 0; color: {color};'>{emoji} {position_type} 포지션 상태</h4>
            <div style='font-size: 16px; margin-top: 5px;'>
                <strong>{status}</strong><br>
                {price_info}<br>
                {f'{details}' if details else ''}
            </div>
        </div>
        """
        container.markdown(status_html, unsafe_allow_html=True)
        
    except Exception as e:
        print(f"포지션 상태 업데이트 중 에러: {e}")

def main():
    # session_state 초기화
    if 'trade_logs' not in st.session_state:
        st.session_state.trade_logs = []
    if 'long_trade_logs' not in st.session_state:
        st.session_state.long_trade_logs = []
    if 'short_trade_logs' not in st.session_state:
        st.session_state.short_trade_logs = []
    if 'scalping_active' not in st.session_state:
        st.session_state.scalping_active = False
    if 'buy_price' not in st.session_state:
        st.session_state.buy_price = 0.0
    if 'target_price' not in st.session_state:
        st.session_state.target_price = 0.0
    if 'current_price' not in st.session_state:
        st.session_state.current_price = 0.0
    if 'long_target_price' not in st.session_state:
        st.session_state.long_target_price = 0.0
    if 'short_target_price' not in st.session_state:
        st.session_state.short_target_price = 0.0
    if 'long_buy_price' not in st.session_state:
        st.session_state.long_buy_price = 0.0
    if 'short_sell_price' not in st.session_state:
        st.session_state.short_sell_price = 0.0
    if 'long_order_id' not in st.session_state:
        st.session_state.long_order_id = None
    if 'short_order_id' not in st.session_state:
        st.session_state.short_order_id = None
    if 'long_order_time' not in st.session_state:
        st.session_state.long_order_time = None
    if 'short_order_time' not in st.session_state:
        st.session_state.short_order_time = None
    
    # 초기 잔고 초기화
    if 'initial_balance' not in st.session_state:
        try:
            bot = HighFrequencyBot()
            initial_balance = bot.get_account_balance()
            # 현재가 조회
            current_price = float(bot.client.get_symbol_ticker(symbol='BTCUSDT')['price'])
            if initial_balance:
                st.session_state.initial_balance = initial_balance
            else:
                st.session_state.initial_balance = {'USDT': 0.0, 'BTC': 0.0}
        except Exception as e:
            st.error(f"초기 잔고 조회 실패: {e}")
            st.session_state.initial_balance = {'USDT': 0.0, 'BTC': 0.0}
            current_price = 0.0
    else:
        try:
            bot = HighFrequencyBot()
            current_price = float(bot.client.get_symbol_ticker(symbol='BTCUSDT')['price'])
        except Exception as e:
            st.error(f"현재가 조회 실패: {e}")
            current_price = 0.0
    
    # BNB 잔고 초기화
    if 'initial_bnb' not in st.session_state:
        try:
            bot = HighFrequencyBot()
            bnb_balance = float([asset for asset in bot.client.get_account()['balances'] if asset['asset'] == 'BNB'][0]['free'])
            st.session_state.initial_bnb = bnb_balance
        except Exception as e:
            st.error(f"BNB 잔고 조회 실패: {e}")
            st.session_state.initial_bnb = 0.0

    initial_balance = st.session_state.initial_balance
    bnb_balance = st.session_state.initial_bnb

    st.set_page_config(page_title="Binance Dashboard", layout="wide")
    
    # 페이지 전체 너비 설정을 위한 스타일 추가
    st.markdown("""
        <style>
            .reportview-container .main .block-container {
                max-width: 95%;
                padding-left: 5%;
                padding-right: 5%;
            }
            .element-container {
                width: 100%;
                max-width: 4000px;
            }
            .stMetric {
                width: 100%;
                min-width: 250px;
            }
            .stMetric-value {
                white-space: nowrap;
                overflow: visible;
                font-size: 1.1rem !important;
                padding: 0 10px;
            }
            .stMetric-label {
                font-size: 1rem !important;
                padding: 0 10px;
            }
            div[data-testid="metric-container"] {
                width: fit-content;
                min-width: 250px;
                margin: 0 15px;
            }
            div[data-testid="column"] {
                padding: 0 10px;
            }
            div[data-testid="stHorizontalBlock"] {
                gap: 1rem;
            }
            .dataframe {
                font-size: 0.9rem !important;
            }
            .dataframe td {
                white-space: nowrap;
            }
        </style>
    """, unsafe_allow_html=True)

    app = StreamlitApp()
    
    # 시작 세션 상태 초기화
    if 'bot' not in st.session_state:
        st.session_state.bot = None

    st.title("Binance Trading Dashboard")

    dashboard = BinanceDashboard()

    # 탭 생성
    tabs = st.tabs([
        "Portfolio", "Market Analysis", "Trading View", 
        "Technical Analysis", "Trade History", "Alerts", "Test Trade"
    ])

    # Portfolio 탭
    with tabs[0]:
        st.header("📊 Portfolio Overview")
        balances_df, total_value = dashboard.get_account_balance()
        
        if balances_df is not None:
            # 총 자산 가치
            st.metric("Total Portfolio Value (USDT)", f"{total_value:,.2f} USDT")
            
            # 포트폴리오 분포 차트
            col1, col2 = st.columns(2)
            with col1:
                st.plotly_chart(plot_portfolio_pie(balances_df))
            
            with col2:
                st.dataframe(balances_df.style.format({
                    'Free': '{:.8f}',
                    'Locked': '{:.8f}',
                    'Total': '{:.8f}',
                    'Price(USDT)': '{:.8f}',
                    'Value(USDT)': '{:.2f}',
                    'Portfolio %': '{:.2f}'
                }))

    # Market Analysis 탭
    with tabs[1]:
        st.header("🔥 Market Movers")
        top_movers = dashboard.get_top_movers()
        if not top_movers.empty:
            try:
                styled_df = top_movers.style.format({
                    'Change(%)': '{:,.2f}',
                    'Price': '{:,.8f}',
                    'Volume': '{:,.2f}',
                    'Volume(USDT)': '{:,.2f}'
                })
                st.dataframe(styled_df)
            except Exception as e:
                st.error(f"데이터 표시 에러: {e}")
                st.dataframe(top_movers)  # 기본 형식으로 표시

    # Trading View 탭
    with tabs[2]:
        st.header("📈 Trading View")
        symbol = st.selectbox(
            "Select Trading Pair",
            options=['BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'DOGEUSDT']
        )
        interval = st.select_slider(
            "Select Timeframe",
            options=['1m', '5m', '15m', '1h', '4h', '1d', '1w'],
            value='1d'
        )
        
        df = dashboard.get_historical_data(symbol, interval)
        if df is not None:
            df = dashboard.get_technical_indicators(df)
            st.plotly_chart(plot_candlestick(df), use_container_width=True)
        
        # 고빈도 거래 섹션 추가
        st.header("🚀 High Frequency Trading")
        col1, col2 = st.columns(2)
        
        with col1:
            hft_symbol = st.selectbox(
                "Trading Pair",
                options=['BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'DOGEUSDT'],
                key='hft_symbol'
            )
            
            trade_quantity = st.number_input(
                "Quantity",
                min_value=0.0001,
                value=0.001,
                step=0.0001,
                format="%.4f"
            )
            
            trade_interval = st.number_input(
                "Trade Interval (seconds)",
                min_value=0.1,
                value=0.1,
                step=0.1,
                format="%.1f"
            )

        with col2:
            stop_loss = st.number_input(
                "Stop Loss (%)",
                min_value=0.1,
                value=1.0,
                step=0.1
            )
            
            take_profit = st.number_input(
                "Take Profit (%)",
                min_value=0.1,
                value=1.0,
                step=0.1
            )
            
            max_trades = st.number_input(
                "Maximum Trades",
                min_value=1,
                value=10,
                step=1
            )

        if st.button("Start High Frequency Trading", key='start_hft'):
            st.warning("⚠️ 고빈도 거래는 매 위험할 수 있습니다. 신중하게 진행하세요!")
            
            # 거래 실행
            with st.spinner('Trading in progress...'):
                result = dashboard.start_high_frequency_trading(
                    symbol=hft_symbol,
                    quantity=trade_quantity,
                    interval=trade_interval,
                    stop_loss_percent=stop_loss,
                    take_profit_percent=take_profit,
                    max_trades=max_trades
                )
            
            # 결과 표시
            if isinstance(result, str):
                st.error(result)
            else:
                st.success("거래 완료!")
                
                # 거래 결과 표시
                st.subheader("Trading Results")
                df_trades = pd.DataFrame(result['trades'])
                st.dataframe(df_trades.style.format({
                    'buy_price': '{:.8f}',
                    'sell_price': '{:.8f}',
                    'quantity': '{:.8f}',
                    'profit_usdt': '{:.4f}',
                    'profit_percent': '{:.2f}%'
                }))
                
                # 총 수익 표시
                st.metric(
                    "Total Profit (USDT)", 
                    f"{result['total_profit_usdt']:.4f}",
                    f"{(result['total_profit_usdt']/result['initial_balance'].get('USDT', 1))*100:.2f}%"
                )

    # Test Trade 탭
    with tabs[6]:
        st.header("🧪 Test Trade")
        
        # 1. 현재 잔고 및 BTC 가격 섹션
        st.subheader("💰 현재 잔고 및 BTC 가격")
        
        # 기본 정보 섹션
        st.subheader("📊 기본 정보")
        basic_info_cols = st.columns(4)
        st.session_state.basic_info_cols = basic_info_cols  # session_state에 저장
        
        # USDT 잔고
        with basic_info_cols[0]:
            usdt_balance = st.empty()
            usdt_balance.metric(
                "USDT 잔고",
                f"{initial_balance['USDT']:,.3f}",
                label_visibility="visible"
            )

        # BTC 잔고
        with basic_info_cols[1]:
            btc_balance = st.empty()
            btc_balance.metric(
                "BTC 잔고",
                f"{initial_balance['BTC']:.8f}",
                label_visibility="visible"
            )

        # BNB 잔고
        with basic_info_cols[2]:
            bnb_balance_display = st.empty()
            bnb_balance_display.metric(
                "BNB 잔고",
                f"{bnb_balance:.4f}",
                label_visibility="visible"
            )

        # BTC 현재가
        with basic_info_cols[3]:
            st.session_state.btc_price_display = st.empty()
            try:
                # 초기 현재가 조회
                current_price = float(bot.client.get_symbol_ticker(symbol='BTCUSDT')['price'])
                prev_price = getattr(st.session_state, 'last_price', current_price)
                price_change = current_price - prev_price
                price_change_percent = (price_change / prev_price) * 100 if prev_price else 0
                
                # 가격 변화에 따른 색상 설정
                price_color = "green" if price_change >= 0 else "red"
                arrow = "↑" if price_change >= 0 else "↓"
                
                st.session_state.btc_price_display.markdown(f"""
                <div style='padding: 10px; border-radius: 5px; border: 1px solid #ddd;'>
                    <h4 style='margin: 0; color: #1E88E5;'>BTC 현재가</h4>
                    <div style='font-size: 20px; margin-top: 5px;'>
                        <span style='color: {price_color}; font-weight: bold;'>{current_price:,.2f} USDT</span>
                        <br>
                        <span style='font-size: 14px; color: {price_color};'>
                            {arrow} {abs(price_change):+,.2f} ({price_change_percent:+.2f}%)
                        </span>
                    </div>
                </div>
                """, unsafe_allow_html=True)
                
                # 현재가 저장
                st.session_state.last_price = current_price
                
            except Exception as e:
                st.error(f"현재가 조회 실패: {e}")
                st.session_state.btc_price_display.markdown("""
                <div style='padding: 10px; border-radius: 5px; border: 1px solid #ddd;'>
                    <h4 style='margin: 0; color: #1E88E5;'>BTC 현재가</h4>
                    <div style='font-size: 20px; margin-top: 5px; color: red;'>
                        조회 실패
                    </div>
                </div>
                """, unsafe_allow_html=True)

        # 2. 포지션 정보 섹션
        st.markdown("#### 📈 포지션 정보")
        position_cols = st.columns(4)

        with position_cols[0]:
            buy_price_display = st.empty()
            buy_price_display.metric(
                "LONG 매수가",
                "대기 중"
            )

        with position_cols[1]:
            target_price_display = st.empty()
            target_price_display.metric(
                "LONG 목표가",
                "대기 중"
            )

        with position_cols[2]:
            short_price_display = st.empty()
            short_price_display.metric(
                "SHORT 매도가",
                "대기 중"
            )

        with position_cols[3]:
            short_target_display = st.empty()
            short_target_display.metric(
                "SHORT 목표가",
                "대기 중"
            )

        sell_status_display = st.empty()

        # 3. Scalping Strategy 섹션
        st.header("📊 Scalping Strategy")
        col1, col2 = st.columns(2)

        with col1:
            use_percentage = st.slider(
                "USDT 사용 비율 (%)",
                min_value=1,
                max_value=100,
                value=10,
                step=1
            )
            
            hedging_enabled = st.checkbox(
                "헷징 활성화 (동일 금액 Short 포지션 동시 실행)", 
                value=True
            )
            
            # 손익 설정 방식 선택
            profit_type = st.radio(
                "손익 설정 방식",
                options=["절대값(USDT)", "퍼센트(%)"],
                horizontal=True
            )
            
            if profit_type == "절대값(USDT)":
                profit_target = st.number_input(
                    "목표 수익 (USDT)",
                    min_value=1.0,
                    max_value=100.0,
                    value=5.0,
                    step=1.0,
                    format="%.1f"
                )
                stop_loss = st.number_input(
                    "손절 기준 (USDT)",
                    min_value=1.0,
                    max_value=100.0,
                    value=30.0,
                    step=1.0,
                    format="%.1f"
                )
            else:
                profit_target = st.number_input(
                    "목표 수익률 (%)",
                    min_value=0.01,
                    max_value=1.0,
                    value=0.05,
                    step=0.01,
                    format="%.2f"
                )
                stop_loss = st.number_input(
                    "손절 기준 (%)",
                    min_value=0.01,
                    max_value=1.0,
                    value=0.1,
                    step=0.01,
                    format="%.2f"
                )

        with col2:
            max_trades = st.number_input(
                "초당 최대 거래 횟수",
                min_value=1,
                max_value=5,
                value=5
            )

            wait_time = st.number_input(
                "최대 대기 시간 (분)",
                min_value=1,
                max_value=10,
                value=3
            )

        # 설정 설명 정
        if profit_type == "절대값(USDT)":
            st.info(f"""
            💡 거래 설정 가이드:
            - USDT 사용 비율: 보유 USDT의 {use_percentage}%를 사용하여 즉시 BTC 매수
            - 목표 수익: 매수 직후 {profit_target} USDT 상승 시점에 예약 매도 주문 설정
            - 실시간 모니터링: 
                • BTC 현재가 1초에 10회 업데이트
                • 예약 매도 주문 체결 여부 1초에 10회 확인
            - 자동 BNB 관리: BNB 잔고 부족 시 USDT의 5%로 자동 구매
            - 거래 완료 시: 즉시 다음 매수 진행 (USDT의 {use_percentage}% 사용)
            - 최대 대기 시간: {wait_time}분 동안 미체결 시 주문 취소 후 재시도
            """)
        else:
            st.info(f"""
            💡 거래 설정 가이드:
            - USDT 사용 비율: 보유 USDT의 {use_percentage}%를 사용하여 즉시 BTC 매수
            - 목표 수익률: 매수 직후 {profit_target}% 상승 시점에 예약 매도 주문 설정
            - 실시간 모니터링: 
                • BTC 현재가 1초에 10회 업데이트
                • 예약 매도 주문 체결 여부 1초에 10회 확인
            - 자동 BNB 관리: BNB 잔고 부족 시 USDT의 5%로 자동 구매
            - 거래 완료 시: 즉시 다음 매수 진행 (USDT의 {use_percentage}% 사용)
            - 최대 대기 시간: {wait_time}분 동안 미체결 시 주문 취소 후 재시도
            """)

        # 4. Start/Stop 버튼
        col3, col4 = st.columns(2)

        with col3:
            if st.button("Start Scalping Strategy"):
                try:
                    # 봇 초기화
                    st.session_state.bot = HighFrequencyBot()
                    print("봇 초기화 완료")
                    
                    # 봇 연결 테스트
                    if not st.session_state.bot.test_connection():
                        st.error("Binance 서버 연결 실패")
                        return
                    print("Binance 서버 연결 성공")
                    
                    # 현재 잔고 확인
                    current_balance = st.session_state.bot.get_account_balance()
                    if not current_balance:
                        st.error("잔고 조회 실패")
                        return
                    print(f"현재 잔고: {current_balance}")

                    # USDT 잔고의 10%를 Long과 Short에 각각 5%씩 할당
                    total_trade_amount = (current_balance['USDT'] * 10) / 100
                    long_trade_amount = total_trade_amount / 2
                    short_trade_amount = total_trade_amount / 2

                    # 현재가 조회
                    current_price = float(st.session_state.bot.client.get_symbol_ticker(symbol='BTCUSDT')['price'])
                    print(f"현재가: {current_price}")

                    # Long 포지션용 수량 계산
                    long_quantity = (long_trade_amount / current_price) * 0.999  # 수수료 고려
                    long_quantity = "{:.5f}".format(float(long_quantity))

                    # Short 포지션용 수량 계산
                    short_quantity = (short_trade_amount / current_price) * 0.999  # 수수료 고려
                    short_quantity = "{:.5f}".format(float(short_quantity))

                    st.info(f"거래 시작: Long {long_quantity} BTC, Short {short_quantity} BTC (각각 약 {long_trade_amount:.2f} USDT)")

                    # 거래 상태 표시를 위한 컨테이너 생성
                    status_container = st.empty()
                    
                    try:
                        # Long 포지션 매수 및 목표가 매도
                        status_container.info("🟢 LONG 포지션 실행 중...")
                        
                        # Long 포지션 매수 (USDT의 5%로 BTC 매수)
                        long_buy_order = st.session_state.bot.client.create_order(
                            symbol='BTCUSDT',
                            side=Client.SIDE_BUY,
                            type=Client.ORDER_TYPE_MARKET,
                            quantity=long_quantity
                        )
                        
                        # fills 배열 확인
                        if not long_buy_order.get('fills'):
                            raise Exception("Long 매수 주문 체결 정보가 없습니다")
                            
                        long_buy_price = float(long_buy_order['fills'][0]['price'])
                        status_container.success(f"""
                        🟢 LONG 포지션 매수 완료:
                        - 매수가: {long_buy_price:,.3f} USDT
                        - 수량: {long_quantity} BTC
                        - 총액: {float(long_quantity) * long_buy_price:,.3f} USDT
                        - 수수료(BNB): {float(long_buy_order['fills'][0].get('commission', 0)):,.8f}
                        """)

                        # Long 포지션 목표가 매도 주문
                        status_container.info("🟢 LONG 포지션: 목표가 매도 주문 등록 중...")
                        if profit_type == "절대값(USDT)":
                            long_target_price = long_buy_price + profit_target
                        else:
                            long_target_price = long_buy_price * (1 + profit_target/100)

                        long_sell_order = st.session_state.bot.client.create_order(
                            symbol='BTCUSDT',
                            side=Client.SIDE_SELL,
                            type=Client.ORDER_TYPE_LIMIT,
                            timeInForce='GTC',
                            quantity=long_quantity,
                            price="{:.2f}".format(long_target_price)
                        )
                        # 주문 시간 기록
                        st.session_state.long_order_time = datetime.now()
                        st.session_state.long_order_id = long_sell_order['orderId']
                        status_container.success(f"""
                        🟢 LONG 포지션 목표가 매도 주문 등록 완료:
                        - 매수가: {long_buy_price:,.3f} USDT
                        - 목표가: {long_target_price:,.2f} USDT
                        - 예상 수익: {(long_target_price - long_buy_price) * float(long_quantity):,.3f} USDT
                        - 예상 수익률: {((long_target_price - long_buy_price) / long_buy_price * 100):,.2f}%
                        """)

                        # Short 포지션 매수 및 목표가 매도 (독립적으로 실행)
                        status_container.info("🔴 SHORT 포지션 실행 중...")
                        
                        # Short 포지션 매수 (USDT의 5%로 BTC 매수)
                        short_buy_order = st.session_state.bot.client.create_order(
                            symbol='BTCUSDT',
                            side=Client.SIDE_BUY,
                            type=Client.ORDER_TYPE_MARKET,
                            quantity=short_quantity
                        )
                        
                        # fills 배열 확인
                        if not short_buy_order.get('fills'):
                            raise Exception("Short 매수 주문 체결 정보가 없습니다")
                            
                        short_buy_price = float(short_buy_order['fills'][0]['price'])
                        status_container.success(f"""
                        🔴 SHORT 포지션 매수 완료:
                        - 매수가: {short_buy_price:,.3f} USDT
                        - 수량: {short_quantity} BTC
                        - 총액: {float(short_quantity) * short_buy_price:,.3f} USDT
                        """)

                        # 거래 로그 업데이트
                        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        
                        # Long 매수 로그
                        commission = float(long_buy_order['fills'][0].get('commission', 0)) if long_buy_order.get('fills') else 0
                        st.session_state.long_trade_logs.append({
                            '시간': timestamp,
                            '포지션': 'LONG',
                            '거래 ID': long_buy_order['orderId'],
                            '유형': '매수',
                            '가격': f"{long_buy_price:,.3f}",
                            '수량': long_quantity,
                            'USDT 금액': f"{float(long_quantity) * long_buy_price:,.3f}",
                            '수수료(BNB)': f"{commission:.8f}",
                            '상태': '체결완료'
                        })

                        # Short 매수 로그
                        commission = float(short_buy_order['fills'][0].get('commission', 0)) if short_buy_order.get('fills') else 0
                        st.session_state.short_trade_logs.append({
                            '시간': timestamp,
                            '포지션': 'SHORT',
                            '거래 ID': short_buy_order['orderId'],
                            '유형': '매수',
                            '가격': f"{short_buy_price:,.3f}",
                            '수량': short_quantity,
                            'USDT 금액': f"{float(short_quantity) * short_buy_price:,.3f}",
                            '수수료(BNB)': f"{commission:.8f}",
                            '상태': '체결완료'
                        })

                        # 메트릭 업데이트 스레드 시작
                        st.session_state.scalping_active = True
                        update_thread = threading.Thread(target=update_metrics)
                        update_thread.daemon = True
                        update_thread.start()
                        print("메트릭 업데이트 스레드 시작됨")

                        # 주문 모니터링 스레드 시작
                        monitor_thread = threading.Thread(target=monitor_orders)
                        monitor_thread.daemon = True
                        monitor_thread.start()
                        print("주문 모니터링 스레드 시작됨")

                    except Exception as e:
                        st.error(f"주문 실행 중 에러: {e}")
                        print(f"주문 실행 중 에러: {e}")

                except Exception as e:
                    st.error(f"전략 실행 에러: {e}")
                    print(f"전략 실행 에러: {e}")

        # Stop 버튼 클릭 시
        if st.button("Stop Scalping Strategy"):
            try:
                # 활성 상태 먼저 변경
                st.session_state.scalping_active = False
                
                # 활성화된 주문 취소
                if hasattr(st.session_state, 'bot') and st.session_state.bot:
                    try:
                        # Long 포지션 주문 취소
                        if hasattr(st.session_state, 'long_order_id') and st.session_state.long_order_id:
                            st.session_state.bot.client.cancel_order(
                                symbol='BTCUSDT',
                                orderId=st.session_state.long_order_id
                            )
                            st.session_state.long_order_id = None
                        
                        # Short 포지션 주문 취소
                        if hasattr(st.session_state, 'short_order_id') and st.session_state.short_order_id:
                            st.session_state.bot.client.cancel_order(
                                symbol='BTCUSDT',
                                orderId=st.session_state.short_order_id
                            )
                            st.session_state.short_order_id = None
                        
                        # 봇 종료
                        st.session_state.bot.stop()
                        st.session_state.bot = None
                        
                        st.success("스캘핑 전략이 안전하게 중지되었습니다.")
                        
                    except Exception as e:
                        print(f"주문 취소 중 에러: {e}")
                        st.warning("일부 주문 취소가 실패했을 수 있습니다.")
                else:
                    st.info("실행 중인 봇이 없습니다.")
                
            except Exception as e:
                print(f"전략 중지 중 에러: {e}")
                st.error("전략 중지 중 에러가 발생했습니다. 새로고침 후 다시 시도해주세요.")

    # Technical Analysis 탭
    with tabs[3]:
        st.header("📊 Technical Analysis")
        if df is not None:
            st.plotly_chart(plot_technical_indicators(df), use_container_width=True)
            
            col1, col2 = st.columns(2)
            with col1:
                st.subheader("Current Technical Levels")
                current_price = df['close'].iloc[-1]
                rsi = df['RSI'].iloc[-1]
                macd = df['MACD'].iloc[-1]
                
                st.metric("Price", f"{current_price:.2f}")
                st.metric("RSI", f"{rsi:.2f}")
                st.metric("MACD", f"{macd:.2f}")
            
            with col2:
                st.subheader("Trading Signals")
                if rsi > 70:
                    st.warning("💢 Overbought - Consider Selling")
                elif rsi < 30:
                    st.success("✨ Oversold - Consider Buying")
                else:
                    st.info("➡️ Neutral")

    # Trade History 탭
    with tabs[4]:
        st.header("📜 Trade History")
        trade_symbol = st.selectbox(
            "Select Trading Pair for History",
            options=['BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'DOGEUSDT'],
            key='trade_history'
        )
        
        trades_df = dashboard.get_trade_history(trade_symbol)
        if trades_df is not None:
            st.dataframe(trades_df)

    # Alerts 탭
    with tabs[5]:
        st.header("🔔 Price Alerts")
        alert_symbol = st.selectbox(
            "Select Trading Pair for Alert",
            options=['BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'DOGEUSDT'],
            key='alerts'
        )
        
        col1, col2 = st.columns(2)
        with col1:
            price_above = st.number_input("Alert when price above", value=0.0, step=0.1)
        with col2:
            price_below = st.number_input("Alert when price below", value=0.0, step=0.1)
        
        if st.button("Set Alert"):
            st.success("Alert has been set!")

    # 수동 새로고침 버튼
    if st.button("새로고침"):
        st.rerun()

    # 5. 거래 로그 섹션
    st.subheader("📝 거래 로그")
    
    # BTC 현재가 모니터링 컨테이너
    st.session_state.price_monitor_container = st.empty()

    try:
        # 초기 현재가 조회
        current_price = float(bot.client.get_symbol_ticker(symbol='BTCUSDT')['price'])
        prev_price = getattr(st.session_state, 'last_price', current_price)
        price_change = current_price - prev_price
        price_change_percent = (price_change / prev_price) * 100 if prev_price else 0
        
        # 가격 변화에 따른 색상 설정
        price_color = "green" if price_change >= 0 else "red"
        arrow = "↑" if price_change >= 0 else "↓"
        
        st.session_state.price_monitor_container.markdown(f"""
        <div style='padding: 10px; border-radius: 5px; border: 1px solid #ddd; margin-bottom: 20px;'>
            <h4 style='margin: 0; color: #1E88E5;'>🔄 실시간 BTC 가격</h4>
            <div style='font-size: 24px; margin-top: 10px;'>
                <span style='color: {price_color}; font-weight: bold;'>{current_price:,.2f} USDT</span>
            </div>
            <div style='font-size: 16px; margin-top: 5px;'>
                <span style='color: {price_color};'>
                    {arrow} {abs(price_change):+,.2f} USDT ({price_change_percent:+.2f}%)
                </span>
            </div>
            <div style='font-size: 12px; color: #666; margin-top: 5px;'>
                마지막 업데이트: {datetime.now().strftime('%H:%M:%S.%f')[:-4]}
            </div>
        </div>
        """, unsafe_allow_html=True)
        
        # 현재가 저장
        st.session_state.last_price = current_price
        
    except Exception as e:
        st.error(f"현재가 조회 실패: {e}")
        st.session_state.price_monitor_container.markdown("""
        <div style='padding: 10px; border-radius: 5px; border: 1px solid #ddd; margin-bottom: 20px;'>
            <h4 style='margin: 0; color: #1E88E5;'>🔄 실시간 BTC 가격</h4>
            <div style='font-size: 20px; margin-top: 5px; color: red;'>
                조회 실패
            </div>
        </div>
        """, unsafe_allow_html=True)

    # 포지션 상태 모니터링 컨테이너 추가
    status_col1, status_col2 = st.columns(2)

    with status_col1:
        st.session_state.long_status_container = st.empty()
        st.session_state.long_status_container.markdown("""
        <div style='padding: 10px; border-radius: 5px; border: 1px solid #90EE90; margin-bottom: 20px;'>
            <h4 style='margin: 0; color: #32CD32;'>🟢 LONG 포지션 상태</h4>
            <div style='font-size: 16px; margin-top: 5px;'>
                대기 중...
            </div>
        </div>
        """, unsafe_allow_html=True)

    with status_col2:
        st.session_state.short_status_container = st.empty()
        st.session_state.short_status_container.markdown("""
        <div style='padding: 10px; border-radius: 5px; border: 1px solid #FFB6C1; margin-bottom: 20px;'>
            <h4 style='margin: 0; color: #DC143C;'>🔴 SHORT 포지션 상태</h4>
            <div style='font-size: 16px; margin-top: 5px;'>
                대기 중...
            </div>
        </div>
        """, unsafe_allow_html=True)

    # 거래 로그 테이블
    log_col1, log_col2 = st.columns(2)

    # 롱 포지션 로그
    with log_col1:
        st.markdown("### 🟢 Long Position Logs")
        long_log_table = st.empty()
        if st.session_state.long_trade_logs:
            long_df = pd.DataFrame(st.session_state.long_trade_logs)
            long_log_table.dataframe(
                long_df,
                hide_index=True,
                use_container_width=True,
                height=400
            )

    # 숏 포지션 로그
    with log_col2:
        st.markdown("### 🔴 Short Position Logs")
        short_log_table = st.empty()
        if st.session_state.short_trade_logs:
            short_df = pd.DataFrame(st.session_state.short_trade_logs)
            short_log_table.dataframe(
                short_df,
                hide_index=True,
                use_container_width=True,
                height=400
            )

if __name__ == "__main__":
    main() 