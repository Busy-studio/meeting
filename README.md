# 회의록 자동생성기

비공개 Google Cloud Storage의 `meeting_DB.sqlite`를 내려받아 유사 회의를 검색하고, OpenAI API로 회의 목적·회의내용·향후계획을 생성하는 Streamlit 앱입니다.

## 주요 기능

- 카테고리만 선택하여 자동 생성
- 키워드 또는 완성된 회의 목적을 입력하여 생성
- 실제 DB의 외부 참석자만 최대 5명 선정
- 부산대학교기술지주 소속 자동 제외
- 회의 목적 1문장, 회의내용 3개, 향후계획 3개
- 항목별 복사 버튼 및 드래그 복사

## GitHub 업로드

이 폴더의 파일을 공개 GitHub 저장소에 업로드합니다. 다음 파일은 절대 업로드하지 않습니다.

- `meeting_DB.sqlite`
- 서비스 계정 JSON
- `.streamlit/secrets.toml`
- `.env`

## Streamlit Community Cloud 배포

1. Streamlit Community Cloud에서 `New app` 선택
2. 공개 GitHub 저장소와 `app.py` 선택
3. Advanced settings → Secrets에 `secrets.example.toml` 형식으로 실제 값을 입력
4. Deploy

## 필요한 Streamlit Secrets

```toml
OPENAI_API_KEY = "sk-..."
OPENAI_MODEL = "gpt-4.1-mini"
GCS_BUCKET_NAME = "버킷명"
GCS_OBJECT_NAME = "meeting_DB.sqlite"

[gcp_service_account]
# 서비스 계정 JSON의 모든 키를 TOML 형식으로 입력
```

서비스 계정에는 해당 버킷의 `Storage Object Viewer` 권한만 부여합니다.

## 로컬 실행

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
mkdir -p .streamlit
# secrets.example.toml을 .streamlit/secrets.toml로 복사 후 실제 값 입력
streamlit run app.py
```

## DB 요구 구조

`meetings` 테이블과 다음 컬럼이 필요합니다.

- `id`
- `status`
- `participants_raw`
- `meeting_purpose_raw`
- `discussion_raw`
- `followup_raw`
- `project_name`
- `research_project_name`
