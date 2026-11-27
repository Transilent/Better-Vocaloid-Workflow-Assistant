"""Pure tuning option validation, digest, and style-plan helpers shared by all format backends.

Lower layer beneath format-specific modules (ace_tuning, ustx_tuning, vpr_tuning,
vsqx_tuning) and tuning_engine so format modules never reach upward into tuning_engine.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math

from svp_tuning import GROUP_STYLE_ONLY, Layout

VERSION = 3
REFERENCE_CONTROLS = ("dynamics", "tension", "breathiness", "voicing")
EXPRESSION = ("loudness", "tension", "breathiness", "voicing", "gender", "toneShift")
DEFAULTS = {
    "expression_mode": "preserve",
    "track_indices": None,
    "reference_sources": [],
    "reference_dynamics": False,
    "reference_tension": False,
    "reference_breathiness": False,
    "reference_voicing": False,
    "dynamics_strength": 0.6,
    "reference_calibration": None,
    "retained_project": None,
    "retained_track_index": 0,
    "retained_sources": [],
    "retained_match_policy": "exact",
    "performance_style": "none",
    "style_strength": 0.6,
    "style_pitch": False,
    "style_scope": "lead",
    "style_detail": None,
    "style_recipe": None,
    "style_regions": [],
    "expression_density": "dense",
    "style_technique": "none",
    "technique_strength": 0.6,
    "technique_pitch": True,
}


def digest(data):
    return hashlib.sha256(
        json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()


def settings(options):
    if not isinstance(options, dict):
        raise ValueError("调音选项必须为对象。")
    unknown = set(options) - set(DEFAULTS)
    if unknown:
        raise ValueError("未知调音选项：" + ",".join(sorted(unknown)))
    result = {key: copy.deepcopy(options.get(key, value)) for key, value in DEFAULTS.items()}
    mode = result["expression_mode"]
    if mode not in ("preserve", "reference", "calibrated", "retained"):
        raise ValueError("请选择有效的参数来源。")
    for name in REFERENCE_CONTROLS:
        if type(result["reference_" + name]) is not bool:
            raise ValueError("参考参数开关必须为布尔值。")
    for name in ("dynamics_strength", "style_strength"):
        value = result[name]
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError("调音强度应为0到1的有限数值。")
        result[name] = float(value)
    from style_presets import (
        detail_settings,
        pitch_default,
        recipe_settings,
        region_settings,
        settings as style_settings,
        technique_settings,
    )

    if "style_pitch" not in options:
        result["style_pitch"] = pitch_default(result["performance_style"])
    from calibration_refinement import density_settings

    style_settings(result)
    density_settings(result)
    result["style_detail"] = detail_settings(result["style_detail"])
    recipe = result.pop("style_recipe")
    if recipe is not None:
        result["style_recipe"] = recipe_settings(recipe, result["performance_style"], result["style_pitch"])
    result["style_regions"] = region_settings(result["style_regions"])
    result.update(technique_settings(result))
    indices = result["track_indices"]
    if indices is not None and (
        not isinstance(indices, list)
        or not indices
        or any(type(x) is not int or x < 0 for x in indices)
        or len(indices) != len(set(indices))
    ):
        raise ValueError("请选择不重复的有效演唱轨索引。")
    for name in ("reference_sources", "retained_sources"):
        if not isinstance(result[name], list):
            raise ValueError("调音来源映射必须为列表。")
    for name in ("reference_calibration", "retained_project"):
        value = result[name]
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ValueError("来源路径不能为空。")
    if type(result["retained_track_index"]) is not int or result["retained_track_index"] < 0:
        raise ValueError("原参数声部索引无效。")
    has_reference = any(result["reference_" + name] for name in REFERENCE_CONTROLS)
    if mode != "reference" and has_reference:
        raise ValueError("参考映射、校准拟合和沿用参数只能选择一种；请关闭其他来源。")
    if mode != "calibrated" and result["reference_calibration"]:
        raise ValueError("校准配置与当前参数来源冲突。")
    if mode != "retained" and (result["retained_project"] or result["retained_sources"]):
        raise ValueError("沿用工程与当前参数来源冲突。")
    if mode not in ("reference", "calibrated") and result["reference_sources"]:
        raise ValueError("当前参数来源不使用参考音频。")
    if mode == "reference" and not has_reference:
        raise ValueError("请至少选择一种参考演唱参数。")
    if mode == "calibrated" and not result["reference_calibration"]:
        raise ValueError("请选择当前乐谱对应的声库校准配置。")
    if mode == "retained" and not (result["retained_project"] or result["retained_sources"]):
        raise ValueError("请选择原参数工程。")
    if result["retained_match_policy"] not in ("exact", "legacy"):
        raise ValueError("沿用参数匹配策略无效。")
    if mode != "retained" and result["retained_match_policy"] != "exact":
        raise ValueError("当前参数来源不使用沿用匹配策略。")
    return result


def selected_indices(data, options, *, layout=None):
    layout = layout if layout is not None else Layout(data)
    indices = options["track_indices"]
    if indices is None:
        vocals = [row.index for row in layout.tracks if row.instrumental is False]
        lead = [index for index in vocals if data["tracks"][index].get("name") in ("Vocals", "Lead Vocal")]
        indices = vocals if options["style_scope"] == "all_vocals" else (lead or vocals[:1])
    if not indices:
        raise ValueError("工程中没有可处理的演唱轨。")
    for index in indices:
        if not 0 <= index < len(layout.tracks) or layout.tracks[index].instrumental is not False:
            raise ValueError("所选索引不是有效的演唱轨。")
        track = layout.tracks[index]
        if not track.writable:
            raise ValueError(f"演唱轨 {index + 1}：{track.reason}")
        if track.style_only and options["expression_mode"] != "preserve":
            raise ValueError(GROUP_STYLE_ONLY)
    return list(indices)


def _has_incomplete(report):
    if isinstance(report, dict):
        if report.get("status") in ("partial", "skipped", "partial_unsupported") and report.get("reason") not in (
            "disabled",
            "zero_strength",
        ):
            return True
        return any(_has_incomplete(value) for value in report.values() if isinstance(value, (dict, list)))
    if isinstance(report, list):
        return any(_has_incomplete(value) for value in report)
    return False


def _apply_style_layers(
    project,
    preset,
    *,
    strength,
    pitch,
    detail,
    technique,
    indices,
    time_range=None,
    cancel=lambda: None,
    controls=None,
    recipe=None,
):
    """Two explicitly selected layers in one transaction, with separate audit."""
    from style_presets import apply_style

    output, emotion = apply_style(
        project,
        preset,
        strength=strength,
        track_indices=indices,
        density_mode="original",
        pitch_enabled=pitch,
        detail=detail,
        time_range=time_range,
        cancel=cancel,
        finalize=False,
        controls_allowed=controls,
        recipe=recipe,
    )
    if technique["style_technique"] == "none" or technique["technique_strength"] == 0:
        return output, emotion
    output, layer = apply_style(
        output,
        technique["style_technique"],
        strength=technique["technique_strength"],
        track_indices=indices,
        density_mode="original",
        pitch_enabled=technique["technique_pitch"],
        detail=detail,
        time_range=time_range,
        cancel=cancel,
        finalize=False,
        controls_allowed=controls,
    )
    selected = [emotion, layer]
    applied = any(r["status"] in ("applied", "partial") for r in selected)
    incomplete = any(r["status"] in ("partial", "skipped") for r in selected)
    combined = copy.deepcopy(layer if emotion["status"] == "not_selected" else emotion)
    combined.update(
        preset=preset,
        emotion=emotion,
        technique=layer,
        technique_id=technique["style_technique"],
        label=(emotion["label"] + " · " if preset != "none" or recipe is not None else "") + layer["label"],
        status="partial" if applied and incomplete else "applied" if applied else "skipped" if incomplete else "not_selected",
        applied_controls=sum(r.get("applied_controls", 0) for r in selected),
        skipped_controls=sum(r.get("skipped_controls", 0) for r in selected),
        pitch_changed=any(r.get("pitch_changed", False) for r in selected),
        notes_and_pitch_unchanged=not any(r.get("pitch_changed", False) for r in selected),
        source_content_sha256=digest(project),
        output_content_sha256=digest(output),
    )
    return output, combined


class StyleTargets:
    """Map style-engine tracks back to the real track and placement they represent."""

    def __init__(self, project, placements=()):
        self.placements = placements
        self.project = project
        if placements:
            from vocal_expression import TempoMap

            clock = TempoMap(project["time"]["tempo"])
            self.spans = [
                (
                    clock.to_seconds(min(n.start for n in p.notes)),
                    clock.to_seconds(max(n.end for n in p.notes)),
                )
                for p in self.placements
            ]

    def indices(self, tracks, time_range=None):
        if not self.placements:
            return tracks
        return [
            i
            for i, p in enumerate(self.placements)
            if p.track_index in tracks
            and (not time_range or self.spans[i][0] < time_range[1] and self.spans[i][1] > time_range[0])
        ]

    def audit(self, receipt):
        if not self.placements:
            return receipt
        if isinstance(receipt, list):
            return [self.audit(item) for item in receipt]
        if not isinstance(receipt, dict):
            return receipt
        row = {}
        for key, value in receipt.items():
            if key == "track_index":
                placement = self.placements[value]
                row.update(track_index=placement.track_index, group_placement=placement.key)
            elif key == "track_indices":
                row[key] = list(dict.fromkeys(self.placements[i].track_index for i in value))
            elif key in ("source_content_sha256", "output_content_sha256"):
                row["style_view_" + key] = value
            else:
                row[key] = self.audit(value)
        return row


def apply_style_plan(targets, options, indices, *, controls=None, cancel=lambda: None, progress=lambda text: None):
    """Apply the same region, emotion and technique rules to native placements."""
    regions = options["style_regions"]
    attempted = False
    style = {"status": "not_selected", "pitch_changed": False}
    if regions:
        progress("按时间片段叠加风格增量")
        receipts = []
        for region in regions:
            cancel()
            tracks = [region["track_index"]] if "track_index" in region else indices
            time_range = (region["start"], region["end"])
            selection = targets.indices(tracks, time_range)
            if not selection:
                receipts.append(
                    {
                        "status": "skipped",
                        "reason": "no_notes_in_region",
                        "track_indices": tracks,
                        "time_range_seconds": list(time_range),
                    }
                )
                continue
            targets.project, item = _apply_style_layers(
                targets.project,
                region["preset"],
                strength=region["strength"],
                pitch=region["pitch"],
                indices=selection,
                detail=region["detail"],
                time_range=time_range,
                cancel=cancel,
                controls=controls,
                recipe=region.get("style_recipe"),
                technique={
                    "style_technique": region.get("technique", "none"),
                    "technique_strength": region.get("technique_strength", 0.6),
                    "technique_pitch": region.get("technique_pitch", True),
                },
            )
            receipts.append(targets.audit(item))
        applied = any(r["status"] in ("applied", "partial") for r in receipts)
        incomplete = any(r["status"] in ("partial", "skipped") for r in receipts)
        style = {
            "status": "partial" if applied and incomplete else "applied" if applied else "skipped" if incomplete else "not_selected",
            "mode": "regions",
            "label": "时间片段风格",
            "regions": receipts,
            "applied_controls": sum(r.get("applied_controls", 0) for r in receipts),
            "skipped_controls": sum(r.get("skipped_controls", 0) for r in receipts),
            "pitch_changed": any(r.get("pitch_changed") for r in receipts),
            "outside_regions_style_unchanged": True,
        }
        attempted = True
    elif (
        (options["performance_style"] != "none" or options.get("style_recipe")) and options["style_strength"] > 0
    ) or (options["style_technique"] != "none" and options["technique_strength"] > 0):
        progress("叠加所选风格增量")
        targets.project, style = _apply_style_layers(
            targets.project,
            options["performance_style"],
            strength=options["style_strength"],
            indices=targets.indices(indices),
            pitch=options["style_pitch"],
            detail=options["style_detail"],
            technique=options,
            cancel=cancel,
            controls=controls,
            recipe=options.get("style_recipe"),
        )
        style = targets.audit(style)
        attempted = True
    return style, attempted
