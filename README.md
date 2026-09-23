# 회의 뭐했니? v1.2

Supabase의 회의 지식 DB에서 유사 회의와 실제 참석자 이력을 검색하고, GPT-5.6 Luna로 회의 목적·회의내용·향후계획을 생성한 뒤 사용자가 직접 수정하여 기존 PNU 회의록 Excel 양식으로 출력하는 Streamlit 앱입니다.

## v1.2 주요 기능

- 기존 GCS + SQLite 읽기 구조를 Supabase 기반으로 전환
- GPT-5.6 Luna (`gpt-5.6-luna`) 사용
- GPT-5.6 implicit prompt cache를 사용하지 않도록 explicit cache mode 적용
- AI 생성 결과 직접 수정
- 지원사업 선택 및 모든 사업정보 수정
- 신규 사업 직접 입력 시 Supabase에 자동 등록하여 다음 접속부터 선택 가능
- 최종 확정본만 Supabase에 저장하고 수정이력 관리
- 최종 확정 참석자를 인물/기관 관계 DB에 반영
- 내부 인원 23명 초기 명단을 4열 체크박스로 선택하고, 이름·직급 수정 / 신규 인원 추가 / 퇴사자 비활성화 후 Supabase에 저장
- 체크된 내부 인원을 부산대학교기술지주㈜ 소속으로 외부 참석자 앞에 병합하여 기존 Excel·PDF 및 참석자 예산검증에 반영
- 기존 회의록 서식을 보존한 `.xlsx` 생성 및 다운로드
- 최종 회의록 PDF 별도 다운로드: 회의록 Excel 서식을 PDF 1페이지로 변환하고, 영수증이 있으면 과세유형을 하단에 표시한 영수증 PDF 1페이지를 결합하여 총 2페이지 출력
- 국세청 과세유형이 확인되지 않으면 임의 판정하지 않고 영수증 하단에 `미확인 (국세청 조회 필요)` 표시
- 선택형 영수증 PDF 업로드 및 AI 기반 스캔 영수증 정보 추출
- 결제대행/플랫폼이 있는 영수증은 실제 판매자·이용상점을 거래처로 우선 판별
- 국세청 사업자등록 상태조회 API로 실제 판매자의 계속/휴폐업 상태와 과세유형 확인
- OCR 사업자등록번호가 10자리로 추출되면 국세청 상태조회 자동 실행
- 사업자등록번호는 화면에서 직접 수정·입력 가능하며 수정 후 `사업자 조회`로 재확인 가능
- 결제일시·거래처·총액·공급가액·부가세액을 회의 기본정보에 자동 반영
- 공급가액/부가세액 수동 수정 시 항상 소요금액 합계에 맞춰 반대 항목 자동 조정
- 최종 수정된 참석자 명단을 GPT-5.6 Luna가 분석하여 명시된 사람 수를 판별하고 1인당 50,000원 한도 검증
- 소속·직급이 없어도 명시된 이름은 1명으로 인정하며, 동명이인은 소속/직급 등 원문 구분정보가 명확하면 별도 인원으로 계산
- 동일 이름이 반복되고 소속·직급·직책까지 확인해도 동명이인 여부를 구분할 수 없으면 최종 출력을 막고 확인 안내 표시
- 소요금액이 `참석자 수 × 50,000원`을 초과하면 최종 회의록 다운로드와 DB 저장을 막고 초과 안내 표시
- 참석자 명단은 있으나 인원수를 판별하지 못하면 최종 출력을 막고 이름 확인 안내 표시
- 영수증 OCR은 1회만 수행하고 재분석·웹검색 fallback은 수행하지 않음
- OCR 누락 항목은 사용자에게 즉시 안내하여 회의 기본정보에서 직접 보완
- 영수증 PDF 원본은 Supabase/DB에 저장하지 않으며 OpenAI 임시 파일은 분석 직후 삭제
- 영수증 원본 PDF는 현재 세션 메모리에서만 유지하며 업로드 중에는 `영수증 PDF 다운로드`로 원본도 따로 받을 수 있음
- 영수증 Responses 요청은 `store=False`로 처리
- 영수증 분석과 회의내용 생성은 백그라운드 작업으로 실행하여 처리 중에도 다른 입력·수정 가능
- 백그라운드 처리 중 사용자가 직접 수정한 날짜·시간·거래처·금액은 완료된 OCR 결과가 덮어쓰지 않음
- 사업 정보 상세영역은 기본 접힘 상태로 표시

## 기본 지원사업

1. 2024년도 대학기술경영촉진사업(TLO혁신형)
2. 전략기술 딥테크 창업 촉진
3. 기술경영촉진 컴퍼니빌더 지원형
4. 원천기술 상용화 플랫폼 구축사업

새 사업은 화면의 `＋ 새로운 사업 직접 입력`에서 등록할 수 있습니다.

## 배포

Streamlit Community Cloud에서 이 저장소의 `app.py`를 배포합니다.

### 필요한 Streamlit Secrets

```toml
OPENAI_API_KEY = "sk-..."
OPENAI_MODEL = "gpt-5.6-luna"

NTS_BUSINESS_SERVICE_KEY = "공공데이터포털-서비스키"

SUPABASE_URL = "https://your-project-ref.supabase.co"
SUPABASE_SECRET_KEY = "sb_secret_..."

MEETING_GENERATOR_PASSWORD = "원하는-생성-비밀번호"
DEVICE_AUTH_SECRET = "충분히-길고-무작위인-별도-서명키"
DEVICE_AUTH_DAYS = 365
```

`SUPABASE_SECRET_KEY`는 서버에서만 사용해야 하며 GitHub 코드나 브라우저에 노출하면 안 됩니다. 기존 legacy `service_role` 키를 사용할 경우 `SUPABASE_SERVICE_ROLE_KEY` Secret도 호환됩니다.

## 데이터 흐름

```text
사용자
  ↓
Streamlit 서버
  ├─ Supabase: 유사 회의 / 참석자 / 사업정보 조회
  ├─ OpenAI GPT-5.6 Luna: 회의내용 생성 / 영수증 PDF 분석
  ├─ 국세청 API: 실제 판매자 사업자등록 상태·과세유형 조회
  ↓
사용자 직접 수정
  ↓
최종 회의록 Excel 생성
  ├─ 최종본 + 수정이력 Supabase 저장
  ├─ 신규 사업 / 참석자·기관 지식 반영
  ├─ 기존 양식 XLSX 다운로드
  └─ 회의록 PDF 1페이지 + 과세유형 표시 영수증 PDF 1페이지 다운로드 (영수증 업로드 시)
```

## 보안

- `meeting` 스키마은 RLS가 활성화되어 있고 일반 `anon/authenticated` 테이블 접근권한이 없습니다.
- Streamlit 서버만 Supabase Secret Key로 제한된 RPC를 호출합니다.
- Secret Key, OpenAI API Key, 비밀번호, DB 파일은 GitHub에 커밋하지 않습니다.
- 영수증 PDF 원본은 Supabase/DB에 저장하지 않습니다.
- OCR을 위해 OpenAI Files에 생성한 임시 파일은 분석 성공/실패와 관계없이 즉시 삭제를 시도합니다.
- Excel 템플릿은 개인 회의정보가 제거된 빈 양식만 저장소에 포함합니다.

## PDF 출력 의존성

Streamlit Community Cloud에서는 `packages.txt`의 `libreoffice-calc`, `fonts-nanum`을 설치합니다.
`requirements.txt`에는 `pypdf`, `reportlab`이 포함되어 있습니다.
로컬 환경에서는 LibreOffice와 나눔고딕을 별도 설치해야 통합 PDF가 생성됩니다.
영수증은 1페이지 PDF를 기준으로 통합하며, 여러 페이지이거나 암호화된 파일은 오류를 안내합니다.
원본 PDF 파일은 수정하지 않고, 병합된 결과물만 현재 세션에서 생성합니다.

## 로컬 실행

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
mkdir -p .streamlit
# secrets.example.toml을 .streamlit/secrets.toml로 복사 후 실제 값을 입력
streamlit run app.py
```
## 내부 인원 기능 데이터베이스 적용

배포 전에 Supabase SQL Editor에서 supabase/migrations/20260923_meeting_v1_2_internal_staff.sql을 실행해야 합니다.
첨부된 manager_order(1).xlsx의 23명을 초기 등록합니다. 기존 회의 기록은 변경하지 않으며 퇴사자를 비활성화해도 과거 회의록의 참석자 표기는 유지됩니다.
기존 로그인 비밀번호를 통과한 사용자는 내부 인원 명단을 추가하거나 변경할 수 있습니다. 별도 관리자 권한 구분은 현재 v1.2에 없습니다.
마이그레이션이 아직 적용되지 않은 환경에서는 내부 인원 영역에 오류를 안내하고 기존 외부 참석자 기반 생성은 계속 사용할 수 있습니다.
