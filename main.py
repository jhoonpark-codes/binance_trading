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
        # Testnet API 키 사용
        self.api_key = os.getenv('BINANCE_TESTNET_API_KEY')
        self.api_secret = os.getenv('BINANCE_TESTNET_SECRET_KEY')
        
        # Testnet 클라이언트 설정
        self.client = Client(
            self.api_key, 
            self.api_secret,
            testnet=True  # Testnet 사용 설정
        )
        
        # 간단한 연결 테스트
        try:
            self.client.ping()
            print("Binance Testnet 서버 연결 성공")
        except Exception as e:
            print(f"Binance Testnet 서버 연결 실패: {e}")
        
        # 서버 시간 동기화
        try:
            server_time = self.client.get_server_time()
            self.client.timestamp_offset = server_time['serverTime'] - int(time.time() * 1000)
            print("서버 시간 동기화 완료")
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
        
        # 캐시 추가
        self.price_cache = {'price': None, 'timestamp': 0}
        self.balance_cache = {'balance': None, 'timestamp': 0}
        self.cache_timeout = 1  # 1초 캐시 유효시간
        
        # WebSocket 관련 변수
        self.ws_data = {
            'price': None,
            'last_update': 0,
            'balances': {},
            'order_status': {}
        }
        self.ws_connected = False
        self.start_websocket()
        
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
            
            # WebSocket 매니저를 테스트넷으로 설정
            self.twm = ThreadedWebsocketManager(
                api_key=self.api_key,
                api_secret=self.api_secret,
                testnet=True  # Testnet 사용 설정
            )
            return True
        except Exception as e:
            print(f"WebSocket 초기화 에러: {e}")
            return False

    def start_websocket(self):
        """WebSocket 연결 시작"""
        def handle_socket_message(msg):
            try:
                if msg.get('e') == 'trade':
                    self.ws_data['price'] = float(msg['p'])
                    self.ws_data['last_update'] = time.time()
                elif msg.get('e') == 'outboundAccountPosition':
                    for balance in msg['B']:
                        self.ws_data['balances'][balance['a']] = float(balance['f'])
                elif msg.get('e') == 'executionReport':
                    self.ws_data['order_status'][msg['i']] = msg['X']
            except Exception as e:
                print(f"WebSocket 메시지 처리 에러: {e}")

        try:
            if not hasattr(self, 'twm') or self.twm is None:
                self.twm = ThreadedWebsocketManager(
                    api_key=self.api_key,
                    api_secret=self.api_secret,
                    testnet=True
                )
                
            # 이미 실행 중인 WebSocket이 있다면 중지
            if hasattr(self, 'twm') and self.twm._running:
                try:
                    self.twm.stop()
                    time.sleep(1)
                except:
                    pass

            # WebSocket 시작
            self.twm.start()
            time.sleep(1)

            # 스트림 구독
            self.twm.start_trade_socket(
                symbol=self.symbol,
                callback=handle_socket_message
            )
            self.twm.start_user_socket(
                callback=handle_socket_message
            )
            
            self.ws_connected = True
            print("WebSocket 연결 성공")
            
        except Exception as e:
            print(f"WebSocket 연결 에러: {e}")
            self.ws_connected = False

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
        """WebSocket에서 잔고 가져오기"""
        if self.ws_data['balances']:
            return {
                'BTC': self.ws_data['balances'].get('BTC', 0),
                'USDT': self.ws_data['balances'].get('USDT', 0),
                'BNB': self.ws_data['balances'].get('BNB', 0)
            }
        
        # WebSocket 잔고가 없는 경우에만 REST API 사용
        try:
            account_info = self.client.get_account()
            time.sleep(0.1)  # API 호출 제한 방지
            balances = {}
            for asset in account_info['balances']:
                if asset['asset'] in ['BTC', 'USDT', 'BNB']:
                    balances[asset['asset']] = float(asset['free'])
            return balances
        except Exception as e:
            print(f"잔고 조회 에러: {e}")
            return None

    def check_sufficient_balance(self, side, quantity):
        """거래 잔고 충분한지 확인"""
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
        """봇 종료"""
        try:
            # 먼저 실행 상태 변경
            self.is_running = False
            
            # WebSocket 연결 종료
            if hasattr(self, 'twm') and self.twm:
                try:
                    self.twm._exit = True  # 종료 플래그 설정
                    self.twm.close()  # 연결 종료
                    time.sleep(1)  # 종료 대기
                except:
                    pass
                finally:
                    self.twm = None
            
            # 활성 주문 취소
            try:
                open_orders = self.client.get_open_orders(symbol='BTCUSDT')
                for order in open_orders:
                    self.client.cancel_order(
                        symbol='BTCUSDT',
                        orderId=order['orderId']
                    )
            except:
                pass
            
            print("봇이 안전하게 종료되었습니다.")
            
        except Exception as e:
            print(f"봇 종료 중 에러: {e}")

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
                    profit_target=5,  # 5 USDT 이상 수익
                    stop_loss=30,  # 30 USDT 이하 손실
                    max_trades=5,  # 당 최대 5회 거래
                    wait_minutes=3,  # 3분 대기
                    profit_type="절대값(USDT)"  # 손익 설정 방식 선택
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
                
                # 여에 하는 거래 로직 추가
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
                    print("USDT 잔고 부족으로 BNB 매할 수 없습니다")
                    return False
            return True
            
        except Exception as e:
            print(f"BNB 잔고 확인 실패: {e}")
            return False

    def manage_usdt_balance(self, min_usdt=2000):
        """USDT 잔고 관리"""
        try:
            # 현재 잔고 확인
            balance = self.get_account_balance()
            if not balance:
                return False
            
            current_usdt = balance['USDT']
            current_btc = balance['BTC']
            
            # USDT가 최소 금액보다 적은 경우
            if current_usdt < min_usdt:
                print(f"USDT 잔고 부족: {current_usdt} USDT")
                
                # 실행 중인 스캘핑 전략 중지
                if hasattr(self, 'is_running') and self.is_running:
                    print("스캘핑 전략 일시 중지...")
                    temp_is_running = self.is_running
                    self.is_running = False
                    time.sleep(2)  # 진행 중인 거래 완료 대기
                else:
                    temp_is_running = False
                
                try:
                    # 현재 BTC 가격 확인
                    btc_price = float(self.client.get_symbol_ticker(symbol='BTCUSDT')['price'])
                    
                    # 1 BTC는 유지하 나머지 BTC만 사용
                    excess_btc = current_btc - 1.0
                    if excess_btc > 0:
                        # 필요한 USDT 계산 (수수료 고려)
                        needed_usdt = min_usdt - current_usdt
                        btc_to_sell = min(excess_btc, (needed_usdt / btc_price) * 1.01)  # 1% 마진 추가
                        
                        if btc_to_sell >= 0.00001:  # 최소 거래 수량 확인
                            try:
                                # BTC를 USDT로 변환
                                sell_order = self.client.create_order(
                                    symbol='BTCUSDT',
                                    side=Client.SIDE_SELL,
                                    type=Client.ORDER_TYPE_MARKET,
                                    quantity="{:.5f}".format(btc_to_sell)
                                )
                                print(f"BTC 매도 완료: {btc_to_sell} BTC")
                                
                                # 변환 후 잔고 확인
                                new_balance = self.get_account_balance()
                                if new_balance:
                                    print(f"변환 후 잔고 - USDT: {new_balance['USDT']}, BTC: {new_balance['BTC']}")
                                
                                # 스캘핑 전략 재시작
                                if temp_is_running:
                                    print("스캘핑 전략 재시작...")
                                    self.is_running = True
                                
                                return True
                                
                            except Exception as e:
                                print(f"BTC 매도 실패: {e}")
                                # 에러 발생 시에도 스캘핑 전략 재시작
                                if temp_is_running:
                                    self.is_running = True
                                return False
                    else:
                        print("추가 BTC 매도 불가: 1 BTC 유지 필요")
                        if temp_is_running:
                            self.is_running = True
                        return False
                except Exception as e:
                    print(f"BTC 매도 처리 중 에러: {e}")
                    # 에러 발생 시에도 스캘핑 전략 재시작
                    if temp_is_running:
                        self.is_running = True
                    return False
                
            return True
            
        except Exception as e:
            print(f"USDT 잔고 관리 에러: {e}")
            return False

    def convert_btc_to_usdt(self, btc_amount):
        """BTC를 USDT로 변환"""
        try:
            # BTC를 USDT로 변환는 주문
            sell_order = self.client.create_order(
                symbol='BTCUSDT',
                side=Client.SIDE_SELL,
                type=Client.ORDER_TYPE_MARKET,
                quantity="{:.5f}".format(btc_amount)
            )
            
            sell_price = float(sell_order['fills'][0]['price'])
            sold_usdt = sell_price * btc_amount
            
            print(f"BTC 매도 완료: {btc_amount} BTC -> {sold_usdt:.2f} USDT")
            return True
        except Exception as e:
            print(f"BTC 매도 실패: {e}")
            return False

    def ensure_minimum_usdt(self, min_usdt=2000):
        """최소 USDT 잔고 확보"""
        try:
            balance = self.get_account_balance()
            if not balance:
                return False
            
            current_usdt = balance['USDT']
            current_btc = balance['BTC']
            
            if current_usdt < min_usdt:
                print(f"USDT 잔고 부족: {current_usdt:.2f} USDT")
                
                # 현재 BTC 가격 확인
                btc_price = float(self.client.get_symbol_ticker(symbol='BTCUSDT')['price'])
                
                # 필요한 USDT 계산
                needed_usdt = min_usdt - current_usdt
                
                # BTC의 절반을 계산
                half_btc = current_btc / 2
                btc_to_sell = min(half_btc, (needed_usdt / btc_price) * 1.01)  # 1% 마진 추가
                
                if btc_to_sell >= 0.00001:  # 최소 거래 수량 확인
                    success = self.convert_btc_to_usdt(btc_to_sell)
                    if success:
                        # 변환 후 잔고 재확인
                        new_balance = self.get_account_balance()
                        if new_balance and new_balance['USDT'] >= min_usdt:
                            print(f"USDT 잔고 확보 완료: {new_balance['USDT']:.2f} USDT")
                            return True
            
            return False
            
        except Exception as e:
            print(f"USDT 잔고 확보 실패: {e}")
            return False

    def save_transaction_log(self, trade_data):
        """거래 로그를 파일에 저장"""
        try:
            with open('transaction_history.txt', 'a', encoding='utf-8') as f:
                log_entry = (
                    f"시간: {trade_data['time']}\n"
                    f"매수가: {trade_data['buy_price']:.2f} USDT\n"
                    f"매도���: {trade_data['sell_price']:.2f} USDT\n"
                    f"수량: {trade_data['quantity']:.8f} BTC\n"
                    f"손익: {trade_data['profit']:.2f} USDT\n"
                    f"수익률: {trade_data['profit_percent']:.2f}%\n"
                    f"수수료: {trade_data['fee_bnb']:.8f} BNB\n"
                    f"수수료 상세: {trade_data['fee_details']}\n"
                    f"결과: {trade_data['result']}\n"
                    f"{'='*50}\n"
                )
                f.write(log_entry)
        except Exception as e:
            print(f"로그 저장 실패: {e}")

    def execute_scalping_strategy(self, use_percentage=10, profit_target=5, stop_loss=30, max_trades=5, wait_minutes=3, profit_type="절대값(USDT)"):
        """스캘핑 전략 실행"""
        while self.is_running:
            try:
                # 1. 현재 잔고 확인
                balance = self.get_account_balance()
                if not balance:
                    print("잔고 조회 실패")
                    time.sleep(1)
                    continue

                # 2. USDT 잔고 충분한지 확인 (최소 2000 USDT)
                if balance['USDT'] < 2000:
                    print(f"USDT 잔고 부족: {balance['USDT']:.2f} USDT")
                    time.sleep(1)
                    continue

                # 3. USDT의 10% 계산
                trade_amount_usdt = (balance['USDT'] * use_percentage) / 100
                current_price = self.get_current_price()
                
                if not current_price:
                    print("현재가 조회 실패")
                    time.sleep(1)
                    continue

                # 4. BTC 매수 수량 계산 (수수료 고려)
                quantity = (trade_amount_usdt / current_price) * 0.999  # 0.1% 수수료 고려
                quantity = "{:.5f}".format(float(quantity))

                print(f"\n매수 시도 - 수량: {quantity} BTC (현재가: {current_price:.2f} USDT)")

                # 5. 시장가 매수 주문
                try:
                    buy_order = self.client.create_order(
                        symbol='BTCUSDT',
                        side=Client.SIDE_BUY,
                        type=Client.ORDER_TYPE_MARKET,
                        quantity=quantity
                    )

                    buy_price = float(buy_order['fills'][0]['price'])
                    print(f"매수 완료 - 가격: {buy_price:.2f} USDT")

                    # 6. 목표가 계산 (매수가 기준)
                    if profit_type == "절대값(USDT)":
                        target_price = buy_price + profit_target
                    else:
                        target_price = buy_price * (1 + profit_target/100)

                    print(f"목표 매도가 설정: {target_price:.2f} USDT")

                    # 7. 예약 매도 주문
                    sell_order = self.client.create_order(
                        symbol='BTCUSDT',
                        side=Client.SIDE_SELL,
                        type=Client.ORDER_TYPE_LIMIT,
                        timeInForce='GTC',
                        quantity=quantity,
                        price="{:.2f}".format(target_price)
                    )

                    sell_order_id = sell_order['orderId']
                    print(f"예약 매도 주문 설정 완료 (주문 ID: {sell_order_id})")

                    # 8. 주문 체결 확인 루프
                    check_start_time = time.time()
                    while time.time() - check_start_time < wait_minutes * 60:
                        order_status = self.check_order_status(sell_order_id)
                        
                        if order_status == 'FILLED':
                            print("\n매도 주문 체결!")
                            
                            # 9. 0.5 BTC 제외하고 나머지 BTC를 USDT로 변환
                            new_balance = self.get_account_balance()
                            if new_balance['BTC'] > 0.5:
                                excess_btc = new_balance['BTC'] - 0.5
                                print(f"\n0.5 BTC 제외한 {excess_btc:.8f} BTC를 USDT로 변환 시도")
                                
                                try:
                                    convert_order = self.client.create_order(
                                        symbol='BTCUSDT',
                                        side=Client.SIDE_SELL,
                                        type=Client.ORDER_TYPE_MARKET,
                                        quantity="{:.5f}".format(excess_btc)
                                    )
                                    print("BTC -> USDT 변환 완료")
                                except Exception as e:
                                    print(f"BTC 변환 실패: {e}")
                            
                            break
                        
                        time.sleep(0.1)  # 1초에 10회 확인

                except Exception as e:
                    print(f"주문 실행 중 에러: {e}")
                    continue

            except Exception as e:
                print(f"전략 실행 중 에러: {e}")
                time.sleep(1)

    def get_current_price(self):
        """WebSocket에서 가격 가져오기"""
        if self.ws_data['price'] is not None:
            return self.ws_data['price']
        
        # WebSocket 가격이 없는 경우에만 REST API 사용
        try:
            price = float(self.client.get_symbol_ticker(symbol='BTCUSDT')['price'])
            time.sleep(0.1)  # API 호출 제한 방지
            return price
        except Exception as e:
            print(f"가격 조회 에러: {e}")
            return None

    def __del__(self):
        """소멸자"""
        self.stop()

    def test_connection(self):
        """API 연결 테스트"""
        try:
            # 서버 연결 테스트
            self.client.ping()
            print("서버 연결: OK")
            
            # 시간 동기화 테스트
            server_time = self.client.get_server_time()
            print("서버 시간 동기화: OK")
            
            # 계정 정보 테스트
            account = self.client.get_account()
            print("계정 정보 조회: OK")
            
            # 잔고 조회 테스트
            balances = {
                asset['asset']: float(asset['free']) 
                for asset in account['balances'] 
                if float(asset['free']) > 0
            }
            print("보유 자산:", balances)
            
            return True
            
        except Exception as e:
            print(f"연결 테스트 실패: {e}")
            return False

    def check_order_status(self, order_id):
        """주문 상태 확인"""
        try:
            order = self.client.get_order(
                symbol='BTCUSDT',
                orderId=order_id
            )
            time.sleep(0.1)  # API 호출 제한 방지
            return order['status']
        except Exception as e:
            if "Order does not exist" in str(e):
                # 주문이 없는 경우 None 반환
                return None
            print(f"주문 상태 확인 에러: {e}")
            return None

if __name__ == "__main__":
    bot = HighFrequencyBot()
    # 연결 테스트 실행
    if bot.test_connection():
        print("테스트넷 연결 성공")
        # 잔고 확인
        balance = bot.get_account_balance()
        if balance:
            print("테스트넷 잔고:", balance)
    else:
        print("테스트넷 연결 실패")