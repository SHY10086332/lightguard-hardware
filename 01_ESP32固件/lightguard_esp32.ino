/*
 * 光稳智检盒 - ESP32最小版固件
 *
 * 引脚：
 *   BH1750 SDA -> GPIO21, SCL -> GPIO22
 *   MOSFET PWM -> GPIO25
 *   共阴RGB模块 G -> GPIO26
 *   共阴RGB模块 R -> GPIO27
 *   有源蜂鸣器模块 IN -> GPIO32
 *
 * 串口协议：115200 baud，每条命令以换行结束，每次返回一行JSON。
 */

#include <Wire.h>
#include <BH1750.h>

const int LED_PWM_PIN = 25;
const int GREEN_LED_PIN = 26;
const int RED_LED_PIN = 27;
const int BUZZER_PIN = 32;
// PWM 频率：原为 1kHz（兼容 YYMOS-1 的 ≤1kHz 限制）。
// 2026-10-07 实测发现：1kHz 下相机画面出现固定横向条纹（PWM 频闪 × 卷帘快门，
// 逐行灰度波动标准差约 8.8 灰阶）。据此把频率提高到 8kHz，预期相机的单行曝光
// 会跨越多个 PWM 周期而被平均掉；**该改动尚未重新采集逐帧数据复测**，需实机确认。
// 本项目实际使用的 MOS 模块标称支持 0~20kHz，8kHz 在其范围内。
// 若换用额定 ≤1kHz 的模块（如 YYMOS-1），必须改回 1000。
const int LED_PWM_FREQUENCY = 8000;
const int LED_PWM_RESOLUTION = 8;

BH1750 lightMeter;
// ★ 上电默认亮度 = 0（安全第一）。
// 原来这里是 45：结果"上位机被强杀（关窗口/任务管理器）或板子复位"时灯带会自己亮到 45%，
// 实测确认过（进程被杀后回读 brightness=45、照度 415 lx）。正式固件改成 0 之后，
// 任何复位/重插 USB 都不会有光；要亮灯一律由上位机显式发 LIGHT n。
int brightnessPercent = 0;
String resultState = "idle";

void applyBrightness(int value) {
  brightnessPercent = constrain(value, 0, 100);
  int duty = map(brightnessPercent, 0, 100, 0, 255);
  analogWrite(LED_PWM_PIN, duty);
}

float readLuxSafe() {
  float lux = lightMeter.readLightLevel();
  if (lux < 0 || isnan(lux)) {
    return 0.0;
  }
  return lux;
}

void setResult(String value) {
  value.toLowerCase();
  resultState = value;

  digitalWrite(GREEN_LED_PIN, value == "normal" ? HIGH : LOW);
  digitalWrite(RED_LED_PIN, value == "abnormal" ? HIGH : LOW);

  if (value == "abnormal") {
    digitalWrite(BUZZER_PIN, HIGH);
    delay(250);
    digitalWrite(BUZZER_PIN, LOW);
  } else {
    digitalWrite(BUZZER_PIN, LOW);
  }
}

void sendStatus() {
  float lux = readLuxSafe();
  Serial.print("{\"ok\":true,\"lux\":");
  Serial.print(lux, 1);
  Serial.print(",\"brightness\":");
  Serial.print(brightnessPercent);
  Serial.print(",\"result\":\"");
  Serial.print(resultState);
  Serial.println("\"}");
}

void sendError(const String &message) {
  Serial.print("{\"ok\":false,\"error\":\"");
  Serial.print(message);
  Serial.println("\"}");
}

void handleCommand(String command) {
  command.trim();
  String upper = command;
  upper.toUpperCase();

  if (upper == "PING") {
    Serial.println("{\"ok\":true,\"device\":\"LIGHTGUARD_ESP32\"}");
    return;
  }

  if (upper == "STATUS" || upper == "LUX?") {
    sendStatus();
    return;
  }

  if (upper.startsWith("LIGHT ")) {
    int value = command.substring(6).toInt();
    if (value < 0 || value > 100) {
      sendError("brightness must be 0-100");
      return;
    }
    applyBrightness(value);
    sendStatus();
    return;
  }

  if (upper.startsWith("RESULT ")) {
    String value = command.substring(7);
    value.trim();
    value.toLowerCase();
    if (value != "normal" && value != "abnormal" && value != "idle") {
      sendError("result must be NORMAL, ABNORMAL or IDLE");
      return;
    }
    setResult(value);
    sendStatus();
    return;
  }

  sendError("unknown command");
}

void setup() {
  Serial.begin(115200);
  Serial.setTimeout(100);

  pinMode(LED_PWM_PIN, OUTPUT);
  pinMode(GREEN_LED_PIN, OUTPUT);
  pinMode(RED_LED_PIN, OUTPUT);
  pinMode(BUZZER_PIN, OUTPUT);

  digitalWrite(GREEN_LED_PIN, LOW);
  digitalWrite(RED_LED_PIN, LOW);
  digitalWrite(BUZZER_PIN, LOW);
  // 先让analogWrite建立通道，再显式固定频率和8位分辨率。
  analogWrite(LED_PWM_PIN, 0);
  analogWriteFrequency(LED_PWM_PIN, LED_PWM_FREQUENCY);
  analogWriteResolution(LED_PWM_PIN, LED_PWM_RESOLUTION);
  applyBrightness(brightnessPercent);

  Wire.begin(21, 22);
  lightMeter.begin(BH1750::CONTINUOUS_HIGH_RES_MODE);
}

void loop() {
  if (Serial.available()) {
    String command = Serial.readStringUntil('\n');
    handleCommand(command);
  }
}
