# imgvec — 그림 → CAD 벡터 → DNCAD 장치(블랙박스 + 장치 접속점)

DNCAD 장치 생성 프로그램 작업 계획서의 **0~2단계** 산출물입니다.
그림 파일을 직선·호·원으로 이루어진 벡터로 바꾸고(1단계), 접속점·본체·축척을 더해 검증된 **장치 모델**과 **DNCAD 배치 명세**를 만듭니다(2단계).
DNCAD 쪽은 배치 명세를 읽는 어댑터로만 연결하며, 시험용 `.dncadbak` 생성기가 들어 있습니다(실험, 아직 DNCAD 가져오기 미확인).

| 단계 | 상태 | 어디에 |
| --- | --- | --- |
| 0 조사·준비 | 완료 (가져오기 시험은 사용자 확인 대기) | `docs/DNCAD-연동-조사서.md`, `samples/set/`, `docs/장치-모델-스키마.md` |
| 1 벡터화 엔진 | 완료 | 아래 ‘1단계’ |
| 2 장치 모델 | 완료 | 아래 ‘2단계’ |
| 3 DNCAD 어댑터 | 시험 키트(실험) | 아래 ‘DNCAD 시험’ |
| 4 자동 인식 · 5 GUI · 6 배포 | 아직 | |

## 설치

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev,pdf]"
.\.venv\Scripts\Activate.ps1          # 이후 `imgvec` 명령을 바로 쓸 수 있습니다
```

Python 3.11 이상. 의존성 고정본은 `requirements-lock.txt`입니다. 한글 경로에서도 동작하도록 이미지는 Pillow로 읽고 씁니다.
콘솔에서 한글이 깨지면 `python -X utf8 -m imgvec …`로 실행하세요.

## 2단계: 그림 → 장치

```powershell
# 1) 화면에서 클릭해 접속점·본체·축척을 지정 → 그림.spec.json + 그림.pins.csv
imgvec device pick 그림.png --name MY-DEVICE

# 2) 장치 만들기: 장치 모델(JSON) + 주석 SVG + DNCAD 배치 명세 + 검증 결과
imgvec device build --spec 그림.spec.json -o out\MY-DEVICE
#    (명세 없이) 접속점 CSV와 인자로도 된다
imgvec device build 그림.png --pins 핀.csv --body 400 260 1000 640 --mm-per-px 0.1 --name MY-DEVICE

# 3) SVG를 Inkscape 등에서 고친 뒤 다시 읽기 / 모델 검증 / 배치 명세만 다시 계산
imgvec device from-svg out\MY-DEVICE.device.svg -o out\MY-DEVICE.device.json
imgvec device validate out\MY-DEVICE.device.json
imgvec device layout out\MY-DEVICE.device.json -o out\MY-DEVICE.layout.json
```

| 출력 | 내용 |
| --- | --- |
| `<이름>.device.json` | 장치 모델 `device-model/1.0` — 본체·접속점·외형 도형. DNCAD 방식과 무관한 중간 데이터 |
| `<이름>.device.svg` | 주석 SVG. 접속점에 `data-role`·`data-name` 등이 있어 고친 뒤 되읽을 수 있음 |
| `<이름>.layout.json` | DNCAD 배치 명세 `dncad-device-layout/1.0` — 블랙박스 크기·접속점 슬롯·회전 (단위 6.4/mm) |
| `<이름>.issues.txt` | 검증 결과. **오류가 있으면 종료 코드 1** |

- **축척은 필수**입니다: `--mm-per-px`, `--ref x1 y1 x2 y2 mm`, `--body-width-mm`(본체 폭의 실제 길이), `--pin-pitch-mm`(같은 줄 접속점 간격) 중 하나.
- 접속점 CSV 열: `name`(필수) · `side` · `offset_mm` · `x_mm`·`y_mm` · `x_px`·`y_px` · `kind` · `label` · `group` (한글 머리글 가능). 좌표계·검증 코드는 `docs/장치-모델-스키마.md`.
- **좌표계:** 원점 = 본체 왼쪽 위, y는 아래, 단위 mm (DNCAD·SVG·그림과 같음).
- DNCAD 블랙박스의 접속점은 **위·아래 변**만 확인되어 있습니다. 왼쪽·오른쪽 변 접속점은 `--remap-lr`로 위·아래로 옮깁니다.
- 같은 이름의 접속점은 기본 오류입니다 (생성기 API 규칙). 기존 DNCAD 백업에는 중복이 있어 `--allow-dup-names`로 경고로 낮출 수 있습니다.
- 샘플 20종 일괄: `imgvec device batch samples\set --remap-lr -o out\set` → `summary.csv`.

## DNCAD 시험 (실험·미검증)

DNCAD 백업(`.dncadbak`)의 구조를 실제 백업에서 읽어 알아냈고, 배치 명세를 시험용 백업으로 내보낼 수 있습니다.
**DNCAD가 손으로 만든 백업을 받아들이는지는 아직 확인하지 못했습니다.** 새 프로젝트로만 가져와 보세요.

```powershell
imgvec device kit out\set -o out\시험.dncadbak          # *.layout.json들 → 시험용 백업 (+ 태그 대응표 .map.csv)
imgvec device check-bak out\시험.dncadbak               # ZIP·해시·행 수·참조·블랙박스 규칙 점검
imgvec device read-bak 실제백업.dncadbak -o out\real    # 실제 백업의 블랙박스를 읽어 배치 명세로 내보내기
python tools\make_dncad_testkit.py --control 실제백업.dncadbak   # 시험 키트: out\dncad_testkit\ (README.txt 참고)
python tools\extract_dncad_template.py 실제백업.dncadbak          # DNCAD 스키마가 바뀌면 템플릿 갱신
```

자세한 내용과 근거는 `docs/DNCAD-연동-조사서.md`입니다.

## 1단계: 벡터화

```powershell
# 1장 변환 (축척: 두 점의 픽셀 좌표와 그 사이 실제 거리(mm)로 지정)
imgvec convert 그림.png -o out\장치.svg --ref 320 500 1080 500 76

# 픽셀 1개의 길이를 아는 경우, 도형 JSON(2단계 입력용)도 함께 저장
imgvec convert 그림.png -o out\장치.svg --mm-per-px 0.1 --json

# 폴더 일괄 변환 (파일별 축척은 CSV: 파일명,mm_per_px)
imgvec batch 이미지폴더 -o 출력폴더 --scales scales.csv

imgvec info 그림.png       # 크기·DPI·종류 확인
imgvec samples             # 정답이 있는 합성 샘플 생성 (samples\synthetic)
imgvec selftest            # 합성 샘플로 정확도 점검 (계획서 9장 기준 0.5 mm)
```

축척을 주지 않으면 원본 px 단위로 출력하고 안내 문구를 띄웁니다. PDF는 `--pdf-dpi`(기본 300)로 그린 뒤 같은 DPI를 축척으로 씁니다.
`-o`를 생략하면 `out\<입력 이름>.svg`에 저장합니다. 같은 경로에 imgvec이 만들지 않은 SVG가 있으면 덮어쓰지 않고 멈춥니다(`--force`로 허용).

| 출력 | 내용 |
| --- | --- |
| `<이름>.svg` | 결과 벡터. 단위 mm, y는 아래로 증가(SVG 표준) |
| `<이름>.report.json` | 평가 지표·설정·단계별 시간 |
| `<이름>.overlay.png` | 원본 위에 결과(빨강)와 꼭짓점(파랑)을 겹친 이미지 — 눈으로 검수할 때 사용 |
| `<이름>.prims.json` | (`--json`) 선·호·원 목록 |

### 그림 종류(프리셋)

`--preset auto`(기본)는 색 분포로 판별합니다. 판별이 틀리면 직접 지정하세요.

| 프리셋 | 대상 | 방식 |
| --- | --- | --- |
| `lineart` | 흑백 선 그림, 스캔·PDF 도면 | Otsu(또는 `--binarize sauvola`) 이진화 → 경계 리본 → 중심선 |
| `illustration` | 색 영역이 있는 제품 정면도·카탈로그 그림 | 우세한 색으로 양자화 → 색 경계 리본 → 중심선 |
| `photo` | 사진 (실험적) | 강한 평활화, 작은 영역 제거 |

### 처리 방식

두 프리셋 모두 **서로 다른 색(라벨)이 맞닿은 경계를 선으로 본다**는 같은 원리입니다.
획은 양쪽 경계가 가까워 모폴로지 닫힘으로 한 줄 리본이 되고, 그 골격이 중심선이 됩니다.
넓은 영역은 경계가 한 줄이라 윤곽선이 그대로 남습니다.

| 모듈 | 하는 일 |
| --- | --- |
| `labels.py` | 선화 이진화 / 색 팔레트 양자화, 얇은 띠·작은 점 제거, 경계 리본, 닫힘 반경 자동 추정 |
| `skeleton.py` | 골격 → 그래프(끝점·교차점·변), 잔가지·짧은 선 제거, 차수 2 교차점 이음 |
| `fit.py` | 직선·호 탐욕 분할, 호 경계 보정, **둥근 모서리 필렛 피팅**, 파편 정리, 수평·수직 맞춤, 닫힌 경로의 원 판정 |
| `pipeline.py` | 위 단계 조립, 교차점 최소제곱 맞춤, 자유 끝점 연장 |
| `consolidate.py` | 쪼개진 호를 원으로 합침, 겹친 도형 제거 |
| `svg.py`, `export.py`, `metrics.py` | SVG·JSON 출력, 평가 지표, 겹쳐보기 |

### 정확도

**정답을 아는 합성 샘플 6종** (`imgvec selftest`, 허용 오차 0.2 mm, 단위 mm):

| 샘플 | 재현율 최대 | p99 | 정밀도 최대 | p99 |
| --- | --- | --- | --- | --- |
| device_clean | 0.090 | 0.037 | 0.214 | 0.037 |
| device_blur | 0.066 | 0.039 | 0.193 | 0.039 |
| device_noisy | 0.135 | 0.037 | 0.227 | 0.037 |
| device_jpeg | 0.091 | 0.036 | 0.214 | 0.036 |
| device_coarse | 0.136 | 0.073 | 0.336 | 0.073 |
| device_pins6 | 0.040 | 0.036 | 0.141 | 0.036 |

재현율은 정답 도형 → 결과 도형, 정밀도는 결과 도형 → 정답 도형의 거리이고 글자 주변은 정밀도에서 제외합니다.
모두 계획서 9장 기준(최대 편차 0.5 mm 이하)을 만족합니다. 둥근 모서리는 반지름 ±1 px, 호 중심각 90.0°로 복원됩니다.

**정답이 없는 실제 그림**은 리포트의 지표를 봅니다.

- `fit`: 추적한 골격 픽셀이 결과 도형에서 떨어진 거리. 글자 같은 잡획 때문에 최대값이 커지므로 `p99`를 함께 봅니다.
- `edge_coverage`: 원본 색 경계 중 결과 도형 근처(닫힘 반경+2 px)에 있는 비율.
- `edge_precision`: 결과 도형 위의 점 중 경계 근처에 있는 비율.
- `simplification_ratio`: 결과 점 수 / 골격 픽셀 수 (계획서 목표: 1/5 이하, 실측 0.01~0.02).

## 알려진 한계

- **글자**는 OCR이 없어 획 그대로 선·호로 바뀌므로 지저분합니다. 4단계에서 문자 영역을 따로 인식합니다.
- 글자나 선이 단단히 붙은 단자 원은 호·선으로 남을 수 있습니다 (릴레이 샘플은 18개 중 15개가 원으로 복원).
- 획 폭 정도로 좁은 구조(나사 홈 등)는 한 줄로 합쳐질 수 있습니다. `--merge-px`로 조정합니다.
- 사진·그림자·그라데이션이 많은 그림은 경계가 많아 결과가 복잡해집니다 (`photo`는 실험적). 사진은 외형 윤곽만 의미가 있습니다.
- 기울어진 스캔의 자동 보정(deskew)과 원근 보정은 아직 없습니다.
- 외형 도형은 DNCAD 블랙박스에 들어가지 않습니다(블랙박스는 크기를 가진 직사각형). 외형은 주석 SVG·확인용입니다.
- 접속점은 사람이 지정합니다 (자동 인식은 4단계).

## 폴더

```
src/imgvec/     엔진·장치 모델·검증·배치 명세·DNCAD 백업 어댑터·CLI
  data/         DNCAD 백업 템플릿 (tools/extract_dncad_template.py로 생성)
tests/          pytest (141개)
docs/           DNCAD-연동-조사서.md, 장치-모델-스키마.md, schema/*.json (JSON Schema)
samples/        set (정답 있는 20종 + README), synthetic (1단계 정확도용), real
tools/          샘플 세트 생성, DNCAD 시험 키트, 백업 템플릿 추출
out/            결과 (git 제외)
```

## 개발

```powershell
python -m pytest          # 141개, 약 25초
ruff check src tests tools
```

- 린트 규칙과 의존성은 `pyproject.toml`, 고정 버전은 `requirements-lock.txt`입니다.
- 좌표·단위·검증 코드의 약속은 `docs/장치-모델-스키마.md`, DNCAD 쪽 근거와 열린 질문은 `docs/DNCAD-연동-조사서.md`입니다.
