import requests, datetime as dt, csv, os, time, threading, sys
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

API_KEY = "R+Lu4UYCdVMewCu83nOCLox/2Hyf22H6s5uX6NqbS6Lsyersc2IHiw/NlQdR/NbM9i0NRVCIXS+afq6Ta9i0ag=="
NX, NY = 61, 127
LOG = "nowcast_log.csv"

stop_flag = False


# --------------------------
#   종료 입력 처리 스레드
# --------------------------
def keyboard_listener():
    global stop_flag
    while True:
        user = input()
        if user.strip().lower() == "q":  # q 입력 → 종료
            stop_flag = True
            print("\n[종료 요청 감지] 프로그램을 종료합니다...\n")
            break


# --------------------------
#   발표시각 계산
# --------------------------
def latest_base_datetime():
    now = dt.datetime.now()
    base = now - dt.timedelta(minutes=40)
    base_date = base.strftime("%Y%m%d")
    minute = 0 if base.minute < 30 else 30
    base_time = base.strftime("%H") + f"{minute:02d}"
    return base_date, base_time


# --------------------------
#   세션
# --------------------------
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


# --------------------------
#   API 호출
# --------------------------
def get_nowcast():
    base_date, base_time = latest_base_datetime()
    url = "https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getUltraSrtNcst"
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


# --------------------------
#   CSV 저장
# --------------------------
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


# --------------------------
#   메인 루프
# --------------------------
if __name__ == "__main__":
    print("=== 초단기실황 자동 수집 시작 ===")
    print("종료하려면 q 입력 후 엔터\n")

    # 키보드 입력 스레드 실행
    thread = threading.Thread(target=keyboard_listener, daemon=True)
    thread.start()

    last_logged_time = None

    while not stop_flag:
        try:
            now_base_date, now_base_time = latest_base_datetime()
            current_key = f"{now_base_date}{now_base_time}"

            # 발표시각이 새로 갱신되었을 때만 로깅
            if last_logged_time != current_key:
                res = get_nowcast()
                saved = save_csv(res)
                print("[OK] 기록됨:", saved)
                last_logged_time = current_key
            else:
                pass

        except Exception as e:
            print("[ERROR]", e)

        time.sleep(60)  # 1분 주기

    print("=== 프로그램 종료 ===")

