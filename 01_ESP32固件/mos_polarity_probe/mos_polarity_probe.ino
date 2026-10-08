/*
 * mos_polarity_probe —— 判定 MOS 模块两位输入端子哪个是 IN-、哪个是 IN+
 * ------------------------------------------------------------------
 * 为什么要测：这块 MOS 模块的输入是 IN+/IN- 两个端子。
 *   接对了：GPIO25 → IN+，ESP32 GND → IN-      （正常，PWM 控制灯带）
 *   接反了：GPIO25 → IN-（内部直接接模块地）   = 把 GPIO25 短路到地，可能烧掉这个引脚
 * 而 IN+/IN- 的丝印是竖排小字，容易看错。所以用这个程序自动判定。
 *
 * ★ 原理（全程零风险）：
 *   把两个引脚都设成 INPUT_PULLUP（输入 + 内部弱上拉，约 45kΩ）。
 *   - 读到 LOW  ：那个端子内部就是模块的地（被拉到 0V）→ 它是 IN-
 *   - 读到 HIGH ：那个端子经电阻接 MOS 管栅极（高阻，无直流通路）→ 它是 IN+
 *   两个引脚全程只做"输入 + 弱上拉"，**不会输出电流、不会短路**，
 *   就算接反也完全安全。
 *
 * ★ 接线（只接 3 根线，此刻不要接 5V 电源、不要接灯带！）：
 *      GPIO25  → MOS 模块 端子A（两个 IN 脚中的任意一个）
 *      GPIO26  → MOS 模块 端子B（另一个）
 *      ESP32 GND → MOS 模块 DC-    ← 这根必须接，给模块一个地参考，否则读数会漂
*      ★ 前提：本探针只适用于**非隔离**模块（如本项目的 XY-MOS）。
*        若模块是光耦隔离输入型，控制地不能与负载地（DC-）短接，请勿使用本探针。
 *
 *   测完拆掉这 3 根线，再按正确极性正式接线。
 */

#define PIN_A 25
#define PIN_B 26

static void printResult() {
  int a = digitalRead(PIN_A);
  int b = digitalRead(PIN_B);

  Serial.printf("端子A(GPIO25)=%s   端子B(GPIO26)=%s   ->  ", a ? "HIGH" : "LOW", b ? "HIGH" : "LOW");

  if (a == LOW && b == HIGH) {
    Serial.println("端子A = IN-（信号地）  |  端子B = IN+（信号）");
    Serial.println("       正确接线：ESP32 GND -> 端子A ，GPIO25 -> 端子B");
  } else if (a == HIGH && b == LOW) {
    Serial.println("端子A = IN+（信号）    |  端子B = IN-（信号地）");
    Serial.println("       正确接线：GPIO25 -> 端子A ，ESP32 GND -> 端子B");
  } else if (a == HIGH && b == HIGH) {
    Serial.println("两个都是 HIGH：可能是输入隔离（光耦）型，或有一根线没接好");
    Serial.println("       先检查三根杜邦线是否插到底；仍为 HIGH 时不要按丝印猜测，改用带明确丝印/资料的模块");
  } else {
    Serial.println("两个都是 LOW：两个端子内部都接地（异常或短路）");
    Serial.println("       这种结果也可能来自带限流电阻的三极管/光耦输入（PN 结把 IN+ 钳位到约 1V）
       ★ 结论不可靠：不要据此接线，先不要接 5V 电源，改查模块资料或换模块");
  }
}

void setup() {
  Serial.begin(115200);
  delay(800);

  Serial.println();
  Serial.println("===== MOS 输入极性自检 =====");
  Serial.println("# 接线： GPIO25 -> 端子A     GPIO26 -> 端子B     ESP32 GND -> DC-");
  Serial.println("# 此刻不要接 5V 电源、不要接灯带");
  Serial.println("# 两个脚全程为“输入+弱上拉”，接错也不会短路，安全");
  Serial.println("============================");

  pinMode(PIN_A, INPUT_PULLUP);
  pinMode(PIN_B, INPUT_PULLUP);
}

void loop() {
  static uint32_t last = 0;
  if (millis() - last >= 1000) {
    last = millis();
    printResult();
  }
}
