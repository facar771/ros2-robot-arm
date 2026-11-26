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
from control_hub.desktop.gui_sections import PAD, SectionsMixin
from control_hub.desktop.service import HubService, ServiceError
from control_hub.hw import list_serial_ports


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


class HubWindow(SectionsMixin):
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
        self.v_serial = tk.StringVar(value=s.serial_port)
        self.v_baud = tk.StringVar(value=str(s.serial_baud))
        self.v_bport = tk.StringVar(value=str(s.bridge_port))
        self.v_bopen = tk.BooleanVar(value=s.bridge_open)
        self.v_aprov = tk.StringVar(value=s.agent_provider)
        self.v_amodel = tk.StringVar(value=s.agent_model)
        self.v_aurl = tk.StringVar(value=s.agent_base_url)
        self.v_alimit = tk.StringVar(value=str(s.agent_daily_limit))
        self.v_amot = tk.StringVar(value=str(s.agent_max_motions))
        self.v_adry = tk.BooleanVar(value=s.agent_dry_run)
        self.v_akey = tk.StringVar(value='')              # ASLA settings.json'a okunmaz
        self.v_aremember = tk.BooleanVar(value=False)
        self.keystore: Any = None                         # ilk kullanımda açılır (KeyStore)
        self._build()
        root.protocol('WM_DELETE_WINDOW', self.close)

    # --- iskelet -------------------------------------------------------------------------------
    def _build(self) -> None:
        f = ttk.Frame(self.root, padding=PAD)
        f.pack(fill='both', expand=True)
        ttk.Label(f, text='Robot Kontrol Merkezi', font=('', 15, 'bold')).pack(anchor='w')
        for section in (self._build_settings, self._build_bridge, self._build_agent, self._build_actions,
                        self._build_connection, self._build_firewall, self._build_events):
            section(f)                                     # sıra = ekran sırası (gui_sections.py)
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
            pin=self.v_pin.get().strip(), remember_pin=bool(self.v_remember.get()),
            serial_port=self.v_serial.get().strip(), serial_baud=self._int(self.v_baud, -1),
            bridge_port=self._int(self.v_bport, -1), bridge_open=bool(self.v_bopen.get()),
            agent_provider=self.v_aprov.get(), agent_model=self.v_amodel.get().strip(),
            agent_base_url=self.v_aurl.get().strip(), agent_daily_limit=self._int(self.v_alimit, -1),
            agent_max_motions=self._int(self.v_amot, -1), agent_dry_run=bool(self.v_adry.get()))

    @staticmethod
    def _int(var: tk.StringVar, default: int) -> int:
        try:
            return int(var.get().strip())
        except ValueError:
            return default

    # --- ajan anahtarı (yalnızca bu pencere ve 127.0.0.1; settings.json'a YAZILMAZ) ---------------
    def _keys(self) -> Any:
        if self.keystore is None:
            from control_hub.agent import KeyStore
            self.keystore = KeyStore()
        return self.keystore

    def _key_text(self) -> str:
        status = self._keys().status()
        def word(v: dict) -> str:
            if not v['set']:
                return 'yok'
            return 'ayarlı (hatırlanıyor)' if v['remembered'] else 'ayarlı (yalnızca bellekte)'
        parts = [f'{p}: {word(v)}' for p, v in status.items()]
        return 'Anahtar — ' + ', '.join(parts)

    def agent_set_key(self) -> None:
        provider = self.v_aprov.get()
        if provider == 'offline':
            self.info('Anahtar gerekmez', 'Çevrimdışı kural çözücü bir LLM değildir; anahtar istemez.')
            return
        try:
            remembered, warning = self._keys().set(provider, self.v_akey.get(), bool(self.v_aremember.get()))
        except ValueError as exc:
            self.error('Anahtar kabul edilmedi', str(exc))
            return
        finally:
            self.v_akey.set('')                           # anahtar kutuda kalmasın
        self.l_akey.config(text=self._key_text())
        if warning:
            self.info('Anahtar', warning)

    def agent_clear_key(self) -> None:
        provider = self.v_aprov.get()
        if provider == 'offline':
            return
        self._keys().clear(provider, forget=True)
        self.l_akey.config(text=self._key_text())

    def refresh_ports(self) -> None:
        self.cb_serial.config(values=list_serial_ports())

    def _set_running(self, running: bool) -> None:
        for w in (self.e_robot, self.c_fake, self.e_port, self.e_pin, self.c_remember, self.b_start,
                  self.e_baud, self.e_bport, self.c_bopen, self.b_ports):
            w.state(['disabled'] if running else ['!disabled'])
        self.cb_mode.state(['disabled'] if running else ['!disabled', 'readonly'])
        self.cb_serial.state(['disabled'] if running else ['!disabled'])
        for w in (self.e_amodel, self.e_aurl, self.e_alimit, self.e_amot, self.c_adry):
            w.state(['disabled'] if running else ['!disabled'])
        self.cb_aprov.state(['disabled'] if running else ['!disabled', 'readonly'])
        self.b_stop.state(['!disabled'] if running else ['disabled'])

    # --- başlat/durdur -------------------------------------------------------------------------
    def start(self) -> None:
        s = self.read_settings()
        problems = s.problems()
        if problems:
            self.error('Ayarlar geçersiz', '\n'.join(problems))
            return
        svc = self.service_factory(s)
        if self.keystore is not None:                     # pencerede girilen anahtar yeniden başlatmada kalır
            svc.keystore = self.keystore
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
        if svc.bridge_token:
            self._log(f'Köprü belirteci (token): {svc.bridge_token}  (istemci: HUBBRIDGE-AUTH <token>)')
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
        for lab in (self.l_local, self.l_lan, self.l_pin, self.l_robot, self.l_bridge):
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
        self.l_bridge.config(text=self._bridge_text(svc))
        for ev in svc.hub.events.since(self._seq):
            self._seq = ev.seq
            self._log(f'[{ev.level}] {ev.message}')
        self._tick_job = self.root.after(1000, self._tick)

    @staticmethod
    def _bridge_text(svc: HubService) -> str:
        br = svc.hub.bridge if svc.hub else None
        if br is None:
            return ''
        st = br.status()
        com = 'seri BAĞLI' if st['serial_connected'] else f"seri YOK ({st['last_error'] or '...'})"
        cli = 'istemci var' if st['client_connected'] else 'istemci yok'
        return f"ESP32 köprüsü: {com}, {cli} — tcp://{st['listen']}"

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
