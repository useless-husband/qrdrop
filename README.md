# qrdrop
Send files between your computer and phone by scanning a QR code on the same Wi-Fi. Zero dependencies, with a QR encoder written from scratch.

掃 QR Code 就能在電腦和手機之間傳檔（同一個 Wi-Fi）。不用裝 App、不用登入、不用傳到雲端，只用 Python 標準函式庫，連 QR Code 編碼器都是自己寫的。

## 這是什麼

想把電腦上的檔案丟到手機，或把手機的照片丟回電腦，常常得繞去雲端、通訊軟體或傳輸線。`qrdrop` 讓你在終端機打一行指令：

- 電腦在區網裡開一個臨時的小網站，終端機直接印出 QR Code。
- 手機掃描後，用瀏覽器就能下載檔案，或把照片、檔案上傳回電腦。
- 傳完或逾時，網站就關掉。

它的另一個重點是 **QR Code 編碼器完全自己實作**（Reed–Solomon、遮罩評分、版本 1–40 都有），沒有用 `qrcode`、`segno` 之類的套件，也沒有 PIL。

## 功能

- `qrdrop send 檔案或資料夾...`：分享給手機下載。
  - 自動找出對外網卡的區網 IP，預設隨機埠，可用 `--port` 指定。
  - 網址帶一段隨機 token（`secrets.token_urlsafe`），token 不對一律回 404。
  - 多個檔案或資料夾會用串流方式打包成 zip（邊讀邊送，不先寫暫存檔）。
  - 下載頁是乾淨的手機網頁，列出檔案與大小，支援深色模式。
  - 支援 Range 請求，中斷的下載可以續傳。
- `qrdrop receive [--dir 目錄]`：讓手機把檔案傳到電腦。
  - 上傳頁可一次選多張照片或多個檔案，有進度條。
  - 檔名消毒：擋 `../`、絕對路徑、磁碟代號、NUL、控制字元、雙向文字控制碼、Windows 保留名（`CON`、`NUL`、`COM1`…）。
  - 同名自動改成 `photo (1).jpg`，不會覆蓋既有檔案。
  - `--max-size` 單檔大小上限，並且會檢查磁碟剩餘空間。
- 安全相關：
  - 預設只綁區網 IP，不綁 `0.0.0.0`（除非你用 `--bind` 明說）。
  - `--once` 完成一次傳輸後自動關閉；`--timeout 10m` 逾時自動關閉（預設 30 分鐘）。
  - `--pin 4821` 要求輸入 4 位數 PIN；連續錯 5 次同一個 IP 會被鎖 60 秒。
  - 每個請求都會在終端機記錄時間、來源 IP 與動作。
- `qrdrop qr "任意文字"`：把文字編成 QR Code，印在終端機，或輸出 `--png`、`--svg`。
  - 自動在 numeric / alphanumeric / byte（UTF-8）模式中挑最省的，版本 1–40 自動選最小，錯誤修正等級 L / M / Q / H。

## 安全提醒（請先看）

**qrdrop 用的是明文 HTTP，沒有加密。** 同一個 Wi-Fi 上的其他人理論上可以看到傳輸內容。它只適合你信任的網路（自己家裡、自己的手機熱點）。不要在咖啡廳、學校公用 Wi-Fi 傳敏感檔案。token 與 PIN 只能防止「不知道網址的人」誤連或亂猜，不能防止有人在網路上竊聽。

## 安裝與執行

需要 Python 3.10 以上。先在終端機檢查：

```bash
python3 --version
```

### 方法 A：用 pipx 安裝（推薦）

pipx 會把命令列工具裝在獨立環境，不會弄亂你的 Python。

```bash
# macOS（用 Homebrew）
brew install pipx
pipx ensurepath

# Windows / Linux
python3 -m pip install --user pipx
python3 -m pipx ensurepath
```

`ensurepath` 之後請**關掉終端機再重開**，然後安裝：

```bash
pipx install git+https://github.com/useless-husband/qrdrop
qrdrop --version
```

### 方法 B：用 uv 安裝

```bash
uv tool install git+https://github.com/useless-husband/qrdrop
qrdrop --version
```

### 方法 C：不安裝，直接跑原始碼

```bash
git clone https://github.com/useless-husband/qrdrop
cd qrdrop
python3 -m qrdrop --version
```

以下範例都用 `qrdrop`；如果你用方法 C，請改成 `python3 -m qrdrop`。

## 使用範例

### 電腦傳給手機

```bash
qrdrop send 報告.pdf 旅行照片/
```

終端機會印出 QR Code 和網址，用手機相機掃描（電腦和手機要連同一個 Wi-Fi），照著網頁按下載。下面是實際跑出來的輸出（QR Code 省略；這次是在同一台電腦上用 curl 測，所以來源 IP 是 127.0.0.1，用手機時會是手機的區網 IP）：

```
分享 3 個檔案（293.0 KB）
網址：http://127.0.0.1:18601/mIAwS5GAaDq1PrjZ/
用手機掃描上面的 QR Code（要在同一個 Wi-Fi）。（5 分鐘後自動關閉）
提醒：這是 HTTP，沒有加密，只適合信任的區網。按 Ctrl+C 結束。

[05:39:16] 127.0.0.1       開啟下載頁
[05:39:16] 127.0.0.1       下載  qrdrop.zip（3 個檔案，打包中）
[05:39:16] 127.0.0.1       完成  qrdrop.zip
[05:39:16] 127.0.0.1       下載  single.txt (4 B)
[05:39:16] 127.0.0.1       完成  single.txt
[05:39:16] 127.0.0.1       拒絕  404 GET /WRONG/
```

最後一行是有人用錯的 token 連進來，一律回 404 並記錄。

常用組合：

```bash
qrdrop send 影片.mp4 --once            # 下載完一次就自動關閉
qrdrop send 合約.pdf --pin 4821        # 手機打開網址後要輸入 PIN
qrdrop send 資料夾/ --timeout 10m      # 10 分鐘後自動關閉
```

### 手機傳給電腦

```bash
qrdrop receive --dir ~/Downloads/從手機來 --max-size 500M
```

手機掃描後會看到上傳頁：選擇照片或檔案、按「上傳」，畫面會顯示進度條。電腦端的紀錄（實際輸出，`--max-size 1M`；同名的檔案自動改名，超過上限的被拒絕）：

```
[05:40:34] 127.0.0.1       拒絕  413 檔案超過上限 1.0 MB
[05:40:34] 127.0.0.1       收到  photo.jpg (488.3 KB)
[05:40:34] 127.0.0.1       收到  photo (1).jpg (488.3 KB)
```

### 只想要 QR Code

```bash
qrdrop qr "https://example.com" --info
qrdrop qr "你好，世界" --ecc H --png hello.png --scale 10
qrdrop qr "WIFI:T:WPA;S:MyWiFi;P:password;;" --svg wifi.svg
echo "從標準輸入" | qrdrop qr -
```

輸出：

```
版本 2（25x25）  等級 M  模式 byte  遮罩 2
█████████████████████████████████
█████████████████████████████████
████ ▄▄▄▄▄ ███▀▄▄ ▀▀ █ ▄▄▄▄▄ ████
████ █   █ █ ▄█▄█▀ ▀██ █   █ ████
████ █▄▄▄█ █ ▄▄▀█▄▀▀▄█ █▄▄▄█ ████
████▄▄▄▄▄▄▄█ █▄█▄█ █ █▄▄▄▄▄▄▄████
████▄▀▄▄  ▄█▀█▀▀▄▀███▀▄ ▄▄▄▀█████
████  ▄  ▀▄█ ▄▀▀█ ▄  █▀ █▄█▄ ████
████▀   ▀█▄█▀█▀▄▄█▄▄▀▄▄▀▄▀▄ ▄████
████ ██▀██▄ ██▄ ▄█▀ ▀▀   ▄█▄ ████
████▄█▄██▄▄█  ▄▄▀▀▀▀ ▄▄▄ ▀▄██████
████ ▄▄▄▄▄ █▀▀██▀ ▄█ █▄█ ▀▄ ▄████
████ █   █ █ ▄▄█▄█▄ ▄  ▄ ▀ ▀ ████
████ █▄▄▄█ █▄▄▄ ▄█▀▄▀▀█▀▀ ▄█ ████
████▄▄▄▄▄▄▄█▄▄█▄█████▄▄▄▄▄▄▄▄████
█████████████████████████████████
█████████████████████████████████
```

上面是 `--plain` 模式的輸出（給不支援顏色的環境，假設終端機是深色背景）。在一般的終端機裡，`qrdrop` 會用明確的「黑字白底」24 位元色畫，quiet zone（四周留白）也是白色，所以深色或淺色背景的終端機都能掃。如果你加了 `--plain` 又是淺色背景，再加 `--invert`。

### 所有選項

```
qrdrop send   檔案或資料夾... [--port N] [--bind ADDR] [--once] [--timeout 時間] [--pin 4位數] [--plain] [--invert]
qrdrop receive [--dir 目錄] [--max-size 大小] [--port N] [--bind ADDR] [--once] [--timeout 時間] [--pin 4位數]
qrdrop qr      文字 [--ecc L|M|Q|H] [--min-version N] [--max-version N] [--mask 0-7]
                    [--png 檔案] [--svg 檔案] [--scale N] [--border N] [--show] [--info] [--plain] [--invert]
```

時間可寫 `90s`、`10m`、`1h`，`0` 代表不逾時；大小可寫 `500M`、`2G`。

## 連不上時怎麼辦

- 手機和電腦必須在同一個 Wi-Fi。有些公共 Wi-Fi 或訪客網路會開「用戶端隔離」，這種網路連不上；改用手機熱點通常就行。
- macOS 第一次可能跳出「允許終端機尋找區域網路上的裝置」，請按允許（系統設定 > 隱私權與安全性 > 區域網路）。
- Windows 防火牆若跳出提示，請允許 Python 使用「私人網路」。
- 如果偵測到的 IP 不對（例如你有 VPN 或虛擬網卡），可以用 `--bind 你的區網IP` 指定。

## 專案結構

```
qrdrop/
├── qrdrop/
│   ├── qrcode.py      QR 編碼器：GF(256)、Reed–Solomon、區塊交錯、遮罩、BCH
│   ├── render.py      終端機半格方塊、PNG（zlib + struct）、SVG
│   ├── server.py      HTTP 伺服器：下載、上傳、token、PIN、--once
│   ├── multipart.py   串流式 multipart/form-data 解析器
│   ├── zipstream.py   串流 zip（含 zip64）
│   ├── names.py       檔名消毒、安全建立檔案
│   ├── pages.py       手機看到的網頁（單檔 HTML/CSS/JS）
│   ├── util.py        區網 IP、時間與大小的解析
│   └── cli.py         命令列介面
├── tests/             unittest，160 個測試
├── scripts/verify_with_browser.mjs   用瀏覽器交叉驗證 QR（不在 CI 內）
└── .github/workflows/ci.yml
```

## 跑測試

不需要安裝任何東西：

```bash
git clone https://github.com/useless-husband/qrdrop
cd qrdrop
python3 -m unittest discover -s tests -t . -v
```

全部約 20 秒。測試涵蓋：GF(256) 與 Reed–Solomon（含規格附錄的已知向量）、160 種版本/等級組合的容量與區塊表、遮罩公式與罰分、格式/版本資訊的 BCH、以獨立寫的簡易解碼器把 1–40 版 × 4 種等級的矩陣讀回原文、PNG/SVG/終端機輸出、檔名消毒（大量惡意檔名）、multipart 解析（逐位元組餵入）、zip 串流、以及伺服器整合測試（下載、上傳、錯誤 token、路徑穿越、`--once`、PIN、磁碟空間不足）和真的用子程序跑 CLI（含 Ctrl+C）。

### 用瀏覽器交叉驗證 QR Code（選用）

單元測試的解碼器是我自己寫的，可能和編碼器犯同樣的錯，所以另外提供一支腳本：用 Playwright 開 Chromium，把 `qrdrop` 產生的 PNG/SVG 載入，再交給獨立實作的解碼器（若瀏覽器有 `BarcodeDetector` 就用它，否則用 [jsQR](https://github.com/cozmo/jsQR)）解回原文字，和輸入比對。這不是專案依賴，需要自己裝：

```bash
mkdir /tmp/qrv && cd /tmp/qrv && npm install playwright jsqr && npx playwright install chromium
cd 回到專案資料夾
PLAYWRIGHT_PATH=/tmp/qrv/node_modules/playwright JSQR_PATH=/tmp/qrv/node_modules/jsqr/dist/jsQR.js \
  node scripts/verify_with_browser.mjs
```

它會測試 numeric / alphanumeric / 中文 / emoji / 換行、版本 1、2、3、6、7、10、14、21、27、32、40 × 四種等級、8 種遮罩，以及接近容量上限（版本 40 的 2953 位元組）的內容，共 81 張圖，全部要解回一模一樣的文字。

## 原理簡介

### QR Code 編碼流程（`qrcode.py`）

1. **選模式**：純數字用 numeric（每 3 位 10 bits）、大寫英數與少數符號用 alphanumeric（每 2 字 11 bits）、其他一律 UTF-8 byte 模式。
2. **選版本**：從版本 1 開始，找第一個資料量放得下的（容量表來自規格，測試會檢查 160 種組合的一致性）。
3. **組資料碼字**：模式指示 + 字元數 + 資料 + 結束符 + 補位，不足時交替補 `0xEC`、`0x11`。
4. **Reed–Solomon**：在 GF(2⁸)（模多項式 `0x11D`）上，對每個區塊算出錯誤修正碼字；再把各區塊的資料與 ECC 碼字依規格交錯。
5. **放進矩陣**：先畫定位圖樣、定位線、對齊圖樣、版本資訊，再以「兩欄一組、由下往上再由上往下」的蛇形順序放碼字。
6. **遮罩**：8 種遮罩都試一次，依規格的四項罰分（連續同色、2×2 區塊、類定位圖樣、深淺比例）選分數最低的。
7. **格式資訊**：錯誤修正等級 + 遮罩編號，用 BCH(15,5) 加上檢查碼並 XOR `0x5412`，寫兩份。版本 7 以上另有 BCH(18,6) 的版本資訊。

目前一個 QR Code 只用單一模式（不做混合模式分段），所以在某些混合內容上會比最佳解略大一點，但仍然是合法且可掃描的。

### 傳檔（`server.py`）

- 用標準函式庫的 `ThreadingHTTPServer`，網址是 `http://區網IP:埠/token/`。token 用 `secrets.token_urlsafe` 產生，比較時用 `hmac.compare_digest`。
- 上傳是邊收邊解析 multipart、邊寫進磁碟，不會把整個檔案放進記憶體。檔名經過消毒後，用 `O_EXCL` 建立檔案，所以同名不會覆蓋、也不會被預先放好的符號連結騙去寫別的地方。
- zip 使用 data descriptor，所以可以一邊讀檔一邊輸出；已經壓縮過的格式（jpg、mp4、zip…）用 stored，其他用 deflate。
- 網頁帶有 `Content-Security-Policy`（每次回應用新的 nonce）、`Cache-Control: no-store`、`nosniff`，並且不載入任何外部資源。

## 已知限制

- 明文 HTTP，沒有 TLS（見上面的安全提醒）。
- 只做 IPv4。
- 一個 QR Code 只用單一編碼模式，沒有做 ECI、結構化附加（Structured Append）或 Kanji 模式。
- 下載 zip 時事先不知道總大小，所以手機瀏覽器不會顯示總進度。
- 上傳超過 `--max-size` 時，會先回應錯誤再丟掉最多 8 MB 的剩餘內容；很大的檔案在某些瀏覽器上可能只看到「連線中斷」而不是明確的錯誤訊息。

## 授權

MIT License，見 [LICENSE](LICENSE)。
