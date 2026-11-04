/* ÜRETİLDİ: src/robot_description/profile/hardware.profile.yaml + robot profilinden `python3 -m robot_core.hardware_gen` ile üretildi. ELLE DÜZENLEME: profili düzenle, yeniden üret (pytest güncel mi diye denetler). */
#ifndef ROBOT_LIMITS_H
#define ROBOT_LIMITS_H

#include <stdint.h>

/* !!! AÇIK DÖNGÜ UYARISI !!!
 * AÇIK DÖNGÜ: encoder yok, adım kaybı ALGILANAMAZ; konum bir kestirimdir (komut sayacı). Her oturumda homing ve mekanik uç limit anahtarı şart; tork/hız/ivme marjı geniş tutulmalı.
 * Encoder'sız eksenler: joint_1, joint_2, joint_3, joint_4, joint_5, joint_6.
 * Konum komut sayacıdır; kayıp adım görünmez. Homing ve limit anahtarı olmadan ENABLE açılmamalı. */
#define ROBOT_AXIS_COUNT 6
#define ROBOT_OPEN_LOOP_ANY 1
#define ROBOT_OPEN_LOOP_MASK 0x3Fu    /* bit i: eksen i encoder'sız */
#define ROBOT_OPEN_LOOP_WARNING "AÇIK DÖNGÜ: encoder yok, adım kaybı ALGILANAMAZ; konum bir kestirimdir (komut sayacı). Her oturumda homing ve mekanik uç limit anahtarı şart; tork/hız/ivme marjı geniş tutulmalı."

/* değerler protokoldeki EncoderMode ile aynı (robot_hw_bridge.protocol) */
#define ENCODER_NONE 0
#define ENCODER_INCREMENTAL 1
#define ENCODER_ABSOLUTE 2

/* referanslama yöntemi */
#define HOMING_NONE 0
#define HOMING_SWITCH 1
#define HOMING_INDEX 2
#define HOMING_ABSOLUTE 3

/* Eksen başına sabitler. TÜM değerler MOTOR tarafı mikroadımdır: yön, dişli ve sıfır
 * ofseti zaten işlendi; firmware yönü bir daha UYGULAMAMALI. Sınırlar içe yuvarlanır.
 *   soft_*: eklem (URDF) limitleri -> CONFIG SOFT_MIN/SOFT_MAX
 *   hard_*: motor/mekanik limitler (marjsız)
 *   homing_dir: motor adım işaretiyle arama yönü (0: yok); home_position: referans
 *   anında sayaca yazılan adım; backoff: anahtardan uzaklaşma (mikroadım) */
typedef struct {
    float steps_per_rad;            /* eklem radyanı başına mikroadım (işaretsiz) */
    uint8_t encoder;
    uint8_t homing_method;
    int8_t homing_dir;
    int32_t soft_min, soft_max;
    int32_t hard_min, hard_max;
    uint32_t max_speed;             /* adım/sn */
    uint32_t max_accel;             /* adım/sn^2 */
    uint32_t homing_speed;          /* adım/sn */
    int32_t home_position;
    int32_t homing_backoff;
} robot_axis_limits_t;

static const robot_axis_limits_t ROBOT_AXES[ROBOT_AXIS_COUNT] = {
    /* 0 joint_1: none (AÇIK DÖNGÜ), yön +1 (adıma işlendi) */
    { 509.29581789406507f, ENCODER_NONE, HOMING_SWITCH, -1,
      -1599, 1599, -1599, 1599,
      509u, 509u, 50u, 0, 25 },
    /* 1 joint_2: none (AÇIK DÖNGÜ), yön +1 (adıma işlendi) */
    { 509.29581789406507f, ENCODER_NONE, HOMING_SWITCH, -1,
      -799, 799, -799, 799,
      509u, 509u, 50u, 0, 25 },
    /* 2 joint_3: none (AÇIK DÖNGÜ), yön +1 (adıma işlendi) */
    { 509.29581789406507f, ENCODER_NONE, HOMING_SWITCH, -1,
      -1018, 1018, -1018, 1018,
      509u, 509u, 50u, 0, 25 },
    /* 3 joint_4: none (AÇIK DÖNGÜ), yön +1 (adıma işlendi) */
    { 509.29581789406507f, ENCODER_NONE, HOMING_SWITCH, -1,
      -1599, 1599, -1599, 1599,
      509u, 509u, 50u, 0, 25 },
    /* 4 joint_5: none (AÇIK DÖNGÜ), yön +1 (adıma işlendi) */
    { 509.29581789406507f, ENCODER_NONE, HOMING_SWITCH, -1,
      -799, 799, -799, 799,
      509u, 509u, 50u, 0, 25 },
    /* 5 joint_6: none (AÇIK DÖNGÜ), yön +1 (adıma işlendi) */
    { 509.29581789406507f, ENCODER_NONE, HOMING_SWITCH, -1,
      -1599, 1599, -1599, 1599,
      509u, 509u, 50u, 0, 25 }
};

#endif /* ROBOT_LIMITS_H */
