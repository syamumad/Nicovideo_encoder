"""動画＋コメントを合成して書き出すモジュール。FFmpeg が必要。

処理の流れ:
  1. ffprobe でビデオの解像度・フレームレートを取得
  2. ffmpeg で動画を raw RGBA フレーム列にデコード（stdout へ）
  3. 各フレームに対して Qt でコメントを描画
  4. 合成済み RGBA フレームを ffmpeg にパイプして MP4 にエンコード（stdin から）
     ※ 音声は元ファイルからコピーまたは再エンコード
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from typing import Callable, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)

from .commands import CommandStyle
from .layout import CommentLayout
from .overlay import OUTLINE_MIN_PX, OUTLINE_RATIO, resolve_comment_font

# ピクスマップキャッシュの最大エントリ数
_CACHE_MAX = 512

# Windows でコンソールウィンドウを出さないフラグ
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


# ---------------------------------------------------------------------------
# オフスクリーンコメント描画
# ---------------------------------------------------------------------------

class FrameRenderer:
    """コメントをフレームに合成するオフスクリーンレンダラー。
    
    QWidget を使わず QPixmap + QPainter だけで動作する。
    CommentOverlay と同じピクスマップキャッシュ戦略を採用。
    """

    def __init__(self, width: int, height: int, comments: list) -> None:
        self.width = width
        self.height = height
        self.layout = CommentLayout(comments)
        self.layout.rebuild(float(width), float(height))
        self._font_cache: dict[int, QFont] = {}
        self._fm_cache: dict[int, QFontMetricsF] = {}
        self._pixmap_cache: dict[tuple, QPixmap] = {}
        self._base_font = resolve_comment_font()

    def _font_for(self, px: float) -> QFont:
        key = max(6, int(round(px)))
        if key not in self._font_cache:
            font = QFont(self._base_font)
            font.setPixelSize(key)
            self._font_cache[key] = font
        return self._font_cache[key]

    def _fm_for(self, px: float) -> QFontMetricsF:
        key = max(6, int(round(px)))
        if key not in self._fm_cache:
            self._fm_cache[key] = QFontMetricsF(self._font_for(px))
        return self._fm_cache[key]

    def _get_comment_pixmap(self, text: str, font_px: float, color: str) -> QPixmap:
        px_key = max(6, int(round(font_px)))
        cache_key = (text, px_key, color)
        pix = self._pixmap_cache.get(cache_key)
        if pix is not None:
            return pix

        font = self._font_for(font_px)
        fm = self._fm_for(font_px)
        outline_px = max(OUTLINE_MIN_PX, font_px * OUTLINE_RATIO)
        margin = int(outline_px) + 2

        pix_w = max(1, int(fm.horizontalAdvance(text)) + margin * 2)
        pix_h = max(1, int(fm.height()) + margin * 2)

        pix = QPixmap(pix_w, pix_h)
        pix.fill(Qt.GlobalColor.transparent)

        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        path = QPainterPath()
        path.addText(margin, margin + fm.ascent(), font, text)

        pen = QPen(QColor(0, 0, 0), outline_px)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(color))
        p.drawPath(path)
        p.end()

        if len(self._pixmap_cache) >= _CACHE_MAX:
            drop = list(self._pixmap_cache)[:_CACHE_MAX // 4]
            for k in drop:
                del self._pixmap_cache[k]

        self._pixmap_cache[cache_key] = pix
        return pix

    def render(self, raw_rgba: bytes, time_ms: int) -> bytes:
        """raw RGBA フレームにコメントを描画して raw RGBA で返す。"""
        # QImage は raw_rgba の参照を持つ（コピーしない）ので変数を保持する
        img = QImage(raw_rgba, self.width, self.height,
                     self.width * 4, QImage.Format.Format_RGBA8888)
        pixmap = QPixmap.fromImage(img)

        visible = self.layout.visible(time_ms)
        if visible:
            painter = QPainter(pixmap)
            for item, x, y in visible:
                if item.style.rotate:
                    continue  # 回転コメントは書き出し時省略
                cmt_pix = self._get_comment_pixmap(
                    item.comment.body, item.font_px, item.style.color
                )
                outline_px = max(OUTLINE_MIN_PX, item.font_px * OUTLINE_RATIO)
                margin = int(outline_px) + 2
                painter.drawPixmap(int(x) - margin, int(y) - margin, cmt_pix)
            painter.end()

        # QPixmap → QImage → bytes
        out_img = pixmap.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
        return _qimage_to_bytes(out_img)


def _qimage_to_bytes(img: QImage) -> bytes:
    """QImage の生ピクセルデータを bytes として返す。"""
    ptr = img.bits()
    # PySide6 では bits() が sip.voidptr を返す場合がある
    if hasattr(ptr, "tobytes"):
        return ptr.tobytes()
    if hasattr(ptr, "setsize"):
        ptr.setsize(img.sizeInBytes())
        return bytes(ptr)
    return bytes(ptr)


# ---------------------------------------------------------------------------
# FFmpeg ユーティリティ
# ---------------------------------------------------------------------------

def find_ffmpeg() -> tuple[str, str] | None:
    """FFmpeg と FFprobe のパスを返す。見つからなければ None。"""
    ffmpeg  = shutil.which("ffmpeg")  or shutil.which("ffmpeg.exe")
    ffprobe = shutil.which("ffprobe") or shutil.which("ffprobe.exe")
    if ffmpeg and ffprobe:
        return ffmpeg, ffprobe
    return None


def get_video_info(ffprobe: str, video_path: str) -> dict:
    """ffprobe でビデオのメタ情報（解像度・fps・長さ）を取得する。"""
    result = subprocess.run(
        [ffprobe, "-v", "quiet", "-print_format", "json",
         "-show_streams", "-show_format", video_path],
        capture_output=True, text=True, creationflags=_NO_WINDOW,
    )
    info = json.loads(result.stdout)
    video = next(
        (s for s in info.get("streams", []) if s.get("codec_type") == "video"),
        None,
    )
    if video is None:
        raise RuntimeError("ビデオストリームが見つかりません。")

    fps_str = video.get("r_frame_rate", "30/1")
    num, den = map(int, fps_str.split("/"))
    fps = num / den if den else 30.0

    duration = float(video.get("duration") or
                     info.get("format", {}).get("duration") or 0)
    total_frames = int(duration * fps) if duration > 0 else 0

    return {
        "width":        int(video["width"]),
        "height":       int(video["height"]),
        "fps":          fps,
        "fps_str":      fps_str,
        "duration":     duration,
        "total_frames": total_frames,
    }


# ---------------------------------------------------------------------------
# メイン書き出し関数
# ---------------------------------------------------------------------------

def export_with_comments(
    video_path: str,
    output_path: str,
    comments: list,
    ffmpeg: str,
    ffprobe: str,
    progress_cb: Callable[[int, int], bool] | None = None,
) -> None:
    """
    動画＋コメントを合成して output_path に書き出す。

    Args:
        video_path:   入力動画ファイルパス
        output_path:  出力ファイルパス（.mp4 推奨）
        comments:     Comment のリスト
        ffmpeg/ffprobe: 各バイナリのパス
        progress_cb:  (current_frame, total_frames) -> bool
                      False を返すとキャンセル
    """
    info = get_video_info(ffprobe, video_path)
    width        = info["width"]
    height       = info["height"]
    fps          = info["fps"]
    fps_str      = info["fps_str"]
    total_frames = info["total_frames"]

    renderer = FrameRenderer(width, height, comments)

    # --- デコーダ（動画 → raw RGBA）------------------------------------
    decode_cmd = [
        ffmpeg,
        "-i", video_path,
        "-f", "rawvideo", "-pix_fmt", "rgba",
        "-vf", f"fps={fps_str}",
        "pipe:1",
    ]
    decode_proc = subprocess.Popen(
        decode_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        creationflags=_NO_WINDOW,
    )

    # --- エンコーダ（raw RGBA + 元音声 → MP4）--------------------------
    encode_cmd = [
        ffmpeg, "-y",
        "-f", "rawvideo", "-pix_fmt", "rgba",
        "-s", f"{width}x{height}",
        "-r", fps_str,
        "-i", "pipe:0",        # コメント合成済み映像
        "-i", video_path,      # 音声ソース
        "-map", "0:v",
        "-map", "1:a?",        # 音声が無くても続行
        "-c:v", "libx264", "-crf", "18", "-preset", "fast",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        output_path,
    ]
    encode_proc = subprocess.Popen(
        encode_cmd,
        stdin=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        creationflags=_NO_WINDOW,
    )

    frame_size = width * height * 4  # RGBA
    frame_num  = 0
    cancelled  = False

    try:
        while True:
            # 1フレーム分を読み切る（ストリームの境界とずれることがある）
            raw = b""
            while len(raw) < frame_size:
                chunk = decode_proc.stdout.read(frame_size - len(raw))
                if not chunk:
                    break
                raw += chunk
            if len(raw) < frame_size:
                break  # ストリーム終端

            time_ms = int(frame_num * 1000 / fps)
            composited = renderer.render(raw, time_ms)
            encode_proc.stdin.write(composited)
            frame_num += 1

            # 5フレームごとに進捗コールバックを呼ぶ
            if progress_cb is not None and frame_num % 5 == 0:
                if not progress_cb(frame_num, total_frames):
                    cancelled = True
                    break

    finally:
        try:
            encode_proc.stdin.close()
        except Exception:
            pass
        decode_proc.terminate()
        encode_proc.wait()
        decode_proc.wait()

    if cancelled and os.path.isfile(output_path):
        os.remove(output_path)
