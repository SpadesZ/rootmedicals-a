# 檔案路徑: rootmedicals-a/ebm-rag/rag_core/core4_ragging/calculators.py
# 產生時間: 2026-06-17 16:10 +08:00
# 版本: v0.1-交付整理
# 說明: RAG Core4 查詢/驗證層，負責 retrieval、EBM 生成、ICD gate 與安全燈號。
# 交付: 保留於交付包；若未來刪除，需先確認閉環 demo 與對應文件不再依賴。
# ----------------------------------------------------------------------------------------------------

# File Path: ebm-rag/rag_core/core4_ragging/calculators.py
# Timestamp: 2026-06-09
# Version: v0.4
# Description: 本地醫學計算器。LLM 只可接收計算結果；實際公式由 Python 函式完成。
#              支援 CHA2DS2-VASc / CKD-EPI eGFR / BMI；缺欄位回傳 insufficient_data，不猜測。
# ----------------------------------------------------------------------------------------------------

from typing import Any


def calc_cha2ds2_vasc(
    age: int = None,
    sex: str = None,
    chf: bool = False,
    hypertension: bool = False,
    stroke_tia: bool = False,
    vascular_disease: bool = False,
    diabetes: bool = False
) -> dict:
    if age is None or sex is None:
        return {"status": "insufficient_data", "calculator": "CHA2DS2-VASc", "score": None}
    score = 0
    if chf:
        score += 1
    if hypertension:
        score += 1
    if int(age) >= 75:
        score += 2
    elif int(age) >= 65:
        score += 1
    if diabetes:
        score += 1
    if stroke_tia:
        score += 2
    if vascular_disease:
        score += 1
    if str(sex).lower() == "female":
        score += 1
    return {"status": "ok", "calculator": "CHA2DS2-VASc", "score": score}


def calc_egfr_ckd_epi(creatinine_mg_dl: float = None, age: int = None, sex: str = None) -> dict:
    if creatinine_mg_dl is None or age is None or sex is None:
        return {"status": "insufficient_data", "calculator": "eGFR", "egfr": None}
    sex_text = str(sex).lower()
    if sex_text not in {"female", "male"}:
        return {"status": "insufficient_data", "calculator": "eGFR", "egfr": None, "error": "sex must be female or male"}
    creatinine = float(creatinine_mg_dl)
    age_value = int(age)
    kappa = 0.7 if sex_text == "female" else 0.9
    alpha = -0.241 if sex_text == "female" else -0.302
    sex_factor = 1.012 if sex_text == "female" else 1.0
    ratio = creatinine / kappa
    egfr = 142 * (min(ratio, 1) ** alpha) * (max(ratio, 1) ** -1.200) * (0.9938 ** age_value) * sex_factor
    return {"status": "ok", "calculator": "eGFR", "egfr": round(egfr, 1), "unit": "mL/min/1.73m2"}


def calc_bmi(weight_kg: float = None, height_cm: float = None) -> dict:
    if weight_kg is None or height_cm is None:
        return {"status": "insufficient_data", "calculator": "BMI", "bmi": None}
    height = float(height_cm)
    weight = float(weight_kg)
    if height <= 0 or weight <= 0:
        return {"status": "insufficient_data", "calculator": "BMI", "bmi": None, "error": "weight_kg and height_cm must be positive"}
    bmi = weight / ((height / 100) ** 2)
    return {"status": "ok", "calculator": "BMI", "bmi": round(bmi, 1)}


CALCULATORS = {
    "CHA2DS2-VASc": calc_cha2ds2_vasc,
    "eGFR": calc_egfr_ckd_epi,
    "BMI": calc_bmi
}


def _clean_key_lookup(data: dict, *keys: str):
    if not isinstance(data, dict):
        return None
    lower_map = {str(key).lower(): value for key, value in data.items()}
    for key in keys:
        if key in data:
            return data.get(key)
        lowered = key.lower()
        if lowered in lower_map:
            return lower_map[lowered]
    return None


def _nested_lookup(data: dict, *keys: str):
    direct = _clean_key_lookup(data, *keys)
    if direct is not None:
        return direct
    for container_key in ("labs", "vitals", "measurements", "patient"):
        container = _clean_key_lookup(data, container_key)
        if isinstance(container, dict):
            nested = _clean_key_lookup(container, *keys)
            if nested is not None:
                return nested
    return None


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"true", "1", "yes", "y", "present", "positive"}


def _clinical_text(case_context: dict[str, Any]) -> str:
    if not isinstance(case_context, dict):
        return ""
    fragments = []
    for key in ("dx", "hx", "history", "pmh", "assessment", "problem", "diagnosis", "conditions"):
        value = case_context.get(key)
        if isinstance(value, list):
            fragments.extend(str(item) for item in value)
        elif isinstance(value, dict):
            fragments.extend(str(item_value) for item_value in value.values())
        elif value is not None:
            fragments.append(str(value))
    return " ".join(fragments).lower()


def _explicit_bool_or_none(case_context: dict[str, Any], *keys: str) -> bool | None:
    value = _nested_lookup(case_context, *keys)
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    lowered = str(value).strip().lower()
    if lowered in {"true", "1", "yes", "y", "present", "positive"}:
        return True
    if lowered in {"false", "0", "no", "n", "absent", "negative"}:
        return False
    return None


def _condition_present(case_context: dict[str, Any], keys: tuple, terms: tuple) -> bool:
    explicit = _explicit_bool_or_none(case_context, *keys)
    if explicit is not None:
        return explicit
    text = f" {_clinical_text(case_context)} "
    for term in terms:
        start = 0
        while True:
            found_at = text.find(term, start)
            if found_at < 0:
                break
            prefix = text[max(0, found_at - 24):found_at]
            clean_prefix = prefix.strip()
            negated = (
                clean_prefix.endswith("no")
                or clean_prefix.endswith("without")
                or clean_prefix.endswith("denies")
                or clean_prefix.endswith("denied")
                or clean_prefix.endswith("negative for")
            )
            if not negated:
                return True
            start = found_at + len(term)
    return False


def _has_af_signal(case_context: dict[str, Any]) -> bool:
    lowered = _clinical_text(case_context)
    return "atrial fibrillation" in lowered or "afib" in lowered or " af " in f" {lowered} "


def infer_calculator_requests(case_context: dict[str, Any]) -> list[dict]:
    if not isinstance(case_context, dict):
        return []
    requests: list[dict] = []

    age = _nested_lookup(case_context, "age")
    sex = _nested_lookup(case_context, "sex", "gender")
    creatinine = _nested_lookup(case_context, "creatinine_mg_dl", "creatinine", "scr", "serum_creatinine")
    weight = _nested_lookup(case_context, "weight_kg", "weight")
    height = _nested_lookup(case_context, "height_cm", "height")

    if creatinine is not None and age is not None and sex is not None:
        requests.append({
            "name": "eGFR",
            "params": {
                "creatinine_mg_dl": creatinine,
                "age": age,
                "sex": sex
            },
            "source": "auto_detected"
        })

    if weight is not None and height is not None:
        requests.append({
            "name": "BMI",
            "params": {
                "weight_kg": weight,
                "height_cm": height
            },
            "source": "auto_detected"
        })

    if _has_af_signal(case_context) and age is not None and sex is not None:
        requests.append({
            "name": "CHA2DS2-VASc",
            "params": {
                "age": age,
                "sex": sex,
                "chf": _condition_present(case_context, ("chf", "heart_failure"), (" chf ", "heart failure", "congestive heart failure")),
                "hypertension": _condition_present(case_context, ("hypertension", "htn"), (" hypertension", " htn ", "high blood pressure")),
                "stroke_tia": _condition_present(case_context, ("stroke_tia", "stroke", "tia"), (" stroke", " tia ", "transient ischemic attack", "thromboembolism")),
                "vascular_disease": _condition_present(case_context, ("vascular_disease", "pad", "mi"), ("vascular disease", " pad ", "peripheral artery disease", " myocardial infarction", " mi ")),
                "diabetes": _condition_present(case_context, ("diabetes", "dm"), (" diabetes", " diabetes mellitus", " dm ", "type 2 diabetes", "type ii diabetes"))
            },
            "source": "auto_detected"
        })

    return requests


def run_calculator(name: str, params: dict) -> dict:
    calculator_name = str(name or "").strip()
    fn = CALCULATORS.get(calculator_name)
    if not fn:
        return {"status": "unknown_calculator", "calculator": calculator_name or None}
    if not isinstance(params, dict):
        return {"status": "insufficient_data", "calculator": calculator_name, "error": "params must be an object"}
    try:
        result = fn(**params)
        result["requested_calculator"] = calculator_name
        result["input_params"] = params
        return result
    except (TypeError, ValueError) as e:
        return {
            "status": "insufficient_data",
            "calculator": calculator_name,
            "error": str(e),
            "input_params": params
        }


def run_requested_calculators(case_context: dict[str, Any]) -> list[dict]:
    if not isinstance(case_context, dict):
        return []
    requested = case_context.get("calculators")
    if requested is None:
        requested = []
    if not isinstance(requested, list):
        return [{
            "status": "invalid_request",
            "calculator": None,
            "error": "case_context.calculators must be a list"
        }]

    results = []
    combined_requests = list(requested) + infer_calculator_requests(case_context)
    seen: set[tuple[str, str]] = set()
    for item in combined_requests:
        if not isinstance(item, dict):
            results.append({
                "status": "invalid_request",
                "calculator": None,
                "error": "calculator request must be an object"
            })
            continue
        name = item.get("name")
        params = item.get("params", {})
        dedupe_key = (str(name or ""), str(sorted(params.items())) if isinstance(params, dict) else str(params))
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        result = run_calculator(name, params)
        if item.get("source") == "auto_detected":
            result["source"] = "auto_detected"
        results.append(result)
    return results
