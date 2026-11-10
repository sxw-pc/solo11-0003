"""纯本地关键帧检测算法（F3）。

算法组合：
1. 帧间差分法：缩小到固定分析尺寸后的灰度图，计算平均绝对差；
2. HSV 颜色直方图比对：量化色相直方图，计算直方图相交距离；
3. 无效帧过滤：基于亮度均值与标准差识别全黑/全白帧；
4. 片头静态跳过：自动跳过开头连续无变化片段，全程无变化时保留首帧。

全程仅使用 numpy + Pillow 在本地 CPU 运算，无任何云端 AI 调用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import numpy as np
from PIL import Image

# 分析用统一灰度尺寸，降低长视频的计算量
ANALYZE_WIDTH = 160
ANALYZE_HEIGHT = 90
# 全黑/全白判定阈值（仅依据亮度均值，避免误伤低照度有效画面）
BLACK_BRIGHTNESS = 6.0
WHITE_BRIGHTNESS = 250.0
# 保留标准差作为参考指标，用于自检报告而非无效帧硬判定
UNIFORM_STD = 6.0
# 直方图量化箱数
HIST_BINS = 32

# 灵敏度档位 → 算法阈值，阈值越低越敏感（基于 0-1 归一化帧差标定）
SENSITIVITY_PROFILES: Dict[str, Dict[str, float]] = {
    "high": {"diff_threshold": 0.0015, "hist_threshold": 0.02,
             "static_threshold": 0.0010},
    "medium": {"diff_threshold": 0.015, "hist_threshold": 0.08,
               "static_threshold": 0.010},
    "low": {"diff_threshold": 0.050, "hist_threshold": 0.20,
            "static_threshold": 0.030},
}


@dataclass
class FrameSample:
    """单个采样帧的分析输入。"""

    path: Path
    index: int
    timestamp: float


@dataclass
class KeyFrame:
    """筛选出的关键帧结果。"""

    path: Path
    index: int
    timestamp: float
    diff_score: float
    hist_distance: float
    reason: str

    def to_dict(self) -> dict:
        """序列化为可入库/可返回的字典。"""
        return {
            "path": str(self.path),
            "index": self.index,
            "timestamp": round(self.timestamp, 3),
            "diff_score": round(self.diff_score, 4),
            "hist_distance": round(self.hist_distance, 4),
            "reason": self.reason,
        }


@dataclass
class FrameFeatures:
    """单帧预计算特征，避免重复解码。"""

    gray: np.ndarray
    hist: np.ndarray
    brightness: float
    stddev: float
    is_invalid: bool = False
    reasons: List[str] = field(default_factory=list)


class KeyframeEngine:
    """关键帧筛选引擎，线程安全（无可变实例状态）。"""

    def __init__(self, sensitivity: str = "medium",
                 filter_invalid: bool = True):
        """
        :param sensitivity: 灵敏度档位 high/medium/low。
        :param filter_invalid: 是否启用全黑/全白无效帧过滤。
        """
        if sensitivity not in SENSITIVITY_PROFILES:
            raise ValueError(
                f"非法灵敏度档位 {sensitivity}，可选 "
                f"{', '.join(SENSITIVITY_PROFILES)}")
        self.sensitivity = sensitivity
        self.filter_invalid = filter_invalid
        self.profile = SENSITIVITY_PROFILES[sensitivity]

    # ---------- 特征计算 ----------

    def extract_features(self, frame_path: Path | str) -> FrameFeatures:
        """解码图片并计算灰度、直方图、亮度等特征。"""
        with Image.open(frame_path) as image:
            rgb = image.convert("RGB")
            small = rgb.resize((ANALYZE_WIDTH, ANALYZE_HEIGHT))
            hsv = small.convert("HSV")
        pixels = np.asarray(small, dtype=np.float32)
        hsv_array = np.asarray(hsv, dtype=np.uint8)
        gray = pixels.mean(axis=2) / 255.0
        brightness = float(pixels.mean())
        stddev = float(pixels.std())
        hist = self._hue_histogram(hsv_array)
        invalid, reasons = self._judge_invalid(brightness, stddev)
        return FrameFeatures(gray=gray, hist=hist, brightness=brightness,
                             stddev=stddev, is_invalid=invalid,
                             reasons=reasons)

    @staticmethod
    def _hue_histogram(hsv_array: np.ndarray) -> np.ndarray:
        """计算归一化色相直方图（低饱和度像素不参与统计）。"""
        hue = hsv_array[:, :, 0].flatten()
        saturation = hsv_array[:, :, 1].flatten()
        valid = hue[saturation > 32]
        if valid.size == 0:
            return np.ones(HIST_BINS, dtype=np.float32) / HIST_BINS
        hist, _ = np.histogram(valid, bins=HIST_BINS,
                               range=(0, 256))
        normalized = hist.astype(np.float32) / max(1, hist.sum())
        return normalized

    @staticmethod
    def _judge_invalid(brightness: float, stddev: float):
        """判定全黑/全白无效帧，返回 (是否无效, 原因列表)。

        仅以亮度均值作为硬条件：全黑帧接近纯黑、全白帧接近纯白；
        低照度但含内容的夜间画面不会被误伤。
        """
        reasons: List[str] = []
        if brightness < BLACK_BRIGHTNESS:
            reasons.append("all_black")
        if brightness > WHITE_BRIGHTNESS:
            reasons.append("all_white")
        return bool(reasons), reasons

    @staticmethod
    def frame_diff(prev: FrameFeatures, curr: FrameFeatures) -> float:
        """两帧灰度平均绝对差，归一化到 [0, 1]。"""
        return float(np.mean(np.abs(curr.gray - prev.gray)))

    @staticmethod
    def hist_distance(prev: FrameFeatures, curr: FrameFeatures) -> float:
        """色相直方图相交距离，0 表示完全相同，1 表示完全不同。"""
        intersection = float(np.minimum(prev.hist, curr.hist).sum())
        return 1.0 - intersection

    # ---------- 主筛选流程 ----------

    def detect(self, samples: Sequence[FrameSample]
               ) -> List[KeyFrame]:
        """对有序采样帧执行关键帧筛选。

        :param samples: 按时间顺序排列的采样帧。
        :return: 按时间顺序排列的关键帧列表；无有效帧时返回空列表。
        """
        if not samples:
            return []
        features = self._build_features(samples)
        valid_indexes = [i for i, item in enumerate(features)
                         if item is not None]
        if not valid_indexes:
            return []
        chosen = self._skip_leading_static(features, valid_indexes)
        keyframes: List[KeyFrame] = []
        last_idx: Optional[int] = None
        for idx in valid_indexes:
            if idx < chosen:
                continue
            if last_idx is None:
                # 片头静态片段被跳过时，首帧保留以保证摘要完整
                keyframes.append(self._make_keyframe(
                    samples[idx], features[idx], 0.0, 0.0,
                    "first_valid_frame"))
                last_idx = idx
                continue
            diff = self.frame_diff(features[last_idx], features[idx])
            hist = self.hist_distance(features[last_idx], features[idx])
            if self._is_keyframe(diff, hist):
                reason = self._describe_reason(diff, hist)
                keyframes.append(self._make_keyframe(
                    samples[idx], features[idx], diff, hist, reason))
                last_idx = idx
        if not keyframes:
            # 全程变化低于阈值（如夜间弱光静态画面）时保留首有效帧，
            # 保证摘要拼接始终有内容可用
            idx = chosen
            keyframes.append(self._make_keyframe(
                samples[idx], features[idx], 0.0, 0.0,
                "fallback_first_frame"))
        return keyframes

    def _build_features(self, samples: Sequence[FrameSample]
                        ) -> List[Optional[FrameFeatures]]:
        """批量解码特征，无效帧按配置决定保留或置空。"""
        features: List[Optional[FrameFeatures]] = []
        for sample in samples:
            feat = self.extract_features(sample.path)
            if self.filter_invalid and feat.is_invalid:
                features.append(None)
            else:
                features.append(feat)
        return features

    def _skip_leading_static(self,
                             features: List[Optional[FrameFeatures]],
                             valid_indexes: List[int]) -> int:
        """跳过片头连续静态片段，返回首个关键帧候选下标。"""
        base = features[valid_indexes[0]]
        threshold = self.profile["static_threshold"]
        for idx in valid_indexes[1:]:
            diff = self.frame_diff(base, features[idx])  # type: ignore[arg-type]
            if diff >= threshold:
                return idx
        return valid_indexes[0]

    def _is_keyframe(self, diff: float, hist: float) -> bool:
        """帧差或直方图距离任一超过档位阈值即判定为关键帧。"""
        return (diff >= self.profile["diff_threshold"]
                or hist >= self.profile["hist_threshold"])

    @staticmethod
    def _describe_reason(diff: float, hist: float) -> str:
        """生成关键帧命中原因，便于结果解释。"""
        if diff >= 0.09 and hist >= 0.32:
            return "motion_and_scene_change"
        if hist >= 0.32:
            return "scene_change"
        return "motion_detected"

    @staticmethod
    def _make_keyframe(sample: FrameSample, feat: FrameFeatures,
                       diff: float, hist: float, reason: str) -> KeyFrame:
        """构造关键帧结果对象。"""
        return KeyFrame(path=sample.path, index=sample.index,
                        timestamp=sample.timestamp, diff_score=diff,
                        hist_distance=hist, reason=reason)
