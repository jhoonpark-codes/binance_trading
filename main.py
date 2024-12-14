import threading
from binance.client import Client
from binance.websockets import BinanceSocketManager
from twisted.internet import reactor
import time
import queue
import pandas as pd
import numpy as np
from datetime import datetime

class HighFrequencyBot:
    def __init__(self):
        self.api_key = '여기에_API_키_입력'
        self.api_secret = '여기에_시크릿_키_입력'
        self.client = Client(self.api_key, self.api_secret)
        self.bm = BinanceSocketManager(self.client)
        
        self.symbol = 'BTCUSDT'
        self.trade_quantity = 0.001
        self.price_queue = queue.Queue()
        self.trades = []
        self.position = 0  # 0: 중립, 1: 롱, -1: 숏
        
        # 거래 제한 설정
        self.max_trades_per_second = 10
        self.last_trade_time = time.time()
        self.trade_counter = 0
        
        # 가격 데이터 저장
        self.price_history = []
        self.max_history_size = 100
        
    def start_websocket(self):
        """웹소켓 연결로 실시간 가격 데이터 수신"""
        def process_message(msg):
            if msg['e'] == 'trade':
                price = float(msg['p'])
                self.price_queue.put(price)
                self.update_price_history(price)
                
        conn_key = self.bm.start_trade_socket(self.symbol, process_message)
        self.bm.start()
        
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
                    
    def run(self):
        """메인 실행 루프"""
        self.start_websocket()
        
        while True:
            try:
                if not self.price_queue.empty():
                    _ = self.price_queue.get()
                    signal = self.calculate_signals()
                    
                    if signal != 0:
                        self.execute_trade(signal)
                        
                    self.risk_management()
                    
                time.sleep(0.1)  # CPU 사용량 조절
                
            except Exception as e:
                print(f"실행 에러: {e}")
                continue

if __name__ == "__main__":
    bot = HighFrequencyBot()
    bot.run()