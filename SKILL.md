---
name: psd-to-html
description: PSD（Photoshopデザインカンプ）からHTML/CSS/JSを起こす。PSDの中身を調べる、レイヤー構造やテキスト・フォント情報を抽出する、アートボードからPC/SP両対応のレスポンシブページを実装する、素材画像を書き出す、楽天市場（GOLD・商品ページ・スマホページ）向けにPSDから実装する、といった場面で必ず使う。.psdファイルのパスが会話に出てきたら、「開くだけ」「見るだけ」に思えても必ずこのスキルを使うこと。デザインカンプ・カンプ・PSD・Photoshopファイル・コーディング依頼という語が出た時も同様。
---

# PSDからのコーディング

PSDは「見た目の絵」ではなく**設計データ**として読める。テキストの実文字列、
フォント名、サイズ、色、字間、座標、シェイプの塗り・線・角丸まで数値で入っている。
目視でスポイトを使うより、データを読んだほうが速くて正確になる。

## 依存

`psd-tools>=1.17` だけ。ImageMagickもPhotoshopも要らないので、WindowsでもmacOSでも同じ手順で動く。

```bash
pip install "psd-tools>=1.17"
```

1.10系は新しいPhotoshopが書いたリンクレイヤーを読めずエラーになる（`Invalid version 8`）。
`AssertionError` が出たらまずバージョンを疑う。

## 手順

### 1. 構造を把握する

推測でマークアップを始めない。まず中身を出す。

```bash
python scripts/psd_inspect.py design.psd
```

画面の一覧、各画面のセクション、コンテナ幅、レスポンシブ計画、使用フォントが出る。
詳細な数値が要るときは `--json out.json` で全部を保存し、必要な画面だけ
`--screen SP_top --texts` で読む。テキストが100件を超えることも多いので、
JSONを全文読むのではなく必要な画面に絞る。

### 2. 目で見る

数値だけでは意図が読めない。プレビューを書き出して**実際に画像として見る**。

```bash
python scripts/psd_export.py design.psd --screens -o assets/
```

Readツールで `assets/screen-*.png` を開いて、余白の呼吸・要素の並び・
装飾の意図を掴む。ここを飛ばすと、数値は合っているのに雰囲気が違うものができる。

**非表示レイヤーを必ず確認する。** デザイナーは「ハンバーガーを開いた状態」
「ホバー時の色」「モーダル」を、同じPSDに非表示で置いておくことが多い。
手順1の出力でセクションに `← 非表示（別状態のデザイン）` と付いていたら、
それは実装が必要な状態であって、消し忘れではない。開いた姿を見る:

```bash
python scripts/psd_export.py design.psd --screen SP_top --reveal hamburger -o assets/
```

`screen-SP_top-revealed.png` として別名で出るので、通常状態と並べて確認できる。
（Photoshopで目のアイコンを付け替える作業を、そのままコマンドにしたものと考えてよい）

### 3. レスポンシブの方針を決める

`psd_inspect.py` の「レスポンシブ計画」がそのまま方針になる。

- **PC/SPが揃っているページ** → 1つのHTMLで実装し、**768px**をブレイクポイントにする。
  指示がなければこの値でよい。`ready` と出ていれば確認は不要、そのまま進める。
- **片方しか無いページ**（`片方のみ。実装方針を要確認` と出る） → 勝手に決めない。
  もう一方を推測で作るのか、単独ページとして組むのかをユーザーに聞く。
- **アートボードが無いPSD**（`detect: canvas` や `group`） → 画面の切り分け自体が
  推測になる。何をどう分けたと解釈したかを伝え、その解釈でよいか確認してから進む。

コンテナ幅は `content=1084px(中央)` のように出る。これが `max-width` になる。
SP側の `content=352px` は、390pxカンプに対して左右19pxの余白という意味。

### 4. 実装する

**座標をそのまま `position: absolute` にしない。** カンプの座標は要素どうしの
関係（並び・間隔・揃え）を読み取るための材料であって、絶対配置で写経すると
文字量が変わっただけで崩れる。読み取った関係をFlexboxやGridで表現する。

- 縦の間隔 → `margin` / `gap`
- 横並び → `flex` + `gap`（座標の差分がそのまま `gap` になることが多い）
- 中央寄せ → `margin-inline: auto` + `max-width`

**テキストはテキストとして書く。** PSDから実文字列が取れるのだから、
見出しを画像にする理由はない（楽天スマホモードを除く）。SEOにも保守にも効く。

`psd_inspect.py` が出す値とCSSの対応:

| 出力 | CSS |
|---|---|
| `font.family` / `font.weight` | `font-family` / `font-weight` |
| `font_size` | `font-size`（px） |
| `color` | `color` |
| `letter_spacing` | `letter-spacing`（em単位で出る。そのまま書ける） |
| `line_height` | `line-height`（倍率。`null` なら Photoshop の自動行送り） |
| `text_align` | `text-align` |

**シェイプはCSSで描く。** `css_ready: true` のシェイプは画像化せずCSSで書ける。

| 出力 | CSS |
|---|---|
| `fill` + `fill_enabled: true` | `background-color` |
| `fill_enabled: false` | 背景なし（塗りを付けてはいけない） |
| `stroke` | `border: {width}px solid {color}` |
| `radius` | `border-radius`（配列なら左上→右上→右下→左下） |
| `shape_bbox` | 線を含まない素の形状の位置とサイズ |

`fill_enabled: false` なのに色が入っているシェイプは、Photoshop上で塗りを
切った「枠だけのボタン」。色を拾って背景に使うと見た目が変わるので注意。

### 5. 素材を書き出す

```bash
python scripts/psd_export.py design.psd --assets -o assets/
```

写真・グラデーション・アイコンなど、CSSで再現できないものだけが出る。
ベタ塗りの矩形や角丸ボタンは意図的に除外される（CSSで書くため）。

セクション単位で見比べたいときは `--sections`、レイヤーを名指しするときは
`--layer "header/logo"`、パスが分からなければ `--list`。

## フォントの扱い

`psd_inspect.py` はPostScript名をCSSのfamily/weightに変換して出す
（`NotoSansJP-Bold` → `"Noto Sans JP"` / `700`）。

- **`※Web配信不可のため代替を提案` と付いたもの** は、小塚ゴシックなどAdobe同梱
  フォントで、Webフォントとして配信するライセンスが無い。Noto Sans JP / Noto Serif JP
  に読み替える。見た目の差はほとんど出ない。読み替えたことはユーザーに一言伝える。
- Google Fontsにあるフォント（Noto Sans JP、Outfit など）はCDNで読めばよい。
- ヒラギノ・游ゴシック・メイリオはOS標準フォント。配信せず font-family の指定だけで使う。

## 楽天市場向けの場合

貼り先によって使えるHTMLがまったく違う。**書き始める前に貼り先を確定させる**こと。
後から移すと作り直しになる。

- 楽天GOLD → 普通のWeb制作と同じ。CSSもJSも自由
- PC用商品説明文など → `<style>` は書けるが外部CSSは不可。1ファイルに同梱する
- スマホ用の説明文 → CSSが一切使えない。`<table>` と `<img>` で組む

貼り先が楽天だと分かったら **`references/rakuten.md` を読む**。
使えるタグ・属性の一覧、R-Cabinetの画像制限（2MB・3840px）、Webフォントが
使える場所と使えない場所を、根拠つきでまとめてある。

## つまずきやすいところ

- **座標は画面ごとに原点が違う。** スクリプトは各画面の原点を引いた相対座標を返す。
  生のPSD座標（キャンバス絶対座標）と混同しない。
- **非表示レイヤーは「別の状態」であって不要物ではない。** 同じ座標に同じ文字が
  色違いで2つあれば、それは通常時とホバー時。`hamburger` のようなグループが
  非表示なら、それは開いた状態のメニュー。どちらも実装対象になる。
  なお非表示グループはPSD上で bbox が (0,0,0,0) に潰れるが、`psd_inspect.py` は
  読む間だけ表示に切り替えて正しい座標を取るので、そのまま数値を使ってよい
  （PSDファイル自体は変更しない）。
- **同じ名前のレイヤーが複数ある。** 書き出しでは自動で連番が付く。
  マークアップ側では役割で名前を付け直す。
- **SPカンプが750px幅なら2倍サイズで作られている。** CSSでは半分の値で書く。
  390pxや375pxなら等倍。
