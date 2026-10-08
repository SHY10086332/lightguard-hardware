/*
 * light_pwm_test —— PWM 调光 / 灯带控制验证固件
 * ------------------------------------------------------------------
 * 目标：验证 ESP32 能否用 PWM 平滑调光。
 *
 * ★ 安全前提（务必先读）：
 *   灯带绝对不可以直接接 GPIO！GPIO 只能输出 3.3V/几十 mA 的信号。
 *   灯带电流必须由 MOSFET / 灯带驱动模块承担，ESP32 只输出 PWM 信号。
 *   接线见 ../wiring/接线图.md
 *
 * 接线（默认）：
 *   5V 电源 +   -> 灯带 +
 *   灯带 -      -> MOSFET 漏极(D/OUT)
 *   MOSFET 源极(S/GND) -> 5V 电源 -
 *   ESP32 GND   -> 5V 电源 -         （必须共地，否则 PWM 无效/乱闪）
 *   ESP32 GPIO25 (PWM) -> MOSFET 栅极(G/SIGNAL)
 *
 * 两种模式（用 MODE 选择）：
 *   MODE 0 = 固定 PWM 阶梯测试：0/25/50/75/100% 循环，只验证调光本身
 *   MODE 1 = 结合 BH1750 的照度闭环：自动把照度拉到 TARGET_LUX 附近
 *
 * 串口（115200）输出：
 *   BRIGHTNESS:72        <- 当前亮度百分比
 *   LUX:450.2            <- MODE 1 时附带照度
 * 串口输入（可在监视器里直接敲，回车结束）：
 *   B:50     手动把亮度设为 50%
 *   T:500    把目标照度设为 500 lx（MODE 1 生效）
 *   A        切回自动（MODE 1 下解除手动锁定）
 */

#include <Wire.h>

// ---------------- 模式与参数 ----------------
// 用 #ifndef 包住，方便命令行临时覆盖：
//   arduino-cli compile --fqbn esp32:esp32:esp32 --build-property build.extra_flags=-DMODE=1 <此目录>
#ifndef MODE
#define MODE              0        // 0 = 固定 PWM 阶梯；1 = BH1750 闭环
#endif
#ifndef PWM_PIN
#define PWM_PIN           25       // 信号脚，避免用 6~11(SPI Flash) 与 34~39(仅输入)
#endif
#ifndef PWM_FREQ
#define PWM_FREQ          8000     // 8kHz：与正式固件 lightguard_esp32.ino 完全一致
#endif                             // （1kHz 会让相机画面出现 PWM 频闪条纹，故与正式固件同步提到 8kHz）
#ifndef PWM_RESOLUTION
#define PWM_RESOLUTION    8        // 8 位 -> duty 0~255，同样与正式固件一致
#endif
#ifndef PWM_INVERT
#define PWM_INVERT        0        // 若 MOSFET 是反相驱动（亮度反着来）改成 1
#endif

#define STEP_INTERVAL_MS  2000     // MODE 0 每档停留时间
#define LOOP_INTERVAL_MS  200      // 控制周期

#if MODE == 1
  #define I2C_SDA         21
  #define I2C_SCL         22
  #define BH1750_ADDR     0x23
  #define BH1750_POWER_ON 0x01
  #define BH1750_CONT_H_RES 0x10
  #define TARGET_LUX      900.0f   // 默认目标照度（与验收点 900±30 lx 一致；对应亮度需按 BH1750 最终位置标定）
  // ---- 闭环参数（2026-10-06 实测标定）----
  // 被控对象增益实测（第一次标定）：亮度 0% → 49 lx；100% → 3952 lx
  //   ⇒ 约 39 lx / 每 1% 亮度。⚠️ 增益与 BH1750 摆放位置强相关：最终标定为 0% → 45.8 lx、100% → 2897.5 lx（约 28.5 lx / 1%）
  // 环路增益 = KP × 被控对象增益，必须远小于 1 才不振荡。
  // 原值 KP=0.06 时环路增益 2.34 → 亮度在 0%↔100% 之间来回跳（已实测到）。
  #define KP              0.0025f  // 第一次标定 39 lx/% 时 ≈0.098，最终标定 28.5 lx/% 时 ≈0.071，均稳定
  #define MAX_STEP_PCT    8        // 每周期最多调整 8% 亮度，避免猛冲
  #define MIN_STEP_PCT    1        // ★ 最小步长 1%：否则小误差被取整成 0，永远差一口气
  // 死区按目标值的百分比算（而不是固定 lx），这样换到箱内不同照度也能自适应。
  // 取 2%：等于约"一个最小步长"的照度，既收敛又不会来回抖。
  #define DEADBAND_PCT    2.0f
#endif

#ifndef SAFE_START_WAIT
// 1 = 安全启动：上电后停在 0% 亮度，收到串口命令才开始升亮
//     （目的是"第一次上电"这一步：万一 IN+/IN- 接反，GPIO25 等于被短路到地，
//       0% 时引脚一直输出低电平，不会有大电流，也就不会烧引脚。
//       确认没问题后再用 B:25 / B:50… 逐级升亮。）
#define SAFE_START_WAIT   1
#endif

static const uint32_t DUTY_MAX = (1u << PWM_RESOLUTION) - 1u;
static int   brightness = 0;         // 0~100
static bool  manualLock = false;     // 串口手动设定后锁定
static bool  started    = false;     // 安全启动：是否已收到命令开始输出

// ---------------- 亮度输出 ----------------
static void applyBrightness(int percent) {
  if (percent < 0)   percent = 0;
  if (percent > 100) percent = 100;
  brightness = percent;

  uint32_t duty = (uint32_t)((uint64_t)DUTY_MAX * percent / 100);
#if PWM_INVERT
  duty = DUTY_MAX - duty;
#endif
  ledcWrite(PWM_PIN, duty);

  Serial.printf("BRIGHTNESS:%d\n", brightness);
}

// ---------------- BH1750（MODE 1） ----------------
#if MODE == 1
static bool bhInit() {
  Wire.begin(I2C_SDA, I2C_SCL);
  Wire.setClock(100000);
  Wire.beginTransmission(BH1750_ADDR);
  if (Wire.endTransmission() != 0) return false;
  Wire.beginTransmission(BH1750_ADDR); Wire.write(BH1750_POWER_ON);     Wire.endTransmission();
  delay(20);
  Wire.beginTransmission(BH1750_ADDR); Wire.write(BH1750_CONT_H_RES);   Wire.endTransmission();
  delay(180);
  return true;
}

static float bhReadLux() {
  if (Wire.requestFrom((int)BH1750_ADDR, 2) != 2) return -1.0f;
  uint16_t raw = ((uint16_t)Wire.read() << 8) | (uint16_t)Wire.read();
  return raw / 1.2f;
}
static float targetLux = TARGET_LUX;
#endif

// ---------------- 串口命令 ----------------
static void handleSerial() {
  if (!Serial.available()) return;
  String line = Serial.readStringUntil('\n');
  line.trim();
  if (line.length() == 0) return;
  line.toUpperCase();

  if (line.startsWith("B:")) {
    int v = line.substring(2).toInt();
    manualLock = true;                // 只锁亮度，不自动调节
#if MODE == 1
    started = true;                   // MODE 1：手动亮度下也持续读照度（方便标定），但不自动调整
#endif
    applyBrightness(v);
    Serial.println("# 已手动锁定亮度，输入 A 恢复自动，输入 S 归零停机");
  } else if (line == "A") {
    manualLock = false;
    started = true;
    Serial.println("# 已恢复自动控制");
  } else if (line == "S") {
    // 安全停机：亮度归零并停止自动输出
    manualLock = false;
    started = false;
    applyBrightness(0);
    Serial.println("# 已停机（亮度 0%，停止自动输出）");
  }
#if MODE == 1
  else if (line.startsWith("T:")) {
    targetLux = line.substring(2).toFloat();
    manualLock = false;
    Serial.printf("# 目标照度改为 %.1f lx\n", targetLux);
  }
#endif
  else {
    Serial.println("# 未知命令。可用：B:50 设亮度 / T:500 设目标照度 / A 恢复自动 / S 安全停机" );
  }
}

void setup() {
  Serial.begin(115200);
  delay(300);

  Serial.println();
  Serial.println("===== Light PWM Test =====");
  Serial.printf("# chip %s   mode %d   pwm GPIO%d @ %uHz  %u-bit\n",
                ESP.getChipModel(), MODE, PWM_PIN, PWM_FREQ, PWM_RESOLUTION);
  Serial.println("# 提醒：灯带必须经 MOSFET/驱动模块，ESP32 GND 必须与灯带电源共地");

  ledcAttach(PWM_PIN, PWM_FREQ, PWM_RESOLUTION);
  applyBrightness(0);                 // ★ 上电先 0%，绝不自动升亮

#if SAFE_START_WAIT
  started = false;
  Serial.println("# 【安全启动】亮度 = 0%，暂不输出。");
  Serial.println("#   确认接线无误后，用串口发命令升亮：B:10 → B:25 → B:50 → B:100");
  Serial.println("#   （若灯带不亮或灯带端电压不对，立刻发 B:0 并断电检查 IN+/IN-）");
#else
  started = true;
#endif

#if MODE == 1
  if (bhInit()) {
    Serial.println("# BH1750 就绪，进入照度闭环");
  } else {
    Serial.println("# 未找到 BH1750！MODE 1 无法闭环，改用固定 50% 亮度");
    Serial.println("# （接线请检查 GPIO21=SDA / GPIO22=SCL / 3V3 / GND）");
  }
  Serial.printf("# 目标照度 = %.1f lx\n", targetLux);
#endif
  Serial.println("==========================");
}

void loop() {
  handleSerial();

#if MODE == 0
  // ---------- 固定 PWM 阶梯：只验证"能不能调、灯亮不亮" ----------
  static uint32_t last = 0;
  static uint8_t  step = 0;
  static const int steps[5] = { 0, 25, 50, 75, 100 };

  if (!started) {                     // 安全启动：等命令，保持 0%
    static uint32_t hint = 0;
    if (millis() - hint >= 4000) { hint = millis(); Serial.println("BRIGHTNESS:0"); }
    return;
  }

  if (millis() - last >= STEP_INTERVAL_MS) {
    last = millis();
    manualLock = false;               // MODE 0 下忽略锁定，继续跑阶梯
    applyBrightness(steps[step]);
    step = (step + 1) % 5;
  }
#else
  // ---------- 照度闭环：把照度稳到目标值附近 ----------
  static uint32_t last = 0;

  if (!started) {                     // 安全启动：等命令，保持 0%
    static uint32_t hint = 0;
    if (millis() - hint >= 4000) {
      hint = millis();
      Serial.println("# 等待开始：发 A 进入照度闭环，或发 B:<0-100> 手动设亮度");
    }
    return;
  }

  if (millis() - last >= LOOP_INTERVAL_MS) {
    last = millis();

    float lux = bhReadLux();
    if (lux >= 0) {
      Serial.printf("LUX:%.1f\n", lux);

      if (!manualLock) {
        float err = targetLux - lux;          // 太暗 -> err>0 -> 加亮度
        float deadband = targetLux * DEADBAND_PCT / 100.0f;   // 死区随目标值缩放
        if (fabs(err) > deadband) {           // 进入死区就不动，避免在目标值附近来回抖
          int step = (int)(err * KP);
          if (step >  MAX_STEP_PCT) step =  MAX_STEP_PCT;   // 单步限幅，防止猛冲
          if (step < -MAX_STEP_PCT) step = -MAX_STEP_PCT;
          if (step == 0) step = (err > 0) ? MIN_STEP_PCT : -MIN_STEP_PCT;  // ★ 最小步长
          applyBrightness(brightness + step);
        }
      }
    } else {
      Serial.println("# BH1750 读取失败，保持当前亮度");
    }
  }
#endif
}
