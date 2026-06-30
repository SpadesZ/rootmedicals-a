# 檔案路徑: rootmedicals-a/llmxx-server/server_app/ocr/server_ocr.py
# 產生時間: 2026-06-18 09:30 +08:00
# 版本: v0.3-server OCR 權責註解
# 模組定位:
#   這支檔案是 thin capture payload 進入 server 後的第一個轉換點。
#   client 只送截圖與 layout hints，本檔負責 OCR、文字正規化、ICD 擷取與 formal payload 組裝。
# 主要責任:
#   1. 將 llmxx-client-screenshot.v0.1 轉成 llmxx-client-local-ocr.v0.1 相容格式。
#   2. 使用 layout hints 或 server fallback 偵測 SOAP、Vital Signs、ICD 區域。
#   3. 優先採用 ClinicalGuard sidecar 的結構化 SOAP/vitals，OCR 則保留為沒有 sidecar 時的 fallback。
#   4. 在進 RAG 前修正已知 OCR 字詞錯誤，並保留 normalizations 供除錯。
# 呼叫來源:
#   server_app.api.main._parse_payload() 在收到 screenshot schema 時呼叫 screenshot_payload_to_formal()。
# 輸入契約:
#   ScreenshotClientPayload 必須包含 PNG base64、視窗尺寸、layout_regions；ICD sidecar 若存在會優先採用。
# 輸出契約:
#   FormalClientPayload；後續 clinical_mapper 不需要知道原始資料來自截圖或舊 OCR payload。
# 安全邊界:
#   截圖只在記憶體中解碼，不應寫入磁碟。若 base64 無效、圖片過大或 OCR 套件缺失，要明確失敗。
#   OCR 修正只能改善查詢品質，不能自行決定綠燈；燈號仍由 response_builder 的 gate 決定。
# 維護提醒:
#   修改 OCR 區域偵測時，要同步測 Ctrl+Alt+G、ICD sidecar、沒有 sidecar 的畫面 OCR fallback。
# ----------------------------------------------------------------------------------------------------

from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from io import BytesIO
from typing import Any

from ..domain.icd10_master import (
    find_icd10_in_text,
    label_for_icd10,
    normalize_icd10_code,
    normalized_diagnosis_for_icd10,
    terms_for_icd10,
)
from ..contracts.schemas import FormalClientPayload, ScreenshotClientPayload


SOAP_FIELDS = ("S", "O", "A", "P")
VITAL_FIELDS = ("bp", "hr", "temp", "rr", "spo2")
MAX_IMAGE_BYTES = 8 * 1024 * 1024

LITERAL_REPLACEMENTS = {
    # 這些是目前 demo HIS + EasyOCR 最常見的錯字修正。
    # 只放可解釋、可重現的錯字，不把醫療推論塞進正規化字典。
    "a lleigic fhinitis": "allergic rhinitis",
    "a lleigic": "allergic",
    "fhinitis": "rhinitis",
    "spray _puff": "spray 1 puff",
    "spray _ puff": "spray 1 puff",
    "_puff": "1 puff",
    "nasal block": "nasal congestion",
    "Sp02": "SpO2",
    "temp 36,8": "temp 36.8",
    "follom": "follow",
    "fof": "for",
    "healache": "headache",
    " fni ": " for ",
    "iays": "days",
    " ani ": " and ",
    "oiientei": "oriented",
    "suspectei": "suspected",
    "atiial fihrillatin": "atrial fibrillation",
    "atiial fibrillatin": "atrial fibrillation",
    "atrial fibrillatin": "atrial fibrillation",
    "Lunsilef nfal anticuagulatio": "consider oral anticoagulation",
    "anticuagulatio": "anticoagulation",
    "fnf stroke prewentln": "for stroke prevention",
    "stroke prewentln": "stroke prevention",
    "prefer Iont": "prefer DOAC",
    "if llu Contralnditatlo": "if no contraindication",
    "Contralnditatlo": "contraindication",
    "Contraindicatlo": "contraindication",
    "fullum gliieline- hasei": "follow guideline-based",
    "gliieline- hasei": "guideline-based",
    "aSSesSment": "assessment",
    "I chest paln": "No chest pain",
    "I chest pain": "No chest pain",
    "i actire hleedirg": "No active bleeding",
    "u actire hleedirg": "No active bleeding",
    "actire hleedirg": "active bleeding",
}

ICD_PATTERNS = {
    "J30": {
        "label": "allergic rhinitis",
        "terms": {"allergic rhinitis", "rhinitis", "hay fever", "nasal allergy"},
    },
    "I48": {
        "label": "atrial fibrillation",
        "terms": {"atrial fibrillation", "afib", "a fib", "af ", "af,", "af."},
    },
    "E11": {
        "label": "type 2 diabetes mellitus",
        "terms": {"type 2 diabetes", "diabetes mellitus", "dm2", "t2dm"},
    },
    "G43": {
        "label": "migraine",
        "terms": {"migraine"},
    },
    "J20": {
        "label": "acute bronchitis",
        "terms": {"acute bronchitis", "bronchitis"},
    },
    "K52": {
        "label": "gastroenteritis",
        "terms": {"gastroenteritis", "colitis"},
    },
}


@dataclass(frozen=True)
class Region:
    field_name: str
    x: int
    y: int
    w: int
    h: int
    method: str


def screenshot_payload_to_formal(payload: ScreenshotClientPayload) -> FormalClientPayload:
    # 這裡是 client/server 權責分界：client 傳來的是截圖，不是已 OCR 的 SOAP。
    # 轉成 formal payload 後，後面的 clinical_mapper/RAG/final gate 才能沿用同一份契約。
    image = _decode_png(payload.screenshot.image_b64)
    ocr = _ServerOCR()
    layout_regions = [region.model_dump() for region in payload.layout_regions]
    parsed = ocr.extract_fields(image, layout_regions=layout_regions)
    soap = {field: parsed["fields"].get(field, "") for field in SOAP_FIELDS}
    vital_signs = {field: parsed["fields"].get(field, "") for field in VITAL_FIELDS}
    normalizations = {key: list(value) for key, value in parsed["normalizations"].items()}
    metadata = payload.clinical_metadata
    metadata_soap = metadata.soap.model_dump()
    for field in SOAP_FIELDS:
        value = _clean_text(metadata_soap.get(field, ""), max_length=1200)
        if value:
            # 維護筆記:
            # ClinicalGuard mock HIS 已掌握文字欄位本身；使用 sidecar 是為了避免 OCR 將正確 P 欄
            # 讀成無意義字串後誤降級。這不是 client OCR，語意判斷仍在 server/RAG。
            if soap.get(field) != value:
                normalizations.setdefault(field, []).append(
                    {"from": soap.get(field, ""), "to": value, "rule": "clinical_metadata_soap_preferred"}
                )
            soap[field] = value
    metadata_vitals = metadata.vital_signs.model_dump()
    for field in VITAL_FIELDS:
        value = _normalize_vital(metadata_vitals.get(field, ""))
        if value:
            if vital_signs.get(field) != value:
                normalizations.setdefault(field, []).append(
                    {"from": vital_signs.get(field, ""), "to": value, "rule": "clinical_metadata_vitals_preferred"}
                )
            vital_signs[field] = value
    metadata_icd_code = normalize_icd_code(metadata.icd10_code or metadata.icd_code)
    if metadata_icd_code:
        # ClinicalGuard sidecar 來自 HIS 互動欄位，比 OCR 畫面文字穩定。
        # 有 sidecar 時優先採用；OCR 到的 ICD 文字只作為沒有 sidecar 時的備援。
        icd_code = metadata_icd_code
        diagnosis_label = str(metadata.diagnosis_label or "").strip() or label_for_icd10(icd_code)
        normalized_diagnosis = (
            str(metadata.normalized_diagnosis or "").strip()
            or normalized_diagnosis_for_icd10(icd_code, diagnosis_label)
        )
        normalizations.setdefault("icd_code", []).append(
            {
                "from": str(metadata.metadata_source or "clinical_metadata"),
                "to": icd_code,
                "rule": "clinical_metadata_preferred",
            }
        )
    else:
        # 沒有 sidecar 時才從畫面 OCR 嘗試找 ICD。這條路徑保留是為了 demo 以外的泛用截圖，
        # 但正式 HIS 整合仍應把 ICD code 以結構化欄位傳入。
        icd_text = parsed["fields"].get("icd_raw_text", "") or parsed["fields"].get("icd_code", "")
        icd_record = find_icd10_in_text(icd_text)
        icd_code = normalize_icd_code(parsed["fields"].get("icd_code", "")) or (icd_record.code if icd_record else "")
        diagnosis_label = (icd_record.label if icd_record else "") or label_for_icd10(icd_code)
        normalized_diagnosis = normalized_diagnosis_for_icd10(icd_code, diagnosis_label)
    dx_text = soap.get("A", "")
    clinical_text = "\n".join(f"{field}: {soap[field]}" for field in SOAP_FIELDS if soap[field])
    formal = {
        "schema_version": "llmxx-client-local-ocr.v0.1",
        "session_id": payload.session_id,
        "created_at": payload.created_at,
        "source": "llmxx-client-thin-screenshot/server-ocr",
        "input_origin": payload.input_origin,
        "zero_disk_image_io": payload.zero_disk_image_io,
        "patient_uid": payload.patient_uid,
        "soap": soap,
        "vital_signs": vital_signs,
        "icd_code": icd_code,
        "icd10_code": icd_code,
        "diagnosis_label": diagnosis_label or normalized_diagnosis,
        "normalized_diagnosis": normalized_diagnosis,
        "dx_text": dx_text,
        "clinical_text": clinical_text,
        "redactions": {field: [] for field in SOAP_FIELDS},
        "normalizations": normalizations,
        "demo_mode": payload.demo_mode,
        "demo_fixture_id": payload.demo_fixture_id,
    }
    return FormalClientPayload.model_validate(formal)


def normalize_icd_code(value: str) -> str:
    return normalize_icd10_code(value)


def icd_family(code: str) -> str:
    normalized = normalize_icd_code(code)
    if len(normalized) < 3:
        return ""
    return normalized[:3]


def expected_icd_label(code: str) -> str:
    label = label_for_icd10(code)
    if label:
        return label
    family = icd_family(code)
    data = ICD_PATTERNS.get(family)
    return str(data.get("label") or "") if isinstance(data, dict) else ""


def icd_dx_mismatch_reason(icd_code: str, dx: str, diagnosis_label: str = "") -> str:
    # 這個 helper 只回答「A 欄是否明顯落在另一個已知 ICD 家族」。
    # 如果 A 欄文字太模糊，就回空字串交給上層 review；不要在這裡猜診斷。
    family = icd_family(icd_code)
    if not family:
        return "icd_missing"
    expected = ICD_PATTERNS.get(family, {})
    dx_lower = f" {str(dx or '').lower()} "
    expected_terms = set(terms_for_icd10(icd_code, diagnosis_label))
    if isinstance(expected, dict):
        expected_terms.update(expected.get("terms") or set())
    if any(str(term).lower() in dx_lower for term in expected_terms):
        return ""
    for other_family, other in ICD_PATTERNS.items():
        if other_family == family:
            continue
        other_terms = other.get("terms") if isinstance(other, dict) else set()
        if any(str(term).lower() in dx_lower for term in other_terms):
            return "icd_dx_mismatch"
    return ""


def _decode_png(image_b64: str) -> Any:
    try:
        image_bytes = base64.b64decode(str(image_b64 or ""), validate=True)
    except Exception as exc:
        raise ValueError("screenshot.image_b64 is not valid base64") from exc
    if not image_bytes or len(image_bytes) > MAX_IMAGE_BYTES:
        raise ValueError("screenshot image is empty or too large")
    try:
        from PIL import Image
        import numpy as np
    except Exception as exc:
        raise RuntimeError("Pillow and numpy are required for server-side OCR") from exc
    with Image.open(BytesIO(image_bytes)) as img:
        rgb = img.convert("RGB")
        return np.array(rgb)


def _clean_text(value: Any, max_length: int = 700) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    for src, dst in LITERAL_REPLACEMENTS.items():
        text = re.sub(re.escape(src), dst, text, flags=re.IGNORECASE)
    return text[:max_length]


def _normalize_vital(value: str, max_length: int = 40) -> str:
    text = _clean_text(value, max_length=max_length)
    return text.replace(" ,", ".").replace(",", ".")


class _ServerOCR:
    def __init__(self) -> None:
        self._reader: Any | None = None

    def extract_fields(self, image_array: Any, layout_regions: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        # 優先使用 client 傳來的 layout hints，因為 GUI 元件座標比影像輪廓偵測穩定。
        # server fallback 仍保留，避免舊 client 或手工 replay payload 沒有 hints 時完全失效。
        regions = self._detect_regions(image_array, layout_regions=layout_regions)
        fields: dict[str, str] = {}
        normalizations: dict[str, list[dict[str, Any]]] = {field: [] for field in SOAP_FIELDS}
        normalizations["icd_code"] = []
        for region in regions:
            raw_text = self._recognize_region(image_array, region)
            if region.field_name in SOAP_FIELDS:
                clean = _clean_text(raw_text)
                if clean != raw_text:
                    normalizations[region.field_name].append({"from": raw_text, "to": clean, "rule": "server_literal_replacements"})
                fields[region.field_name] = clean
            elif region.field_name in VITAL_FIELDS:
                fields[region.field_name] = _normalize_vital(raw_text)
            elif region.field_name == "icd_code":
                code = normalize_icd_code(raw_text)
                if code and code != raw_text:
                    normalizations["icd_code"].append({"from": raw_text, "to": code, "rule": "icd_code_normalization"})
                fields["icd_code"] = code
                fields["icd_raw_text"] = raw_text
        for field in SOAP_FIELDS + VITAL_FIELDS + ("icd_code", "icd_raw_text"):
            fields.setdefault(field, "")
        return {"fields": fields, "normalizations": normalizations}

    def _reader_instance(self) -> Any:
        # EasyOCR reader 初始化昂貴，所以 lazy load 並快取在 instance 上。
        # 不在模組 import 時載入，讓 /api/health 與非截圖 payload 不會被 OCR 相依套件拖慢或拖垮。
        if self._reader is not None:
            return self._reader
        try:
            import easyocr
        except Exception as exc:
            raise RuntimeError("easyocr is required for server-side OCR") from exc
        self._reader = easyocr.Reader(["ch_tra", "en"], gpu=False, verbose=False)
        return self._reader

    def _recognize_region(self, image_array: Any, region: Region) -> str:
        # 裁切時略縮 2px，是為了避開 Tk Text/Entry 邊框；邊框線進 OCR 會增加亂碼。
        height, width = image_array.shape[:2]
        x1 = max(0, region.x + 2)
        y1 = max(0, region.y + 2)
        x2 = min(width, region.x + max(1, region.w) - 2)
        y2 = min(height, region.y + max(1, region.h) - 2)
        crop = image_array[y1:y2, x1:x2].copy()
        if crop.size == 0:
            return ""
        crop = self._prepare_crop(crop, region.field_name)
        result = self._reader_instance().readtext(crop, detail=0, paragraph=False)
        if not isinstance(result, list):
            return ""
        return _clean_text(" ".join(str(item) for item in result), max_length=900)

    def _prepare_crop(self, crop: Any, field_name: str) -> Any:
        # SOAP 多行文字和 Vital/ICD 單行欄位大小不同，放大倍率分開處理。
        # 這不是影像美化，而是讓 OCR 在 Windows Tk 字體下穩定讀到小字。
        try:
            from PIL import Image, ImageEnhance
            import numpy as np
        except Exception:
            return crop
        scale = 3 if field_name in SOAP_FIELDS else 4
        img = Image.fromarray(crop).convert("L")
        img = ImageEnhance.Contrast(img).enhance(1.7)
        img = img.resize((max(1, img.width * scale), max(1, img.height * scale)))
        return np.array(img)

    def _detect_regions(self, image_array: Any, layout_regions: list[dict[str, Any]] | None = None) -> list[Region]:
        hinted = self._regions_from_layout_hints(layout_regions or [], image_array)
        hinted_names = {region.field_name for region in hinted}
        if all(field in hinted_names for field in SOAP_FIELDS):
            return hinted
        soap_regions = self._detect_soap_regions(image_array)
        vital_regions = self._detect_vital_regions(image_array, soap_regions)
        icd_region = self._detect_icd_region(image_array, soap_regions)
        regions: list[Region] = []
        if icd_region:
            regions.append(icd_region)
        regions.extend(soap_regions)
        regions.extend(vital_regions)
        return regions

    def _regions_from_layout_hints(self, layout_regions: list[dict[str, Any]], image_array: Any) -> list[Region]:
        # layout hint 是跨 process 傳來的不可信資料，仍要做 bounds check。
        # 這裡只接受已知欄位，避免任意座標造成大量裁切或讀到不該讀的畫面區塊。
        height, width = image_array.shape[:2]
        allowed = set(SOAP_FIELDS + VITAL_FIELDS + ("icd_code",))
        out: list[Region] = []
        seen: set[str] = set()
        for item in layout_regions:
            if not isinstance(item, dict):
                continue
            field_name = str(item.get("field_name") or "").strip()
            if field_name not in allowed or field_name in seen:
                continue
            try:
                x = int(item.get("x") or 0)
                y = int(item.get("y") or 0)
                w = int(item.get("w") or 0)
                h = int(item.get("h") or 0)
            except (TypeError, ValueError):
                continue
            if w < 5 or h < 5 or x < 0 or y < 0 or x >= width or y >= height:
                continue
            out.append(
                Region(
                    field_name=field_name,
                    x=max(0, min(x, width - 1)),
                    y=max(0, min(y, height - 1)),
                    w=max(1, min(w, width - x)),
                    h=max(1, min(h, height - y)),
                    method=f"layout_hint:{str(item.get('method') or '')[:40]}",
                )
            )
            seen.add(field_name)
        order = {field: idx for idx, field in enumerate(("icd_code",) + SOAP_FIELDS + VITAL_FIELDS)}
        return sorted(out, key=lambda region: order.get(region.field_name, 99))

    def _detect_soap_regions(self, image_array: Any) -> list[Region]:
        try:
            import cv2
        except Exception:
            return self._fallback_soap_regions(image_array)
        height, width = image_array.shape[:2]
        gray = cv2.cvtColor(image_array, cv2.COLOR_RGB2GRAY)
        mask = cv2.inRange(gray, 245, 255)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (9, 5))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        boxes: list[tuple[int, int, int, int]] = []
        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)
            if w / max(1, width) >= 0.55 and 0.045 <= h / max(1, height) <= 0.20 and 0.14 <= y / max(1, height) <= 0.86:
                boxes.append((x, y, w, h))
        chosen = self._choose_best_four(sorted(boxes, key=lambda item: (item[1], item[0])))
        if len(chosen) != 4:
            return self._fallback_soap_regions(image_array)
        return [Region(field, x, y, w, h, "server_cv2_soap") for field, (x, y, w, h) in zip(SOAP_FIELDS, chosen)]

    def _detect_vital_regions(self, image_array: Any, soap_regions: list[Region]) -> list[Region]:
        try:
            import cv2
        except Exception:
            return self._fallback_vital_regions(image_array)
        if not soap_regions:
            return self._fallback_vital_regions(image_array)
        height, width = image_array.shape[:2]
        p_bottom = max(region.y + region.h for region in soap_regions)
        gray = cv2.cvtColor(image_array, cv2.COLOR_RGB2GRAY)
        mask = cv2.inRange(gray, 245, 255)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        boxes: list[tuple[int, int, int, int]] = []
        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)
            if p_bottom < y < int(height * 0.95) and 0.045 <= w / max(1, width) <= 0.18 and 0.012 <= h / max(1, height) <= 0.05:
                boxes.append((x, y, w, h))
        row = self._choose_best_row(boxes, 5)
        if len(row) != 5:
            return self._fallback_vital_regions(image_array)
        return [Region(field, x, y, w, h, "server_cv2_vital") for field, (x, y, w, h) in zip(VITAL_FIELDS, row)]

    def _detect_icd_region(self, image_array: Any, soap_regions: list[Region]) -> Region:
        try:
            import cv2
        except Exception:
            return self._fallback_icd_region(image_array)
        height, width = image_array.shape[:2]
        first_soap_y = min((region.y for region in soap_regions), default=int(height * 0.22))
        gray = cv2.cvtColor(image_array, cv2.COLOR_RGB2GRAY)
        mask = cv2.inRange(gray, 245, 255)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        candidates: list[tuple[int, int, int, int]] = []
        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)
            if (
                int(height * 0.08) <= y <= first_soap_y - 4
                and 0.18 <= w / max(1, width) <= 0.55
                and 0.018 <= h / max(1, height) <= 0.055
            ):
                candidates.append((x, y, w, h))
        if candidates:
            x, y, w, h = sorted(candidates, key=lambda item: (-item[2], item[1], item[0]))[0]
            return Region("icd_code", x, y, w, h, "server_cv2_icd")
        return self._fallback_icd_region(image_array)

    def _fallback_soap_regions(self, image_array: Any) -> list[Region]:
        height, width = image_array.shape[:2]
        ratios = {
            "S": (0.035, 0.205, 0.94, 0.145),
            "O": (0.035, 0.365, 0.94, 0.145),
            "A": (0.035, 0.525, 0.94, 0.145),
            "P": (0.035, 0.685, 0.94, 0.145),
        }
        return [self._ratio_region(field, ratios[field], width, height, "server_ratio_soap") for field in SOAP_FIELDS]

    def _fallback_vital_regions(self, image_array: Any) -> list[Region]:
        height, width = image_array.shape[:2]
        ratios = {
            "bp": (0.035, 0.895, 0.12, 0.035),
            "hr": (0.185, 0.895, 0.12, 0.035),
            "temp": (0.335, 0.895, 0.12, 0.035),
            "rr": (0.485, 0.895, 0.12, 0.035),
            "spo2": (0.635, 0.895, 0.12, 0.035),
        }
        return [self._ratio_region(field, ratios[field], width, height, "server_ratio_vital") for field in VITAL_FIELDS]

    def _fallback_icd_region(self, image_array: Any) -> Region:
        height, width = image_array.shape[:2]
        return self._ratio_region("icd_code", (0.08, 0.125, 0.42, 0.04), width, height, "server_ratio_icd")

    @staticmethod
    def _ratio_region(field: str, ratio: tuple[float, float, float, float], width: int, height: int, method: str) -> Region:
        x, y, w, h = ratio
        return Region(field, int(width * x), int(height * y), max(1, int(width * w)), max(1, int(height * h)), method)

    @staticmethod
    def _choose_best_four(boxes: list[tuple[int, int, int, int]]) -> list[tuple[int, int, int, int]]:
        if len(boxes) < 4:
            return []
        best = boxes[:4]
        best_score: float | None = None
        for start in range(0, len(boxes) - 3):
            group = boxes[start:start + 4]
            ys = [item[1] for item in group]
            xs = [item[0] for item in group]
            widths = [item[2] for item in group]
            gaps = [ys[idx + 1] - ys[idx] for idx in range(3)]
            score = (max(xs) - min(xs)) + (max(widths) - min(widths)) + (max(gaps) - min(gaps))
            if best_score is None or score < best_score:
                best_score = float(score)
                best = group
        return best

    @staticmethod
    def _choose_best_row(boxes: list[tuple[int, int, int, int]], count: int) -> list[tuple[int, int, int, int]]:
        if len(boxes) < count:
            return []
        best: list[tuple[int, int, int, int]] = []
        best_score: float | None = None
        for candidate in sorted(boxes, key=lambda item: (item[1], item[0])):
            row = [box for box in boxes if abs(box[1] - candidate[1]) <= 10]
            if len(row) < count:
                continue
            row = sorted(row, key=lambda item: item[0])[:count]
            y_values = [box[1] for box in row]
            widths = [box[2] for box in row]
            score = (max(y_values) - min(y_values)) + (max(widths) - min(widths))
            if best_score is None or score < best_score:
                best_score = float(score)
                best = row
        return best

