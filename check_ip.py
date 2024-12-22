import requests

def get_ip():
    try:
        # 외부 IP 확인
        response = requests.get('https://api.ipify.org?format=json')
        return response.json()['ip']
    except Exception as e:
        print(f"Error: {e}")
        return None

if __name__ == "__main__":
    ip = get_ip()
    print(f"Your public IP address is: {ip}") 