/*
 * bh1750_test —— BH1750 光照传感器验证固件
 * ------------------------------------------------------------------
 * 目标：只验证 BH1750 能否被正确读取，不接灯带、不做闭环。
 *
 * 接线（详见 ../wiring/接线图.md）：
 *   BH1750 VCC  -> ESP32 3V3
 *   BH1750 GND  -> ESP32 GND
 *   BH1750 SDA  -> ESP32 GPIO21
 *   BH1750 SCL  -> ESP32 GPIO22
 *   BH1750 ADDR -> 悬空 = 0x23；接 VCC = 0x5C（本程序两种都自动识别）
 *
 * 本文件自带最简 BH1750 驱动，不依赖任何第三方库，直接可编译。
 *
 * 串口协议（115200）：
 *   启动时打印板子信息与 I2C 扫描结果；
 *   之后每 500ms 输出一行：  LUX:450.2
 *   打开 DEBUG_VERBOSE 时额外输出 # 开头的调试行（可被上位机忽略）
 */

#include <Wire.h>

// ---------------- 可调参数 ----------------
#define I2C_SDA        21
#define I2C_SCL        22
#define SAMPLE_MS      500
#define DEBUG_VERBOSE  1      // 1 = 额外打印原始值/地址等调试信息

// ---------------- BH1750 常量 ----------------
#define BH1750_ADDR_LOW   0x23   // ADDR 悬空 / 接地
#define BH1750_ADDR_HIGH  0x5C   // ADDR 接 VCC

#define BH1750_POWER_ON       0x01
#define BH1750_RESET          0x07
#define BH1750_CONT_H_RES     0x10   // 连续 H 分辨率模式，约 120ms 一次，1lx
#define BH1750_ONE_H_RES      0x20   // 单次 H 分辨率模式

static uint8_t bhAddr = 0;

// ---------------- 最简驱动 ----------------
static bool bhWrite(uint8_t cmd) {
  Wire.beginTransmission(bhAddr);
  Wire.write(cmd);
  return Wire.endTransmission() == 0;
}

// 返回 lux；读取失败返回 -1
static float bhReadLux(uint16_t *rawOut = nullptr) {
  if (Wire.requestFrom((int)bhAddr, 2) != 2) return -1.0f;
  uint16_t raw = ((uint16_t)Wire.read() << 8) | (uint16_t)Wire.read();
  if (rawOut) *rawOut = raw;
  return raw / 1.2f;              // 官方换算：lux = raw / 1.2
}

// 扫描 I2C 总线，返回找到的 BH1750 地址（0 = 没找到）
static uint8_t findBH1750() {
  const uint8_t candidates[2] = { BH1750_ADDR_LOW, BH1750_ADDR_HIGH };
  for (uint8_t i = 0; i < 2; i++) {
    Wire.beginTransmission(candidates[i]);
    if (Wire.endTransmission() == 0) return candidates[i];
  }
  return 0;
}

static void scanBus() {
  Serial.println("# I2C 扫描结果：");
  uint8_t found = 0;
  for (uint8_t addr = 1; addr < 127; addr++) {
    Wire.beginTransmission(addr);
    if (Wire.endTransmission() == 0) {
      Serial.printf("#   发现设备 0x%02X\n", addr);
      found++;
    }
  }
  if (found == 0) Serial.println("#   没有任何 I2C 设备！检查 VCC/GND/SDA/SCL 是否接反或松动");
}

void setup() {
  Serial.begin(115200);
  delay(500);

  uint64_t mac = ESP.getEfuseMac();
  Serial.println();
  Serial.println("===== BH1750 Test =====");
  Serial.printf("# chip %s   flash %u MB   sdk %s\n",
                ESP.getChipModel(),
                (unsigned)(ESP.getFlashChipSize() / (1024 * 1024)),
                ESP.getSdkVersion());
  Serial.printf("# i2c  SDA=GPIO%d  SCL=GPIO%d\n", I2C_SDA, I2C_SCL);

  Wire.begin(I2C_SDA, I2C_SCL);
  Wire.setClock(100000);          // BH1750 标准速率 100kHz，接线长时更稳
  delay(100);

  scanBus();
  bhAddr = findBH1750();

  if (bhAddr == 0) {
    Serial.println("# 未找到 BH1750（0x23 / 0x5C）");
    Serial.println("# 排查顺序：VCC 是否 3V3 -> GND 是否共地 -> SDA/SCL 是否接反 -> 模块 ADDR 脚");
    Serial.println("LUX:-1");
    return;
  }

  Serial.printf("# 找到 BH1750：0x%02X\n", bhAddr);
  bhWrite(BH1750_POWER_ON);
  bhWrite(BH1750_RESET);
  delay(50);
  bhWrite(BH1750_CONT_H_RES);     // 连续测量模式
  delay(180);                     // 第一次转换需要约 120~180ms

  Serial.println("# 初始化完成，开始输出 LUX:<照度>");
  Serial.println("=======================");
}

void loop() {
  static uint32_t last = 0;
  static uint32_t n = 0;

  if (millis() - last < SAMPLE_MS) return;
  last = millis();

  if (bhAddr == 0) {              // 初始化就没找到，重试探测
    bhAddr = findBH1750();
    Serial.println("LUX:-1");
    return;
  }

  uint16_t raw = 0;
  float lux = bhReadLux(&raw);

  if (lux < 0) {
    Serial.println("LUX:-1");
    Serial.println("# 读取失败：模块掉线或接线松动");
    bhAddr = 0;                   // 下一轮重新探测
    return;
  }

  Serial.printf("LUX:%.1f\n", lux);

#if DEBUG_VERBOSE
  if (n % 5 == 0) {
    Serial.printf("#   raw=%u  addr=0x%02X  mode=CONT_H_RES\n", raw, bhAddr);
  }
#endif
  n++;
}
