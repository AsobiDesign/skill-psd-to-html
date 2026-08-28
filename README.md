# psd-to-html

PSD（Photoshopデザインカンプ）からHTML/CSS/JSを起こす [Claude Code](https://claude.com/claude-code) スキル。

PSDは「見た目の絵」ではなく**設計データ**として読める。テキストの実文字列、フォント名、
サイズ、色、字間、座標、シェイプの塗り・線・角丸まで数値で入っている。目視でスポイトを
使うより、データを読んだほうが速くて正確になる。

- レイヤー構造・テキスト・フォント情報の抽出
- アートボードからPC/SP両対応のレスポンシブページを実装
- CSSで再現できない素材だけを画像として書き出し
- 楽天市場（GOLD・商品ページ・スマホページ）向けの実装

文言だけが欲しい場合は [skill-psd-to-text-extraction](https://github.com/AsobiDesign/skill-psd-to-text-extraction) を使う。

## 依存

`psd-tools>=1.17` だけ（Pillowは psd-tools が連れてくる）。

```bash
pip install "psd-tools>=1.17"
```

ImageMagickもPhotoshopも要らない。外部コマンドを呼ばないので、WindowsでもmacOSでも
同じ手順で動く。

1.10系は新しいPhotoshopが書いたリンクレイヤーを読めずエラーになる（`Invalid version 8`）。
`AssertionError` が出たらまずバージョンを疑う。

## インストール

### Claude Code のスキルとして使う

全プロジェクトで使えるようにする場合:

```bash
git clone https://github.com/AsobiDesign/skill-psd-to-html.git \
  ~/.claude/skills/psd-to-html
```

特定のプロジェクトだけで使う場合:

```bash
git clone https://github.com/AsobiDesign/skill-psd-to-html.git \
  <プロジェクト>/.claude/skills/psd-to-html
```

導入後は `.psd` のパスを会話に出すだけで、Claudeが自動でこのスキルを使う。

### コマンドとして単体で使う

Claude Code なしでも、スクリプト単体で動く。

```bash
git clone https://github.com/AsobiDesign/skill-psd-to-html.git
cd skill-psd-to-html
pip install "psd-tools>=1.17"
python scripts/psd_inspect.py design.psd
```

## 手順

### 1. 構造を把握する

推測でマークアップを始めない。まず中身を出す。

```bash
python scripts/psd_inspect.py design.psd
```

画面の一覧、各画面のセクション、コンテナ幅、レスポンシブ計画、使用フォントが出る。

```
detect  : artboard  （artboard=アートボード / group=グループ推定 / canvas=単一）

== 画面 ==
  [pc    ] PC_top  1920x4242  page=top content=1084px(中央)  text=87 img=12 shape=31
      - header                       y=    0 h=  100 w= 1920
      - hamburger                    y=    0 h=  812 w=  390  ← 非表示（別状態のデザイン）

== レスポンシブ計画 ==
  top          PC:PC_top(1920px) + SP:SP_top(390px)  -> 1ファイルで実装 / breakpoint 768px / container 1084px
  すべてのページでPC/SPが揃っています。確認なしでレスポンシブ実装に進めます。

== 使用フォント ==
  NotoSansJP-Bold          -> font-family: "Noto Sans JP"; font-weight: 700;  (34箇所)
  KozGoPr6N-Regular        -> font-family: "Noto Sans JP"; font-weight: 400;  ※Web配信不可のため代替を提案  (12箇所)
```

詳細な数値が要るときは `--json out.json` で全部を保存し、必要な画面だけ
`--screen SP_top --texts` で読む。

### 2. 目で見る

数値だけでは意図が読めない。プレビューを書き出して**実際に画像として見る**。

```bash
python scripts/psd_export.py design.psd --screens -o assets/
```

余白の呼吸・要素の並び・装飾の意図を掴む。ここを飛ばすと、数値は合っているのに
雰囲気が違うものができる。

**非表示レイヤーを必ず確認する。** デザイナーは「ハンバーガーを開いた状態」「ホバー時の色」
「モーダル」を、同じPSDに非表示で置いておくことが多い。手順1の出力で
`← 非表示（別状態のデザイン）` と付いていたら、それは実装が必要な状態であって
消し忘れではない。開いた姿を見る:

```bash
python scripts/psd_export.py design.psd --screen SP_top --reveal hamburger -o assets/
```

`screen-SP_top-revealed.png` として別名で出るので、通常状態と並べて確認できる。
（Photoshopで目のアイコンを付け替える作業を、そのままコマンドにしたものと考えてよい。
PSDファイル自体は変更しない）

### 3. レスポンシブの方針を決める

`psd_inspect.py` の「レスポンシブ計画」がそのまま方針になる。

| 出力 | 方針 |
|---|---|
| PC/SPが揃っている | 1つのHTMLで実装し、**768px** をブレイクポイントにする |
| `片方のみ。実装方針を要確認` | 勝手に決めない。単独ページか、もう一方を起こすかを確認する |
| `detect: canvas` / `group` | 画面の切り分け自体が推測。解釈が妥当か確認してから進む |

コンテナ幅は `content=1084px(中央)` のように出る。これが `max-width` になる。

### 4. 実装する

**座標をそのまま `position: absolute` にしない。** カンプの座標は要素どうしの関係
（並び・間隔・揃え）を読み取るための材料であって、絶対配置で写経すると文字量が
変わっただけで崩れる。読み取った関係をFlexboxやGridで表現する。

`psd_inspect.py` が出す値とCSSの対応:

| 出力 | CSS |
|---|---|
| `font.family` / `font.weight` | `font-family` / `font-weight` |
| `font_size` | `font-size`（px） |
| `color` | `color` |
| `letter_spacing` | `letter-spacing`（em単位で出る。そのまま書ける） |
| `line_height` | `line-height`（倍率。`null` なら Photoshop の自動行送り） |
| `fill` + `fill_enabled: true` | `background-color` |
| `fill_enabled: false` | 背景なし（塗りを付けてはいけない） |
| `stroke` | `border: {width}px solid {color}` |
| `radius` | `border-radius`（配列なら左上→右上→右下→左下） |
| `shape_bbox` | 線を含まない素の形状の位置とサイズ |

`css_ready: true` のシェイプは画像化せずCSSで書ける。`fill_enabled: false` なのに色が
入っているシェイプは、Photoshop上で塗りを切った「枠だけのボタン」。色を拾って背景に
使うと見た目が変わる。

### 5. 素材を書き出す

```bash
python scripts/psd_export.py design.psd --assets -o assets/
```

写真・グラデーション・アイコンなど、CSSで再現できないものだけが出る。ベタ塗りの矩形や
角丸ボタンは意図的に除外される（CSSで書くため）。

## コマンド

### psd_inspect.py — 構造を読む

| オプション | 意味 |
|---|---|
| `--json PATH` | 全情報をJSONで書き出す |
| `--screen NAME` | 画面名で絞り込む（部分一致） |
| `--texts` | テキスト一覧を座標・書式つきで表示 |

### psd_export.py — 画像を書き出す

| オプション | 意味 |
|---|---|
| `-o`, `--outdir` | 出力先ディレクトリ（既定 `assets`） |
| `--screens` | 画面ごとの全体プレビュー |
| `--assets` | 画像化が必要なレイヤーを自動抽出 |
| `--sections` | セクション単位で書き出す |
| `--layer QUERY` | パスの部分一致でレイヤーを書き出す（複数指定可） |
| `--flat` | `--layer` の出力をレイヤー名だけの短いファイル名にする |
| `--list` | レイヤーのパス一覧を表示する |
| `--screen NAME` | 画面名で絞り込む（部分一致） |
| `--reveal QUERY` | 非表示グループを表示した状態でプレビューする |

## フォントの扱い

`psd_inspect.py` はPostScript名をCSSのfamily/weightに変換して出す
（`NotoSansJP-Bold` → `"Noto Sans JP"` / `700`）。

- **`※Web配信不可のため代替を提案`** が付いたものは、小塚ゴシックなどAdobe同梱フォントで、
  Webフォントとして配信するライセンスが無い。Noto Sans JP / Noto Serif JP に読み替える。
- Google Fontsにあるフォント（Noto Sans JP、Outfit など）はCDNで読めばよい。
- ヒラギノ・游ゴシック・メイリオはOS標準フォント。配信せず `font-family` の指定だけで使う。

## 楽天市場向けの場合

貼り先によって使えるHTMLがまったく違う。**書き始める前に貼り先を確定させる**こと。
後から移すと作り直しになる。

| 貼り先 | 使えるもの |
|---|---|
| 楽天GOLD | 普通のWeb制作と同じ。CSSもJSも自由 |
| PC用商品説明文など | `<style>` は書けるが外部CSSは不可。1ファイルに同梱する |
| スマホ用の説明文 | CSSが一切使えない。`<table>` と `<img>` で組む |

詳細は [`references/rakuten.md`](references/rakuten.md) にまとめてある。使えるタグ・属性の
一覧、R-Cabinetの画像制限（2MB・3840px）、Webフォントが使える場所と使えない場所を、
根拠つきで記載。

## つまずきやすいところ

- **座標は画面ごとに原点が違う。** スクリプトは各画面の原点を引いた相対座標を返す。
  生のPSD座標（キャンバス絶対座標）と混同しない。
- **非表示レイヤーは「別の状態」であって不要物ではない。** 同じ座標に同じ文字が色違いで
  2つあれば、それは通常時とホバー時。`hamburger` のようなグループが非表示なら、
  それは開いた状態のメニュー。どちらも実装対象になる。
- **同じ名前のレイヤーが複数ある。** 書き出しでは自動で連番が付く。マークアップ側では
  役割で名前を付け直す。
- **SPカンプが750px幅なら2倍サイズで作られている。** CSSでは半分の値で書く。
  390pxや375pxなら等倍。

## ファイル構成

```
SKILL.md                Claude 向けのスキル定義
references/rakuten.md   楽天市場の制約（貼り先ごとの使用可能タグ・R-Cabinet・Webフォント）
scripts/psd_inspect.py  構造とテキスト書式の抽出（単体でも動く）
scripts/psd_export.py   画像の書き出し（単体でも動く）
```
