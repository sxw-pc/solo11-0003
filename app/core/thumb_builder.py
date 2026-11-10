"""缩略图生成、时间戳叠加与摘要长图拼接（F4）。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFont

from ..utils.time_helper import format_timestamp

# 宽度允许配置区间
MIN_THUMB_WIDTH = 160
MAX_THUMB_WIDTH = 1920
DEFAULT_THUMB_WIDTH = 320
# 拼接方向
ORIENTATIONS = ("horizontal", "vertical")
# 缩略图之间的分隔线宽度与颜色
GAP = 4
GAP_COLOR = (40, 40, 40)
TEXT_COLOR = (255, 255, 0)
TEXT_BG = (0, 0, 0)


@dataclass
class ThumbItem:
    """单张缩略图产物。"""

    path: Path
    timestamp: float
    index: int

    def to_dict(self) -> dict:
        """序列化输出。"""
        return {"path": str(self.path), "index": self.index,
                "timestamp": round(self.timestamp, 3)}


class ThumbBuilder:
    """缩略图与拼接图构建器。"""

    def __init__(self, thumb_width: int = DEFAULT_THUMB_WIDTH,
                 orientation: str = "vertical"):
        """
        :param thumb_width: 缩略图统一宽度，限制在 160-1920。
        :param orientation: 拼接方向 horizontal/vertical。
        """
        if not MIN_THUMB_WIDTH <= thumb_width <= MAX_THUMB_WIDTH:
            raise ValueError(
                f"缩略图宽度需在 {MIN_THUMB_WIDTH}-{MAX_THUMB_WIDTH} 之间")
        if orientation not in ORIENTATIONS:
            raise ValueError(
                f"拼接方向仅支持 {', '.join(ORIENTATIONS)}")
        self.thumb_width = thumb_width
        self.orientation = orientation

    def build_thumbnails(self, frame_paths: Sequence[Path],
                         timestamps: Sequence[float],
                         output_dir: Path | str) -> List[ThumbItem]:
        """批量生成统一尺寸、带时间戳叠加的缩略图。

        :param frame_paths: 源帧图片路径，按时间顺序排列。
        :param timestamps: 与帧一一对应的视频时间戳（秒）。
        :param output_dir: 缩略图输出目录。
        :return: 缩略图描述对象列表。
        """
        if len(frame_paths) != len(timestamps):
            raise ValueError("帧路径与时间戳数量不一致")
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        items: List[ThumbItem] = []
        target_size = self._resolve_target_size(frame_paths[0])
        font = self._load_font(max(12, target_size[1] // 12))
        for index, (frame_path, ts) in enumerate(
                zip(frame_paths, timestamps)):
            out_path = output_dir / f"thumb_{index:04d}.jpg"
            with Image.open(frame_path) as image:
                canvas = image.convert("RGB").resize(target_size)
            self._draw_timestamp(canvas, format_timestamp(ts), font)
            canvas.save(out_path, format="JPEG", quality=88)
            items.append(ThumbItem(path=out_path, timestamp=ts,
                                   index=index))
        return items

    def build_montage(self, thumb_items: Sequence[ThumbItem],
                      output_path: Path | str) -> Path:
        """把缩略图按时间顺序拼接为单张长图。

        :param output_path: 拼接图输出路径（.jpg）。
        :return: 实际写入的文件路径。
        """
        if not thumb_items:
            raise ValueError("没有可拼接的缩略图")
        images = [Image.open(item.path).convert("RGB")
                  for item in thumb_items]
        try:
            canvas = self._compose_canvas(images)
        finally:
            for image in images:
                image.close()
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(output_path, format="JPEG", quality=90)
        if not output_path.is_file():
            raise OSError(f"拼接图写入失败: {output_path}")
        return output_path

    def _resolve_target_size(self, sample_frame: Path) -> Tuple[int, int]:
        """按源帧宽高比计算统一缩略图尺寸。"""
        with Image.open(sample_frame) as image:
            src_w, src_h = image.size
        height = round(self.thumb_width * src_h / max(1, src_w))
        return self.thumb_width, max(1, height)

    def _compose_canvas(self, images: List[Image.Image]) -> Image.Image:
        """根据拼接方向创建画布并逐张粘贴。"""
        if self.orientation == "horizontal":
            width = sum(img.width for img in images) + GAP * (
                len(images) - 1)
            height = max(img.height for img in images)
            canvas = Image.new("RGB", (width, height), GAP_COLOR)
            offset = 0
            for img in images:
                canvas.paste(img, (offset, 0))
                offset += img.width + GAP
            return canvas
        width = max(img.width for img in images)
        height = sum(img.height for img in images) + GAP * (
            len(images) - 1)
        canvas = Image.new("RGB", (width, height), GAP_COLOR)
        offset = 0
        for img in images:
            canvas.paste(img, (0, offset))
            offset += img.height + GAP
        return canvas

    @staticmethod
    def _draw_timestamp(canvas: Image.Image, text: str,
                        font: ImageFont.ImageFont) -> None:
        """在缩略图左上角绘制黑底黄字时间戳。"""
        draw = ImageDraw.Draw(canvas)
        left, top = 4, 4
        bbox = draw.textbbox((left, top), text, font=font)
        draw.rectangle((bbox[0] - 2, bbox[1] - 2,
                        bbox[2] + 2, bbox[3] + 2), fill=TEXT_BG)
        draw.text((left, top), text, fill=TEXT_COLOR, font=font)

    @staticmethod
    def _load_font(size: int) -> ImageFont.ImageFont:
        """加载本地等宽字体，找不到 TrueType 时回退到内置位图字体。

        仅探测操作系统自带字体文件，不下载任何外部字体资源。
        """
        windir = Path(os.environ.get("WINDIR", "C:/Windows"))
        candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            str(windir / "Fonts" / "consola.ttf"),
            str(windir / "Fonts" / "arial.ttf"),
            "/System/Library/Fonts/Menlo.ttc",
        ]
        for candidate in candidates:
            if Path(candidate).is_file():
                try:
                    return ImageFont.truetype(candidate, size=size)
                except OSError:  # pragma: no cover - 字体损坏时继续回退
                    continue
        return ImageFont.load_default()
