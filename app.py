import os
import json
import math
from typing import Any, Dict, List, Tuple

import numpy as np
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS


# ============================================================
# Paths
# ============================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

MODEL_PATH = os.getenv("MODEL_PATH", os.path.join(BASE_DIR, "model.tflite"))
NORM_PATH = os.getenv("NORM_PATH", os.path.join(BASE_DIR, "norm.json"))
POLICY_PATH = os.getenv("POLICY_PATH", os.path.join(BASE_DIR, "policy_v9.json"))

STATIC_DIR = os.path.join(BASE_DIR, "static")


# ============================================================
# TFLite import
# ============================================================

try:
    import tflite_runtime.interpreter as tflite
except Exception:
    try:
        import tensorflow.lite as tflite
    except Exception as e:
        raise RuntimeError(
            "No TFLite runtime found. Install either tflite-runtime or tensorflow."
        ) from e


# ============================================================
# Flask
# ============================================================

app = Flask(__name__, static_folder=STATIC_DIR)
CORS(app)


# ============================================================
# Load norm + policy
# ============================================================

def load_json(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        raise FileNotFoundError(f"Missing required file: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


norm_cfg = load_json(NORM_PATH)
policy_cfg = load_json(POLICY_PATH)

feature_cols: List[str] = norm_cfg.get("feature_cols", [])
if not feature_cols:
    raise ValueError('norm.json must contain non-empty "feature_cols".')


# Supports either:
# {
#   "mean": {"f1": 0.1, "f2": 2.0},
#   "std": {"f1": 1.0, "f2": 0.5}
# }
#
# or:
# {
#   "mean": [0.1, 2.0],
#   "std": [1.0, 0.5],
#   "feature_cols": ["f1", "f2"]
# }

mean_raw = norm_cfg.get("mean", {})
std_raw = norm_cfg.get("std", {})

if isinstance(mean_raw, list):
    mean_map = {col: float(mean_raw[i]) for i, col in enumerate(feature_cols)}
else:
    mean_map = {k: float(v) for k, v in mean_raw.items()}

if isinstance(std_raw, list):
    std_map = {col: float(std_raw[i]) for i, col in enumerate(feature_cols)}
else:
    std_map = {k: float(v) for k, v in std_raw.items()}


# ============================================================
# Load model
# ============================================================

if not os.path.exists(MODEL_PATH):
    raise FileNotFoundError(f"Missing model file: {MODEL_PATH}")

interpreter = tflite.Interpreter(model_path=MODEL_PATH)
interpreter.allocate_tensors()

input_details = interpreter.get_input_details()
output_details = interpreter.get_output_details()

if not input_details:
    raise RuntimeError("TFLite model has no input tensor.")

if not output_details:
    raise RuntimeError("TFLite model has no output tensor.")

model_input = input_details[0]
model_output = output_details[0]

input_index = model_input["index"]
output_index = model_output["index"]

input_shape = model_input["shape"].tolist()
input_dtype = model_input["dtype"]


# ============================================================
# Helpers
# ============================================================

def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        x = float(value)
        if math.isnan(x) or math.isinf(x):
            return default
        return x
    except Exception:
        return default


def softmax(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype=np.float32)
    x = x - np.max(x)
    e = np.exp(x)
    s = np.sum(e)
    if s <= 0:
        return np.array([1 / 3, 1 / 3, 1 / 3], dtype=np.float32)
    return e / s


def normalize_probs(raw_output: np.ndarray) -> Tuple[float, float, float]:
    """
    Expected model output order:
    [p_sell, p_hold, p_buy]

    Handles either already-probabilities or logits.
    """
    arr = np.asarray(raw_output).reshape(-1).astype(np.float32)

    if arr.size < 3:
        raise ValueError(f"Model output must have at least 3 values, got {arr.size}")

    arr = arr[:3]

    total = float(np.sum(arr))
    min_val = float(np.min(arr))
    max_val = float(np.max(arr))

    # If output already looks like probabilities, normalize lightly.
    if min_val >= 0.0 and max_val <= 1.0 and 0.8 <= total <= 1.2:
        probs = arr / max(total, 1e-9)
    else:
        probs = softmax(arr)

    return float(probs[0]), float(probs[1]), float(probs[2])


def build_exact_feature_vector(features: Dict[str, Any]) -> Tuple[List[float], List[str]]:
    """
    1. Receive frontend input.
    2. Build exact feature vector.
    3. Match order sa norm.json["feature_cols"].
    """
    missing = []
    vector = []

    for col in feature_cols:
        if col not in features:
            missing.append(col)
            vector.append(0.0)
        else:
            vector.append(safe_float(features.get(col), 0.0))

    return vector, missing


def normalize_vector(vector: List[float]) -> List[float]:
    """
    4. Normalize gamit mean/std sa norm.json.
    """
    out = []

    for col, raw_value in zip(feature_cols, vector):
        mu = safe_float(mean_map.get(col), 0.0)
        sigma = safe_float(std_map.get(col), 1.0)

        if abs(sigma) < 1e-12:
            sigma = 1.0

        out.append((raw_value - mu) / sigma)

    return out


def prepare_model_input(normalized_vector: List[float]) -> np.ndarray:
    """
    Supports common shapes:
    [1, n_features]
    [1, timesteps, n_features]
    [n_features]
    """
    x = np.asarray(normalized_vector, dtype=np.float32)

    shape = input_shape

    if len(shape) == 1:
        x = x.reshape(shape)
    elif len(shape) == 2:
        x = x.reshape((1, len(normalized_vector)))
    elif len(shape) == 3:
        # Common sequence model shape: [1, 1, n_features]
        if shape[1] == 1 or shape[1] == -1:
            x = x.reshape((1, 1, len(normalized_vector)))
        else:
            # If your model needs real timesteps, send flattened current vector duplicated.
            timesteps = int(shape[1])
            x = np.tile(x.reshape(1, 1, -1), (1, timesteps, 1))
    else:
        raise ValueError(f"Unsupported input shape: {shape}")

    return x.astype(input_dtype)


def run_model(normalized_vector: List[float]) -> Tuple[float, float, float, List[float]]:
    """
    5. Run model.tflite.
    6. Get output probabilities.
    """
    x = prepare_model_input(normalized_vector)

    interpreter.set_tensor(input_index, x)
    interpreter.invoke()

    raw_output = interpreter.get_tensor(output_index)
    p_sell, p_hold, p_buy = normalize_probs(raw_output)

    return p_sell, p_hold, p_buy, np.asarray(raw_output).reshape(-1).astype(float).tolist()


def get_nested(cfg: Dict[str, Any], keys: List[str], default: Any = None) -> Any:
    cur = cfg
    for key in keys:
        if not isinstance(cur, dict) or key not in cur:
            return default
        cur = cur[key]
    return cur


def detect_volatility_bin(meta: Dict[str, Any], features: Dict[str, Any]) -> str:
    """
    7. volatility bin.

    Priority:
    - frontend meta.volatility_bin
    - feature value named volatility_bin
    - numeric volatility / atr_pct / realized_vol
    """
    direct = meta.get("volatility_bin") or features.get("volatility_bin")
    if isinstance(direct, str) and direct:
        return direct.lower()

    vol = (
        meta.get("volatility")
        or features.get("volatility")
        or features.get("atr_pct")
        or features.get("realized_vol")
        or features.get("vol")
    )

    vol = safe_float(vol, 0.0)

    bins = policy_cfg.get("volatility_bins", {})
    if isinstance(bins, dict) and bins:
        low_max = safe_float(get_nested(bins, ["low", "max"], None), None)
        mid_max = safe_float(get_nested(bins, ["mid", "max"], None), None)

        if low_max is not None and vol <= low_max:
            return "low"
        if mid_max is not None and vol <= mid_max:
            return "mid"
        return "high"

    if vol < 0.005:
        return "low"
    if vol < 0.015:
        return "mid"
    return "high"


def detect_regime(meta: Dict[str, Any], features: Dict[str, Any]) -> str:
    """
    7. regime shift kung naa.

    Priority:
    - frontend meta.regime
    - feature regime
    - regime_shift boolean
    """
    regime = meta.get("regime") or features.get("regime")
    if isinstance(regime, str) and regime:
        return regime.lower()

    regime_shift = bool(meta.get("regime_shift", features.get("regime_shift", False)))
    return "shift" if regime_shift else "normal"


def alpha_calibrate_probs(
    p_sell: float,
    p_hold: float,
    p_buy: float,
    policy: Dict[str, Any],
    regime: str,
    volatility_bin: str
) -> Tuple[float, float, float, Dict[str, Any]]:
    """
    7. alpha calibration.

    Supports policy styles:

    {
      "alpha": 1.15
    }

    or

    {
      "alpha_by_regime": {"normal": 1.0, "shift": 0.85},
      "alpha_by_volatility": {"low": 1.05, "mid": 1.0, "high": 0.9}
    }

    Calibration behavior:
    - alpha > 1 sharpens probabilities
    - alpha < 1 softens probabilities
    """
    alpha = safe_float(policy.get("alpha"), 1.0)

    alpha_by_regime = policy.get("alpha_by_regime", {})
    if isinstance(alpha_by_regime, dict):
        alpha *= safe_float(alpha_by_regime.get(regime), 1.0)

    alpha_by_vol = policy.get("alpha_by_volatility", {})
    if isinstance(alpha_by_vol, dict):
        alpha *= safe_float(alpha_by_vol.get(volatility_bin), 1.0)

    alpha = max(0.05, min(alpha, 10.0))

    probs = np.asarray([p_sell, p_hold, p_buy], dtype=np.float64)
    probs = np.clip(probs, 1e-12, 1.0)

    logits = np.log(probs)
    calibrated = softmax((logits * alpha).astype(np.float32))

    return (
        float(calibrated[0]),
        float(calibrated[1]),
        float(calibrated[2]),
        {"alpha_used": alpha}
    )


def get_dynamic_thresholds(
    policy: Dict[str, Any],
    regime: str,
    volatility_bin: str
) -> Dict[str, float]:
    """
    7. dynamic threshold.

    Supports multiple policy shapes.

    Example policy_v9.json:

    {
      "base_thresholds": {
        "buy": 0.60,
        "sell": 0.60,
        "hold": 0.50,
        "margin": 0.05
      },
      "threshold_by_volatility": {
        "low": {"buy": 0.58, "sell": 0.58},
        "mid": {"buy": 0.60, "sell": 0.60},
        "high": {"buy": 0.66, "sell": 0.66}
      },
      "threshold_by_regime": {
        "shift": {"buy": 0.70, "sell": 0.70, "hold": 0.45}
      }
    }
    """
    base = policy.get("base_thresholds", {})

    buy_th = safe_float(
        base.get("buy", policy.get("buy_threshold", policy.get("threshold", 0.60))),
        0.60
    )
    sell_th = safe_float(
        base.get("sell", policy.get("sell_threshold", policy.get("threshold", 0.60))),
        0.60
    )
    hold_th = safe_float(
        base.get("hold", policy.get("hold_threshold", 0.50)),
        0.50
    )
    margin = safe_float(
        base.get("margin", policy.get("min_margin", 0.03)),
        0.03
    )

    by_vol = policy.get("threshold_by_volatility", {})
    if isinstance(by_vol, dict):
        vol_cfg = by_vol.get(volatility_bin, {})
        if isinstance(vol_cfg, dict):
            buy_th = safe_float(vol_cfg.get("buy"), buy_th)
            sell_th = safe_float(vol_cfg.get("sell"), sell_th)
            hold_th = safe_float(vol_cfg.get("hold"), hold_th)
            margin = safe_float(vol_cfg.get("margin"), margin)

    by_regime = policy.get("threshold_by_regime", {})
    if isinstance(by_regime, dict):
        reg_cfg = by_regime.get(regime, {})
        if isinstance(reg_cfg, dict):
            buy_th = safe_float(reg_cfg.get("buy"), buy_th)
            sell_th = safe_float(reg_cfg.get("sell"), sell_th)
            hold_th = safe_float(reg_cfg.get("hold"), hold_th)
            margin = safe_float(reg_cfg.get("margin"), margin)

    # Extra safety if regime shift.
    if regime == "shift":
        shift_cfg = policy.get("regime_shift", {})
        if isinstance(shift_cfg, dict):
            buy_th += safe_float(shift_cfg.get("buy_add"), 0.0)
            sell_th += safe_float(shift_cfg.get("sell_add"), 0.0)
            hold_th += safe_float(shift_cfg.get("hold_add"), 0.0)
            margin += safe_float(shift_cfg.get("margin_add"), 0.0)

    return {
        "buy": max(0.0, min(buy_th, 0.99)),
        "sell": max(0.0, min(sell_th, 0.99)),
        "hold": max(0.0, min(hold_th, 0.99)),
        "margin": max(0.0, min(margin, 0.99)),
    }


def apply_policy_v9(
    p_sell: float,
    p_hold: float,
    p_buy: float,
    features: Dict[str, Any],
    meta: Dict[str, Any]
) -> Dict[str, Any]:
    """
    7. Apply policy_v9.json:
       alpha calibration
       dynamic threshold
       volatility bin
       regime shift kung naa

    8. Return result.
    """
    volatility_bin = detect_volatility_bin(meta, features)
    regime = detect_regime(meta, features)

    cp_sell, cp_hold, cp_buy, cal_info = alpha_calibrate_probs(
        p_sell,
        p_hold,
        p_buy,
        policy_cfg,
        regime,
        volatility_bin
    )

    thresholds = get_dynamic_thresholds(policy_cfg, regime, volatility_bin)

    probs = {
        "SELL": cp_sell,
        "HOLD": cp_hold,
        "BUY": cp_buy,
    }

    sorted_probs = sorted(probs.items(), key=lambda kv: kv[1], reverse=True)
    top_label, top_prob = sorted_probs[0]
    second_label, second_prob = sorted_probs[1]

    margin_ok = (top_prob - second_prob) >= thresholds["margin"]

    decision = "HOLD"
    reason = "default_hold"

    if cp_hold >= thresholds["hold"] and cp_hold >= cp_buy and cp_hold >= cp_sell:
        decision = "HOLD"
        reason = "hold_probability_dominant"

    elif top_label == "BUY":
        if cp_buy >= thresholds["buy"] and margin_ok:
            decision = "BUY"
            reason = "buy_threshold_pass"
        else:
            decision = "HOLD"
            reason = "buy_threshold_or_margin_fail"

    elif top_label == "SELL":
        if cp_sell >= thresholds["sell"] and margin_ok:
            decision = "SELL"
            reason = "sell_threshold_pass"
        else:
            decision = "HOLD"
            reason = "sell_threshold_or_margin_fail"

    else:
        decision = "HOLD"
        reason = "hold_top_probability"

    # Optional policy: never trade during regime shift.
    block_on_shift = bool(policy_cfg.get("block_on_regime_shift", False))
    if regime == "shift" and block_on_shift:
        decision = "HOLD"
        reason = "blocked_by_regime_shift"

    return {
        "decision": decision,
        "reason": reason,
        "probabilities_raw": {
            "p_sell": p_sell,
            "p_hold": p_hold,
            "p_buy": p_buy,
        },
        "probabilities_calibrated": {
            "p_sell": cp_sell,
            "p_hold": cp_hold,
            "p_buy": cp_buy,
        },
        "policy": {
            "volatility_bin": volatility_bin,
            "regime": regime,
            "thresholds": thresholds,
            **cal_info,
        },
        "ranking": [
            {"label": label, "probability": prob}
            for label, prob in sorted_probs
        ],
    }


# ============================================================
# Routes
# ============================================================

@app.route("/", methods=["GET"])
def home():
    return send_from_directory(STATIC_DIR, "index.html")


@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "ok": True,
        "model_loaded": True,
        "feature_count": len(feature_cols),
        "input_shape": input_shape,
        "input_dtype": str(input_dtype),
        "output_shape": model_output["shape"].tolist(),
    })


@app.route("/schema", methods=["GET"])
def schema():
    return jsonify({
        "feature_cols": feature_cols,
        "feature_count": len(feature_cols),
        "norm_has_mean": bool(mean_map),
        "norm_has_std": bool(std_map),
        "input_shape": input_shape,
        "policy_keys": list(policy_cfg.keys()),
    })


@app.route("/predict", methods=["POST"])
def predict():
    try:
        payload = request.get_json(force=True, silent=False) or {}

        features = payload.get("features", payload)
        meta = payload.get("meta", {})

        if not isinstance(features, dict):
            return jsonify({
                "ok": False,
                "error": "features must be an object/dict"
            }), 400

        if not isinstance(meta, dict):
            meta = {}

        exact_vector, missing_features = build_exact_feature_vector(features)
        normalized_vector = normalize_vector(exact_vector)

        p_sell, p_hold, p_buy, raw_model_output = run_model(normalized_vector)

        policy_result = apply_policy_v9(
            p_sell=p_sell,
            p_hold=p_hold,
            p_buy=p_buy,
            features=features,
            meta=meta,
        )

        return jsonify({
            "ok": True,
            "result": policy_result["decision"],
            "reason": policy_result["reason"],
            "p_sell": policy_result["probabilities_calibrated"]["p_sell"],
            "p_hold": policy_result["probabilities_calibrated"]["p_hold"],
            "p_buy": policy_result["probabilities_calibrated"]["p_buy"],
            "raw": {
                "p_sell": p_sell,
                "p_hold": p_hold,
                "p_buy": p_buy,
                "model_output": raw_model_output,
            },
            "policy": policy_result["policy"],
            "ranking": policy_result["ranking"],
            "debug": {
                "missing_features_filled_zero": missing_features,
                "feature_order": feature_cols,
                "exact_vector": exact_vector,
                "normalized_vector": normalized_vector,
            }
        })

    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e),
        }), 500


# ============================================================
# Main
# ============================================================

if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
