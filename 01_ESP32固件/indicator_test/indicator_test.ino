/*
 * indicator_test —— 红绿指示灯（共阴 RGB 模块）+ 有源蜂鸣器 验证固件
 * ------------------------------------------------------------------
 * 引脚与正式固件 lightguard_esp32.ino 完全一致：
 *     GPIO26 → RGB 模块 G（绿灯 = 检测正常）
 *     GPIO27 → RGB 模块 R（红灯 = 检测异常）
 *     GPIO32 → 有源蜂鸣器 IN（高电平触发，异常时短鸣）
 *
 * 串口输出同样是 JSON，但**协议并不与正式固件完全一致**：本固件只实现 PING / T / RESULT，
* 不实现 STATUS 与 LIGHT，因此**不能用网页（上位机）直接连它**，只用于离线点亮验证。
* 正式演示前必须烧回 lightguard_esp32.ino：
 *     PC → ESP32:  PING                 → {"ok":true,"device":"LIGHTGUARD_INDICATOR"}
 *     PC → ESP32:  RESULT NORMAL        → 绿灯亮、红灯灭
 *     PC → ESP32:  RESULT ABNORMAL      → 红灯亮、绿灯灭、蜂鸣器短鸣 250ms
 *     PC → ESP32:  RESULT IDLE          → 全部熄灭
 *     PC → ESP32:  T                    → 自检循环一次（绿→红+响→全灭）
 *
 * 安全启动：上电后全部熄灭，不会自己亮、不会自己响。
 *
 * 若模块是"共阳"（公共端接 3V3）而不是共阴（公共端接 GND），
 * 把 RGB_COMMON_ANODE 改成 1 重新编译即可。
 */

#ifndef RGB_COMMON_ANODE
#define RGB_COMMON_ANODE  0        // 0 = 共阴（高电平点亮）  1 = 共阳（低电平点亮）
#endif

const int GREEN_PIN  = 26;
const int RED_PIN    = 27;
const int BUZZER_PIN = 32;

static bool greenOn = false;
static bool redOn   = false;

static void applyOutputs() {
#if RGB_COMMON_ANODE
  digitalWrite(GREEN_PIN, greenOn ? LOW : HIGH);
  digitalWrite(RED_PIN,   redOn   ? LOW : HIGH);
#else
  digitalWrite(GREEN_PIN, greenOn ? HIGH : LOW);
  digitalWrite(RED_PIN,   redOn   ? HIGH : LOW);
#endif
}

static void beep(uint16_t ms) {
  digitalWrite(BUZZER_PIN, HIGH);
  delay(ms);
  digitalWrite(BUZZER_PIN, LOW);
}

static void setResult(const String &state) {   // 传入已转小写的状态
  greenOn = (state == "normal");
  redOn   = (state == "abnormal");
  applyOutputs();
  if (redOn) beep(250);
}

void setup() {
  Serial.begin(115200);
  Serial.setTimeout(100);
  delay(300);

  pinMode(GREEN_PIN,  OUTPUT);
  pinMode(RED_PIN,    OUTPUT);
  pinMode(BUZZER_PIN, OUTPUT);

  // 安全启动：全部熄灭
  greenOn = false; redOn = false;
  applyOutputs();
  digitalWrite(BUZZER_PIN, LOW);

  Serial.println();
  Serial.println("===== Indicator Test (RGB + Buzzer) =====");
  Serial.printf("# chip %s   green=GPIO%d  red=GPIO%d  buzzer=GPIO%d  %s\n",
                ESP.getChipModel(), GREEN_PIN, RED_PIN, BUZZER_PIN,
                RGB_COMMON_ANODE ? "common-anode" : "common-cathode");
  Serial.println("# 当前状态：全部熄灭（安全启动）");
  Serial.println("# 命令：RESULT NORMAL | RESULT ABNORMAL | RESULT IDLE | PING | T");
  Serial.println("========================================");
}

void loop() {
  if (!Serial.available()) return;

  String cmd = Serial.readStringUntil('\n');
  cmd.trim();
  if (cmd.length() == 0) return;

  String upper = cmd;
  upper.toUpperCase();

  if (upper == "PING") {
    Serial.println("{\"ok\":true,\"device\":\"LIGHTGUARD_INDICATOR\"}");
    return;
  }

  if (upper == "T") {                        // 自检：绿 → 红+响 → 全灭
    Serial.println("# 自检开始：绿灯 2 秒");
    setResult("normal");  delay(2000);
    Serial.println("# 自检：红灯 + 短鸣");
    setResult("abnormal"); delay(2000);
    Serial.println("# 自检结束：全部熄灭");
    setResult("idle");
    Serial.println("{\"ok\":true,\"selftest\":\"done\"}");
    return;
  }

  if (upper.startsWith("RESULT ")) {
    String state = cmd.substring(7);
    state.trim();
    state.toLowerCase();
    if (state != "normal" && state != "abnormal" && state != "idle") {
      Serial.println("{\"ok\":false,\"error\":\"result must be NORMAL, ABNORMAL or IDLE\"}");
      return;
    }
    setResult(state);
    Serial.printf("{\"ok\":true,\"result\":\"%s\"}\n", state.c_str());
    return;
  }

  Serial.println("{\"ok\":false,\"error\":\"unknown command\"}");
}
