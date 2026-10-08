# 샘플 세트 (20종) — 그림 + 정답 접속점

그림에서 장치를 만드는 흐름(축척 → 본체 → 접속점 → 검증 → 배치 명세)을 매번 같은 입력으로 점검하는 기준 세트다. 계획서 0단계의 ‘샘플 이미지 20장 + 정답’에 해당한다.

## 구성

| 분류 | 수 | 파일 | 정답이 정해진 방법 |
| --- | --- | --- | --- |
| 제품 사진 | 1 | `real_pilz_pnoz_x3p.jpg` (저장소에는 없음 — 아래 ‘출처와 사용 범위’) | 사진 위에서 단자 나사 중심을 직접 재어 정함 (오차 약 ±3 px ≈ 0.2 mm) |
| 제품 일러스트 | 7 | `real_relay_icon.png`(사용자 SVG를 4배로 그림), `ill_*.png` 6종 | 그리는 코드가 단자 위치를 알고 있음 (`tools/illustrations.py`) |
| 선화 심볼 | 4 | `line_dev_lr`, `line_dev_tb`, `line_dev_tb_pitch5`, `line_dev_lr_thin` | 생성 코드가 정답 좌표를 같이 낸다 (`imgvec/synth.py`) |
| 블록도 | 2 | `block_plc`, `block_inverter` | 〃 |
| 열화본 | 6 | `deg_*.jpg·png` | 위 그림에 블러·잡음·조명 불균일·JPEG 압축·축소를 가한 것. 정답은 원본과 같은 기준 |

`manifest.csv`에 파일별 크기·축척 근거·접속점 수·변별 개수·프리셋·비고가 있다. 변 정보는 **위/아래/왼쪽/오른쪽**이며, 왼쪽·오른쪽 접속점이 있는 것은 `line_dev_lr`, `line_dev_lr_thin` 둘뿐이다 (DNCAD가 확인하지 못한 배치 → `--remap-lr` 시험용).

각 그림 `<id>`마다 두 파일이 짝으로 있다.

| 파일 | 내용 |
| --- | --- |
| `<id>.spec.json` | 장치 명세(`device-spec/1.0`): 본체 사각형, 축척, 접속점의 그림 좌표·이름·단자열 |
| `<id>.pins.csv` | 같은 접속점을 사람이 고치기 쉬운 CSV로 |

## 쓰는 법

```bash
# 20종 전부 변환·검증 (좌우 변 접속점은 위·아래로 옮겨 배치 명세를 만든다)
python -m imgvec device batch samples/set --remap-lr -o out/set

# 한 장만, 명세로
python -m imgvec device build --spec samples/set/block_plc.spec.json -o out/block_plc

# 합성 18종과 명세·manifest를 다시 만들기 (실제 그림 2종은 건드리지 않음. 일러스트는 Edge 또는 Chrome이 필요)
python tools/make_sample_set.py
```

결과는 `out/set/`의 `<id>.device.json`(장치 모델), `.device.svg`(주석 SVG), `.layout.json`(DNCAD 배치 명세), `.issues.txt`, 그리고 `summary.csv`다. `tests/test_sample_set.py`가 20종 모두 검증을 통과하고 배치가 정답과 맞는지 매번 확인한다.

## 알아 둘 점

- **축척.** 합성 그림은 생성할 때 정한 값이 정답(예: 0.1 mm/px, 단자 간격 5.08 mm 기준 0.0635 mm/px)이다. 실제 사진 `real_pilz_pnoz_x3p`는 본체 폭 45 mm(제조사 자료 값으로 알려졌으나 이 PC에서 확인하지 못함)로 축척을 잡았고, 측정한 단자 간격은 약 4.8~5.0 mm다. 실제 치수가 필요하면 제조사 도면으로 바로잡아야 한다.
- **벡터 품질.** 사진(`real_pilz_pnoz_x3p`, `deg_pilz_lowres`)은 선화와 달리 외형 윤곽만 의미가 있다 (계획서 10장의 위험 대응: 사진은 목표에서 분리해 평가).
- **출처와 사용 범위.** `real_pilz_pnoz_x3p.jpg`는 사용자가 제공한 제조사(Pilz) 카탈로그 사진이고 `deg_pilz_lowres.jpg`는 그 열화본이다. **제3자 저작물이라 공개 저장소에는 올리지 않았다**(`.gitignore`). 명세(`*.spec.json`)와 핀 CSV는 저장소에 있고, 그림을 같은 이름으로 이 폴더에 넣으면 20종이 완전해진다(그림이 없으면 `tools/make_sample_set.py`는 두 샘플을 건너뛰고, 시험은 그림 없이도 통과한다). `real_relay_icon.png`는 사용자 제공 SVG(`hosoo_diagnostic_relay_icon.svg`)에서 만든 것이다. 나머지 17종은 코드로 만든 합성 그림이라 제한이 없다.
- **정답의 한계.** 합성 그림의 정답은 ‘그림이 그려진 방식’에서 나온 것이라, 실제 고객 그림에 있는 애매함(겹친 글자, 비표준 단자 모양)은 담지 못한다. 실제 입력 그림 5~10장을 받으면 이 세트에 더해 사람이 정답을 지정해야 한다 (`imgvec device pick`).
