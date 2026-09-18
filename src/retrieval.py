from __future__ import annotations

import random
import re
from dataclasses import dataclass

import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity


CATEGORIES: dict[str, tuple[str, ...]] = {
    "기술이전": ("기술이전", "계약", "기술료", "이전 협상", "라이선스"),
    "기술사업화": ("기술사업화", "사업화", "실용화", "상용화", "시장 진입"),
    "기업수요 발굴": ("기업수요", "수요기술", "수요 발굴", "애로기술", "기업 상담"),
    "공동연구·R&D": ("공동연구", "연구개발", "R&D", "과제 기획", "실증"),
    "기술마케팅": ("기술마케팅", "기술설명회", "SMK", "홍보", "마케팅"),
    "창업·투자": ("창업", "투자", "자회사", "IR", "액셀러레이팅"),
    "특허·지식재산": ("특허", "지식재산", "IP", "권리화", "자산실사"),
    "사업기획·운영": ("사업기획", "사업 운영", "성과관리", "프로그램", "지원사업"),
}

INTERNAL_ORG_PATTERNS = (
    "부산대학교기술지주", "부산대학교 기술지주", "부산대기술지주", "부산대 기술지주",
    "부산대학교 산학협력단 기술지주", "부산대학교산학협력단 기술지주",
    "부산대학교 산학협력단", "부산대학교산학협력단",
    "부산대 산학협력단", "부산대산학협력단",
)
TITLE_WORDS = (
    "대표이사", "부대표", "전무이사", "상무이사", "책임심사역", "수석부주임",
    "책임연구원", "수석연구원", "선임연구원", "전문위원", "센터장", "본부장",
    "대표", "이사", "상무", "전무", "부장", "차장", "과장", "팀장", "실장",
    "소장", "원장", "연구원", "교수", "부교수", "조교수", "변리사", "회계사",
    "박사", "매니저", "지사장", "주임", "대리", "사원",
)
TITLE_SET = set(TITLE_WORDS)
TITLE_ALT = "|".join(sorted((re.escape(x) for x in TITLE_WORDS), key=len, reverse=True))
NAME_RE = re.compile(r"^[가-힣]{2,4}$")
ATTACHED_NAME_TITLE_RE = re.compile(rf"^([가-힣]{{2,4}})({TITLE_ALT})$")


@dataclass(frozen=True)
class Participant:
    organization: str
    name: str
    title: str
    meeting_id: int

    @property
    def display(self) -> str:
        return " ".join(x for x in (self.organization, self.name, self.title) if x).strip()


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _normalize_org(text: str) -> str:
    compact = re.sub(r"\s+", "", str(text or ""))
    compact = compact.replace("주식회사", "").replace("㈜", "").replace("(주)", "").replace("（주）", "")
    return compact.strip("()（）")


def _normalize_name(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "")).strip()


def build_search_text(df: pd.DataFrame) -> pd.Series:
    cols = ["meeting_purpose_raw", "discussion_raw", "followup_raw", "project_name", "research_project_name"]
    return df[cols].agg(" ".join, axis=1).map(_clean)


def search_similar(df: pd.DataFrame, query: str, category: str, top_k: int = 10, randomize: bool = False) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    texts = build_search_text(df)
    category_terms = " ".join(CATEGORIES.get(category, (category,)))
    effective_query = _clean(f"{query} {category_terms}")
    vectorizer = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 5), min_df=1, sublinear_tf=True)
    matrix = vectorizer.fit_transform(pd.concat([texts, pd.Series([effective_query])], ignore_index=True))
    scores = cosine_similarity(matrix[-1], matrix[:-1]).ravel()
    ranked = df.copy()
    ranked["search_text"] = texts
    ranked["similarity"] = scores
    pool_size = min(max(top_k * 3, 20), len(ranked))
    pool = ranked.nlargest(pool_size, "similarity")
    if randomize and len(pool) > top_k:
        weights = (pool["similarity"].clip(lower=0.01) ** 2).tolist()
        chosen = random.choices(list(pool.index), weights=weights, k=min(top_k * 2, len(pool)))
        chosen = list(dict.fromkeys(chosen))[:top_k]
        if len(chosen) < top_k:
            chosen += [i for i in pool.index if i not in chosen][: top_k - len(chosen)]
        return pool.loc[chosen].sort_values("similarity", ascending=False)
    return pool.head(top_k)


def _is_internal(text: str) -> bool:
    compact = re.sub(r"\s+", "", str(text or ""))
    return any(re.sub(r"\s+", "", p) in compact for p in INTERNAL_ORG_PATTERNS)


def select_participants(similar: pd.DataFrame, participant_links: pd.DataFrame, max_people: int = 5) -> list[str]:
    if similar.empty:
        return []

    score_by_meeting: dict[int, float] = {}
    for rank, row in enumerate(similar.itertuples(index=False)):
        score_by_meeting[int(row.id)] = float(getattr(row, "similarity", 0.0)) + max(0, 0.05 - rank * 0.004)

    candidates: list[tuple[float, Participant]] = []
    if not participant_links.empty:
        subset = participant_links[participant_links["meeting_id"].isin(score_by_meeting.keys())]
        for row in subset.itertuples(index=False):
            org = _clean(row.organization)
            name = _clean(row.name)
            title = _clean(row.title)
            if not name or not org or _is_internal(org):
                continue
            candidates.append((score_by_meeting.get(int(row.meeting_id), 0.0), Participant(org, name, title, int(row.meeting_id))))

    candidates.sort(key=lambda x: x[0], reverse=True)
    selected: list[str] = []
    seen_names: set[str] = set()
    for _, p in candidates:
        if p.name in seen_names or len(p.display) < 4:
            continue
        selected.append(p.display)
        seen_names.add(p.name)
        if len(selected) >= max_people:
            break
    return selected


def _split_name_title(tokens: list[str]) -> tuple[int | None, str, str]:
    for i, token in enumerate(tokens):
        m = ATTACHED_NAME_TITLE_RE.match(token)
        if m:
            return i, m.group(1), m.group(2)
        if NAME_RE.match(token) and token not in TITLE_SET:
            title = ""
            if i + 1 < len(tokens) and tokens[i + 1] in TITLE_SET:
                title = tokens[i + 1]
            return i, token, title
    return None, "", ""


def parse_participants_for_save(raw: str) -> list[dict[str, str]]:
    """Parse only relationships written in the final participant text.

    Organization carry-over is limited to the same line. Nothing is inferred from
    another meeting, which prevents same-name / affiliation mixing.
    """
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    text = str(raw or "").replace("ㆍ", ",").replace("·", ",")

    for line in text.splitlines() or [text]:
        carry_org = ""
        fragments = [x.strip(" -•\t") for x in re.split(r"[,;]+", line) if x.strip()]
        for frag in fragments:
            tokens = frag.split()
            if not tokens:
                continue
            idx, name, attached_title = _split_name_title(tokens)
            if idx is None:
                # A short fragment with no person can establish organization only within this line.
                if len(tokens) <= 6 and not any(t in TITLE_SET for t in tokens):
                    carry_org = frag
                continue

            before = tokens[:idx]
            after = tokens[idx + 1 :]
            org = _clean(" ".join(before)) or carry_org
            title = attached_title
            if not title and after and after[0] in TITLE_SET:
                title = after[0]

            if org and org in TITLE_SET:
                org = ""
            if org:
                carry_org = org

            key = (_normalize_name(name), _normalize_org(org), title)
            if key in seen:
                continue
            seen.add(key)
            result.append(
                {
                    "name": name,
                    "normalized_name": _normalize_name(name),
                    "organization": org,
                    "normalized_organization": _normalize_org(org),
                    "title": title,
                    "raw_fragment": frag,
                }
            )
    return result