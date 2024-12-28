import os
import time
import signal
from main import HighFrequencyBot

def monitor_dashboard():
    """dashboard.py 프로세스 모니터링"""
    print("Bot Monitor 시작...")
    
    # 상태 파일 경로
    status_file = "bot_status.txt"
    last_modified = 0
    
    while True:
        try:
            # 상태 파일이 존재하고 마지막 수정 시간이 30초 이상 지났다면
            if os.path.exists(status_file):
                current_time = time.time()
                file_modified = os.path.getmtime(status_file)
                
                if current_time - file_modified > 30:  # 30초 이상 업데이트 없음
                    try:
                        # 상태 파일에서 활성 봇 정보 읽기
                        with open(status_file, 'r') as f:
                            active_bots = f.read().strip().split(',')
                        
                        print("Dashboard가 응답하지 않음. 활성 봇 정리 중...")
                        
                        # 각 봇 인스턴스 정리
                        for bot_id in active_bots:
                            if bot_id:
                                try:
                                    bot = HighFrequencyBot()
                                    bot.stop()
                                    print(f"Bot {bot_id} 종료됨")
                                except Exception as e:
                                    print(f"Bot {bot_id} 종료 중 에러: {e}")
                        
                        # 상태 파일 삭제
                        os.remove(status_file)
                        break
                        
                    except Exception as e:
                        print(f"봇 정리 중 에러: {e}")
                        break
            
            time.sleep(1)
            
        except KeyboardInterrupt:
            print("\nBot Monitor 종료 중...")
            break
        except Exception as e:
            print(f"모니터링 중 에러: {e}")
            time.sleep(1)

if __name__ == "__main__":
    monitor_dashboard() 