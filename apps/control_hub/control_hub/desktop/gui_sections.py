"""Tk penceresinin BÖLÜMLERİ: ``HubWindow._build`` bunları ekran sırasıyla çağırır (R10: 140 satırlık
tek işlev yerine bölüm başına bir yöntem; böylece gui.py 400 satır sınırının altına indi).

Burada yalnızca widget kurulur: Tk değişkenleri (``v_*``) ve olay işleyicileri (``start``, ``stop``,
``firewall_add`` ...) ``HubWindow``'dadır. Kurma sırası = ``pack`` sırası = ekrandaki sıra = Tab sırası;
sırayı değiştirmek yerleşimi değiştirir.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from control_hub.desktop import settings as settings_mod
from control_hub.hw import list_serial_ports

PAD = 8


class SectionsMixin:
    """``HubWindow`` ile birlikte kullanılır; ``self`` üzerinde widget alanlarını kurar."""

    def _build_settings(self, f: ttk.Frame) -> None:
        """Ayarlar kutusu: robot adresi, sahte robot, port, PIN, erişim kipi."""
        box = ttk.LabelFrame(f, text='Ayarlar', padding=PAD)
        box.pack(fill='x', pady=(PAD, 0))
        box.columnconfigure(1, weight=1)
        ttk.Label(box, text='Robot adresi').grid(row=0, column=0, sticky='w')
        self.e_robot = ttk.Entry(box, textvariable=self.v_robot)
        self.e_robot.grid(row=0, column=1, columnspan=2, sticky='ew', pady=2)
        self.c_fake = ttk.Checkbutton(box, text='Sahte robot (robot yokken arayüzü dene)',
                                      variable=self.v_fake)
        self.c_fake.grid(row=1, column=1, columnspan=2, sticky='w')
        ttk.Label(box, text='Port').grid(row=2, column=0, sticky='w')
        self.e_port = ttk.Entry(box, textvariable=self.v_port, width=8)
        self.e_port.grid(row=2, column=1, sticky='w', pady=2)
        ttk.Label(box, text='PIN').grid(row=3, column=0, sticky='w')
        self.e_pin = ttk.Entry(box, textvariable=self.v_pin, width=14)
        self.e_pin.grid(row=3, column=1, sticky='w', pady=2)
        ttk.Label(box, text='(boş = her açılışta rastgele)', foreground='gray').grid(
            row=3, column=2, sticky='w', padx=PAD)
        self.c_remember = ttk.Checkbutton(
            box, text='PIN\'i hatırla (ayar dosyasına düz metin yazılır)', variable=self.v_remember)
        self.c_remember.grid(row=4, column=1, columnspan=2, sticky='w')
        ttk.Label(box, text='Erişim kipi').grid(row=5, column=0, sticky='w')
        self.cb_mode = ttk.Combobox(box, textvariable=self.v_mode, values=settings_mod.ACCESS_MODES,
                                    state='readonly', width=10)
        self.cb_mode.grid(row=5, column=1, sticky='w', pady=2)

    def _build_bridge(self, f: ttk.Frame) -> None:
        """ESP32 donanım köprüsü kutusu (isteğe bağlı): seri port, baud, köprü portu."""
        hw = ttk.LabelFrame(f, text='ESP32 donanım köprüsü (USB-seri ↔ TCP) — isteğe bağlı', padding=PAD)
        hw.pack(fill='x', pady=(PAD, 0))
        hw.columnconfigure(1, weight=1)
        ttk.Label(hw, text='Seri port').grid(row=0, column=0, sticky='w')
        self.cb_serial = ttk.Combobox(hw, textvariable=self.v_serial, values=list_serial_ports())
        self.cb_serial.grid(row=0, column=1, sticky='ew', pady=2)
        self.b_ports = ttk.Button(hw, text='Yenile', width=8, command=self.refresh_ports)
        self.b_ports.grid(row=0, column=2, padx=(PAD, 0))
        ttk.Label(hw, text='Baud / Köprü portu').grid(row=1, column=0, sticky='w')
        rowf = ttk.Frame(hw)
        rowf.grid(row=1, column=1, columnspan=2, sticky='w')
        self.e_baud = ttk.Entry(rowf, textvariable=self.v_baud, width=9)
        self.e_baud.pack(side='left')
        self.e_bport = ttk.Entry(rowf, textvariable=self.v_bport, width=7)
        self.e_bport.pack(side='left', padx=PAD)
        self.c_bopen = ttk.Checkbutton(
            hw, text='Docker/başka makine erişsin (0.0.0.0 + token)', variable=self.v_bopen)
        self.c_bopen.grid(row=2, column=1, columnspan=2, sticky='w')
        ttk.Label(hw, foreground='gray', wraplength=430, justify='left', text=(
            'Boş bırakırsan köprü açılmaz. Başlat\'a basınca port açılır; Docker\'daki ROS düğümü '
            'host.docker.internal:<köprü portu> ile bağlanır.')).grid(row=3, column=0, columnspan=3,
                                                                       sticky='w')

    def _build_agent(self, f: ttk.Frame) -> None:
        """Yapay zekâ ajanı kutusu (isteğe bağlı): sağlayıcı/model, sınırlar, API anahtarı."""
        ag = ttk.LabelFrame(f, text='Yapay zekâ ajanı (sohbet paneli) — isteğe bağlı', padding=PAD)
        ag.pack(fill='x', pady=(PAD, 0))
        ag.columnconfigure(1, weight=1)
        ttk.Label(ag, text='Sağlayıcı / model').grid(row=0, column=0, sticky='w')
        rowa = ttk.Frame(ag)
        rowa.grid(row=0, column=1, columnspan=2, sticky='ew', pady=2)
        self.cb_aprov = ttk.Combobox(rowa, textvariable=self.v_aprov, state='readonly', width=10,
                                     values=('anthropic', 'openai', 'offline'))
        self.cb_aprov.pack(side='left')
        self.e_amodel = ttk.Entry(rowa, textvariable=self.v_amodel)
        self.e_amodel.pack(side='left', fill='x', expand=True, padx=(PAD, 0))
        ttk.Label(ag, text='OpenAI uç adresi').grid(row=1, column=0, sticky='w')
        self.e_aurl = ttk.Entry(ag, textvariable=self.v_aurl)
        self.e_aurl.grid(row=1, column=1, columnspan=2, sticky='ew', pady=2)
        ttk.Label(ag, text='Günlük sınır / tur başına hareket').grid(row=2, column=0, sticky='w')
        rowl = ttk.Frame(ag)
        rowl.grid(row=2, column=1, columnspan=2, sticky='w')
        self.e_alimit = ttk.Entry(rowl, textvariable=self.v_alimit, width=7)
        self.e_alimit.pack(side='left')
        self.e_amot = ttk.Entry(rowl, textvariable=self.v_amot, width=5)
        self.e_amot.pack(side='left', padx=PAD)
        self.c_adry = ttk.Checkbutton(rowl, text='Kuru çalıştırma (robota gitmez)', variable=self.v_adry)
        self.c_adry.pack(side='left', padx=PAD)
        ttk.Label(ag, text='API anahtarı').grid(row=3, column=0, sticky='w')
        self.e_akey = ttk.Entry(ag, textvariable=self.v_akey, show='•')
        self.e_akey.grid(row=3, column=1, sticky='ew', pady=2)
        self.b_akey = ttk.Button(ag, text='Ayarla', width=8, command=self.agent_set_key)
        self.b_akey.grid(row=3, column=2, padx=(PAD, 0))
        self.c_aremember = ttk.Checkbutton(
            ag, text='Beni hatırla (Windows Credential Manager; ayar dosyasına YAZILMAZ)',
            variable=self.v_aremember)
        self.c_aremember.grid(row=4, column=1, sticky='w')
        self.b_aclear = ttk.Button(ag, text='Sil', width=8, command=self.agent_clear_key)
        self.b_aclear.grid(row=4, column=2, padx=(PAD, 0))
        self.l_akey = ttk.Label(ag, text='Anahtar: girilmedi', foreground='gray')
        self.l_akey.grid(row=5, column=0, columnspan=3, sticky='w')

    def _build_actions(self, f: ttk.Frame) -> None:
        """Başlat / Durdur / yapılandırma klasörünü aç düğme çubuğu."""
        bar = ttk.Frame(f)
        bar.pack(fill='x', pady=PAD)
        self.b_start = ttk.Button(bar, text='Başlat', command=self.start)
        self.b_start.pack(side='left')
        self.b_stop = ttk.Button(bar, text='Durdur', command=self.stop, state='disabled')
        self.b_stop.pack(side='left', padx=PAD)
        self.b_cfg = ttk.Button(bar, text='Senaryo/arayüz dosyalarını aç…', command=self.open_config)
        self.b_cfg.pack(side='right')

    def _build_connection(self, f: ttk.Frame) -> None:
        """Bağlantı kutusu: adresler, PIN, robot/köprü durumu, QR (Başlat'tan sonra dolar)."""
        self.conn = ttk.LabelFrame(f, text='Bağlantı', padding=PAD)
        self.conn.pack(fill='x')
        self.l_status = ttk.Label(self.conn, text='Hub kapalı.')
        self.l_status.pack(anchor='w')
        self.l_local = ttk.Label(self.conn, text='', cursor='hand2')
        self.l_local.pack(anchor='w')
        self.l_lan = ttk.Label(self.conn, text='')
        self.l_lan.pack(anchor='w')
        self.l_pin = ttk.Label(self.conn, text='', font=('', 14, 'bold'))
        self.l_pin.pack(anchor='w', pady=(4, 0))
        self.l_robot = ttk.Label(self.conn, text='')
        self.l_robot.pack(anchor='w')
        self.l_bridge = ttk.Label(self.conn, text='')
        self.l_bridge.pack(anchor='w')
        self.l_qr = ttk.Label(self.conn)
        self.l_qr.pack(anchor='w', pady=(4, 0))

    def _build_firewall(self, f: ttk.Frame) -> None:
        """Windows güvenlik duvarı kutusu (düğmeler yalnızca onay kutusunu açar)."""
        fwb = ttk.LabelFrame(f, text='Telefon erişimi (Windows güvenlik duvarı)', padding=PAD)
        fwb.pack(fill='x', pady=(PAD, 0))
        ttk.Label(fwb, wraplength=430, justify='left', text=(
            'Telefon bağlanamıyorsa güvenlik duvarı gelen bağlantıyı engelliyordur. İzin vermek '
            'için aşağıdaki düğmeyi kullan: ne eklenecek gösterilir, onaylarsan Windows yönetici '
            'izni (UAC) ister. Kendiliğinden hiçbir şey eklenmez.')).pack(anchor='w')
        row = ttk.Frame(fwb)
        row.pack(fill='x', pady=(4, 0))
        self.b_fw_add = ttk.Button(row, text='Telefon erişimine izin ver…', command=self.firewall_add)
        self.b_fw_add.pack(side='left')
        self.b_fw_del = ttk.Button(row, text='Kuralı kaldır…', command=self.firewall_remove)
        self.b_fw_del.pack(side='left', padx=PAD)
        self.b_fw_copy = ttk.Button(row, text='Komutu kopyala', command=self.firewall_copy)
        self.b_fw_copy.pack(side='left')
        self.l_fw = ttk.Label(fwb, text='')
        self.l_fw.pack(anchor='w', pady=(4, 0))

    def _build_events(self, f: ttk.Frame) -> None:
        """Olaylar günlüğü (salt okunur metin kutusu)."""
        logb = ttk.LabelFrame(f, text='Olaylar', padding=4)
        logb.pack(fill='both', expand=True, pady=(PAD, 0))
        self.t_log = tk.Text(logb, height=6, wrap='word', state='disabled')
        self.t_log.pack(fill='both', expand=True)
