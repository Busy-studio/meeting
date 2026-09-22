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
- 기존 회의록 서식을 보존한 `.xlsx` 생성 및 다운로드
- 선택형 영수증 PDF 업로드 및 AI 기반 스캔 영수증 정보 추출
- 결제대행/플랫폼이 있는 영수증은 실제 판매자·이용상점을 거래처로 우선 판별
- 국세청 사업자등록 상태조회 API로 실제 판매자의 계속/휴폐업 상태와 과세유형 확인
- 결제일시·거래처·총액·공급가액·부가세액을 회의 기본정보에 자동 반영
- 공급가액/부가세액 수동 수정 시 항상 소요금액 합계에 맞춰 반대 항목 자동 조정
- 영수증 업로드 시 최종 생성 한 번으로 기존 Excel과 영수증 포함 통합 PDF를 함께 다운로드
- 통합 PDF 1페이지는 생성된 Excel 회의록 PDF 출력본, 2페이지는 영수증 원본이며 하단에 일반과세자/간이과세자 표시

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
  └─ 영수증 업로드 시 회의록 1p + 영수증 1p 통합 PDF 다운로드
```

## 보안

- `meeting` 스키마은 RLS가 활성화되어 있고 일반 `anon/authenticated` 테이블 접근권한이 없습니다.
- Streamlit 서버만 Supabase Secret Key로 제한된 RPC를 호출합니다.
- Secret Key, OpenAI API Key, 비밀번호, DB 파일은 GitHub에 커밋하지 않습니다.
- Excel 템플릿은 개인 회의정보가 제거된 빈 양식만 저장소에 포함합니다.

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