# Görüntü modeli lisansları (tespit modelleri için not)

*Hazırlanma tarihi: 2026-11-02. Hukuki danışmanlık değildir; ticari bir üründe kullanmadan önce her modelin
**kod** ve **ağırlık** lisansını kendi sürümü için yeniden doğrula (lisanslar sürümler arasında değişebilir).
Kaynak bağlantıları sayfanın sonundadır.*

## Neden önemli
Bu depodaki `robot_vision` kodu dedektörü bir **arayüz** (`Detector`) olarak tanımlar; `OnnxDetector` sizin
getirdiğiniz herhangi bir ONNX modeli çalıştırır ve depoya **hiçbir model ağırlığı eklenmez**. Ama ticari bir
girişim planlıyorsan, hangi modeli seçtiğin lisans yükümlülüğünü belirler. Burada kod değil, **seçeceğin
modelin** lisansı belirleyicidir.

## Ultralytics YOLO (YOLOv5 / v8 / YOLO11 / YOLO26 …): **AGPL-3.0 ya da Enterprise lisans**
- Ultralytics iki lisans sunar: **AGPL-3.0** ve ücretli **Enterprise**. Eğitilmiş Ultralytics YOLO modelleri de
  varsayılan olarak AGPL-3.0 kapsamındadır (ultralytics.com/license).
- Ultralytics'in kendi lisans sayfasına göre kod, model, mimari, eğitim hattı **ya da bunlarla eğitilmiş/ince
  ayarlanmış modeller** için ya **tüm projeyi AGPL-3.0 altında açmak** ya da **Enterprise lisans almak**
  gerekir; bu, modeli sıfırdan eğitsen, hazır ağırlık kullanmasan veya yalnızca şirket içinde/Ar-Ge'de
  kullansan da geçerli diye belirtilir (ultralytics.com/request-license).
- Sonuç: kapalı kaynaklı/ticari bir ürüne (ya da onu çalıştıran sunucu yazılımına) Ultralytics modeli gömmek,
  AGPL-3.0'ın "ağ üzerinden kullanıcıya hizmet de dağıtım sayılır" yükümlülüğü veya Enterprise lisans
  gerektirir. **Ticari girişim planı varken varsayılan seçim olmamalı.**
- Aynı sınıfa giren, kaynaklarda AGPL/GPL olarak anılan diğerleri: YOLOv10 (THU-MIG; AGPL-3.0) ve YOLOv9 (GPL-3.0)
  (Roboflow model sayfası). Lisansı dosyalarından doğrula.
- TEKNOFEST/akademik deneme için AGPL yeterli olabilir; yine de ürünleşme kararından önce karar ver.

## Apache-2.0 alternatifleri (ticari kullanıma izin veren, izin verici lisans)
| Model | Lisans (kod) | Not |
|---|---|---|
| **YOLOX** (Megvii-BaseDetection/YOLOX) | **Apache-2.0** | Anchor-free YOLO; Nano…X boyutları; ONNX/TensorRT/OpenVINO dışa aktarımı var; en az uyum yükü olan "YOLO benzeri" seçenek. `OnnxDetector(decoder='yolox')` çıktı biçimi için hazır. |
| **RT-DETR** (lyuwenyu/RT-DETR; Hugging Face `PekingU/rtdetr_*`) | **Apache-2.0** | NMS gerektirmeyen DETR tipi gerçek zamanlı dedektör; model kartları Apache-2.0 belirtir. ONNX çıktı düzeni dışa aktarıma göre değişir (çoğu zaman `[N, 6]` = `xyxy`+skor+sınıf → `decoder='xyxy'`; farklıysa küçük bir sarmalayıcı gerekir). **Ultralytics paketi üzerinden RT-DETR** kullanırsan Ultralytics kodu/lisansı (AGPL-3.0) devreye girer; resmî Apache-2.0 deposunu kullan. |

**Ağırlıklar ayrı bir konudur:** önceden eğitilmiş ağırlıklar COCO gibi veri kümeleriyle eğitilmiştir; veri
kümesinin ve ağırlık dosyasının koşullarını da kontrol et. En temiz yol: Apache-2.0 kodla **kendi verinle**
eğitmek (TEKNOFEST hedefi için zaten gereken şey: renk/şekil değil, gerçek hedef görüntüleri).

## Bu depodaki durum
- `OnnxDetector` kodu ve testleri yalnızca **sentetik** bir ONNX modeliyle sınandı (sabit çıktılar);
  gerçek YOLOX/RT-DETR/YOLOv8 dışa aktarımlarıyla **denenmedi**. Dışa aktarma düzeni (ör. YOLOX'un `--decode_in_inference`
  seçeneği, RT-DETR'nin çıktı sırası) senin modelinde doğrulanmalı.
- `robot_vision` renk dedektörü model gerektirmez ve bu lisans sorunlarından etkilenmez.

## Kaynaklar
- Ultralytics lisans seçenekleri: https://ultralytics.com/license · https://ultralytics.com/docs · https://www.ultralytics.com/request-license
- YOLOX (Apache-2.0): https://github.com/Megvii-BaseDetection/YOLOX · OpenCV Zoo `object_detection_yolox`: https://huggingface.co/opencv/object_detection_yolox
- RT-DETR (Apache-2.0): https://github.com/lyuwenyu/RT-DETR · https://huggingface.co/PekingU/rtdetr_r50vd
- Karşılaştırma sayfası (YOLOv8/YOLO11/YOLOv10 AGPL-3.0, YOLOv9 GPL-3.0, YOLOX Apache-2.0): https://playground.roboflow.com/models/megvii/yolox
