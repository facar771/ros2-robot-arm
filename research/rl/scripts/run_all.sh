#!/usr/bin/env bash
# Tüm ızgara: 3 görev x 3 algoritma x 5 tohum (2 paralel süreç). Biten koşular atlanır (devam edilebilir).
# Kullanım: bash scripts/run_all.sh      (WSL'de/Linux'ta; 2 çekirdekte yaklaşık 2.5-3 saat)
cd "$(dirname "$0")/.."
declare -A OFF=([reach]=60000 [obstacle]=80000 [track]=80000)
declare -A PPO=([reach]=300000 [obstacle]=400000 [track]=400000)
jobs=()
for task in reach obstacle track; do
  for seed in 0 1 2 3 4; do
    for algo in sac td3 ppo; do
      steps=${OFF[$task]}; [ $algo = ppo ] && steps=${PPO[$task]}
      [ -f runs/${task}_${algo}_dr0.0_s${seed}/result.json ] || jobs+=("$algo $task $seed $steps")
    done
  done
done
printf '%s\n' "${jobs[@]}" | xargs -P 2 -L 1 bash -c 'python3 -W ignore scripts/train.py --algo $0 --task $1 --seed $2 --steps $3 2>&1 | tail -1 | tee -a runs/log.txt'
