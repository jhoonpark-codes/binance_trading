import os
from main import HighFrequencyBot

def test_balance():
    # HighFrequencyBot 인스턴스 생성
    bot = HighFrequencyBot()
    
    # 계좌 잔고 확인
    balance = bot.get_account_balance()
    
    if balance:
        print(f"BTC 잔고: {balance['BTC']}")
        print(f"USDT 잔고: {balance['USDT']}")
    else:
        print("잔고 조회 실패")

if __name__ == "__main__":
    test_balance() 