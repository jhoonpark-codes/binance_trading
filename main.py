from dotenv import load_dotenv
import os

# .env 파일에서 환경 변수 로드
load_dotenv()

import threading
from binance.client import Client
from binance.streams import ThreadedWebsocketManager
import time
import queue
import pandas as pd
import numpy as np
from datetime import datetime

class HighFrequencyBot:
    def __init__(self):
        self.api_key = os.getenv('BINANCE_TESTNET_API_KEY')
        self.api_secret = os.getenv('BINANCE_TESTNET_SECRET_KEY')
        
        # Testnet 클라이언트 설정
        self.client = Client(
            self.api_key, 
            self.api_secret,
            testnet=True
        )
        
        # 서버 시간 동기화
        try:
            server_time = self.client.get_server_time()
            self.client.timestamp_offset = server_time['serverTime'] - int(time.time() * 1000)
        except Exception as e:
            print(f"시간 동기화 에러: {e}")
        
        # 기본 변수 초기화
        self.price_history = []
        self.max_history_size = 100
        self.max_trades_per_second = 5
        self.last_trade_time = time.time()
        self.trade_counter = 0
        
        self.symbol = 'BTCUSDT'
        self.trade_quantity = 0.001
        self.price_queue = queue.Queue()
        self.trades = []
        self.position = 0
        self.is_running = False
        self.thread = None
        self.twm = None
        self.current_price = None
        
    def _init_websocket(self):
        """WebSocket 초기화"""
        try:
            if hasattr(self, 'twm') and self.twm:
                try:
                    self.twm.stop()
                    time.sleep(0.5)
                except:
                    pass
                self.twm = None
            
            # 새로운 WebSocket 매니저 생성
            self.twm = ThreadedWebsocketManager(
                api_key=self.api_key,
                api_secret=self.api_secret,
                testnet=True
            )
            return True
        except Exception as e:
            print(f"WebSocket 초기화 에러: {e}")
            return False

    def start_websocket(self):
        """웹소켓 연결로 실시간 가격 데이터 수신"""
        try:
            # WebSocket 초기화
            if not self._init_websocket():
                return False
            
            def handle_socket_message(msg):
                if msg is None:
                    return
                try:
                    if msg.get('e') == 'trade':
                        price = float(msg['p'])
                        self.price_queue.put(price)
                        self.update_price_history(price)
                except Exception as e:
                    print(f"웹소켓 메시지 처리 에러: {e}")
            
            # 연결 시도 최대 3회
            for attempt in range(3):
                try:
                    # WebSocket 시작
                    self.twm.start()
                    time.sleep(1)
                    
                    # 스트림 구독
                    self.twm.start_trade_socket(
                        symbol=self.symbol,
                        callback=handle_socket_message
                    )
                    
                    # 연결 확인
                    time.sleep(2)
                    if self.twm._running:
                        print("WebSocket 연결 성공")
                        return True
                        
                except Exception as e:
                    print(f"연결 시도 {attempt + 1} 실패: {e}")
                    try:
                        self.twm.stop()
                    except:
                        pass
                    time.sleep(2)
                    
                    if attempt < 2:  # 마지막 시도가 아니면 재초기화
                        self._init_websocket()
            
            print("WebSocket 연결 실패")
            return False
            
        except Exception as e:
            print(f"WebSocket 시작 에러: {e}")
            return False
        
    def update_price_history(self, price):
        """가격 히스토리 업데이트"""
        self.price_history.append(price)
        if len(self.price_history) > self.max_history_size:
            self.price_history.pop(0)
            
    def calculate_signals(self):
        """거래 신호 생성"""
        if len(self.price_history) < self.max_history_size:
            return 0
            
        prices = np.array(self.price_history)
        sma_fast = np.mean(prices[-10:])
        sma_slow = np.mean(prices[-30:])
        
        # 변동성 계산
        volatility = np.std(prices[-20:])
        
        # 모멘텀 계산
        momentum = (prices[-1] - prices[-20]) / prices[-20]
        
        # 거래 신호 생성
        if sma_fast > sma_slow and momentum > 0 and self.position <= 0:
            return 1  # 매수 신호
        elif sma_fast < sma_slow and momentum < 0 and self.position >= 0:
            return -1  # 매도 신호
        return 0

    def get_account_balance(self):
        """계좌 잔고 확인"""
        try:
            # 서버 시간 재동기화
            server_time = self.client.get_server_time()
            self.client.timestamp_offset = server_time['serverTime'] - int(time.time() * 1000)
            
            # 전체 계좌 정보 조회
            account_info = self.client.get_account()
            
            # 특정 코인 잔고 확인
            btc_balance = float([asset for asset in account_info['balances'] if asset['asset'] == 'BTC'][0]['free'])
            usdt_balance = float([asset for asset in account_info['balances'] if asset['asset'] == 'USDT'][0]['free'])
            
            return {
                'BTC': btc_balance,
                'USDT': usdt_balance
            }
        except Exception as e:
            print(f"잔고 조회 에러: {e}")
            return None

    def check_sufficient_balance(self, side, quantity):
        """거래 전 잔고 충분한지 확인"""
        try:
            current_price = float(self.client.get_symbol_ticker(symbol=self.symbol)['price'])
            balances = self.get_account_balance()
            
            if side == Client.SIDE_BUY:
                required_usdt = quantity * current_price
                if balances['USDT'] < required_usdt:
                    print(f"USDT 잔고 부족. 필요: {required_usdt}, 보유: {balances['USDT']}")
                    return False
            else:  # SELL
                if balances['BTC'] < quantity:
                    print(f"BTC 잔고 부족. 필요: {quantity}, 보유: {balances['BTC']}")
                    return False
            return True
        except Exception as e:
            print(f"잔고 확인 에러: {e}")
            return False

    def execute_trade(self, signal):
        """거래 실행 (잔고 확인 추가)"""
        current_time = time.time()
        
        if current_time - self.last_trade_time < 1/self.max_trades_per_second:
            return
            
        try:
            if signal == 1 and self.position <= 0:
                # 매수 전 USDT 잔고 확인
                if not self.check_sufficient_balance(Client.SIDE_BUY, self.trade_quantity):
                    return
                    
                order = self.client.create_order(
                    symbol=self.symbol,
                    side=Client.SIDE_BUY,
                    type=Client.ORDER_TYPE_MARKET,
                    quantity=self.trade_quantity
                )
                self.position = 1
                
            elif signal == -1 and self.position >= 0:
                # 매도 전 BTC 잔고 확인
                if not self.check_sufficient_balance(Client.SIDE_SELL, self.trade_quantity):
                    return
                    
                order = self.client.create_order(
                    symbol=self.symbol,
                    side=Client.SIDE_SELL,
                    type=Client.ORDER_TYPE_MARKET,
                    quantity=self.trade_quantity
                )
                self.position = -1
                
            self.last_trade_time = current_time
            self.trade_counter += 1
            
            # 거래 후 잔고 업데이트 로깅
            current_balance = self.get_account_balance()
            print(f"현재 잔고 - BTC: {current_balance['BTC']}, USDT: {current_balance['USDT']}")
            
        except Exception as e:
            print(f"거래 실행 에러: {e}")
        
    def execute_trade(self, signal):
        """거래 실행"""
        current_time = time.time()
        
        # 거래 빈도 제한 확인
        if current_time - self.last_trade_time < 1/self.max_trades_per_second:
            return
            
        try:
            if signal == 1 and self.position <= 0:
                order = self.client.create_order(
                    symbol=self.symbol,
                    side=Client.SIDE_BUY,
                    type=Client.ORDER_TYPE_MARKET,
                    quantity=self.trade_quantity
                )
                self.position = 1
                self.trades.append({
                    'time': datetime.now(),
                    'type': 'BUY',
                    'price': float(order['fills'][0]['price']),
                    'quantity': self.trade_quantity
                })
                
            elif signal == -1 and self.position >= 0:
                order = self.client.create_order(
                    symbol=self.symbol,
                    side=Client.SIDE_SELL,
                    type=Client.ORDER_TYPE_MARKET,
                    quantity=self.trade_quantity
                )
                self.position = -1
                self.trades.append({
                    'time': datetime.now(),
                    'type': 'SELL',
                    'price': float(order['fills'][0]['price']),
                    'quantity': self.trade_quantity
                })
                
            self.last_trade_time = current_time
            self.trade_counter += 1
            
        except Exception as e:
            print(f"거래 실행 에러: {e}")
            
    def risk_management(self):
        """리스크 관리"""
        if len(self.trades) > 0:
            last_trade = self.trades[-1]
            current_price = self.price_history[-1]
            
            # 손실 제한 (2% 손실시 포지션 정리)
            if last_trade['type'] == 'BUY':
                loss_percent = (current_price - last_trade['price']) / last_trade['price']
                if loss_percent < -0.02:
                    self.execute_trade(-1)
                    
            elif last_trade['type'] == 'SELL':
                loss_percent = (last_trade['price'] - current_price) / last_trade['price']
                if loss_percent < -0.02:
                    self.execute_trade(1)
                    
    def start(self):
        """봇 시작"""
        if not self.is_running:
            try:
                self.is_running = True
                
                # 가격 모니터링 스레드 시작
                self.price_thread = threading.Thread(target=self.monitor_price_without_websocket)
                self.price_thread.daemon = True
                self.price_thread.start()
                
                # 메인 거래 스레드 시작
                self.thread = threading.Thread(target=self.run)
                self.thread.start()
                
                return "Bot started successfully"
                
            except Exception as e:
                self.is_running = False
                return f"Bot start error: {e}"
        return "Bot is already running"
    
    def stop(self):
        """봇 안전 종료"""
        if self.is_running:
            print("봇 종료 시작...")
            try:
                # 실행 플래그를 False로 설정
                self.is_running = False
                
                # 스레드 종료 대기
                if hasattr(self, 'price_thread') and self.price_thread.is_alive():
                    self.price_thread.join(timeout=2)
                
                if self.thread and self.thread.is_alive():
                    self.thread.join(timeout=2)
                
                # 열린 주문 취소
                try:
                    open_orders = self.client.get_open_orders(symbol='BTCUSDT')
                    for order in open_orders:
                        self.client.cancel_order(
                            symbol='BTCUSDT',
                            orderId=order['orderId']
                        )
                except:
                    pass
                
                print("봇 종료 완료")
                return "Bot stopped successfully"
                
            except Exception as e:
                print(f"Error during stop: {e}")
                return "Bot stopped with errors"
            finally:
                self.is_running = False
                self.thread = None
                self.price_thread = None
                self.price_queue = queue.Queue()
                self.trades = []
                self.current_price = None
                self.price_history = []
    
        return "Bot was not running"
        
    def _force_thread_stop(self):
        """스레드 강제 종료"""
        import ctypes
        
        if self.thread and self.thread.ident:
            try:
                thread_id = self.thread.ident
                res = ctypes.pythonapi.PyThreadState_SetAsyncExc(
                    ctypes.c_long(thread_id),
                    ctypes.py_object(SystemExit)
                )
                if res > 1:
                    ctypes.pythonapi.PyThreadState_SetAsyncExc(
                        ctypes.c_long(thread_id),
                        None
                    )
            except:
                pass
        
    def run(self):
        """메인 실행 루프"""
        self.start_websocket()
        
        while self.is_running:  # is_running 플래그 인
            try:
                # 스캘핑 전략 실행
                result = self.execute_scalping_strategy(
                    use_percentage=10,  # USDT 잔고의 10% 사용
                    profit_target=0.01,  # 0.01% 수익 목표
                    max_trades=5  # 초당 최대 5회 거래
                )
                
                if result['success']:
                    print("거래 성공:", result['message'])
                    if result.get('trades'):
                        self.trades.extend(result['trades'])
                else:
                    print("거래 실패:", result['message'])
                
                time.sleep(0.2)  # 0.2초 대기 (초당 5회 제한)
                
            except Exception as e:
                print(f"실행 에러: {e}")
                continue

    def execute_high_frequency_trade(self, symbol, quantity, interval=0.1, 
                                   stop_loss_percent=1.0, take_profit_percent=1.0,
                                   max_trades=10, trades_per_second=5):
        """초당 최대 5회 거래로 제한된 고빈도 거래"""
        try:
            min_interval = 1 / trades_per_second  # 최소 거래 간격
            if interval < min_interval:
                interval = min_interval
                print(f"거래 간격이 너무 짧아 {min_interval}초로 조정되었습니다.")
            
            trades = []
            total_profit = 0
            trade_count = 0
            
            while trade_count < max_trades:
                # 거래 로직 실행
                result = self.execute_single_trade(
                    symbol, quantity, stop_loss_percent, take_profit_percent
                )
                
                if result:
                    trades.append(result)
                    total_profit += result['profit']
                    trade_count += 1
                
                time.sleep(interval)
            
            return {
                'trades': trades,
                'total_profit': total_profit,
                'trade_count': trade_count
            }
                
        except Exception as e:
            print(f"거래 실행 에러: {e}")
            return None

    # 테스트넷에서 단일 거래 테스트
    def test_single_trade(self):
        """테스트넷에서 단일 거래 테스트"""
        try:
            # 버 시간 동기화
            server_time = self.client.get_server_time()
            self.client.timestamp_offset = server_time['serverTime'] - int(time.time() * 1000)
            
            # 현재 잔고 확인
            balance = self.get_account_balance()
            print(f"거래  잔고: {balance}")
            
            try:
                # 시장가 매수
                buy_order = self.client.create_test_order(  # test_order 변경
                    symbol='BTCUSDT',
                    side=Client.SIDE_BUY,
                    type=Client.ORDER_TYPE_MARKET,
                    quantity=0.001
                )
                print(f"매수 주문 테스트 성공")
                
                # 시장가 매도
                sell_order = self.client.create_test_order(  # test_order로 변경
                    symbol='BTCUSDT',
                    side=Client.SIDE_SELL,
                    type=Client.ORDER_TYPE_MARKET,
                    quantity=0.001
                )
                print(f"매도 주문 테스트 성공")
                
                # 거래 후 잔고 확인
                balance = self.get_account_balance()
                print(f"거래 후 잔고: {balance}")
                
                return {
                    'success': True,
                    'balance': balance,
                    'message': "테스트 거래 성공"
                }
                
            except Exception as e:
                print(f"주문 테스트 에러: {e}")
                return {
                    'success': False,
                    'message': f"주문 테스트 에러: {e}"
                }
                
        except Exception as e:
            print(f"테스트 거래 에러: {e}")
            return {
                'success': False,
                'message': f"테스트 거래 에러: {e}"
            }

    def test_trading_strategy(self, symbol='BTCUSDT', quantity=0.001, test_duration=3600):
        """
        거래 전략 테스트 (1시간 동안)
        """
        try:
            start_time = time.time()
            trades = []
            
            while time.time() - start_time < test_duration:
                # 잔고 확인
                balance = self.check_and_refill_testnet_balance()
                if not balance:
                    break
                    
                # 시장 데이터 수집
                current_price = float(self.client.get_symbol_ticker(symbol=symbol)['price'])
                
                # 여기에 하는 거래 로직 추가
                # 예: 단 매수-매도 테스트
                try:
                    # 매수
                    buy_order = self.client.create_order(
                        symbol=symbol,
                        side=Client.SIDE_BUY,
                        type=Client.ORDER_TYPE_MARKET,
                        quantity=quantity
                    )
                    
                    time.sleep(10)  # 10초 대기
                    
                    # 매도
                    sell_order = self.client.create_order(
                        symbol=symbol,
                        side=Client.SIDE_SELL,
                        type=Client.ORDER_TYPE_MARKET,
                        quantity=quantity
                    )
                    
                    trades.append({
                        'time': datetime.now(),
                        'buy_price': float(buy_order['fills'][0]['price']),
                        'sell_price': float(sell_order['fills'][0]['price']),
                        'quantity': quantity
                    })
                    
                    time.sleep(5)  # 5초 대기
                    
                except Exception as e:
                    print(f"거래 실행 에러: {e}")
                    continue
            
            return trades
            
        except Exception as e:
            print(f"전략 테스트 에러: {e}")
            return None

    def monitor_price_without_websocket(self):
        """REST API를 사용한 가격 모니터링"""
        try:
            while self.is_running:
                try:
                    ticker = self.client.get_symbol_ticker(symbol='BTCUSDT')
                    price = float(ticker['price'])
                    self.current_price = price
                    self.price_queue.put(price)
                    self.update_price_history(price)
                    time.sleep(0.2)  # 200ms 간격으로 가격 조회 (초당 5회)
                except Exception as e:
                    print(f"가격 조회 에러: {e}")
                    time.sleep(1)
        except Exception as e:
            print(f"모니터링 에러: {e}")

    def buy_bnb_with_usdt(self, usdt_amount):
        """USDT로 BNB 구매"""
        try:
            # BNB 현재가 확인
            bnb_price = float(self.client.get_symbol_ticker(symbol='BNBUSDT')['price'])
            
            # 구매할 BNB 수량 계산 (소수점 2자리까지)
            bnb_quantity = round(usdt_amount / bnb_price, 2)
            
            # BNB 매수 주문
            order = self.client.create_order(
                symbol='BNBUSDT',
                side=Client.SIDE_BUY,
                type=Client.ORDER_TYPE_MARKET,
                quantity=bnb_quantity
            )
            
            print(f"BNB 구매 완료: {bnb_quantity} BNB (약 {usdt_amount} USDT)")
            return True
            
        except Exception as e:
            print(f"BNB 구매 실패: {e}")
            return False

    def ensure_bnb_balance(self):
        """충분한 BNB 잔고 확보"""
        try:
            balance = self.get_account_balance()
            if not balance:
                return False
            
            bnb_balance = balance.get('BNB', 0)
            if bnb_balance < 0.01:  # BNB 잔고가 부족한 경우
                usdt_balance = balance.get('USDT', 0)
                if usdt_balance > 0:
                    # USDT 잔고의 20%로 BNB 구매
                    usdt_amount = usdt_balance * 0.2
                    return self.buy_bnb_with_usdt(usdt_amount)
                else:
                    print("USDT 잔고 부족으로 BNB를 구매할 수 습니다")
                    return False
            return True
            
        except Exception as e:
            print(f"BNB 잔고 확인 실패: {e}")
            return False

    def execute_scalping_strategy(self, use_percentage=10, profit_target=0.01, max_trades=None):
        """초고빈도 스캘핑 전략"""
        try:
            if not hasattr(self, 'current_price'):
                self.monitor_btc_price()
                time.sleep(0.1)
            
            trades = []
            trade_count = 0
            fee_rate = 0.00075
            min_interval = 0.1
            
            while (max_trades is None or trade_count < max_trades) and self.is_running:
                try:
                    balance = self.get_account_balance()
                    if not balance:
                        continue
                    
                    # BNB 잔고 확인 및 필요시 구매
                    if not self.ensure_bnb_balance():
                        print("BNB 확보 실패. 1분 후 재시도...")
                        time.sleep(60)
                        continue
                    
                    usdt_balance = balance['USDT']
                    trade_amount_usdt = (usdt_balance * use_percentage) / 100
                    trade_amount_usdt = trade_amount_usdt * (1 - fee_rate)
                    
                    # 현재 가격으로 수량 계산 및 형식 지정
                    current_price = float(self.client.get_symbol_ticker(symbol='BTCUSDT')['price'])
                    quantity = trade_amount_usdt / current_price
                    
                    # Binance API 요구사항에 맞게 수량 형식화
                    quantity = "{:.5f}".format(float(quantity))  # 문자열로 변환
                    
                    # 최소 거래 수량 확인
                    if float(quantity) < 0.00001:
                        print("거래 수량이 너무 작습니다")
                        continue
                    
                    # 매수 주문
                    buy_order = self.client.create_order(
                        symbol='BTCUSDT',
                        side=Client.SIDE_BUY,
                        type=Client.ORDER_TYPE_MARKET,
                        quantity=quantity,
                        newOrderRespType='FULL'
                    )
                    
                    buy_price = float(buy_order['fills'][0]['price'])
                    buy_fee_bnb = float(buy_order['fills'][0]['commission'])
                    
                    # 목표가 계산 및 시 매도 주문
                    target_price = buy_price * (1 + profit_target/100)
                    
                    # 매도 주문 즉시 실행
                    sell_order = self.client.create_order(
                        symbol='BTCUSDT',
                        side=Client.SIDE_SELL,
                        type=Client.ORDER_TYPE_MARKET,
                        quantity=quantity,
                        newOrderRespType='FULL'
                    )
                    
                    sell_price = float(sell_order['fills'][0]['price'])
                    sell_fee_bnb = float(sell_order['fills'][0]['commission'])
                    
                    # 수익 계산
                    real_profit = ((sell_price * (1 - fee_rate)) - (buy_price * (1 + fee_rate))) * quantity
                    profit_percent = (sell_price - buy_price) / buy_price * 100
                    
                    trades.append({
                        'time': datetime.now(),
                        'buy_price': buy_price,
                        'sell_price': sell_price,
                        'quantity': quantity,
                        'buy_fee_bnb': buy_fee_bnb,
                        'sell_fee_bnb': sell_fee_bnb,
                        'profit': real_profit,
                        'profit_percent': profit_percent
                    })
                    
                    print(f"거래 #{trade_count+1}")
                    print(f"매수: {buy_price:.2f} -> 매도: {sell_price:.2f}")
                    print(f"수익: {real_profit:.8f} USDT ({profit_percent:.2f}%)")
                    
                    trade_count += 1
                    time.sleep(min_interval)  # API 제한 준수를 위한 최소 대기
                    
                except Exception as e:
                    print(f"거래 실행 중 에러: {e}")
                    time.sleep(0.5)  # 에러 발생 시 잠시 대기
                    continue
            
            return {
                'success': True,
                'trades': trades,
                'total_trades': trade_count,
                'total_profit': sum(t['profit'] for t in trades),
                'message': "전략 실행 완료"
            }
            
        except Exception as e:
            return {
                'success': False,
                'message': f"전략 실행 에러: {e}"
            }

    def __del__(self):
        """소멸자에서 즉시 종료"""
        if hasattr(self, 'is_running') and self.is_running:
            self.stop()

if __name__ == "__main__":
    bot = HighFrequencyBot()
    bot.run()