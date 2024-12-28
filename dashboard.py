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
                
                # 가��� 모니터링 및 매도 조건 확인
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

def main():
    # session_state 초기화
    if 'trade_logs' not in st.session_state:
        st.session_state.trade_logs = []
    if 'scalping_active' not in st.session_state:
        st.session_state.scalping_active = False
    if 'buy_price' not in st.session_state:
        st.session_state.buy_price = 0.0
    if 'target_price' not in st.session_state:
        st.session_state.target_price = 0.0

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
                min-width: 400px;
            }
            .stMetric-value {
                white-space: nowrap;
                overflow: visible;
                font-size: 1.1rem !important;
                padding: 0 20px;
            }
            .stMetric-label {
                font-size: 1rem !important;
                padding: 0 20px;
            }
            div[data-testid="metric-container"] {
                width: fit-content;
                min-width: 400px;
                margin: 0 25px;
            }
            div[data-testid="column"] {
                padding: 0 20px;
            }
            div[data-testid="stHorizontalBlock"] {
                gap: 2rem;
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
    
    # 시작 세션 상 초기화
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
        
        # Scalping Strategy 섹션
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

        # Start/Stop 버튼 부분
        col3, col4 = st.columns(2)

        with col3:
            if st.button("Start Scalping Strategy"):
                # session_state 초기화
                if 'scalping_active' not in st.session_state:
                    st.session_state.scalping_active = False
                if 'buy_price' not in st.session_state:
                    st.session_state.buy_price = 0.0
                if 'target_price' not in st.session_state:
                    st.session_state.target_price = 0.0
                    
                st.warning("⚠️ 스캘핑 전략을 시작합니다.")
                try:
                    bot = HighFrequencyBot()
                    st.session_state.scalping_active = True
                    st.session_state.scalping_bot = bot
                    
                    # 현재 잔고 확인
                    initial_balance = bot.get_account_balance()
                    if not initial_balance:
                        st.error("잔고 조회 실패")
                        return
                    
                    # USDT 사용 금액 계�� (보유 USDT의 use_percentage%)
                    trade_amount_usdt = (initial_balance['USDT'] * use_percentage) / 100
                    
                    # 현재 BTC 가격 확인
                    current_price = float(bot.client.get_symbol_ticker(symbol='BTCUSDT')['price'])
                    
                    # 매수할 BTC 수량 계산 (수수료 0.1% 고려)
                    quantity = (trade_amount_usdt / current_price) * 0.999
                    quantity = "{:.5f}".format(float(quantity))  # 소수점 5자리까지
                    
                    st.info(f"매수 예정: {quantity} BTC (약 {trade_amount_usdt:.2f} USDT)")
                    
                    # UI 컴포넌트 설정 - 수직 레이아웃으로 변경
                    st.subheader("💰 현재 잔고 및 BTC 가격")

                    # 스타일 수정
                    st.markdown("""
                        <style>
                            .element-container {
                                width: 100%;
                                max-width: 600px;  # 너비 축소
                            }
                            .stMetric {
                                width: 100%;
                                min-width: 500px;  # 너비 조정
                                margin-bottom: 10px;  # 메트릭 간 간격
                            }
                            .stMetric-value {
                                white-space: nowrap;
                                overflow: visible;
                                font-size: 1.2rem !important;
                                padding: 0 20px;
                            }
                            .stMetric-label {
                                font-size: 1.1rem !important;
                                padding: 0 20px;
                            }
                            div[data-testid="metric-container"] {
                                width: 100%;
                                margin: 10px 0;  # 상하 마진 추가
                            }
                        </style>
                    """, unsafe_allow_html=True)

                    # 메트릭 컴포넌트들을 수직으로 배치
                    metrics_container = st.container()

                    # USDT 잔고
                    usdt_balance = metrics_container.empty()
                    usdt_balance.metric(
                        "USDT 잔고",
                        f"{initial_balance['USDT']:,.3f}",
                        label_visibility="visible"
                    )

                    # BTC 잔고
                    btc_balance = metrics_container.empty()
                    btc_balance.metric(
                        "BTC 잔고",
                        f"{initial_balance['BTC']:.8f}",
                        label_visibility="visible"
                    )

                    # BNB 잔고
                    bnb_balance_display = metrics_container.empty()
                    bnb_balance = float([asset for asset in bot.client.get_account()['balances'] if asset['asset'] == 'BNB'][0]['free'])
                    bnb_balance_display.metric(
                        "BNB 잔고",
                        f"{bnb_balance:.4f}",
                        label_visibility="visible"
                    )

                    # BTC 현재가
                    btc_price = metrics_container.empty()
                    current_price = float(bot.client.get_symbol_ticker(symbol='BTCUSDT')['price'])
                    btc_price.metric(
                        "BTC 현재가",
                        f"{current_price:,.3f}",
                        label_visibility="visible"
                    )

                    # 매수가
                    buy_price_display = metrics_container.empty()
                    buy_price_display.metric(
                        "매수가",
                        "대기 중",
                        label_visibility="visible"
                    )

                    # 목표 매도가
                    target_price_display = metrics_container.empty()
                    target_price_display.metric(
                        "목표 매도가",
                        "대기 중",
                        label_visibility="visible"
                    )
                    sell_status_display = metrics_container.empty()
                    
                    # 거래 로그 테이블 컨테이너 생성
                    st.subheader("📝 거래 로그")
                    log_container = st.container()

                    # 거래 로그를 저장할 리스트를 session_state 초기화
                    if 'trade_logs' not in st.session_state:
                        st.session_state.trade_logs = []

                    # 거래 로그 테이블 표시
                    log_table = st.empty()

                    # 매수 주문 실행 시 로그 추가
                    buy_order = bot.client.create_order(
                        symbol='BTCUSDT',
                        side=Client.SIDE_BUY,
                        type=Client.ORDER_TYPE_MARKET,
                        quantity=quantity
                    )

                    # 매수 로그 추가
                    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    buy_price = float(buy_order['fills'][0]['price'])
                    st.session_state.trade_logs.append({
                        '시간': timestamp,
                        '유형': '매수',
                        '가격': f"{buy_price:,.3f}",
                        '수량': quantity,
                        'USDT 금액': f"{float(quantity) * buy_price:,.3f}",
                        '상태': '체결완료'
                    })

                    # 매수가 저장
                    st.session_state.buy_price = float(buy_order['fills'][0]['price'])
                    buy_price_display.metric(
                        "매수가",
                        f"{st.session_state.buy_price:,.3f} USDT"
                    )
                    
                    # 목표가 계산 및 저장
                    if profit_type == "절대값(USDT)":
                        st.session_state.target_price = st.session_state.buy_price + profit_target
                    else:
                        st.session_state.target_price = st.session_state.buy_price * (1 + profit_target/100)
                    
                    # 목표가 표시
                    target_price_display.metric(
                        "목표 매도가",
                        f"{st.session_state.target_price:,.3f} USDT",
                        f"+{profit_target} {'USDT' if profit_type == '절대값(USDT)' else '%'}"
                    )
                    
                    # 매도 주문 설정
                    sell_order = bot.client.create_order(
                        symbol='BTCUSDT',
                        side=Client.SIDE_SELL,
                        type=Client.ORDER_TYPE_LIMIT,
                        timeInForce='GTC',
                        quantity=quantity,
                        price="{:.2f}".format(st.session_state.target_price)
                    )
                    
                    # 매도 주문 ID 저장
                    st.session_state.sell_order_id = sell_order['orderId']
                    sell_status_display.info("📋 매도 주문 등록됨")
                    
                    # 실시간 업데이트 루프 수정
                    while st.session_state.scalping_active:
                        try:
                            current_balance = bot.get_account_balance()
                            if current_balance:
                                # 잔고 업데이트
                                usdt_balance.metric(
                                    "USDT 잔고",
                                    f"{current_balance['USDT']:.2f} USDT",
                                    f"{current_balance['USDT'] - initial_balance['USDT']:+.2f}"
                                )
                                
                                btc_balance.metric(
                                    "BTC 잔고",
                                    f"{current_balance['BTC']:.8f} BTC",
                                    f"{current_balance['BTC'] - initial_balance['BTC']:+.8f}"
                                )
                                
                                # BNB 잔고 업데이트
                                current_bnb = float([asset for asset in bot.client.get_account()['balances'] if asset['asset'] == 'BNB'][0]['free'])
                                bnb_balance_display.metric(
                                    "BNB 잔고",
                                    f"{current_bnb:.4f} BNB",
                                    f"{current_bnb - bnb_balance:+.4f}"
                                )
                                
                                # 현재가 업데이트
                                current_price = float(bot.client.get_symbol_ticker(symbol='BTCUSDT')['price'])
                                btc_price.metric(
                                    "BTC 현재가",
                                    f"{current_price:,.2f} USDT",
                                    f"{current_price - st.session_state.buy_price:+.2f}"
                                )
                                
                                # 매도 주문 상태 확인
                                if hasattr(st.session_state, 'sell_order_id'):
                                    order_status = bot.check_order_status(st.session_state.sell_order_id)
                                    if order_status == 'FILLED':
                                        sell_status_display.success("✅ 매도 완료!")
                                        
                                        # 1. BTC를 USDT로 변환 (0.5 BTC 지)
                                        try:
                                            new_balance = bot.get_account_balance()
                                            if new_balance['BTC'] > 0.5:
                                                excess_btc = new_balance['BTC'] - 0.5
                                                if excess_btc > 0:
                                                    convert_order = bot.client.create_order(
                                                        symbol='BTCUSDT',
                                                        side=Client.SIDE_SELL,
                                                        type=Client.ORDER_TYPE_MARKET,
                                                        quantity="{:.5f}".format(excess_btc)
                                                    )
                                                    st.info(f"BTC -> USDT 변환 완료: {excess_btc:.8f} BTC")
                                                    time.sleep(1)  # 잔고 업데이트 대기
                                        except Exception as e:
                                            st.error(f"BTC 변환 실패: {e}")
                                        
                                        # 2. 새로운 매수-매도 주문 설정
                                        try:
                                            # 최신 잔고 확인
                                            updated_balance = bot.get_account_balance()
                                            trade_amount_usdt = (updated_balance['USDT'] * use_percentage) / 100
                                            
                                            # 현재 BTC 가격 확인
                                            current_price = float(bot.client.get_symbol_ticker(symbol='BTCUSDT')['price'])
                                            
                                            # 새로운 매수 수량 계산
                                            new_quantity = (trade_amount_usdt / current_price) * 0.999
                                            new_quantity = "{:.5f}".format(float(new_quantity))
                                            
                                            # 매수 주문
                                            buy_order = bot.client.create_order(
                                                symbol='BTCUSDT',
                                                side=Client.SIDE_BUY,
                                                type=Client.ORDER_TYPE_MARKET,
                                                quantity=new_quantity
                                            )
                                            
                                            # 매수가 업데이트
                                            st.session_state.buy_price = float(buy_order['fills'][0]['price'])
                                            buy_price_display.metric(
                                                "매수가",
                                                f"{st.session_state.buy_price:,.2f} USDT"
                                            )
                                            
                                            # 새로운 목표가 계산
                                            if profit_type == "절대값(USDT)":
                                                st.session_state.target_price = st.session_state.buy_price + profit_target
                                            else:
                                                st.session_state.target_price = st.session_state.buy_price * (1 + profit_target/100)
                                            
                                            # 목표가 표시 업데이트
                                            target_price_display.metric(
                                                "목표 매도가",
                                                f"{st.session_state.target_price:,.2f} USDT",
                                                f"+{profit_target} {'USDT' if profit_type == '절대값(USDT)' else '%'}"
                                            )
                                            
                                            # 새로운 매도 주문
                                            sell_order = bot.client.create_order(
                                                symbol='BTCUSDT',
                                                side=Client.SIDE_SELL,
                                                type=Client.ORDER_TYPE_LIMIT,
                                                timeInForce='GTC',
                                                quantity=new_quantity,
                                                price="{:.2f}".format(st.session_state.target_price)
                                            )
                                            
                                            # 새로운 매도 주문 ID 저장
                                            st.session_state.sell_order_id = sell_order['orderId']
                                            sell_status_display.info("📋 새로운 매도 주문 등록됨")
                                            
                                        except Exception as e:
                                            st.error(f"새로운 주문 설정 실패: {e}")
                                            
                                    elif order_status == 'NEW':
                                        sell_status_display.info("📋 매도 주문 대기 중...")
                                    else:
                                        sell_status_display.warning(f"⚠️ 주문 상태: {order_status}")
                                
                                # 거래 로그 테이블 업데이트
                                if st.session_state.trade_logs:
                                    log_df = pd.DataFrame(st.session_state.trade_logs)
                                    log_table.dataframe(
                                        log_df,
                                        hide_index=True,
                                        use_container_width=True,
                                        height=400
                                    )
                                
                                time.sleep(0.1)
                                
                        except Exception as e:
                            print(f"실시간 업데이트 중 에러: {e}")
                            time.sleep(0.2)
                            
                except Exception as e:
                    st.error(f"전략 실행 에러: {e}")
                    st.session_state.scalping_active = False

        with col4:
            if st.button("Stop Scalping Strategy"):
                if hasattr(st.session_state, 'scalping_bot') and st.session_state.get('scalping_active', False):
                    try:
                        # 먼저 실행 상태 변경
                        st.session_state.scalping_active = False
                        
                        # WebSocket 연결 종료
                        if hasattr(st.session_state.scalping_bot, 'twm') and st.session_state.scalping_bot.twm:
                            try:
                                st.session_state.scalping_bot.twm._exit = True  # 종료 플래그 설정
                                st.session_state.scalping_bot.twm.close()  # 연결 종료
                                time.sleep(1)  # 종료 대기
                            except:
                                pass
                            finally:
                                st.session_state.scalping_bot.twm = None
                        
                        # 활성 주문 취소
                        try:
                            open_orders = st.session_state.scalping_bot.client.get_open_orders(symbol='BTCUSDT')
                            for order in open_orders:
                                st.session_state.scalping_bot.client.cancel_order(
                                    symbol='BTCUSDT',
                                    orderId=order['orderId']
                                )
                        except:
                            pass
                        
                        # 봇 상태 변경 및 정리
                        st.session_state.scalping_bot.is_running = False
                        st.session_state.scalping_bot = None
                        
                        st.success("스캘핑 전략이 안전하게 중지되었습니다.")
                        
                    except Exception as e:
                        st.error(f"전략 중지 중 에러 발생: {e}")
                        # 에러 발생해도 상태는 초기화
                        st.session_state.scalping_active = False
                        st.session_state.scalping_bot = None
                else:
                    st.warning("실행 중인 스캘핑 전략이 없습니다.")

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
                # 간단한 트레이딩 신
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

if __name__ == "__main__":
    main() 