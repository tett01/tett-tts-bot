"""추임새(interjection) 엔진 — 메시지 분위기를 읽고 앞뒤에 반응을 붙입니다.

edge-tts는 SSML 감정 태그가 막혀 있어서, 감정은 '텍스트로' 넣어야 합니다.
추임새를 본문과 같은 TTS 요청에 담으면 하나의 억양 흐름으로 이어져
따로 재생하는 것보다 훨씬 자연스럽게 들립니다.
"""

from __future__ import annotations

import random
import re
from collections import defaultdict, deque

# 분위기별 추임새. 앞에 붙는 것(pre)과 뒤에 붙는 것(post)을 나눕니다.
CHIMES = {
    "laugh": {
        "pre": ["아 진짜", "야", "푸흡", "아하하"],
        "post": ["웃기다 진짜", "아 배야", "미쳤네 진짜"],
    },
    "question": {
        "pre": ["음", "어", "글쎄", "흠"],
        "post": ["어떻게 생각해?", "그치?"],
    },
    "surprise": {
        "pre": ["헐", "우와", "오", "대박", "엥", "와아"],
        "post": ["진짜 대박이다", "말도 안 돼"],
    },
    "greeting": {
        "pre": ["아", "오", "어"],
        "post": ["반가워", "왔구나"],
    },
    "agree": {
        "pre": ["응응", "그치", "맞아", "오"],
        "post": ["내 말이", "인정"],
    },
    "sad": {
        "pre": ["아이고", "저런", "에구", "어어"],
        "post": ["괜찮아?", "힘내"],
    },
    "thanks": {
        "pre": ["아", "에이"],
        "post": ["별말씀을", "천만에"],
    },
    "sorry": {
        "pre": ["아니야", "에이"],
        "post": ["괜찮아", "신경 쓰지 마"],
    },
    "neutral": {
        "pre": ["음", "아", "어", "그", "오"],
        "post": [],
    },
}

# 분위기 판별 규칙. 위에서부터 먼저 걸리는 것이 이깁니다.
MOODS = [
    # 'ㄱㅅ ㅎㅎ' 처럼 뒤에 웃음이 붙는 경우가 많아, 뜻이 분명한 쪽을 먼저 봅니다.
    ("sorry",    r"ㅈㅅ|미안|죄송|잘못했"),
    ("thanks",   r"ㄱㅅ|고마|감사|ㄳ"),
    ("greeting", r"^(안녕|하이|ㅎㅇ|하잉|왔어|반가|굿모닝|잘자|바이|ㅂㅇ)"),
    ("sad",      r"[ㅜㅠ]{2,}|힘들|슬프|망했|죽겠|우울|현타|아프"),
    ("laugh",    r"[ㅋㅎ]{2,}|웃기|개웃"),
    ("surprise", r"대박|헐|미쳤|지린|개쩐|말도\s*안|レ|와[아우]?\b|!"),
    ("agree",    r"^(ㅇㅇ|ㅇㅋ|맞아|그치|인정|ㄹㅇ|그러게|오케)"),
    ("question", r"\?|뭐야|어때|어디|언제|왜|누가|누구|할까|갈까|봤어|있어"),
]

# 분위기가 뚜렷할수록 추임새를 자주 넣습니다. neutral은 드물게.
RATES = {
    "laugh": 0.75, "surprise": 0.72, "sad": 0.68, "question": 0.52,
    "greeting": 0.80, "agree": 0.58, "thanks": 0.70, "sorry": 0.70,
    # 실제 대화의 절반 가까이가 neutral이라, 이 값이 체감 빈도를 좌우합니다.
    "neutral": 0.28,
}
POST_RATE = 0.30   # 뒤에도 붙일 확률 (앞에 붙은 경우에 한해)

# 같은 추임새가 연달아 나오면 티가 나므로 최근 것을 기억해 피합니다.
_recent: dict[int, deque] = defaultdict(lambda: deque(maxlen=6))


def detect_mood(text: str) -> str:
    for mood, pattern in MOODS:
        if re.search(pattern, text):
            return mood
    return "neutral"


def _overlaps(candidate: str, text: str) -> bool:
    """추임새가 원문과 겹치는지 검사 ("헐 대박"에 "진짜 대박이다"를 붙이지 않도록)."""
    if candidate in text:
        return True
    if any(w in text for w in re.findall(r"[가-힣]{2,}", candidate)):
        return True
    # 반대 방향도 검사 ("헐 대박" + "진짜 대박이다" 처럼 어미만 다른 경우)
    return any(w in candidate for w in re.findall(r"[가-힣]{2,}", text))


def _pick(pool: list, guild_id: int, text: str = "") -> str | None:
    """최근에 쓰지 않았고, 원문과 겹치지 않는 것 중에서 고릅니다."""
    fresh = [c for c in pool if c not in _recent[guild_id] and not _overlaps(c, text)]
    if not fresh:
        fresh = [c for c in pool if not _overlaps(c, text)]
    if not fresh:
        fresh = pool
    if not fresh:
        return None
    choice = random.choice(fresh)
    _recent[guild_id].append(choice)
    return choice


def add_chime(text: str, guild_id: int, intensity: float = 1.0) -> str:
    """원문에 추임새를 붙여 돌려줍니다. intensity 0이면 그대로 통과.

    intensity: 0.0(끄기) ~ 2.0(항상). 1.0이 기본 빈도.
    """
    if intensity <= 0 or not text:
        return text

    mood = detect_mood(text)
    if random.random() > RATES[mood] * intensity:
        return text

    pre = _pick(CHIMES[mood]["pre"], guild_id, text)
    if pre:
        text = f"{pre}, {text}"

    posts = CHIMES[mood]["post"]
    if posts and random.random() < POST_RATE * intensity:
        post = _pick(posts, guild_id, text)
        if post:
            text = f"{text.rstrip('.!?, ')}, {post}"

    return text
