import subprocess
import sys
import os
import time

def main():
    """dashboard.py와 bot_monitor.py 실행"""
    try:
        # 이전 상태 파일 제거
        if os.path.exists('bot_status.txt'):
            os.remove('bot_status.txt')
            
        # Dashboard 프로세스 시작
        dashboard = subprocess.Popen(['streamlit', 'run', 'dashboard.py'])
        
        # 잠시 대기하여 dashboard가 시작되도록 함
        time.sleep(3)
        
        # Bot Monitor 프로세스 시작
        monitor = subprocess.Popen([sys.executable, 'bot_monitor.py'])
        
        try:
            # 프로세스 완료 대기
            dashboard.wait()
            monitor.wait()
        except KeyboardInterrupt:
            print("프로그램 종료 중...")
        finally:
            # 프로세스 종료
            try:
                dashboard.terminate()
                monitor.terminate()
            except:
                pass
            
            # 상태 파일 정리
            if os.path.exists('bot_status.txt'):
                os.remove('bot_status.txt')
        
    except Exception as e:
        print(f"실행 중 에러: {e}")
        
if __name__ == "__main__":
    main() 