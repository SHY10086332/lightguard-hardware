/*
 * esp32_blink_test —— ESP32 基础验证固件（只验证板子本身）
 * ------------------------------------------------------------------
 * 目标：证明 板子 + USB 转串口 + 供电 + 烧录链路 全部正常。
 *       不接任何外设（不接 BH1750、不接灯带）。
 *
 * 板型：ESP32 Dev Module (esp32:esp32:esp32)
 * 串口：115200
 *
 * 预期表现：
 *   1) 板载 LED 每 500ms 亮灭一次；
 *   2) 串口每 500ms 打印 LED ON / LED OFF；
 *   3) 启动时打印芯片型号 / Flash 容量 / SDK 版本 / MAC。
 *
 * 烧录注意（本板实测无自动下载电路）：
 *   方式 A（推荐）：按住 BOOT 不放 → 点"上传" → 看到 Writing at 0x... 再松开 BOOT
 *   方式 B：按住 BOOT → 按一下 EN/RST → 松开 BOOT，然后立刻用命令行 esptool 烧录
 */

#ifndef LED_BUILTIN
#define LED_BUILTIN 2          // 经典 ESP32 DevKit 板载 LED 通常接 GPIO2
#endif

#define BLINK_INTERVAL_MS 500

void setup() {
  Serial.begin(115200);
  delay(500);                  // 等 USB 串口稳定

  pinMode(LED_BUILTIN, OUTPUT);

  uint64_t mac = ESP.getEfuseMac();

  Serial.println();
  Serial.println("===== ESP32 Blink Test =====");
  Serial.printf("chip model : %s  rev %d  cores %d\n",
                ESP.getChipModel(), ESP.getChipRevision(), ESP.getChipCores());
  Serial.printf("cpu freq   : %u MHz\n", (unsigned)getCpuFrequencyMhz());
  Serial.printf("flash size : %u MB\n", (unsigned)(ESP.getFlashChipSize() / (1024 * 1024)));
  Serial.printf("sdk version: %s\n", ESP.getSdkVersion());
  Serial.printf("mac        : %02X:%02X:%02X:%02X:%02X:%02X\n",
                (uint8_t)(mac >> 40), (uint8_t)(mac >> 32), (uint8_t)(mac >> 24),
                (uint8_t)(mac >> 16), (uint8_t)(mac >> 8), (uint8_t)mac);
  Serial.printf("led pin    : GPIO%d\n", LED_BUILTIN);
  Serial.println("看到本段信息 + LED 每 0.5 秒闪烁 = 板子完全正常");
  Serial.println("============================");
}

void loop() {
  digitalWrite(LED_BUILTIN, HIGH);
  Serial.println("LED ON");
  delay(BLINK_INTERVAL_MS);

  digitalWrite(LED_BUILTIN, LOW);
  Serial.println("LED OFF");
  delay(BLINK_INTERVAL_MS);
}
