"""Atomik dosya/dizin yazımı için TEK yardımcı (docs/MIMARI.md §7 R4, T4).

Kural: hedef dosya ya ESKİ içeriğiyle ya YENİ içeriğiyle görünür, yarım yazılmış hâlde ASLA. Bunun için
içerik önce aynı dizindeki geçici dosyaya yazılır, sonra TEK ``os.replace`` ile hedefe geçirilir
(aynı dizin = aynı dosya sistemi, bu yüzden değiştirme atomiktir). Dizinler için de aynı: önce
yanındaki geçici dizinde kurulur, sonra ``replace_dir`` ile yerine gelir.

Bu modül davranış EKLEMEZ: dört eski yazım noktasının (program_store, parts_store, settings, profile_view)
küçük farkları parametre olarak korunur (geçici dosya adı, üst dizini açma, fsync, hatada geçici dosyayı
silme). Varsayılanlar en güvenli biçimdir (benzersiz geçici ad + fsync + hatada temizlik); eski çağıranlar
farkını kendi çağrısında açıkça yazar. ``os.replace``/``os.fsync`` her zaman ``os.`` önekiyle çağrılır
(testler bunları yama olarak izler).
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Optional, Union

PathLike = Union[str, 'os.PathLike[str]']


def write_bytes_atomic(path: PathLike, data: bytes, *, prefix: str = '.tmp-', suffix: str = '.part',
                       tmp_name: Optional[str] = None, make_parents: bool = True, fsync: bool = True,
                       cleanup: bool = True) -> None:
    """``data``'yı ``path``'e atomik yazar; hata olursa ``path`` DEĞİŞMEZ.

    * ``prefix``/``suffix``: benzersiz geçici dosyanın adı (``tempfile.mkstemp``, aynı dizinde, mod 0600).
    * ``tmp_name``: verilirse benzersiz ad yerine bu SABİT ad kullanılır (aynı dizinde, düz dosya adı; çağıran
      aynı hedefe eşzamanlı yazmayı kendi kilidiyle engellemelidir). Dosya normal ``open`` ile açılır.
    * ``make_parents``: üst dizin yoksa oluştur.
    * ``fsync``: değiştirmeden önce veriyi diske zorla.
    * ``cleanup``: yazım/değiştirme hata verirse geçici dosyayı sil (hata yine fırlatılır).
    """
    path = Path(path)
    if make_parents:
        path.parent.mkdir(parents=True, exist_ok=True)
    fd = None
    if tmp_name is None:
        fd, name = tempfile.mkstemp(dir=path.parent, prefix=prefix, suffix=suffix)
        tmp = Path(name)
    else:
        if Path(tmp_name).name != tmp_name or tmp_name in ('', '.', '..'):
            raise ValueError('tmp_name düz bir dosya adı olmalı')
        tmp = path.with_name(tmp_name)
    try:
        with (os.fdopen(fd, 'wb') if fd is not None else open(tmp, 'wb')) as f:
            f.write(data)
            f.flush()
            if fsync:
                os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        if cleanup:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        raise


def write_text_atomic(path: PathLike, text: str, *, newline: Optional[str] = None, **options) -> None:
    """Metni UTF-8 olarak ``write_bytes_atomic`` ile yazar; ``options`` oraya geçer.

    ``newline`` ``open(..., 'w', newline=...)`` ile aynı anlamdadır: ``None`` → ``\\n`` satır sonları
    ``os.linesep`` olur (Windows'ta CRLF); ``''``/``'\\n'`` → çevrilmez; ``'\\r'``/``'\\r\\n'`` → o olur.
    """
    eol = os.linesep if newline is None else (newline or '\n')
    write_bytes_atomic(path, (text if eol == '\n' else text.replace('\n', eol)).encode('utf-8'), **options)


def replace_dir(src: PathLike, dst: PathLike) -> None:
    """Hazırlanmış ``src`` dizinini TEK ``os.replace`` ile ``dst`` adına geçirir (yarım dizin görünmez).

    ``src`` ile ``dst`` aynı dosya sisteminde olmalı. ``dst`` yoksa yeni ad olur; ``dst`` varsa sonuç
    ``os.replace``'in platform kuralıdır (POSIX: yalnız BOŞ dizinin üstüne yazar; Windows: hata verir) ve
    hatada ``src`` ile ``dst`` dokunulmaz kalır. Hata durumunda ``src``'yi silmek ÇAĞIRANIN işidir
    (hazırlık dizinini o kurmuştur). Aynı çağrı bir dizini "görünmez yapmak" için de kullanılır
    (``replace_dir(d, d.parent / '.deleted-…')`` sonra ``shutil.rmtree``).
    """
    src, dst = Path(src), Path(dst)
    if not src.is_dir():
        raise NotADirectoryError(f'dizin değil: {src.name}')
    os.replace(src, dst)
