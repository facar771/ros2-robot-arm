"""Firmware limit header (C) from ``hardware.profile.yaml`` + the robot profile.

    cd src/robot_core
    python3 -m robot_core.hardware_gen            # write
    python3 -m robot_core.hardware_gen --check    # exit 1 if the committed header is stale

All values in the header are MOTOR-side microsteps: ``direction``, gear ratio and zero offset are
already applied (the wire protocol only speaks steps, docs/HARDWARE.md). The firmware must not
apply the direction again. Limits are rounded INWARD. When any axis has no encoder the header says
so loudly (comment block + ``ROBOT_OPEN_LOOP_*`` macros).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional, Sequence

from robot_core.hardware import (
    ENCODERS,
    HOMING_METHODS,
    OPEN_LOOP_WARNING,
    Axis,
    HardwareProfile,
    hard_limits,
    load_hardware,
    rate_steps,
    soft_limits,
)
from robot_core.profile import Profile, load_profile

SRC = Path(__file__).resolve().parents[2]
PROFILE_DIR = SRC / 'robot_description' / 'profile'
HARDWARE_PATH = PROFILE_DIR / 'hardware.profile.yaml'
HEADER_REL = 'robot_description/profile/generated/robot_limits.h'
HARDWARE_REL = 'src/robot_description/profile/hardware.profile.yaml'

GENERATED = ('ÜRETİLDİ: {src} + robot profilinden `python3 -m robot_core.hardware_gen` ile '
             'üretildi. ELLE DÜZENLEME: profili düzenle, yeniden üret (pytest güncel mi diye '
             'denetler).')


def _f(value: float) -> str:
    return f'{float(value)!r}f'


def _axis_row(index: int, axis: Axis, robot: Profile) -> str:
    soft, hard = soft_limits(axis, robot), hard_limits(axis)
    joint = next(j for j in robot.joints if j.name == axis.joint)
    h = axis.homing
    return (
        f'    /* {index} {axis.joint}: {axis.encoder}'
        f"{' (AÇIK DÖNGÜ)' if axis.open_loop else ''}, yön {axis.direction:+d} (adıma işlendi) */\n"
        f'    {{ {_f(axis.steps_per_joint_rad)}, ENCODER_{axis.encoder.upper()}, '
        f'HOMING_{h.method.upper()}, {h.direction * axis.direction:+d},\n'
        f'      {soft[0]}, {soft[1]}, {hard[0]}, {hard[1]},\n'
        f'      {rate_steps(axis, joint.velocity)}u, {rate_steps(axis, joint.acceleration)}u, '
        f'{rate_steps(axis, h.speed)}u, {axis.to_steps_int(h.position)}, '
        f'{round(h.backoff * axis.steps_per_joint_rad)} }}')


def generate(hw: HardwareProfile, robot: Profile) -> str:
    n = len(hw.axes)
    mask = sum(1 << i for i, a in enumerate(hw.axes) if a.open_loop)
    out = [f'/* {GENERATED.format(src=HARDWARE_REL)} */',
           '#ifndef ROBOT_LIMITS_H', '#define ROBOT_LIMITS_H', '', '#include <stdint.h>', '']
    if hw.open_loop_axes:
        out += [
            '/* !!! AÇIK DÖNGÜ UYARISI !!!', f' * {OPEN_LOOP_WARNING}',
            f" * Encoder'sız eksenler: {', '.join(hw.open_loop_axes)}.",
            ' * Konum komut sayacıdır; kayıp adım görünmez. Homing ve limit anahtarı olmadan '
            'ENABLE açılmamalı. */']
    out += [f'#define ROBOT_AXIS_COUNT {n}',
            f'#define ROBOT_OPEN_LOOP_ANY {1 if mask else 0}',
            f'#define ROBOT_OPEN_LOOP_MASK 0x{mask:02X}u    /* bit i: eksen i encoder\'sız */']
    if mask:
        out.append(f'#define ROBOT_OPEN_LOOP_WARNING "{OPEN_LOOP_WARNING}"')
    out += ['', '/* değerler protokoldeki EncoderMode ile aynı (robot_hw_bridge.protocol) */']
    out += [f'#define ENCODER_{e.upper()} {i}' for i, e in enumerate(ENCODERS)]
    out += ['', '/* referanslama yöntemi */', '#define HOMING_NONE 0']
    out += [f'#define HOMING_{m.upper()} {i + 1}' for i, m in enumerate(HOMING_METHODS)]
    out += [
        '', '/* Eksen başına sabitler. TÜM değerler MOTOR tarafı mikroadımdır: yön, dişli ve sıfır',
        ' * ofseti zaten işlendi; firmware yönü bir daha UYGULAMAMALI. Sınırlar içe yuvarlanır.',
        ' *   soft_*: eklem (URDF) limitleri -> CONFIG SOFT_MIN/SOFT_MAX',
        ' *   hard_*: motor/mekanik limitler (marjsız)',
        ' *   homing_dir: motor adım işaretiyle arama yönü (0: yok); home_position: referans',
        ' *   anında sayaca yazılan adım; backoff: anahtardan uzaklaşma (mikroadım) */',
        'typedef struct {',
        '    float steps_per_rad;            /* eklem radyanı başına mikroadım (işaretsiz) */',
        '    uint8_t encoder;', '    uint8_t homing_method;', '    int8_t homing_dir;',
        '    int32_t soft_min, soft_max;', '    int32_t hard_min, hard_max;',
        '    uint32_t max_speed;             /* adım/sn */',
        '    uint32_t max_accel;             /* adım/sn^2 */',
        '    uint32_t homing_speed;          /* adım/sn */',
        '    int32_t home_position;', '    int32_t homing_backoff;', '} robot_axis_limits_t;', '',
        'static const robot_axis_limits_t ROBOT_AXES[ROBOT_AXIS_COUNT] = {']
    rows = [_axis_row(i, a, robot) for i, a in enumerate(hw.axes)]
    out.append(',\n'.join(rows))
    out += ['};', '', '#endif /* ROBOT_LIMITS_H */', '']
    return '\n'.join(out)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog='python3 -m robot_core.hardware_gen')
    parser.add_argument('--check', action='store_true',
                        help='write nothing; exit 1 if the header is out of date')
    parser.add_argument('--hardware', type=Path, default=HARDWARE_PATH)
    parser.add_argument('--src', type=Path, default=SRC, help='the repository src/ directory')
    args = parser.parse_args(argv)
    import yaml
    robot_name = yaml.safe_load(args.hardware.read_text(encoding='utf-8')).get('robot')
    robot = load_profile(args.hardware.with_name(f'{robot_name}.yaml'))
    text = generate(load_hardware(args.hardware, robot), robot)
    path = args.src / HEADER_REL
    current = path.read_text(encoding='utf-8') if path.exists() else None
    if current == text:
        print(f'güncel   {HEADER_REL}')
        return 0
    if args.check:
        print(f'ESKİ     {HEADER_REL}\n\nDonanım profili ile başlık uyuşmuyor; yeniden üret: '
              'python3 -m robot_core.hardware_gen')
        return 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')
    print(f'yazıldı  {HEADER_REL}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
