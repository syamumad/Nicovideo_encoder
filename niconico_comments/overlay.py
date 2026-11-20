"""コメントを動画の上に描画する QWidget（ニコ動のコメントレイヤー相当）。"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QFontMetricsF,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QWidget

from .comment_parser import Comment
from .layout import CommentLayout

# コメントの縁取り太さ（px）。文字サイズに応じて少し太くする。
OUTLINE_MIN_PX = 2.0
OUTLINE_RATIO = 0.06

# ピクスマップキャッシュの最大エントリ数（古いものを LRU 的に削除する）
_PIXMAP_CACHE_MAX = 512

# ニコ動風のゴシック体を優先順に探す
PREFERRED_FONTS = (
    "MS Gothic",
    "Meiryo",
    "Yu Gothic UI",
    "Yu Gothic",
    "Noto Sans JP",
    "MS UI Gothic",
)


def resolve_comment_font() -> QFont:
    """この環境で実際に使える日本語フォントを選ぶ。"""
    try:
        families = set(QFontDatabase.families())
    except Exception:  # noqa: BLE001
        families = set()
    for name in PREFERRED_FONTS:
        if name in families:
            return QFont(name)
    return QFont("sans-serif")


class CommentOverlay(QWidget):
    """動画の上に重ねる透明なコメント描画レイヤー。

    動画の表示領域（アスペクト比維持後の矩形）にだけ描画する。
    マウス操作は裏の動画ビューに渡す。

    最適化:
      - コメントテキストを QPixmap にオフスクリーンレンダリングしてキャッシュ。
        同一テキスト×フォントサイズ×色の組み合わせは再利用し、
        毎フレームの QPainterPath 生成と drawPath 2回呼び出しを回避する。
      - QFontMetricsF もフォントサイズ単位でキャッシュ。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self.layout = CommentLayout([])
        self.current_time_ms: int = 0
        self.comments_enabled: bool = True
        self.video_rect = QRectF(0, 0, 1280, 720)

        self._font_cache: dict[int, QFont] = {}
        self._fm_cache: dict[int, QFontMetricsF] = {}
        # key: (text, font_px_key, color_hex) -> QPixmap
        self._pixmap_cache: dict[tuple, QPixmap] = {}
        self._base_font = resolve_comment_font()
        self.resize(1280, 720)

    # ------------------------------------------------------------------ 設定
    def set_comments(self, comments: list[Comment]) -> None:
        self.layout = CommentLayout(comments)
        self.layout.rebuild(self.video_rect.width(), self.video_rect.height())
        self._pixmap_cache.clear()
        self.update()

    def set_current_time(self, time_ms: int) -> None:
        self.current_time_ms = max(0, int(time_ms))

    def set_comments_enabled(self, enabled: bool) -> None:
        self.comments_enabled = enabled
        self.update()

    def set_video_rect(self, rect: QRectF) -> None:
        """動画が表示されている領域を設定する。変わったらレイアウトを組み直す。"""
        rect = QRectF(rect)
        if rect.width() < 1 or rect.height() < 1:
            return
        if (
            abs(rect.width() - self.video_rect.width()) > 0.5
            or abs(rect.height() - self.video_rect.height()) > 0.5
        ):
            self.layout.rebuild(rect.width(), rect.height())
            self._pixmap_cache.clear()
        self.video_rect = rect
        self.update()

    # ------------------------------------------------------------------ 描画
    def paintEvent(self, event) -> None:  # noqa: N802 (Qt API)
        if not self.comments_enabled or not self.layout.laid:
            return
        painter = QPainter(self)
        # ピクスマップ転送なので SmoothPixmapTransform は不要（整数座標）
        area = self.video_rect
        painter.setClipRect(area)
        ox = int(area.left())
        oy = int(area.top())
        for item, x, y in self.layout.visible(self.current_time_ms):
            self._draw_item(painter, item, ox + x, oy + y)

    def _font_for(self, px: float) -> QFont:
        key = max(6, int(round(px)))
        font = self._font_cache.get(key)
        if font is None:
            font = QFont(self._base_font)
            font.setPixelSize(key)
            font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
            self._font_cache[key] = font
        return font

    def _fm_for(self, px: float) -> QFontMetricsF:
        key = max(6, int(round(px)))
        fm = self._fm_cache.get(key)
        if fm is None:
            fm = QFontMetricsF(self._font_for(px))
            self._fm_cache[key] = fm
        return fm

    def _get_pixmap(self, text: str, font_px: float, color: str) -> QPixmap:
        """テキストをオフスクリーンでレンダリングして QPixmap を返す。
        (text, font_px_key, color) の組み合わせでキャッシュされる。
        """
        px_key = max(6, int(round(font_px)))
        cache_key = (text, px_key, color)
        pix = self._pixmap_cache.get(cache_key)
        if pix is not None:
            return pix

        font = self._font_for(font_px)
        fm = self._fm_for(font_px)
        outline_px = max(OUTLINE_MIN_PX, font_px * OUTLINE_RATIO)
        margin = int(outline_px) + 2

        # ピクスマップサイズ: テキスト幅 + 両端の余白
        pix_w = max(1, int(fm.horizontalAdvance(text)) + margin * 2)
        pix_h = max(1, int(fm.height()) + margin * 2)

        pix = QPixmap(pix_w, pix_h)
        pix.fill(Qt.GlobalColor.transparent)

        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # パスはピクスマップ内座標 (margin, margin + ascent) に配置
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

        # キャッシュが上限に達したら古い 1/4 を捨てる
        if len(self._pixmap_cache) >= _PIXMAP_CACHE_MAX:
            drop = list(self._pixmap_cache)[:_PIXMAP_CACHE_MAX // 4]
            for k in drop:
                del self._pixmap_cache[k]

        self._pixmap_cache[cache_key] = pix
        return pix

    def _draw_item(self, painter: QPainter, item, x: float, y: float) -> None:
        style = item.style

        if style.rotate:
            # 回転コメントは頻度が低いので毎回直接描く
            self._draw_rotated(painter, item, x, y)
            return

        pix = self._get_pixmap(item.comment.body, item.font_px, style.color)
        outline_px = max(OUTLINE_MIN_PX, item.font_px * OUTLINE_RATIO)
        margin = int(outline_px) + 2
        # y はテキスト左上座標。ピクスマップの上端は margin 分だけ上にある。
        painter.drawPixmap(int(x) - margin, int(y) - margin, pix)

    def _draw_rotated(self, painter: QPainter, item, x: float, y: float) -> None:
        """回転コメント専用の直接描画。"""
        style = item.style
        text = item.comment.body
        font = self._font_for(item.font_px)
        fm = self._fm_for(item.font_px)
        width = fm.horizontalAdvance(text)
        baseline = y + fm.ascent()

        painter.save()
        center = QPointF(x + width / 2.0, y + item.font_px / 2.0)
        painter.translate(center)
        painter.rotate(style.rotate)
        painter.translate(-center)

        path = QPainterPath()
        path.addText(x, baseline, font, text)

        outline_px = max(OUTLINE_MIN_PX, item.font_px * OUTLINE_RATIO)
        pen = QPen(QColor(0, 0, 0), outline_px)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)

        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(style.color))
        painter.drawPath(path)
        painter.restore()

    # ------------------------------------------------------------------ 情報
    def visible_count(self, time_ms: int) -> int:
        return len(self.layout.visible(time_ms))
