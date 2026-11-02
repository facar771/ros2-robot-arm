"""Tk/ttk masaüstü penceresi (ince): ayarlar, Başlat/Durdur, adresler + QR + PIN, robot durumu,
güvenlik duvarı düğmeleri, olay günlüğü. Mantık ``service`` / ``settings`` / ``firewall``'dadır;
buradakiler yalnızca çizim ve olay bağlamadır (ileride PySide6'ya geçilirse yalnızca bu dosya değişir).

Güvenlik duvarı: düğmeler **hiçbir şeyi kendiliğinden çalıştırmaz**; önce tam komutu ve kapsamı gösteren
bir onay kutusu çıkar, "Hayır" hiçbir şey yapmaz.
"""
from __future__ import annotations

import base64
import os
import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any, Callable, List, Optional

from control_hub import __version__, paths
from control_hub.desktop import firewall as fw_mod
from control_hub.desktop import settings as settings_mod
from control_hub.desktop.service import HubService, ServiceError

PAD = 8


def qr_png_base64(url: str, scale: int = 4) -> Optional[str]:
    """QR kodu PNG (base64). segno PIL'siz yazar; yoksa/başarısızsa None (QR yalnızca kolaylık)."""
    try:
        import io

        import segno
        buf = io.BytesIO()
        segno.make(url, error='m').save(buf, kind='png', scale=scale, border=2)
        return base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return None


def open_folder(path: Path) -> None:
    if sys.platform == 'win32':
        os.startfile(str(path))                        # type: ignore[attr-defined]  # noqa: S606
    elif sys.platform == 'darwin':
        os.system(f'open "{path}"')                    # noqa: S605
    else:
        os.system(f'xdg-open "{path}" >/dev/null 2>&1 &')   # noqa: S605


class HubWindow:
    def __init__(self, root: tk.Tk, *, settings: Optional[settings_mod.Settings] = None,
                 service_factory: Callable[..., HubService] = HubService,
                 elevate: Callable[[List[str]], bool] = fw_mod.run_elevated,
                 rule_exists: Callable[[], Optional[bool]] = fw_mod.rule_exists,
                 ask: Callable[[str, str], bool] = lambda t, m: messagebox.askyesno(t, m, icon='warning'),
                 info: Callable[[str, str], None] = messagebox.showinfo,
                 error: Callable[[str, str], None] = messagebox.showerror,
                 open_dir: Callable[[Path], None] = open_folder,
                 persist: bool = True):
        self.root = root
        self.service_factory, self.elevate, self.rule_exists = service_factory, elevate, rule_exists
        self.ask, self.info, self.error, self.open_dir = ask, info, error, open_dir
        self.persist = persist
        self.service: Optional[HubService] = None
        self._seq = 0
        self._photo: Any = None
        self._tick_job: Optional[str] = None
        s = settings or settings_mod.load()
        root.title(f'Robot Kontrol Merkezi {__version__}')
        root.minsize(480, 560)
        self.v_robot = tk.StringVar(value=s.robot_url)
        self.v_fake = tk.BooleanVar(value=s.fake)
        self.v_port = tk.StringVar(value=str(s.port))
        self.v_pin = tk.StringVar(value=s.pin)
        self.v_remember = tk.BooleanVar(value=s.remember_pin)
        self.v_mode = tk.StringVar(value=s.access_mode)
        self._build()
        root.protocol('WM_DELETE_WINDOW', self.close)

    # --- iskelet -------------------------------------------------------------------------------
    def _build(self) -> None:
        f = ttk.Frame(self.root, padding=PAD)
        f.pack(fill='both', expand=True)
        ttk.Label(f, text='Robot Kontrol Merkezi', font=('', 15, 'bold')).pack(anchor='w')

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

        bar = ttk.Frame(f)
        bar.pack(fill='x', pady=PAD)
        self.b_start = ttk.Button(bar, text='Başlat', command=self.start)
        self.b_start.pack(side='left')
        self.b_stop = ttk.Button(bar, text='Durdur', command=self.stop, state='disabled')
        self.b_stop.pack(side='left', padx=PAD)
        self.b_cfg = ttk.Button(bar, text='Senaryo/arayüz dosyalarını aç…', command=self.open_config)
        self.b_cfg.pack(side='right')

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
        self.l_qr = ttk.Label(self.conn)
        self.l_qr.pack(anchor='w', pady=(4, 0))

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

        logb = ttk.LabelFrame(f, text='Olaylar', padding=4)
        logb.pack(fill='both', expand=True, pady=(PAD, 0))
        self.t_log = tk.Text(logb, height=6, wrap='word', state='disabled')
        self.t_log.pack(fill='both', expand=True)
        self._set_running(False)
        self.refresh_firewall_status()

    # --- ayarlar -------------------------------------------------------------------------------
    def read_settings(self) -> settings_mod.Settings:
        try:
            port = int(self.v_port.get().strip())
        except ValueError:
            port = -1
        return settings_mod.Settings(
            robot_url=self.v_robot.get().strip() or settings_mod.DEFAULT_ROBOT_URL,
            fake=bool(self.v_fake.get()), port=port, access_mode=self.v_mode.get(),
            pin=self.v_pin.get().strip(), remember_pin=bool(self.v_remember.get()))

    def _set_running(self, running: bool) -> None:
        for w in (self.e_robot, self.c_fake, self.e_port, self.e_pin, self.c_remember, self.b_start):
            w.state(['disabled'] if running else ['!disabled'])
        self.cb_mode.state(['disabled'] if running else ['!disabled', 'readonly'])
        self.b_stop.state(['!disabled'] if running else ['disabled'])

    # --- başlat/durdur -------------------------------------------------------------------------
    def start(self) -> None:
        s = self.read_settings()
        problems = s.problems()
        if problems:
            self.error('Ayarlar geçersiz', '\n'.join(problems))
            return
        svc = self.service_factory(s)
        try:
            svc.start()
        except ServiceError as exc:
            self.error('Hub başlatılamadı', str(exc))
            return
        self.service = svc
        if self.persist:
            try:
                settings_mod.save(s)
            except OSError:
                pass                                   # ayar kaydedilemese de Hub çalışsın
        self._seq = 0
        self._set_running(True)
        urls = svc.urls()
        self.l_status.config(text='Hub çalışıyor.')
        self.l_local.config(text=f'Bilgisayar: {urls["local"]}')
        self.l_lan.config(text=f'Telefon (aynı Wi-Fi): {urls["lan"]}')
        pin = svc.pin
        self.l_pin.config(text=f'PIN: {pin}' if pin else f'Erişim kipi: {s.access_mode} (PIN yok)')
        b64 = qr_png_base64(urls['lan'])
        self._photo = tk.PhotoImage(data=b64) if b64 else None
        self.l_qr.config(image=self._photo or '', text='' if self._photo else '(QR üretilemedi)')
        self._log('Hub başlatıldı.')
        self._tick()

    def stop(self) -> None:
        if self._tick_job is not None:
            self.root.after_cancel(self._tick_job)
            self._tick_job = None
        if self.service is not None:
            self.service.stop()
            self.service = None
        self._set_running(False)
        self.l_status.config(text='Hub kapalı.')
        for lab in (self.l_local, self.l_lan, self.l_pin, self.l_robot):
            lab.config(text='')
        self.l_qr.config(image='', text='')
        self._photo = None
        self._log('Hub durduruldu.')

    def close(self) -> None:
        self.stop()
        self.root.destroy()

    # --- canlı durum ---------------------------------------------------------------------------
    def _tick(self) -> None:
        svc = self.service
        if svc is None or svc.hub is None:
            return
        snap = svc.robot_snapshot()
        if snap.get('name') == 'fake':
            self.l_robot.config(text='Robot: SAHTE (deneme)')
        elif snap.get('connected') and snap.get('compatible'):
            self.l_robot.config(text=f"Robot: bağlı (API v{snap.get('api_version')}, mod {snap.get('mode')})")
        else:
            self.l_robot.config(text=f"Robot: bağlı değil — {snap.get('problem') or 'yanıt yok'}")
        for ev in svc.hub.events.since(self._seq):
            self._seq = ev.seq
            self._log(f'[{ev.level}] {ev.message}')
        self._tick_job = self.root.after(1000, self._tick)

    def _log(self, line: str) -> None:
        self.t_log.config(state='normal')
        self.t_log.insert('end', line + '\n')
        self.t_log.see('end')
        self.t_log.config(state='disabled')

    # --- güvenlik duvarı (HER ZAMAN onayla) ----------------------------------------------------
    def _port(self) -> int:
        return self.service.port if self.service else self.read_settings().port

    def refresh_firewall_status(self) -> None:
        exists = self.rule_exists()
        text = {True: 'Kural: VAR (telefon erişimine izin verilmiş).', False: 'Kural: yok.',
                None: 'Kural durumu bilinmiyor (Windows değil ya da okunamadı).'}[exists]
        self.l_fw.config(text=text)

    def firewall_add(self) -> None:
        try:
            port = self._port()
            args = fw_mod.add_rule_args(port)
            text = fw_mod.confirmation_text(port) + '\n\nKomut:\n' + fw_mod.netsh_command_line(args)
        except fw_mod.FirewallError as exc:
            self.error('Güvenlik duvarı', str(exc))
            return
        if not self.ask('Güvenlik duvarı izni', text):
            self._log('Güvenlik duvarı: kullanıcı reddetti, hiçbir şey eklenmedi.')
            return
        ok = self.elevate(args)
        self._log('Güvenlik duvarı: yönetici izni istendi.' if ok else
                  'Güvenlik duvarı: yükseltme yapılamadı (UAC reddedildi ya da Windows değil).')
        self.root.after(2500, self.refresh_firewall_status)

    def firewall_remove(self) -> None:
        args = fw_mod.delete_rule_args()
        if not self.ask('Güvenlik duvarı kuralını kaldır',
                        f'"{fw_mod.RULE_NAME}" kuralı kaldırılacak; telefon artık bağlanamaz.\n\n'
                        'Komut:\n' + fw_mod.netsh_command_line(args) + '\n\nDevam edilsin mi?'):
            return
        ok = self.elevate(args)
        self._log('Güvenlik duvarı: kaldırma için yönetici izni istendi.' if ok else
                  'Güvenlik duvarı: yükseltme yapılamadı.')
        self.root.after(2500, self.refresh_firewall_status)

    def firewall_copy(self) -> None:
        try:
            cmd = fw_mod.powershell_add(self._port())
        except fw_mod.FirewallError as exc:
            self.error('Güvenlik duvarı', str(exc))
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(cmd)
        self.info('Komut kopyalandı', 'Yönetici olarak açılmış PowerShell\'e yapıştır:\n\n' + cmd)

    def open_config(self) -> None:
        try:
            dest = paths.export_default_config()
        except OSError as exc:
            self.error('Yapılandırma', f'Klasör hazırlanamadı: {exc}')
            return
        self.info('Yapılandırma klasörü',
                  f'Varsayılan dosyalar buraya kopyalandı (varolanlar ezilmedi):\n{dest}\n\n'
                  'Dosyaları düzenleyip Hub\'ı yeniden başlat; bu klasör gömülü varsayılanın yerine geçer.')
        self.open_dir(dest)


def run_gui() -> int:
    root = tk.Tk()
    HubWindow(root)
    root.mainloop()
    return 0
