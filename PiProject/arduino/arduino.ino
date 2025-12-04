#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>

#define SCREEN_WIDTH 128
#define SCREEN_HEIGHT 64

// OLED 객체
Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, -1);

const int reedPin1 = 4;
const int reedPin2 = 9;
const int reedPin3 = 13;
const int ledPin1 = 3;
const int ledPin2 = 7;
const int ledPin3 = 12;

int reedState1;
int reedState2;
int reedState3;
int prevState1 = HIGH;   // 처음엔 열려있다고 가정
int prevState2 = HIGH;
int prevState3 = HIGH;

// LED 상태를 기억 (OLED에 표시용)
bool led1On = false;
bool led2On = false;
bool led3On = false;

// Python으로부터 받을 데이터
String waterLevel = "NONE";  // NONE, GREEN, YELLOW, RED
float rainfall = 0.0;
String riskLevel = "SAFE";   // SAFE, CAUTION, WARNING, DANGER
unsigned long lastDataUpdate = 0;

// OLED에 통합 정보 출력
void updateDisplay() {
  display.clearDisplay();
  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);
  
  // 첫 번째 줄: 물 높이 상태
  display.setCursor(0, 0);
  display.print("WATER: ");
  display.println(waterLevel);
  
  // 두 번째 줄: 강수량
  display.setCursor(0, 12);
  display.print("RAIN: ");
  display.print(rainfall, 1);
  display.println("mm/h");
  
  // 세 번째 줄: 위험도
  display.setCursor(0, 24);
  display.print("RISK: ");
  display.println(riskLevel);
  
  // 네 번째 줄: 센서 상태
  display.setCursor(0, 36);
  display.print("G:");
  display.print(led1On ? "ON " : "OFF ");
  display.print("Y:");
  display.print(led2On ? "ON " : "OFF ");
  display.print("R:");
  display.print(led3On ? "ON" : "OFF");
  
  // 마지막 줄: 데이터 업데이트 시간
  display.setCursor(0, 48);
  unsigned long secondsSinceUpdate = (millis() - lastDataUpdate) / 1000;
  display.print("Update: ");
  display.print(secondsSinceUpdate);
  display.println("s ago");
  
  display.display();
}

void setup() {
  pinMode(ledPin1, OUTPUT);
  pinMode(ledPin2, OUTPUT);
  pinMode(ledPin3, OUTPUT);
  pinMode(reedPin1, INPUT_PULLUP);
  pinMode(reedPin2, INPUT_PULLUP);
  pinMode(reedPin3, INPUT_PULLUP);

  Serial.begin(9600);

  // OLED 초기화
  if (!display.begin(SSD1306_SWITCHCAPVCC, 0x3C)) { // 주소 0x3C가 일반적
    Serial.println(F("SSD1306 allocation failed"));
    for (;;); // 멈춤
  }

  display.clearDisplay();
  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);
  display.setCursor(0, 0);
  display.println("System Start");
  display.display();
  delay(1000);

  updateDisplay();  // 초기 상태 표시
}

void loop(){
  reedState1 = digitalRead(reedPin1);
  reedState2 = digitalRead(reedPin2);
  reedState3 = digitalRead(reedPin3);

  // ===== 리드스위치1 (GREEN) =====
  if (prevState1 == HIGH && reedState1 == LOW) {
    // 자석 붙는 순간 → 토글
    led1On = !led1On;
    digitalWrite(ledPin1, led1On ? HIGH : LOW);
    Serial.println(led1On ? "led green ON" : "led green OFF");
  }

  // ===== 리드스위치2 (YELLOW) =====
  if (prevState2 == HIGH && reedState2 == LOW) {
    // 자석 붙는 순간 → 토글
    led2On = !led2On;
    digitalWrite(ledPin2, led2On ? HIGH : LOW);
    Serial.println(led2On ? "led yellow ON" : "led yellow OFF");
  }

  // ===== 리드스위치3 (RED) =====
  if (prevState3 == HIGH && reedState3 == LOW) {
    // 자석 붙는 순간 → 토글
    led3On = !led3On;
    digitalWrite(ledPin3, led3On ? HIGH : LOW);
    Serial.println(led3On ? "led red ON" : "led red OFF");
  }

  // 상태 업데이트
  prevState1 = reedState1;
  prevState2 = reedState2;
  prevState3 = reedState3;

  // 센서 상태를 주기적으로 Python으로 전송 (형식: SENSOR:GREEN:YELLOW:RED)
  // 변경 시 즉시 전송 + 2초마다 주기적 전송
  static bool lastLed1On = false;
  static bool lastLed2On = false;
  static bool lastLed3On = false;
  static unsigned long lastSensorSend = 0;
  
  bool sensorChanged = (led1On != lastLed1On || led2On != lastLed2On || led3On != lastLed3On);
  bool timeToSend = (millis() - lastSensorSend >= 2000); // 2초마다
  
  if (sensorChanged || timeToSend) {
    Serial.print("SENSOR:");
    Serial.print(led1On ? "1" : "0");
    Serial.print(":");
    Serial.print(led2On ? "1" : "0");
    Serial.print(":");
    Serial.println(led3On ? "1" : "0");
    lastLed1On = led1On;
    lastLed2On = led2On;
    lastLed3On = led3On;
    lastSensorSend = millis();
  }

  // Python으로부터 데이터 수신 (형식: DATA:WATER_LEVEL:RAINFALL:RISK_LEVEL)
  if (Serial.available() > 0) {
    String received = Serial.readStringUntil('\n');
    received.trim();
    
    if (received.startsWith("DATA:")) {
      // 형식: DATA:WATER_LEVEL:RAINFALL:RISK_LEVEL
      // 예: DATA:YELLOW:100.5:DANGER
      int firstColon = received.indexOf(':');
      int secondColon = received.indexOf(':', firstColon + 1);
      int thirdColon = received.indexOf(':', secondColon + 1);
      
      if (firstColon != -1 && secondColon != -1 && thirdColon != -1) {
        waterLevel = received.substring(firstColon + 1, secondColon);
        rainfall = received.substring(secondColon + 1, thirdColon).toFloat();
        riskLevel = received.substring(thirdColon + 1);
        lastDataUpdate = millis();
        
        // 디버그: 시리얼 모니터에 수신 확인
        Serial.print("RECEIVED: ");
        Serial.print(waterLevel);
        Serial.print(" ");
        Serial.print(rainfall);
        Serial.print(" ");
        Serial.println(riskLevel);
        
        updateDisplay();
      }
    }
  }

  // 주기적으로 디스플레이 업데이트 (데이터가 없어도)
  static unsigned long lastDisplayUpdate = 0;
  if (millis() - lastDisplayUpdate > 500) {
    updateDisplay();
    lastDisplayUpdate = millis();
  }
  
  delay(100); // 안정성을 위한 짧은 딜레이
}
