#!/usr/bin/env python3
"""PSDから画像を書き出す。psd-tools + Pillow だけで完結する。

ImageMagick や Photoshop を使わないので、Windows / macOS のどちらでも
`pip install psd-tools` だけで動く。

使い方:
    # 画面ごとの全体プレビュー（まずこれを見てデザインを把握する）
    python psd_export.py design.psd --screens -o assets/

    # 写真・ロゴなど画像化が必要なレイヤーを自動で書き出す
    python psd_export.py design.psd --assets -o assets/

    # セクション単位で書き出す（実装しながら部分を見比べたいとき）
    python psd_export.py design.psd --sections -o assets/ --screen SP_top

    # レイヤーをパス指定で書き出す
    python psd_export.py design.psd --layer "PC_top/header/header-logo" -o assets/
"""

import argparse
import os
import re
import sys

from psd_tools import PSDImage

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from psd_inspect import can_draw_in_css, find_screens, shape_style  # noqa: E402

# Windowsで使えない文字を落とす。macOSだけを見て `:` などを残すと、
# 成果物をWindowsに渡した瞬間に壊れるので最初から共通の安全側に寄せる。
UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def safe_name(name, fallback="layer"):
    text = UNSAFE.sub("-", str(name)).strip(" .")
    # レイヤー名が 'logo.svg' のように元ファイル名のままのことがある。
    # 書き出しは常にPNGなので、そのままだと 'logo.svg.png' になって紛らわしい。
    text = re.sub(r"\.(png|jpe?g|gif|svg|psd|ai|webp|tiff?)$", "", text, flags=re.I)
    text = re.sub(r"\s+", "-", text)
    text = re.sub(r"-{2,}", "-", text)
    return text[:80] or fallback


def save(image, path, quiet=False):
    if image is None:
        return False
    if image.width < 1 or image.height < 1:
        return False
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    # 透過を保ったままPNGで出す。JPEGへの変換は用途が決まってから行う。
    if image.mode not in ("RGBA", "RGB"):
        image = image.convert("RGBA")
    image.save(path)
    if not quiet:
        print(f"  {path}  ({image.width}x{image.height})")
    return True


def composite_region(layer, rect):
    """レイヤー（またはアートボード）を指定矩形で切り出して合成する。

    アートボードは中身がはみ出していてもその矩形でクリップされるので、
    viewport を渡さないとカンプより広い画像が出てしまう。
    """
    try:
        return layer.composite(viewport=rect)
    except TypeError:
        # 古いpsd-toolsへの保険。viewport非対応なら全体を合成して切る。
        image = layer.composite()
        if image is None:
            return None
        left, top, right, bottom = rect
        origin = layer.bbox
        return image.crop((left - origin[0], top - origin[1],
                           right - origin[0], bottom - origin[1]))


_screen_cache = {}


def screen_image(screen):
    """画面（アートボード）を合成した画像を返す。同じ画面は使い回す。"""
    key = id(screen["layer"])
    if key not in _screen_cache:
        left, top = screen["origin"]
        rect = (left, top, left + screen["width"], top + screen["height"])
        _screen_cache[key] = composite_region(screen["layer"], rect)
    return _screen_cache[key]


def is_blank(image):
    """完全に透明かどうか。"""
    if image is None:
        return True
    return image.convert("RGBA").getbbox() is None


def composite_layer(layer, screen=None):
    """レイヤー単体を合成する。空になったら画面から切り出して代用する。

    ベクター塗りのシェイプは psd-tools が単体で描けず、透明な画像に
    なることがある（ラスタライズ済みのピクセルを持たないため）。
    画面全体の合成には正しく現れるので、そこから同じ矩形を切り出せば
    見た目どおりの絵が得られる。背景ごと写る点だけ注意。
    """
    image = layer.composite()
    if not is_blank(image) or screen is None:
        return image

    base = screen_image(screen)
    if base is None:
        return image
    ox, oy = screen["origin"]
    left, top, right, bottom = layer.bbox
    box = (max(0, left - ox), max(0, top - oy),
           min(base.width, right - ox), min(base.height, bottom - oy))
    if box[2] <= box[0] or box[3] <= box[1]:
        return image
    print("    （単体では描画できないため画面から切り出し）")
    return base.crop(box)


def export_screens(psd, screens, outdir, reveal_queries=None):
    """画面ごとの全体プレビューを書き出す。

    reveal_queries を渡すと、その名前に一致する非表示グループを表示した
    状態で描画する。ハンバーガーを開いた画面など「もう一つの状態」を
    見るための機能で、通常版と別名（-revealed）で保存するので取り違えない。
    """
    print("== 画面プレビュー ==")
    suffix = "-revealed" if reveal_queries else ""
    for screen in screens:
        left, top = screen["origin"]
        rect = (left, top, left + screen["width"], top + screen["height"])
        hidden = []
        if reveal_queries:
            for layer, path in iter_layers(screen["layer"], str(screen["name"])):
                if not layer.visible and any(q.lower() in path.lower() for q in reveal_queries):
                    layer.visible = True
                    hidden.append(layer)
            if not hidden:
                print(f"  （{screen['name']}: 一致する非表示レイヤーなし）")
        try:
            image = composite_region(screen["layer"], rect)
            save(image, os.path.join(outdir, f"screen-{safe_name(screen['name'])}{suffix}.png"))
        finally:
            for layer in hidden:
                layer.visible = False


def iter_layers(container, path=""):
    for layer in container:
        here = f"{path}/{layer.name}" if path else str(layer.name)
        yield layer, here
        if layer.is_group():
            yield from iter_layers(layer, here)


def needs_image(layer):
    """CSSで再現できず、画像として書き出すべきレイヤーかを判定する。

    写真やロゴは当然画像。一方でベタ塗りの矩形は background-color で
    書けるので出さない——出すと無駄なファイルが増え、実装者が
    「画像で置くべきもの」と誤解してマークアップが重くなる。
    """
    if not layer.visible or layer.bbox == (0, 0, 0, 0):
        return False
    if layer.kind in ("smartobject", "pixel"):
        return True
    if layer.kind in ("shape", "solidcolor"):
        # 矩形・角丸・円のベタ塗りはCSSで書ける。グラデーションや自由な
        # パスだけを画像にする。
        return not can_draw_in_css(shape_style(layer))
    return False


def export_assets(psd, screens, outdir, min_size=8):
    print("== 画像素材 ==")
    count = 0
    for screen in screens:
        prefix = safe_name(screen["name"])
        for layer, path in iter_layers(screen["layer"]):
            if layer.is_group() or not needs_image(layer):
                continue
            width = layer.bbox[2] - layer.bbox[0]
            height = layer.bbox[3] - layer.bbox[1]
            if width < min_size or height < min_size:
                continue
            name = safe_name(layer.name)
            target = os.path.join(outdir, prefix, f"{name}.png")
            # 同名レイヤーがある場合に上書きしないよう連番を振る
            index = 2
            while os.path.exists(target):
                target = os.path.join(outdir, prefix, f"{name}-{index}.png")
                index += 1
            if save(composite_layer(layer, screen), target):
                count += 1
    print(f"  合計 {count} 点")


def export_sections(psd, screens, outdir, depth=1):
    print("== セクション ==")
    for screen in screens:
        prefix = safe_name(screen["name"])
        origin_left, origin_top = screen["origin"]
        for layer in screen["layer"]:
            if not layer.is_group() or not layer.visible:
                continue
            if layer.bbox == (0, 0, 0, 0):
                continue
            # セクションは横幅いっぱいに敷かれていても、画面の外まで
            # 見せる必要はないのでアートボード幅にクリップする
            left, top, right, bottom = layer.bbox
            rect = (max(left, origin_left), top,
                    min(right, origin_left + screen["width"]), bottom)
            if rect[2] <= rect[0] or rect[3] <= rect[1]:
                continue
            image = composite_region(layer, rect)
            save(image, os.path.join(outdir, prefix, f"section-{safe_name(layer.name)}.png"))


def reveal(layer):
    """非表示レイヤーを一時的に表示するコンテキスト。

    非表示グループは bbox が潰れて合成もできない。名指しで書き出しを
    頼まれたなら、非表示であっても中身が欲しいはずなので開いて見せる。
    元の状態には必ず戻すので、PSDファイル自体は変わらない。
    """
    class _Reveal:
        def __enter__(self):
            self.restore = not layer.visible
            if self.restore:
                layer.visible = True
            return layer

        def __exit__(self, *exc):
            if self.restore:
                layer.visible = False
            return False
    return _Reveal()


def export_layer(psd, screens, outdir, queries, flat=False):
    """パスの部分一致でレイヤー／グループを書き出す。

    PSDを開き直すのは大きなファイルほど高くつくので、複数の条件を
    一度の実行でまとめて処理できるようにしてある。グループが一致したら
    そのグループを1枚に合成し、配下には降りない（部品をばら撒かない）。
    """
    print(f"== レイヤー指定: {', '.join(queries)} ==")
    found = 0

    def visit(container, path, screen):
        nonlocal found
        for layer in container:
            here = f"{path}/{layer.name}" if path else str(layer.name)
            if any(q.lower() in here.lower() for q in queries):
                with reveal(layer):
                    # flat=True ならレイヤー名だけ、既定はパスを畳んだ一意な名前
                    name = safe_name(layer.name) if flat else safe_name(here.replace("/", "__"))
                    target = os.path.join(outdir, f"{name}.png")
                    index = 2
                    while os.path.exists(target):
                        target = os.path.join(outdir, f"{name}-{index}.png")
                        index += 1
                    if save(composite_layer(layer, screen), target):
                        found += 1
                continue  # 一致した時点で確定。配下には降りない
            if layer.is_group():
                visit(layer, here, screen)

    for screen in screens:
        visit(screen["layer"], str(screen["name"]), screen)
    if not found:
        print("  一致するレイヤーがありません。--list でパスを確認してください。")


def list_layers(screens):
    for screen in screens:
        print(f"\n--- {screen['name']} ({screen['width']}x{screen['height']}) ---")
        for layer, path in iter_layers(screen["layer"], str(screen["name"])):
            mark = "G" if layer.is_group() else layer.kind[:4]
            flag = " " if layer.visible else "x"
            print(f" {flag}[{mark:>5}] {path}")


def main():
    parser = argparse.ArgumentParser(description="PSDから画像を書き出す")
    parser.add_argument("psd")
    parser.add_argument("-o", "--outdir", default="assets", help="出力先ディレクトリ")
    parser.add_argument("--screens", action="store_true", help="画面ごとの全体プレビュー")
    parser.add_argument("--assets", action="store_true", help="画像化が必要なレイヤーを自動抽出")
    parser.add_argument("--sections", action="store_true", help="セクション単位で書き出す")
    parser.add_argument("--layer", action="append", metavar="QUERY",
                        help="パスの部分一致でレイヤーを書き出す（複数指定可）")
    parser.add_argument("--flat", action="store_true",
                        help="--layer の出力をレイヤー名だけの短いファイル名にする")
    parser.add_argument("--list", action="store_true", help="レイヤーのパス一覧を表示する")
    parser.add_argument("--screen", help="画面名で絞り込む（部分一致）")
    parser.add_argument("--reveal", action="append", metavar="QUERY",
                        help="非表示グループを表示した状態でプレビューする（部分一致、複数可）")
    args = parser.parse_args()

    psd = PSDImage.open(args.psd)
    screens, mode = find_screens(psd)
    if args.screen:
        screens = [s for s in screens if args.screen.lower() in s["name"].lower()]
        if not screens:
            print(f"画面 '{args.screen}' が見つかりません")
            return 1

    print(f"detect: {mode} / 画面 {len(screens)}件")

    if args.list:
        list_layers(screens)
        return 0

    if not any([args.screens, args.assets, args.sections, args.layer]):
        args.screens = True  # 何も指定がなければプレビューが一番役に立つ

    if args.screens:
        export_screens(psd, screens, args.outdir, args.reveal)
    if args.sections:
        export_sections(psd, screens, args.outdir)
    if args.assets:
        export_assets(psd, screens, args.outdir)
    if args.layer:
        export_layer(psd, screens, args.outdir, args.layer, args.flat)
    return 0


if __name__ == "__main__":
    sys.exit(main())
