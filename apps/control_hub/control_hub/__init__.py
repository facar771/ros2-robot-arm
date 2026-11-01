"""Robot Kontrol Merkezi (Hub): robot senaryolarını telefondan/PC'den çalıştıran web arayüzü.

Katmanlar (alttan üste; alttakiler üsttekini bilmez):
  hub_core  GUI'siz, ROS'suz çekirdek: backend'ler, senaryo koşucusu, erişim denetimi, olay günlüğü
  server    FastAPI + WebSocket (çekirdeği HTTP'ye açar)
  web       düz HTML/CSS/JS (derleme adımı yok)
"""
__version__ = '0.1.0'
