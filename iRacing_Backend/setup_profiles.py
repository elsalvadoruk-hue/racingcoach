"""Normalize observed Garage names without inventing absent controls or ranges."""
import re


ALIASES = {
    "front_arb_blades": ("chassis.frontbrakes.arbblades", "chassis.front.arbblades", "chassis.frontarb.arbblades", "chassis.frontarbblades"),
    "rear_arb_blades": ("chassis.reararb.arbblades", "chassis.reararbblades"),
    "rear_wing_angle_deg": ("chassis.rear.wingangle", "tiresaero.rearwing.wingangle", "tiresaero.rearwing.rearwingangle", "tiresaero.rear.wingangle"),
    "front_toe_mm": ("chassis.frontbrakes.totaltoein", "chassis.front.totaltoein"),
    "diff_preload_nm": ("chassis.gearsdifferential.diffpreload", "chassis.gearboxdifferential.diffpreload", "chassis.differential.preload", "drivetrain.differential.preload"),
    "brake_bias_pct": ("chassis.incaradjustments.brakebias", "incaradjustments.brakepressurebias", "chassis.frontbrakes.brakepressurebias"),
    "abs_setting": ("incaradjustments.abssetting", "chassis.incaradjustments.abs"),
    "tc_setting": ("incaradjustments.tcsetting", "chassis.incaradjustments.tractioncontrolsetting"),
    "front_high_speed_compression": ("dampers.frontdampers.highspeedcompressiondamping",),
    "rear_high_speed_compression": ("dampers.reardampers.highspeedcompressiondamping",),
}


def normalized_leaves(tree, prefix=""):
    result = {}
    for key, value in tree.items() if isinstance(tree, dict) else []:
        name = re.sub(r"[^a-z0-9]", "", str(key).lower())
        path = f"{prefix}.{name}" if prefix else name
        if isinstance(value, dict):
            result.update(normalized_leaves(value, path))
        else:
            result[path] = value
    return result


def supplement_setup_summary(tree, summary, parse_number):
    summary = dict(summary)
    leaves = normalized_leaves(tree)
    for setting, names in ALIASES.items():
        if summary.get(setting) is not None:
            continue
        for name in names:
            raw = leaves.get(name)
            number = parse_number(raw)
            if number is not None:
                summary[setting] = number
                break
    return summary


def vehicle_setup_capabilities(metadata, summary):
    controls = []
    for setting in ("front_arb_blades", "rear_arb_blades", "rear_wing_angle_deg", "diff_preload_nm", "brake_bias_pct", "abs_setting", "tc_setting", "front_high_speed_compression", "rear_high_speed_compression"):
        current = summary.get(setting)
        controls.append({"setting": setting, "current_value": current, "available": current is not None,
                         "numeric_click_verified": current is not None and setting in {"front_arb_blades", "rear_arb_blades"},
                         "scale_direction_verified": setting not in {"abs_setting", "tc_setting", "front_high_speed_compression", "rear_high_speed_compression"},
                         "reason": None if current is not None else "이 IBT의 차량 셋업에 해당 값이 없습니다."})
    return {"car_name": metadata.get("car_name"), "car_path": metadata.get("car_path"),
            "source": "OBSERVED_EMBEDDED_SETUP", "controls": controls,
            "garage_limits_verified": False,
            "policy": "현재 값이 확인된 조절부만 사용합니다. 차량별 단계가 확인되지 않으면 숫자 대신 Garage 한 클릭을 제안합니다."}
