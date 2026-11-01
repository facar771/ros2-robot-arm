# research/rl — ROS'suz RL deneyleri (MuJoCo)

`src/robot_description/urdf/robot.urdf` -> MJCF -> Gymnasium ortamları + klasik taban + (R2) PPO/SAC/TD3.
ROS'a bağlanmaz, `src/` altındaki hiçbir şeyi değiştirmez.

```bash
cd research/rl
pip install -r requirements.txt
python -m rl_arm.urdf_to_mjcf              # rl_arm/six_axis_robot.xml üretir (URDF'ten)
python scripts/test_consistency.py         # URDF<->MJCF: limit, eksen, kütle, atalet, FK (bağımsız numpy FK ile)
python scripts/test_envs.py                # gymnasium check_env + tekrar üretilebilirlik
python scripts/eval_baselines.py 100       # klasik taban -> results/baselines.json
```

Ortamlar: `ArmReach-v0`, `ArmReachObstacle-v0`, `ArmTrack-v0` (bkz. `rl_arm/envs.py` başlık açıklaması).
