# Sonuç özeti (otomatik üretildi: scripts/aggregate.py)


## reach — birincil metrik: başarı (3 cm)

| algoritma | n tohum | adım | DR=0 | DR=0.5 | DR=1 | ort. mesafe DR=0 (cm) | eğitim (dk) | ilk ≥%50 (adım) | eşit adımda eğri* |
|---|---|---|---|---|---|---|---|---|---|
| SAC | 5 | 60000 | 0.49 ± 0.11 | 0.53 ± 0.09 | 0.57 ± 0.06 | 21.5 ± 1.3 | 12.2 | 60000 (3/5) | 0.47 ± 0.09 |
| TD3 | 5 | 60000 | 0.15 ± 0.03 | 0.22 ± 0.05 | 0.24 ± 0.06 | 23.0 ± 2.0 | 7.5 | ulaşılamadı (0/5) | 0.10 ± 0.10 |
| PPO | 5 | 300000 | 0.29 ± 0.10 | 0.38 ± 0.12 | 0.41 ± 0.11 | 30.8 ± 3.4 | 2.4 | ulaşılamadı (0/5) | 0.04 ± 0.04 |
| **klasik-IK+RRTConnect** (öğrenme yok) | – | – | 1.00 | 1.00 | 0.99 | 48.0 | ~0 | – | – |
| klasik-DLS-hız | – | – | 0.83 | 0.90 | 0.90 | 39.6 | ~0 | – | – |

*eşit adımda eğri: aynı ortam-adımı bütçesinde (off-policy bütçesi) 20 bölümlük değerlendirme noktası; PPO'nun 5x fazla adımla kazandığı yanılgısını önlemek için.


İkili karşılaştırma (DR=0 birincil metrik; Welch p / Mann-Whitney p):

- SAC vs TD3: 0.49 vs 0.15, p=0.002 / 0.008 → anlamlı
- SAC vs PPO: 0.49 vs 0.29, p=0.018 / 0.032 → anlamlı
- TD3 vs PPO: 0.15 vs 0.29, p=0.034 / 0.075 → ANLAMLI DEĞİL
- SAC vs klasik (klasik-IK+RRTConnect): RL 0.49 ± 0.11 vs klasik 1.00
- TD3 vs klasik (klasik-IK+RRTConnect): RL 0.15 ± 0.03 vs klasik 1.00
- PPO vs klasik (klasik-IK+RRTConnect): RL 0.29 ± 0.10 vs klasik 1.00

## obstacle — birincil metrik: çarpışmasız başarı

| algoritma | n tohum | adım | DR=0 | DR=0.5 | DR=1 | ort. mesafe DR=0 (cm) | eğitim (dk) | ilk ≥%50 (adım) | eşit adımda eğri* |
|---|---|---|---|---|---|---|---|---|---|
| SAC | 5 | 80000 | 0.09 ± 0.04 | 0.16 ± 0.04 | 0.21 ± 0.06 | 23.0 ± 1.1 | 17.0 | ulaşılamadı (0/5) | 0.09 ± 0.04 |
| TD3 | 5 | 80000 | 0.01 ± 0.01 | 0.02 ± 0.02 | 0.03 ± 0.02 | 45.5 ± 6.4 | 10.4 | ulaşılamadı (0/5) | 0.01 ± 0.02 |
| PPO | 5 | 400000 | 0.33 ± 0.10 | 0.34 ± 0.10 | 0.35 ± 0.11 | 31.1 ± 3.0 | 3.4 | ulaşılamadı (0/5) | 0.02 ± 0.04 |
| **klasik-IK+RRTConnect** (öğrenme yok) | – | – | 1.00 | 0.99 | 0.99 | 58.4 | ~0 | – | – |
| klasik-DLS-hız | – | – | 0.46 | 0.34 | 0.38 | 49.4 | ~0 | – | – |

*eşit adımda eğri: aynı ortam-adımı bütçesinde (off-policy bütçesi) 20 bölümlük değerlendirme noktası; PPO'nun 5x fazla adımla kazandığı yanılgısını önlemek için.


İkili karşılaştırma (DR=0 birincil metrik; Welch p / Mann-Whitney p):

- SAC vs TD3: 0.09 vs 0.01, p=0.009 / 0.012 → anlamlı
- SAC vs PPO: 0.09 vs 0.33, p=0.003 / 0.008 → anlamlı
- TD3 vs PPO: 0.01 vs 0.33, p=0.002 / 0.012 → anlamlı
- SAC vs klasik (klasik-IK+RRTConnect): RL 0.09 ± 0.04 vs klasik 1.00
- TD3 vs klasik (klasik-IK+RRTConnect): RL 0.01 ± 0.01 vs klasik 1.00
- PPO vs klasik (klasik-IK+RRTConnect): RL 0.33 ± 0.10 vs klasik 1.00

## track — birincil metrik: kuyruk başarısı (<5 cm)

| algoritma | n tohum | adım | DR=0 | DR=0.5 | DR=1 | ort. mesafe DR=0 (cm) | eğitim (dk) | ilk ≥%50 (adım) | eşit adımda eğri* |
|---|---|---|---|---|---|---|---|---|---|
| SAC | 5 | 80000 | 0.08 ± 0.03 | 0.07 ± 0.03 | 0.06 ± 0.02 | 17.3 ± 1.7 | 17.1 | ulaşılamadı (0/5) | 0.08 ± 0.04 |
| TD3 | 5 | 80000 | 0.01 ± 0.01 | 0.01 ± 0.00 | 0.01 ± 0.00 | 33.6 ± 4.1 | 10.5 | ulaşılamadı (0/5) | 0.01 ± 0.00 |
| PPO | 5 | 400000 | 0.03 ± 0.02 | 0.02 ± 0.02 | 0.02 ± 0.01 | 40.5 ± 4.0 | 3.5 | ulaşılamadı (0/5) | 0.01 ± 0.01 |
| **klasik-DLS-hız** (öğrenme yok) | – | – | 0.90 | 0.88 | 0.88 | 8.4 | ~0 | – | – |

*eşit adımda eğri: aynı ortam-adımı bütçesinde (off-policy bütçesi) 20 bölümlük değerlendirme noktası; PPO'nun 5x fazla adımla kazandığı yanılgısını önlemek için.


İkili karşılaştırma (DR=0 birincil metrik; Welch p / Mann-Whitney p):

- SAC vs TD3: 0.08 vs 0.01, p=0.005 / 0.008 → anlamlı
- SAC vs PPO: 0.08 vs 0.03, p=0.011 / 0.016 → anlamlı
- TD3 vs PPO: 0.01 vs 0.03, p=0.102 / 0.151 → ANLAMLI DEĞİL
- SAC vs klasik (klasik-DLS-hız): RL 0.08 ± 0.03 vs klasik 0.90
- TD3 vs klasik (klasik-DLS-hız): RL 0.01 ± 0.01 vs klasik 0.90
- PPO vs klasik (klasik-DLS-hız): RL 0.03 ± 0.02 vs klasik 0.90
