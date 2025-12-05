import requests, datetime as dt, csv, os, time, threading, sys
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import serial
import serial.tools.list_ports

API_KEY = "API_KEY you've received"
NX, NY = 61, 127
LOG = "nowcast_log.csv"

stop_flag = False
arduino_port = None
arduino_serial = None

# 센서 상태
sensor_green = False
sensor_yellow = False
sensor_red = False
sensor_initialized = False  # 센서 초기화 여부
last_sensor_status_time = 0  # 마지막 센서 상태 출력 시간


#   종료 입력 처리 스레드
def keyboard_listener():
    global stop_flag
    while True:
        user = input()
        if user.strip().lower() == "q":  # q 입력 → 종료
            stop_flag = True
            print("\n[종료 요청 감지] 프로그램을 종료합니다...\n")
            break


# 발표시각 계산
def latest_base_datetime():
    now = dt.datetime.now()
    base = now - dt.timedelta(minutes=40)
    base_date = base.strftime("%Y%m%d")
    minute = 0 if base.minute < 30 else 30
    base_time = base.strftime("%H") + f"{minute:02d}"
    return base_date, base_time


# 세션
def get_session():
    s = requests.Session()
    retries = Retry(
        total=3,
        backoff_factor=1.0,
        status_forcelist=[500, 502, 503, 504],
        allowed_methods=["GET"]
    )
    s.mount("https://", HTTPAdapter(max_retries=retries))
    s.mount("http://", HTTPAdapter(max_retries=retries))
    return s


# API 호출
def get_nowcast():
    base_date, base_time = latest_base_datetime()
    url = ""
    params = {
        "serviceKey": API_KEY,
        "pageNo": "1",
        "numOfRows": "60",
        "dataType": "JSON",
        "base_date": base_date,
        "base_time": base_time,
        "nx": str(NX),
        "ny": str(NY),
    }

    s = get_session()
    r = s.get(url, params=params, timeout=30)
    r.raise_for_status()
    data = r.json()

    items = data["response"]["body"]["items"]["item"]

    def pick(cat):
        for it in items:
            if it.get("category") == cat:
                return it.get("obsrValue")
        return None

    return {
        "base_date": base_date,
        "base_time": base_time,
        "T1H": pick("T1H"),
        "RN1": pick("RN1"),
        "REH": pick("REH"),
        "WSD": pick("WSD"),
    }


#   CSV 저장
def save_csv(row):
    newfile = not os.path.exists(LOG) or os.path.getsize(LOG) == 0

    fieldnames = [
        "timestamp", "발표일자", "발표시각",
        "x좌표", "y좌표",
        "기온(°C)", "1시간 강수량(mm)", "습도(%)", "풍속(m/s)"
    ]

    with open(LOG, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        if newfile:
            w.writeheader()

        row2 = {
            "timestamp": dt.datetime.now().isoformat(timespec="seconds"),
            "발표일자": row["base_date"],
            "발표시각": row["base_time"],
            "x좌표": NX,
            "y좌표": NY,
            "기온(°C)": row["T1H"],
            "1시간 강수량(mm)": row["RN1"],
            "습도(%)": row["REH"],
            "풍속(m/s)": row["WSD"],
        }
        w.writerow(row2)
        return row2


# CSV에서 최신 강수량 읽어야함
def get_latest_rainfall():
    """CSV 파일에서 가장 최근의 1시간 강수량을 읽어옴"""
    if not os.path.exists(LOG) or os.path.getsize(LOG) == 0:
        return 0.0
    
    try:
        with open(LOG, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            if not rows:
                return 0.0
            
            # 마지막 행의 강수량
            last_row = rows[-1]
            rainfall_str = last_row.get("1시간 강수량(mm)", "0")
            try:
                return float(rainfall_str)
            except ValueError:
                return 0.0
    except Exception as e:
        print(f"[ERROR] CSV 읽기 실패: {e}")
        return 0.0


#   물 높이 레벨 판단
def get_water_level():
    """센서 상태를 기반으로 물 높이 반환"""
    if sensor_red:
        return "RED"
    elif sensor_yellow:
        return "YELLOW"
    elif sensor_green:
        return "GREEN"
    else:
        return "NONE"



# 위험도 분석 로직
def calculate_risk_level(water_level, rainfall):
    """
    구현 해야할 목록

    <대전제>
    센서를 우선 기준으로 위험도 결정 + 강수량은 보조 지표로 사용
    
    센서 의미:
    - GREEN: 발목 정도 물이 찼음 (초기 경고)
    - YELLOW: 다리까지 물이 찼음 (위험)
    - RED: 침수 직전 (매우 위험)
    
    위험도 레벨:
    - SAFE: 안전
    - CAUTION: 주의
    - WARNING: 경고
    - DANGER: 위험
    
    로직:
    1. 센서 상태로 기본 위험도 결정
    2. 차수벽이 있어서 30mm/h까지는 영향 없음
    3. 30-50mm/h: 위험도 1단계 상향 (CAUTION → WARNING)
    4. 50-100mm/h: 위험도 1단계 상향 (CAUTION → WARNING, WARNING → DANGER)
    5. 100mm/h 이상: 위험도 2단계 상향 (CAUTION → DANGER, WARNING → DANGER)
    """
    # 강수량 기준 (mm/h)
    # 차수벽이 있어서 30mm/h까지는 영향 없음
    RAIN_EXTREME = 100.0  # 100mm/h 이상: 극한 호우 (2단계 상승)
    RAIN_HEAVY = 50.0     # 50-100mm/h: 매우 많은 비 (1단계 상승)
    RAIN_MEDIUM = 30.0    # 30-50mm/h: 많은 비 (1단계 상승)
    # 30mm/h 미만: 차수벽으로 인해서 영향 없음
    
    # 1단계: 센서 기반 기본 위험도 결정
    base_risk = "SAFE"
    if water_level == "NONE":
        base_risk = "SAFE"
    elif water_level == "GREEN":
        base_risk = "CAUTION"  # 발목까지 물: 주의 필요
    elif water_level == "YELLOW":
        base_risk = "WARNING"  # 다리까지 물: 경고
    elif water_level == "RED":
        base_risk = "DANGER"   # 침수 직전: 위험
    
    # 2단계: 강수량으로 위험도 조정 (보조 지표)
    # 위험도 순서: SAFE < CAUTION < WARNING < DANGER
    risk_levels = ["SAFE", "CAUTION", "WARNING", "DANGER"]
    current_index = risk_levels.index(base_risk)
    
    if rainfall >= RAIN_EXTREME:
        # 극한 호우: 위험도 2단계 상향 (최대 DANGER까지)
        # 예: CAUTION → DANGER, WARNING → DANGER
        current_index = min(current_index + 2, len(risk_levels) - 1)
    elif rainfall >= RAIN_HEAVY:
        # 매우 많은 비 (50-100mm/h): 위험도 1단계 상향
        # 예: CAUTION → WARNING, WARNING → DANGER
        current_index = min(current_index + 1, len(risk_levels) - 1)
    elif rainfall >= RAIN_MEDIUM:
        # 많은 비 (30-50mm/h): 위험도 1단계 상향
        # 예: CAUTION → WARNING, WARNING → DANGER
        current_index = min(current_index + 1, len(risk_levels) - 1)
    # 30mm/h 미만은 차수벽으로 영향 없음 (센서 기반 위험도 그대로 유지)
    
    return risk_levels[current_index]


# 아두이노 시리얼 통신 초기화
def init_arduino():
    """아두이노 시리얼 포트 찾기 및 연결"""
    global arduino_serial, arduino_port
    
    # 사용 가능한 포트 찾기
    print("[DEBUG] 사용 가능한 시리얼 포트 검색 중...")
    ports = serial.tools.list_ports.comports()
    
    # 우선순위: usbmodem > usbserial > ttyUSB > COM (Bluetooth 제외)
    arduino_candidates = []
    for port in ports:
        print(f"[DEBUG] 발견된 포트: {port.device} - {port.description}")
        device_lower = port.device.lower()
        desc_lower = (port.description or "").lower()
        
        # Bluetooth 포트 제외
        if "bluetooth" in device_lower or "bluetooth" in desc_lower:
            print(f"[DEBUG] Bluetooth 포트 제외: {port.device}")
            continue
        
        # 아두이노 후보 포트 찾기
        if "usbmodem" in device_lower or "usbserial" in device_lower or "ttyusb" in device_lower:
            # Arduino 키워드가 있으면 우선순위 높음
            priority = 2 if "arduino" in desc_lower else 1
            arduino_candidates.append((priority, port))
        elif "com" in device_lower.upper():
            arduino_candidates.append((1, port))
    
    if not arduino_candidates:
        print("[WARNING] 아두이노 후보 포트를 찾을 수 없습니다.")
        print("[WARNING] 시리얼 통신 없이 실행합니다.")
        return False
    
    # 우선순위 순으로 정렬
    arduino_candidates.sort(key=lambda x: x[0], reverse=True)
    
    print(f"[DEBUG] {len(arduino_candidates)}개의 아두이노 후보 포트 발견")
    for priority, port in arduino_candidates:
        try:
            print(f"[DEBUG] 포트 {port.device} 연결 시도 중... (우선순위: {priority}, 설명: {port.description})")
            arduino_serial = serial.Serial(port.device, 9600, timeout=1)
            arduino_port = port.device
            time.sleep(2)  # 아두이노 초기화 대기
            print(f"[OK] 아두이노 연결됨: {port.device}")
            # 연결 후 버퍼 비우기
            arduino_serial.reset_input_buffer()
            arduino_serial.reset_output_buffer()
            # 연결 확인: 아두이노로부터 데이터가 오는지 확인
            time.sleep(1)
            if arduino_serial.in_waiting > 0:
                print(f"[DEBUG] 연결 후 버퍼에 {arduino_serial.in_waiting} bytes 데이터 발견")
            return True
        except Exception as e:
            print(f"[ERROR] 포트 {port.device} 연결 실패: {e}")
            continue
    
    print("[WARNING] 모든 포트 연결 시도 실패. 시리얼 통신 없이 실행합니다.")
    return False


# 아두이노로부터 센서 데이터 읽기
def read_arduino_sensors():
    """아두이노로부터 센서 상태 읽기"""
    global sensor_green, sensor_yellow, sensor_red
    
    if arduino_serial is None or not arduino_serial.is_open:
        return False
    
    try:
        sensor_updated = False
        # 버퍼에 있는 모든 데이터 읽기
        lines_read = 0
        while arduino_serial.in_waiting > 0:
            line = arduino_serial.readline().decode('utf-8', errors='ignore').strip()
            lines_read += 1
            if line.startswith("SENSOR:"):
                # 형식: SENSOR:1:0:1 (GREEN:YELLOW:RED)
                parts = line.split(":")
                if len(parts) == 4:
                    old_green = sensor_green
                    old_yellow = sensor_yellow
                    old_red = sensor_red
                    
                    sensor_green = (parts[1] == "1")
                    sensor_yellow = (parts[2] == "1")
                    sensor_red = (parts[3] == "1")
                    
                    # 센서 상태가 변경되었을 때만 출력 및 업데이트 플래그 설정
                    if (old_green != sensor_green or old_yellow != sensor_yellow or old_red != sensor_red):
                        print(f"[센서] G:{sensor_green} Y:{sensor_yellow} R:{sensor_red} → 변경 감지")
                        sensor_updated = True
                    # 센서 변경이 없으면 업데이트 플래그는 False 유지
            elif line.startswith("led") or line.startswith("RECEIVED"):
                # 아두이노의 디버그 메시지 무시
                pass
            # 빈 줄이나 다른 메시지는 조용히 무시
        
        return sensor_updated
    except Exception as e:
        print(f"[ERROR] 아두이노 읽기 실패: {e}")
        return False


# 아두이노로 위험도 데이터 전송
def send_risk_data_to_arduino(water_level, rainfall, risk_level):
    """아두이노로 위험도 정보 전송"""
    if arduino_serial is None or not arduino_serial.is_open:
        print("[WARNING] 아두이노가 연결되지 않아 데이터를 전송할 수 없습니다.")
        return
    
    try:
        # 형식: DATA:WATER_LEVEL:RAINFALL:RISK_LEVEL
        message = f"DATA:{water_level}:{rainfall:.1f}:{risk_level}\n"
        arduino_serial.write(message.encode('utf-8'))
        arduino_serial.flush()  # 버퍼 강제 전송
        # 전송 로그는 제거 (너무 많음)
    except Exception as e:
        print(f"[ERROR] 아두이노 전송 실패: {e}")


#   메인 루프
if __name__ == "__main__":
    print("=== 지하주차장 침수 센서 시스템 시작 ===")
    print("종료하려면 q 입력 후 엔터\n")

    # 아두이노 연결 시도
    init_arduino()

    # 키보드 입력 스레드 실행
    thread = threading.Thread(target=keyboard_listener, daemon=True)
    thread.start()

    last_logged_time = None
    last_risk_update = 0
    sensor_check_count = 0  # 센서 체크 카운터
    last_status_print = 0  # 마지막 상태 출력 시간

    print("\n[시작] 시스템이 준비되었습니다.")
    print("[주의] 아두이노 코드가 최신 버전으로 업로드되었는지 확인하세요!")
    print("[주의] 아두이노 IDE의 시리얼 모니터는 닫아주세요.\n")

    while not stop_flag:
        try:
            # 기상청 데이터 수집
            now_base_date, now_base_time = latest_base_datetime()
            current_key = f"{now_base_date}{now_base_time}"

            # 발표시각이 새로 갱신되었을 때만 로깅
            forecast_updated = False
            if last_logged_time != current_key:
                res = get_nowcast()
                saved = save_csv(res)
                print("[OK] 기록됨:", saved)
                last_logged_time = current_key
                forecast_updated = True  # 기상청 예보 갱신 플래그

            # 아두이노로부터 센서 데이터 읽기 (항상 읽기 시도)
            sensor_updated = read_arduino_sensors()
            
            # 센서 초기화 메시지 (10초에 한 번만 출력)
            current_time = time.time()
            if not sensor_initialized or (current_time - last_sensor_status_time >= 10):
                if sensor_green or sensor_yellow or sensor_red:
                    # 센서가 하나라도 켜져있으면 상태 출력
                    print(f"[센서 상태] G:{sensor_green} Y:{sensor_yellow} R:{sensor_red}")
                    last_sensor_status_time = current_time
                    sensor_initialized = True
                elif not sensor_initialized:
                    # 첫 초기화 시에만 출력
                    print(f"[센서 초기화] G:{sensor_green} Y:{sensor_yellow} R:{sensor_red}")
                    sensor_initialized = True
                    last_sensor_status_time = current_time
            
            # 디버그: 30번마다 센서 상태 확인 메시지 (30초마다)
            sensor_check_count += 1
            if sensor_check_count >= 30:
                if arduino_serial and arduino_serial.is_open:
                    if arduino_serial.in_waiting > 100:
                        print(f"[경고] 아두이노 버퍼에 {arduino_serial.in_waiting} bytes 누적됨")
                sensor_check_count = 0

            # 위험도 분석 및 아두이노 전송
            # 기상청 예보 시각이 갱신되면 즉시 업데이트
            # 센서 상태가 변경되면 즉시 업데이트
            # 그 외에는 10초마다 업데이트 (상태 출력과 동기화)
            should_update = False
            update_reason = ""
            
            if forecast_updated:
                should_update = True
                update_reason = "기상청 갱신"
            elif sensor_updated:
                should_update = True
                update_reason = "센서 변경"
            elif current_time - last_risk_update >= 10:
                should_update = True
                update_reason = "주기적 업데이트"
            
            if should_update:
                rainfall = get_latest_rainfall()
                water_level = get_water_level()
                risk_level = calculate_risk_level(water_level, rainfall)
                
                if update_reason == "기상청 갱신":
                    print(f"[🌧️ 기상청 갱신] 물 높이: {water_level}, 강수량: {rainfall:.1f}mm/h, 위험도: {risk_level}")
                elif update_reason == "센서 변경":
                    print(f"[⚠️ 센서 변경] 물 높이: {water_level}, 강수량: {rainfall:.1f}mm/h, 위험도: {risk_level}")
                elif update_reason == "주기적 업데이트":
                    # 10초마다 상태 출력
                    print(f"[상태] 물 높이: {water_level}, 강수량: {rainfall:.1f}mm/h, 위험도: {risk_level}")
                
                send_risk_data_to_arduino(water_level, rainfall, risk_level)
                last_risk_update = current_time

        except Exception as e:
            print("[ERROR]", e)

        time.sleep(1)  # 1초 주기로 체크

    # 정리
    if arduino_serial and arduino_serial.is_open:
        arduino_serial.close()
    print("=== 프로그램 종료 ===")
