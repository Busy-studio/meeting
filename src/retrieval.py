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
    "대표이사", "대표", "부대표", "이사", "상무", "전무", "부장", "차장", "과장", "팀장",
    "실장", "센터장", "본부장", "소장", "원장", "책임연구원", "수석연구원", "선임연구원",
    "연구원", "교수", "부교수", "조교수", "변리사", "박사", "전문위원", "매니저", "주임",
)
NAME_RE = re.compile(r"^[가-힣]{2,4}$")


@dataclass(frozen=True)
class Participant:
    organization: str
    name: str
    title: str
    meeting_id: int
    meeting_text: str

    @property
    def display(self) -> str:
        return " ".join(x for x in (self.organization, self.name, self.title) if x).strip()


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def build_search_text(df: pd.DataFrame) -> pd.Series:
    cols = ["meeting_purpose_raw", "discussion_raw", "followup_raw", "project_name", "research_project_name"]
    return df[cols].agg(" ".join, axis=1).map(_clean)


def search_similar(df: pd.DataFrame, query: str, category: str, top_k: int = 10, randomize: bool = False) -> pd.DataFrame:
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
    compact = re.sub(r"\s+", "", text)
    return any(re.sub(r"\s+", "", p) in compact for p in INTERNAL_ORG_PATTERNS)


def parse_participants(raw: str, meeting_id: int, meeting_text: str) -> list[Participant]:
    raw = str(raw or "").replace("ㆍ", ",").replace("·", ",")
    fragments = [x.strip(" -•\t") for x in re.split(r"[,;\n]+", raw) if x.strip()]
    result: list[Participant] = []
    carry_org = ""
    for frag in fragments:
        if _is_internal(frag):
            carry_org = ""
            continue
        tokens = frag.split()
        name_idx = next((i for i, t in enumerate(tokens) if NAME_RE.match(t) and t not in TITLE_WORDS), None)
        if name_idx is None:
            if len(tokens) <= 4:
                carry_org = frag
            continue
        before = tokens[:name_idx]
        after = tokens[name_idx + 1 :]
        organization = " ".join(before).strip() or carry_org
        name = tokens[name_idx]
        title = " ".join(after).strip()
        if not title and organization:
            possible = organization.split()
            if possible and possible[-1] in TITLE_WORDS:
                title = possible[-1]
                organization = " ".join(possible[:-1])
        if not organization or _is_internal(organization):
            continue
        if title and len(title) > 20:
            title = ""
        result.append(Participant(_clean(organization), name, _clean(title), meeting_id, meeting_text))
        carry_org = organization
    return result


def select_participants(similar: pd.DataFrame, max_people: int = 5) -> list[str]:
    candidates: list[tuple[float, Participant]] = []
    for rank, row in enumerate(similar.itertuples(index=False)):
        score = float(getattr(row, "similarity", 0.0)) + max(0, 0.05 - rank * 0.004)
        for p in parse_participants(row.participants_raw, int(row.id), row.search_text):
            candidates.append((score, p))
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
