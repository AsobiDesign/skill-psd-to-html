#!/usr/bin/env python3
"""PSDの構造を読み取り、コーディングに必要な情報をJSONで出力する。

依存は psd-tools>=1.17 のみ（Pillowは psd-tools が連れてくる）。
ImageMagick などの外部コマンドは使わないので Windows / macOS で同じように動く。

使い方:
    python psd_inspect.py design.psd                    # 要約を人間向けに表示
    python psd_inspect.py design.psd --json out.json    # 全情報をJSONで保存
    python psd_inspect.py design.psd --screen SP_top    # 特定画面だけ見る
"""

import argparse
import json
import re
import sys

from psd_tools import PSDImage
from psd_tools.constants import Tag

# ---------------------------------------------------------------- 出力の文字コード


def use_utf8_stdout():
    """標準出力をUTF-8に固定する。

    Windowsのコンソールは既定がcp932で、日本語混じりの出力をファイルへ
    リダイレクトすると UnicodeEncodeError で落ちる。書き出すファイルは
    encoding を明示しているが、標準出力だけは環境まかせになるため。
    """
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")


# ---------------------------------------------------------------- フォント

# PostScript名の末尾から太さを読む。Photoshopは 'Outfit-Medium' のような
# PostScript名を持つので、ハイフン以降のサフィックスが手がかりになる。
WEIGHT_TABLE = [
    ("extrablack", 950), ("ultrablack", 950),
    ("extrabold", 800), ("ultrabold", 800),
    ("semibold", 600), ("demibold", 600),
    ("extralight", 200), ("ultralight", 200),
    ("black", 900), ("heavy", 900),
    ("bold", 700),
    ("medium", 500),
    ("light", 300),
    ("thin", 100),
    ("regular", 400), ("normal", 400), ("roman", 400), ("book", 400),
]

# 和文フォントはPostScript名とCSSのfamily名が一致しない。実務で出会う
# ものだけ載せている。ここに無いものは推定にまかせて raw_font を見て判断する。
FAMILY_ALIASES = {
    "notosansjp": "Noto Sans JP",
    "notoserifjp": "Noto Serif JP",
    "notosanscjkjp": "Noto Sans JP",
    "notoserifcjkjp": "Noto Serif JP",
    "sourcehansans": "Noto Sans JP",
    "sourcehanserif": "Noto Serif JP",
    "kozgopr6n": "Noto Sans JP",       # 小塚ゴシック: Web配信不可のため代替
    "kozgopro": "Noto Sans JP",
    "kozminpr6n": "Noto Serif JP",     # 小塚明朝: 同上
    "kozminpro": "Noto Serif JP",
    "hiraginosans": "Hiragino Sans",
    "hirakakupron": "Hiragino Sans",
    "hirakakupro": "Hiragino Sans",
    "hiraginokakugothic": "Hiragino Sans",
    "yugothic": "Yu Gothic",
    "yugothicui": "Yu Gothic UI",
    "yumincho": "Yu Mincho",
    "meiryo": "Meiryo",
    "msgothic": "MS Gothic",
    "msmincho": "MS Mincho",
}

# 代替に置き換えたことを利用者に伝えるためのメモ
SUBSTITUTED = {"kozgopr6n", "kozgopro", "kozminpr6n", "kozminpro",
               "sourcehansans", "sourcehanserif", "notosanscjkjp", "notoserifcjkjp"}

# Photoshopが内部的に挿む見えないフォント。CSSには一切関係しない。
IGNORED_FONTS = {"adobeinvisfont", "myriadpro"}


def parse_font(ps_name):
    """PostScript名を CSS の family / weight / style に分解する。

    'NotoSansJP-Bold' -> ('Noto Sans JP', 700, 'normal')
    """
    raw = str(ps_name or "").strip().strip("'\"")
    base = raw.split("-")[0] if "-" in raw else raw
    suffix = raw[len(base) + 1:].lower() if "-" in raw else ""
    key = re.sub(r"[^a-z0-9]", "", base.lower())

    weight = 400
    for token, value in WEIGHT_TABLE:
        if token in suffix or token in raw.lower().replace(base.lower(), "", 1):
            weight = value
            break

    style = "italic" if ("italic" in suffix or "oblique" in suffix) else "normal"

    family = FAMILY_ALIASES.get(key)
    if family is None:
        # CamelCase を単語に割る: 'OpenSans' -> 'Open Sans'
        family = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", base).strip()

    return {
        "family": family,
        "weight": weight,
        "style": style,
        "raw": raw,
        "substituted": key in SUBSTITUTED,
    }


# ---------------------------------------------------------------- 色

def to_hex(values, color_type=1):
    """PhotoshopのFillColor.Valuesを#rrggbbに変換する。

    Values の並びは [alpha, R, G, B]（0.0-1.0）で、alphaが先頭にくる点が
    間違えやすい。Type=1 がRGB、それ以外（グレースケール等）は近似する。
    """
    try:
        vals = [float(v) for v in values]
    except (TypeError, ValueError):
        return None
    if len(vals) >= 4:
        _, r, g, b = vals[0], vals[1], vals[2], vals[3]
    elif len(vals) == 2:  # グレースケール [alpha, gray]
        r = g = b = vals[1]
    else:
        return None
    return "#{:02x}{:02x}{:02x}".format(
        max(0, min(255, round(r * 255))),
        max(0, min(255, round(g * 255))),
        max(0, min(255, round(b * 255))),
    )


def descriptor_color(desc):
    """Photoshopの色Descriptor（0-255の実数）を#rrggbbにする。"""
    if not desc:
        return None
    try:
        vals = [float(desc[k]) for k in (b"Rd  ", b"Grn ", b"Bl  ")]
    except (KeyError, TypeError, ValueError):
        return None
    return "#{:02x}{:02x}{:02x}".format(
        *[max(0, min(255, round(v))) for v in vals]
    )


# keyOriginType: シェイプの素性。CSSで書けるかの判断に直結する。
SHAPE_TYPES = {1: "rect", 2: "roundrect", 4: "line", 5: "ellipse"}


def shape_style(layer):
    """シェイプレイヤーから、CSSで再現するのに必要な情報を取り出す。

    背景の矩形・ボタン・バッジは画像化せずCSSで書いたほうが軽く、
    文字を載せられて、後から色も直せる。そのために塗り・線・角丸を
    Photoshopのタグから直接読む。

    - 塗りつぶしレイヤーは SoCo、シェイプツールで描いた図形は vscg に色が入る
    - vstk の fillEnabled / strokeEnabled は「塗りなし・線だけ」のゴースト
      ボタンを見分けるのに要る。色が取れても fillEnabled が False なら
      背景色を付けてはいけない
    - vogk の keyOriginRRectRadii が border-radius にそのまま対応する
    """
    blocks = getattr(layer, "tagged_blocks", None)
    if not blocks:
        return None

    style = {"fill": None, "fill_enabled": True, "fill_kind": None,
             "stroke": None, "radius": None, "shape_type": None, "shape_bbox": None}

    try:
        if Tag.SOLID_COLOR_SHEET_SETTING in blocks:
            style["fill"] = descriptor_color(blocks.get_data(Tag.SOLID_COLOR_SHEET_SETTING).get(b"Clr "))
            style["fill_kind"] = "solid"
        elif Tag.VECTOR_STROKE_CONTENT_DATA in blocks:
            data = blocks.get_data(Tag.VECTOR_STROKE_CONTENT_DATA)
            if b"Clr " in data:
                style["fill"] = descriptor_color(data[b"Clr "])
                style["fill_kind"] = "solid"
            elif b"Grad" in data or b"Grdn" in data:
                style["fill_kind"] = "gradient"
            else:
                style["fill_kind"] = "pattern"
        elif Tag.GRADIENT_FILL_SETTING in blocks:
            style["fill_kind"] = "gradient"
        elif Tag.PATTERN_FILL_SETTING in blocks:
            style["fill_kind"] = "pattern"
    except Exception:
        pass

    try:
        if Tag.VECTOR_STROKE_DATA in blocks:
            stroke = blocks.get_data(Tag.VECTOR_STROKE_DATA)
            style["fill_enabled"] = bool(stroke.get(b"fillEnabled", True))
            width = round(float(stroke.get(b"strokeStyleLineWidth", 1)), 2)
            # strokeEnabled が立っていても線幅0のことがある（設定の残骸）。
            # そのまま border に書き起こすと存在しない線を引いてしまう。
            if stroke.get(b"strokeEnabled", False) and width > 0:
                style["stroke"] = {
                    "color": descriptor_color((stroke.get(b"strokeStyleContent") or {}).get(b"Clr ")),
                    "width": width,
                    "opacity": round(float(stroke.get(b"strokeStyleOpacity", 100)) / 100, 3),
                }
    except Exception:
        pass

    try:
        if Tag.VECTOR_ORIGINATION_DATA in blocks:
            entries = blocks.get_data(Tag.VECTOR_ORIGINATION_DATA).get(b"keyDescriptorList") or []
            if entries:
                origin = entries[0]
                style["shape_type"] = SHAPE_TYPES.get(int(origin.get(b"keyOriginType", 0)), "path")
                radii = origin.get(b"keyOriginRRectRadii")
                if radii:
                    vals = [float(radii[k]) for k in
                            (b"topLeft", b"topRight", b"bottomRight", b"bottomLeft") if k in radii]
                    if vals:
                        # 4隅が同じなら1値、違えばCSSと同じ時計回りの並びで返す
                        style["radius"] = round(vals[0], 1) if len(set(vals)) == 1 else [round(v, 1) for v in vals]
                box = origin.get(b"keyOriginShapeBBox")
                if box:
                    # 線の太さを含まない、素の形状の矩形。CSSの座標にはこちらが近い
                    style["shape_bbox"] = [round(float(box[k]), 1) for k in
                                           (b"Left", b"Top ", b"Rght", b"Btom")]
    except Exception:
        pass

    return style


CSS_DRAWABLE = {"rect", "roundrect", "ellipse", "line"}


def can_draw_in_css(style):
    """このシェイプをCSSだけで再現できるか。

    単純な矩形・角丸・円で、塗りが単色（または線だけ）なら div とCSSで
    書ける。グラデーションや自由なパスは画像に頼るしかない。
    """
    if not style:
        return False
    if style.get("shape_type") not in CSS_DRAWABLE:
        return False
    has_fill = style.get("fill") and style.get("fill_enabled")
    return bool(has_fill or style.get("stroke"))


# ---------------------------------------------------------------- テキスト

JUSTIFY = {0: "left", 1: "right", 2: "center", 3: "justify",
           4: "justify", 5: "justify", 6: "justify"}


def text_style(layer):
    """テキストレイヤーからCSSに落とせる書式を取り出す。

    Photoshopは1つのレイヤーの中で文字ごとに書式を変えられるので、
    StyleRun（書式の区間）を全部返す。最初の区間が支配的なことが多いが、
    「一部だけ色違い・太字」のデザインを取りこぼさないため全部保持する。
    """
    try:
        engine = layer.engine_dict
        fontset = layer.resource_dict.get("FontSet", [])
    except Exception:
        return None

    fonts = [str(f.get("Name", "")).strip("'\"") for f in fontset]
    text = layer.text.replace("\r", "\n")

    # 変形（拡大縮小）がかかっているとFontSizeの見かけの大きさが変わる
    scale = 1.0
    try:
        tr = layer.transform
        if tr and len(tr) >= 4:
            scale = round(float(tr[0]), 4) or 1.0
    except Exception:
        pass

    runs = []
    try:
        style_run = engine["StyleRun"]
        arr = style_run["RunArray"]
        lengths = style_run["RunLengthArray"]
        pos = 0
        for style, length in zip(arr, lengths):
            data = style["StyleSheet"]["StyleSheetData"]
            length = int(length)
            idx = int(data.get("Font", 0))
            raw_font = fonts[idx] if 0 <= idx < len(fonts) else ""
            if re.sub(r"[^a-z0-9]", "", raw_font.lower()) in IGNORED_FONTS:
                pos += length
                continue
            size = round(float(data.get("FontSize", 0)) * scale, 2)
            tracking = float(data.get("Tracking", 0))
            fill = data.get("FillColor")
            runs.append({
                "text": text[pos:pos + length],
                "font": parse_font(raw_font),
                "font_size": size,
                # Photoshopのtrackingは1/1000em単位。CSSはemでそのまま表せる。
                "letter_spacing": round(tracking / 1000.0, 4),
                "color": to_hex(fill["Values"], int(fill.get("Type", 1))) if fill else None,
                "faux_bold": bool(data.get("FauxBold", False)),
                "faux_italic": bool(data.get("FauxItalic", False)),
                "underline": bool(data.get("Underline", False)),
            })
            pos += length
    except Exception:
        pass

    align = "left"
    line_height = None
    try:
        props = engine["ParagraphRun"]["RunArray"][0]["ParagraphSheet"]["Properties"]
        align = JUSTIFY.get(int(props.get("Justification", 0)), "left")
        if "Leading" in props:
            leading = float(props["Leading"]) * scale
            first = runs[0]["font_size"] if runs else 0
            if first:
                line_height = round(leading / first, 3)
    except Exception:
        pass

    return {"text": text, "runs": runs, "text_align": align, "line_height": line_height}


# ---------------------------------------------------------------- 画面の切り出し

SP_HINT = re.compile(r"(^|[^a-z])(sp|smart\s*phone|smartphone|mobile|mb)([^a-z]|$)|スマホ|スマートフォン|モバイル", re.I)
PC_HINT = re.compile(r"(^|[^a-z])(pc|desktop|dt|web)([^a-z]|$)|デスクトップ|パソコン", re.I)
TABLET_HINT = re.compile(r"(^|[^a-z])(tab|tablet|ipad)([^a-z]|$)|タブレット", re.I)


def guess_device(name, width):
    """画面名と幅からデバイスを推定する。

    名前が最優先。命名は 'PC_top' / 'pc' / 'PC' のように現場ごとに揺れるので、
    名前で決まらないときだけ幅で判定する。SPカンプは375/390/414/750が定番。
    """
    if SP_HINT.search(name or ""):
        return "sp"
    if TABLET_HINT.search(name or ""):
        return "tablet"
    if PC_HINT.search(name or ""):
        return "pc"
    if width <= 500 or width in (750, 828, 1080):
        return "sp"
    if width <= 900:
        return "tablet"
    return "pc"


DEVICE_TOKENS = re.compile(
    r"(?i)(smart\s*phone|smartphone|mobile|tablet|ipad|desktop|"
    r"スマホ|スマートフォン|モバイル|タブレット|デスクトップ|パソコン)"
)
DEVICE_SHORT = re.compile(r"(?i)(^|[^a-z])(sp|pc|dt|mb|tab)([^a-z]|$)")


def page_key(name):
    """画面名からデバイス表記を取り除き、ページ名を得る。

    'PC_top' と 'SP_top' を同じ「topページ」として結びつけるために使う。
    レスポンシブは1つのHTMLでPCとSPを兼ねるので、この対応づけが取れないと
    別ページとして二重に実装してしまう。デバイス表記しか無い（'pc' / 'SP'）
    場合は index ページとみなす。
    """
    text = DEVICE_TOKENS.sub(" ", str(name or ""))
    # 短縮形は前後の文字ごと消えるため、区切りを補いながら繰り返し適用する
    while DEVICE_SHORT.search(text):
        text = DEVICE_SHORT.sub(r"\1 \3", text)
    text = re.sub(r"[_\-\s]+", " ", text).strip(" _-")
    return text.lower() or "index"


def content_box(screen_width, sections, fallback):
    """コンテンツを収めているコンテナ幅（CSSのmax-width）を推定する。

    PCカンプは画面幅1920でも中身は中央1080pxに収まっている、という作りが
    普通で、この内側の幅がCSSのコンテナ幅になる。判定を左右の最大範囲で
    やると、背景の敷き物や意図的にはみ出させた装飾を拾って過大になるため、
    「同じ幅が繰り返し現れる＝それがコンテナ」という性質を使って最頻値を取る。
    画面幅の95%以上ある要素は全幅の背景とみなして最初に除外する。
    """
    limit = screen_width * 0.95
    widths = {}
    origins = {}

    def tally(items):
        for item in items:
            x, _, w, _ = item["bbox"]
            if not (0 < w < limit):
                continue
            key = round(w / 4) * 4  # 1〜2pxのブレを吸収する
            widths[key] = widths.get(key, 0) + 1
            origins.setdefault(key, []).append(x)

    tally([s for s in sections if s["depth"] == 1])
    if not widths:
        tally(sections)
    if not widths:
        tally(fallback)
    if not widths:
        return None

    # 出現回数が同じなら広いほうを採る（狭い内側ブロックより外側の器が欲しい）
    width = max(widths, key=lambda w: (widths[w], w))
    left = min(origins[width])
    return {
        "width": width,
        "left": left,
        "centered": abs(left - (screen_width - (left + width))) <= 8,
        "occurrences": widths[width],
    }


def artboard_rect(layer):
    """レイヤーがアートボードならその矩形を返す。違えばNone。"""
    blocks = getattr(layer, "tagged_blocks", None)
    if not blocks:
        return None
    for key in (Tag.ARTBOARD_DATA1, Tag.ARTBOARD_DATA2, Tag.ARTBOARD_DATA3):
        if key in blocks:
            try:
                rect = blocks.get_data(key)[b"artboardRect"]
                return (int(rect[b"Left"]), int(rect[b"Top "]),
                        int(rect[b"Rght"]), int(rect[b"Btom"]))
            except Exception:
                return tuple(layer.bbox)
    return None


def find_screens(psd):
    """PSDから「1画面ぶん」の単位を取り出す。

    アートボードで作られたPSDと、1枚のキャンバスに直接組まれたPSDの
    両方が実務では来る。次の順に判定して、どちらでも同じ形の結果を返す。

      1. アートボードがある → それが画面
      2. アートボードは無いが、最上位グループ名がPC/SPを示している
         → 横並びに置かれた2カンプとみなす
      3. どちらでもない → キャンバス全体を1画面とする
    """
    artboards = []
    for layer in psd:
        rect = artboard_rect(layer)
        if rect:
            artboards.append((layer, rect))
    if artboards:
        screens = []
        for layer, (left, top, right, bottom) in artboards:
            screens.append({
                "layer": layer, "name": str(layer.name),
                "origin": (left, top),
                "width": right - left, "height": bottom - top,
                "source": "artboard",
            })
        return screens, "artboard"

    groups = [l for l in psd if l.is_group() and l.visible and l.bbox != (0, 0, 0, 0)]
    hinted = [g for g in groups if SP_HINT.search(str(g.name)) or PC_HINT.search(str(g.name))]
    if len(hinted) >= 2:
        screens = []
        for g in hinted:
            left, top, right, bottom = g.bbox
            screens.append({
                "layer": g, "name": str(g.name),
                "origin": (left, top),
                "width": right - left, "height": bottom - top,
                "source": "group",
            })
        return screens, "group"

    return [{
        "layer": psd, "name": "canvas",
        "origin": (0, 0),
        "width": psd.width, "height": psd.height,
        "source": "canvas",
    }], "canvas"


# ---------------------------------------------------------------- 走査

def collect(container, origin, path=""):
    """画面配下のレイヤーを再帰的に集めて、用途別に振り分ける。

    座標は必ずアートボード原点からの相対に直す。PSDのbboxはキャンバス
    絶対座標なので、そのままCSSに書くと画面が横並びのぶんだけズレる。
    """
    ox, oy = origin
    sections, texts, images, shapes = [], [], [], []

    def rel(bbox):
        left, top, right, bottom = bbox
        return [left - ox, top - oy, right - left, bottom - top]

    def walk(layer, depth, parent_path):
        here = f"{parent_path}/{layer.name}" if parent_path else str(layer.name)
        visible = bool(layer.visible)

        # 非表示のグループは bbox が (0,0,0,0) に潰れ、中身の座標も読めなくなる。
        # ところがデザイナーは「開いた状態のハンバーガーメニュー」「ホバー時の
        # 見た目」を非表示のまま同じPSDに置いておくことが多く、そこはコーディング
        # に必要な情報そのもの。読む間だけ表示に切り替えて、読み終えたら元に戻す。
        revealed = False
        if layer.is_group() and not visible and layer.bbox == (0, 0, 0, 0):
            layer.visible = True
            revealed = True

        try:
            empty = layer.bbox == (0, 0, 0, 0)

            if layer.is_group():
                if depth <= 2 and not empty:
                    sections.append({
                        "path": here, "name": str(layer.name),
                        "bbox": rel(layer.bbox), "depth": depth,
                        "visible": visible,
                    })
                for child in layer:
                    walk(child, depth + 1, here)
                return

            if empty:
                return

            collect_leaf(layer, here, visible)
        finally:
            if revealed:
                layer.visible = False

    def collect_leaf(layer, here, visible):
        entry = {"path": here, "name": str(layer.name),
                 "bbox": rel(layer.bbox), "visible": visible,
                 "opacity": round(layer.opacity / 255, 3)}

        if layer.kind == "type":
            style = text_style(layer)
            if style:
                entry.update(style)
                texts.append(entry)
        elif layer.kind in ("smartobject", "pixel"):
            images.append(entry)
        elif layer.kind in ("shape", "solidcolor"):
            style = shape_style(layer) or {}
            if style.get("shape_bbox"):
                left, top, right, bottom = style["shape_bbox"]
                style["shape_bbox"] = [round(left - ox, 1), round(top - oy, 1),
                                       round(right - left, 1), round(bottom - top, 1)]
            entry.update(style)
            entry["css_ready"] = can_draw_in_css(style)
            shapes.append(entry)

    if hasattr(container, "__iter__"):
        for child in container:
            walk(child, 1, path)
    return sections, texts, images, shapes


def inspect(psd_path, screen_filter=None):
    psd = PSDImage.open(psd_path)
    screens_raw, mode = find_screens(psd)

    result = {
        "file": str(psd_path),
        "canvas": {"width": psd.width, "height": psd.height},
        "screen_detection": mode,
        "screens": [],
    }

    fonts_seen = {}
    for screen in screens_raw:
        if screen_filter and screen_filter.lower() not in screen["name"].lower():
            continue
        sections, texts, images, shapes = collect(screen["layer"], screen["origin"])
        for text in texts:
            for run in text.get("runs", []):
                font = run["font"]
                key = (font["family"], font["weight"], font["style"])
                fonts_seen.setdefault(key, {**font, "count": 0})["count"] += 1
        result["screens"].append({
            "name": screen["name"],
            "device": guess_device(screen["name"], screen["width"]),
            "page": page_key(screen["name"]),
            "source": screen["source"],
            "origin": list(screen["origin"]),
            "width": screen["width"],
            "height": screen["height"],
            "content_box": content_box(screen["width"], sections, texts + shapes + images),
            "sections": sections,
            "texts": texts,
            "images": images,
            "shapes": shapes,
        })

    result["fonts"] = sorted(
        fonts_seen.values(), key=lambda f: (-f["count"], f["family"], f["weight"])
    )
    result["responsive"] = plan_responsive(result["screens"])
    return result


DEFAULT_BREAKPOINT = 768


def plan_responsive(screens):
    """PC版とSP版のカンプを突き合わせて、レスポンシブ実装の計画を作る。

    同じページのPC/SPが揃っているなら、1つのHTMLをブレイクポイントで
    出し分けるのが正解。片方しか無いページは、そのまま単独で組むのか
    もう片方を推測で作るのかを人間に決めてもらう必要があるので、
    unpaired として明示的に分けて返す。
    """
    pages = {}
    for screen in screens:
        page = pages.setdefault(screen["page"], {"page": screen["page"], "screens": {}})
        # 同一ページ・同一デバイスが複数ある場合は、背の高い方を本命とみなす
        current = page["screens"].get(screen["device"])
        if current is None or screen["height"] > current["height"]:
            page["screens"][screen["device"]] = {
                "name": screen["name"], "width": screen["width"],
                "height": screen["height"], "content_box": screen["content_box"],
            }

    paired, unpaired = [], []
    for page in pages.values():
        devices = page["screens"]
        entry = {"page": page["page"], "devices": devices}
        if "pc" in devices and "sp" in devices:
            pc_content = devices["pc"].get("content_box") or {}
            entry["breakpoint"] = DEFAULT_BREAKPOINT
            entry["container_max_width"] = pc_content.get("width")
            entry["sp_design_width"] = devices["sp"]["width"]
            paired.append(entry)
        else:
            unpaired.append(entry)

    return {
        "default_breakpoint": DEFAULT_BREAKPOINT,
        "paired": paired,
        "unpaired": unpaired,
        # 全ページでPC/SPが揃っていれば、確認なしでレスポンシブ実装に進める
        "ready": bool(paired) and not unpaired,
    }


# ---------------------------------------------------------------- 表示

def summarize(data):
    lines = []
    canvas = data["canvas"]
    lines.append(f"file    : {data['file']}")
    lines.append(f"canvas  : {canvas['width']} x {canvas['height']}")
    lines.append(f"detect  : {data['screen_detection']}  （artboard=アートボード / group=グループ推定 / canvas=単一）")
    lines.append("")
    lines.append("== 画面 ==")
    for screen in data["screens"]:
        box = screen.get("content_box")
        container = f" content={box['width']}px{'(中央)' if box['centered'] else ''}" if box else ""
        lines.append(f"  [{screen['device']:6s}] {screen['name']}  "
                     f"{screen['width']}x{screen['height']}  page={screen['page']}{container}  "
                     f"text={len(screen['texts'])} img={len(screen['images'])} shape={len(screen['shapes'])}")
        for section in screen["sections"]:
            if section["depth"] == 1:
                x, y, w, h = section["bbox"]
                # 非表示セクションは「開いたメニュー」など別状態のデザインであることが多い
                mark = "" if section.get("visible", True) else "  ← 非表示（別状態のデザイン）"
                lines.append(f"      - {section['name']:<28} y={y:>5} h={h:>5} w={w:>5}{mark}")
    lines.append("")

    plan = data.get("responsive", {})
    lines.append("== レスポンシブ計画 ==")
    for page in plan.get("paired", []):
        devices = page["devices"]
        lines.append(f"  {page['page']:<12} PC:{devices['pc']['name']}({devices['pc']['width']}px)"
                     f" + SP:{devices['sp']['name']}({devices['sp']['width']}px)"
                     f"  -> 1ファイルで実装 / breakpoint {page['breakpoint']}px"
                     f" / container {page.get('container_max_width')}px")
    for page in plan.get("unpaired", []):
        have = ", ".join(f"{d}:{v['name']}" for d, v in page["devices"].items())
        lines.append(f"  {page['page']:<12} {have}  -> 片方のみ。実装方針を要確認")
    if plan.get("ready"):
        lines.append("  すべてのページでPC/SPが揃っています。確認なしでレスポンシブ実装に進めます。")
    lines.append("")
    lines.append("== 使用フォント ==")
    for font in data["fonts"]:
        note = "  ※Web配信不可のため代替を提案" if font["substituted"] else ""
        lines.append(f"  {font['raw']:<24} -> font-family: \"{font['family']}\"; "
                     f"font-weight: {font['weight']};{note}  ({font['count']}箇所)")
    return "\n".join(lines)


def main():
    use_utf8_stdout()
    parser = argparse.ArgumentParser(description="PSDの構造とテキスト書式を抽出する")
    parser.add_argument("psd")
    parser.add_argument("--json", help="全情報をJSONで書き出すパス")
    parser.add_argument("--screen", help="画面名で絞り込む（部分一致）")
    parser.add_argument("--texts", action="store_true", help="テキスト一覧を表示する")
    args = parser.parse_args()

    data = inspect(args.psd, args.screen)

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        print(f"wrote {args.json}")

    print(summarize(data))

    if args.texts:
        print("\n== テキスト ==")
        for screen in data["screens"]:
            print(f"\n--- {screen['name']} ---")
            for text in screen["texts"]:
                run = text["runs"][0] if text["runs"] else {}
                font = run.get("font", {})
                x, y, w, h = text["bbox"]
                body = text["text"].replace("\n", "\\n")
                print(f"  ({x:>4},{y:>5}) {font.get('family','?')} "
                      f"{font.get('weight','?')} {run.get('font_size','?')}px "
                      f"{run.get('color','?')} : {body}")


if __name__ == "__main__":
    sys.exit(main())
