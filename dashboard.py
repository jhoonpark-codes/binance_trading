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
import atexit
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
            atexit.register(self.cleanup_resources)

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

    def create_bot(self):
        """봇 생성 및 관리"""
        bot = HighFrequencyBot()
        st.session_state.active_bots.add(bot)
        return bot

    def stop_bot(self, bot):
        """봇 종료 및 정리"""
        if bot in st.session_state.active_bots:
            try:
                bot.stop()
                st.session_state.active_bots.remove(bot)
                return True
            except Exception as e:
                print(f"Error stopping bot: {e}")
        return False

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
            st.error(f"잔고 조회 ��러: {e}")
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
                return "잔고 조회 실패"
            
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

def plot_candlestick(df):
    """캔들스 차트 생성"""
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
    app = StreamlitApp()
    
    # 시작 시 세션 상태 초기화
    if 'bot' not in st.session_state:
        st.session_state.bot = None

    st.set_page_config(page_title="Binance Dashboard", layout="wide")
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
            st.warning("⚠️ 고빈도 거래는 매�� 위험할 수 있습니다. 신중하게 진행하세요!")
            
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

    # Test Trade 탭 (새로 추가)
    with tabs[6]:  # 7번째 탭
        st.header("🧪 Test Trade")
        
        # Bot Control 섹션
        st.header("🤖 Bot Control")
        col1, col2 = st.columns(2)

        # 봇 인스턴스를 세션 상태로 관리
        if 'bot' not in st.session_state:
            st.session_state.bot = None

        with col1:
            if st.button("Start Bot"):
                if st.session_state.bot is None:
                    try:
                        st.session_state.bot = app.create_bot()
                        result = st.session_state.bot.start()
                        st.success(result)
                    except Exception as e:
                        st.error(f"Bot start error: {e}")
                        if st.session_state.bot:
                            app.stop_bot(st.session_state.bot)
                        st.session_state.bot = None
                else:
                    st.warning("Bot is already running")

        with col2:
            if st.button("Stop Bot"):
                if st.session_state.bot is not None:
                    try:
                        app.stop_bot(st.session_state.bot)
                        st.session_state.bot = None
                        st.success("Bot stopped successfully")
                    except Exception as e:
                        st.error(f"Bot stop error: {e}")
                else:
                    st.warning("Bot is not running")

        # 봇 상태 및 거래 정보 표시
        if st.session_state.bot is not None:
            st.info("Bot Status: Running")
            
            if hasattr(st.session_state.bot, 'trades') and st.session_state.bot.trades:
                st.subheader("Recent Trades")
                trades_df = pd.DataFrame(st.session_state.bot.trades[-5:])
                st.dataframe(trades_df)
                
                total_profit = sum(trade.get('profit', 0) for trade in st.session_state.bot.trades)
                st.metric("Total Profit", f"{total_profit:.4f} USDT")
            else:
                st.info("Bot Status: Stopped")

        # Test Trade 섹션
        st.header("🔬 Single Test Trade")
        col1, col2 = st.columns(2)

        with col1:
            test_symbol = st.selectbox(
                "Test Trading Pair",
                options=['BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'DOGEUSDT'],
                key='test_symbol'
            )
            
            test_quantity = st.number_input(
                "Test Quantity",
                min_value=0.001,
                value=0.001,
                step=0.001,
                format="%.3f"
            )

        if st.button("Execute Test Trade"):
            st.warning("⚠️ 테스트넷에서 거래를 실행합니다.")
            
            with st.spinner('테스트 거래 실행 중...'):
                try:
                    bot = HighFrequencyBot()
                    result = bot.test_single_trade()
                    
                    if result['success']:
                        st.success(result['message'])
                        if result.get('balance'):
                            st.metric("BTC Balance", f"{result['balance'].get('BTC', 0):.8f}")
                            st.metric("USDT Balance", f"{result['balance'].get('USDT', 0):.2f}")
                    else:
                        st.error(result['message'])
                        
                except Exception as e:
                    st.error(f"테스트 거래 에러: {e}")

        # Scalping Strategy 섹션
        st.header("📊 Scalping Strategy Test")
        col1, col2 = st.columns(2)

        with col1:
            use_percentage = st.slider(
                "USDT 사용 비율 (%)",
                min_value=1,
                max_value=100,
                value=10,
                step=1
            )
            
            profit_target = st.number_input(
                "목표 수익률 (%)",
                min_value=0.01,
                max_value=1.0,
                value=0.01,
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

        if st.button("Start Scalping Strategy"):
            st.warning("⚠️ 스캘핑 전략을 시작합니다.")
            
            # 실시간 모니터링을 위한 컨테이너 생성
            monitoring_container = st.container()
            
            with st.spinner('전략 실행 중...'):
                try:
                    bot = HighFrequencyBot()
                    
                    # 실시간 ��보 표시를 위한 컬럼 생성
                    col1, col2, col3 = monitoring_container.columns(3)
                    
                    # 초기 잔고 표시
                    balance_metric = col1.empty()
                    btc_amount_metric = col2.empty()
                    btc_price_metric = col3.empty()
                    
                    # 거래 기록을 위한 테이블
                    trade_table = monitoring_container.empty()
                    
                    # 수익률 차트
                    profit_chart = monitoring_container.empty()
                    
                    while True:
                        # 잔고 업데이트
                        balance = bot.get_account_balance()
                        if balance:
                            balance_metric.metric(
                                "USDT 잔고",
                                f"{balance['USDT']:.2f} USDT"
                            )
                            btc_amount_metric.metric(
                                "BTC 보유량",
                                f"{balance['BTC']:.8f} BTC"
                            )
                        
                        # 현재 BTC 가격 업데이트
                        current_price = float(bot.client.get_symbol_ticker(symbol='BTCUSDT')['price'])
                        btc_price_metric.metric(
                            "BTC 현재가",
                            f"{current_price:.2f} USDT"
                        )
                        
                        # 거래 내역 업데이트
                        if hasattr(bot, 'trades') and bot.trades:
                            df_trades = pd.DataFrame(bot.trades[-5:])  # 최근 5개 거래만 표시
                            trade_table.dataframe(df_trades.style.format({
                                'buy_price': '{:.2f}',
                                'sell_price': '{:.2f}',
                                'quantity': '{:.8f}',
                                'profit': '{:.8f}',
                                'profit_percent': '{:.2f}%'
                            }))
                            
                            # 수익률 차트 업데이트
                            if len(bot.trades) > 0:
                                profit_data = pd.DataFrame(bot.trades)
                                fig = px.line(profit_data, 
                                            x=profit_data.index, 
                                            y='profit',
                                            title='누적 수익 추이')
                                profit_chart.plotly_chart(fig, use_container_width=True)
                        
                        # 0.2초마다 업데이트 (초당 5회)
                        time.sleep(0.2)
                        
                except Exception as e:
                    st.error(f"전략 실행 에러: {e}")

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