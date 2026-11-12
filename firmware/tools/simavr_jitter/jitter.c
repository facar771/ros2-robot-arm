// K9 Faz 2a: simavr ile (16 MHz ATmega328P, GERÇEK firmware ELF'i) seri trafiğin step darbesi zamanlamasına etkisini ölçer.
// Bu bir BENZETİMDİR: AVR komut döngüsünü sayar, gerçek motoru/sürücüyü/USB'yi içermez.
//   jitter <fw.elf> <schedule.txt> <bitiş_us>      schedule: her satır "<us> <bayt 0-255>" (seri girişe bu anda yazılır)
// Çıktı: "J1 <us>" (4 bobin pininden birinin değiştiği an, 12 us içindekiler tek adım), "J7 <us>" (D10 STEP yükselen kenar).
// Derleme: gcc -O2 jitter.c -o jitter -lsimavr -lelf   (apt: simavr libsimavr-dev libelf-dev)
#include <stdio.h>
#include <stdlib.h>
#include <simavr/sim_avr.h>
#include <simavr/sim_elf.h>
#include <simavr/avr_ioport.h>
#include <simavr/avr_uart.h>

static avr_t* avr;
static int dump_tx = 0;
static void tx_notify(avr_irq_t* irq, uint32_t value, void* p) { (void)irq; (void)p; if (dump_tx) printf("U %u\n", value & 0xFF); }
static uint64_t last_j1 = 0;

static void j1_notify(avr_irq_t* irq, uint32_t value, void* p) {
  (void)irq; (void)value; (void)p;
  uint64_t c = avr->cycle;
  if (last_j1 == 0 || c - last_j1 > 200) printf("J1 %.1f\n", c / 16.0);
  last_j1 = c;
}
static void j7_notify(avr_irq_t* irq, uint32_t value, void* p) {
  (void)irq; (void)p;
  if (value) printf("J7 %.1f\n", avr->cycle / 16.0);
}

int main(int argc, char** argv) {
  if (argc < 4) return 2;
  elf_firmware_t f = {{0}};
  if (elf_read_firmware(argv[1], &f)) return 3;
  f.frequency = 16000000;
  avr = avr_make_mcu_by_name("atmega328p");
  avr_init(avr);
  avr_load_firmware(avr, &f);
  avr->log = 0;
  avr_ioport_external_t ext = {.name = 'B', .mask = 1 << 5, .value = 0};  // D13 = LOW: NC döngü kapalı
  avr_ioctl(avr, AVR_IOCTL_IOPORT_SET_EXTERNAL('B'), &ext);
  for (int pin = 2; pin <= 5; pin++)
    avr_irq_register_notify(avr_io_getirq(avr, AVR_IOCTL_IOPORT_GETIRQ('D'), pin), j1_notify, NULL);
  avr_irq_register_notify(avr_io_getirq(avr, AVR_IOCTL_IOPORT_GETIRQ('B'), 2), j7_notify, NULL);
  dump_tx = getenv("JITTER_DUMP_TX") != NULL;  // hata ayıklama: MCU'nun seri çıkışını da yaz
  avr_irq_register_notify(avr_io_getirq(avr, AVR_IOCTL_UART_GETIRQ('0'), UART_IRQ_OUTPUT), tx_notify, NULL);
  avr_irq_t* rx = avr_io_getirq(avr, AVR_IOCTL_UART_GETIRQ('0'), UART_IRQ_INPUT);

  FILE* sch = fopen(argv[2], "r");
  double nextT = -1; int nextB = 0, have = 0;
  double endUs = atof(argv[3]);
  while (1) {
    if (!have) {
      if (fscanf(sch, "%lf %d", &nextT, &nextB) == 2) have = 1; else nextT = 1e30;
    }
    double now = avr->cycle / 16.0;
    if (now >= endUs) break;
    if (have && now >= nextT) { avr_raise_irq(rx, (uint32_t)nextB); have = 0; continue; }
    int st = avr_run(avr);
    if (st == cpu_Done || st == cpu_Crashed) break;
  }
  return 0;
}
