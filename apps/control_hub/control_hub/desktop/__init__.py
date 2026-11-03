"""Masaüstü uygulaması (Windows .exe): ince Tk arayüzü + çekirdeği başlatan servis.

Mantık GUI'den ayrıdır ve Linux'ta test edilir: ``settings`` (ayarlar), ``service`` (Hub'ı arka planda
çalıştırır), ``firewall`` (güvenlik duvarı komutları; KENDİLİĞİNDEN hiçbir şey eklemez),
``winconsole`` (çift tıklamada konsolu gizle), ``launcher`` (GUI mi CLI mi). ``gui`` yalnızca Tk çizer.
"""
