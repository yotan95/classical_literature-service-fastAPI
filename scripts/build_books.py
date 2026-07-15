"""book.json 빌드 스크립트(개발 도구).  # (Build per-book book.json from raw data)

기존 원천 데이터(meta.json + script_summary.json)와 아래의 SCENE_MAP/SUMMARIES를 합쳐
각 책의 단일 정본 `app/data/<slug>/book.json`을 생성한다. 런타임이 아니라 데이터 작성용
개발 도구이며, '책 추가' 유지보수 시 SCENE_MAP/SUMMARIES에 한 권을 더해 다시 실행하면 된다.

생성 결과(book.json)가 서버의 단일 출처가 되고, meta/script/script_summary/voice_profiles 는 폐기한다.

사용법:
    python scripts/build_books.py            # 전체 재생성
    python scripts/build_books.py heosaeng_jeon   # 특정 책만
"""

import json
import sys
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "app" / "data"

# 책 표지/창작하기 목록에 쓰는 책 단위 이모지(기존 book_list.json 값).
BOOK_EMOJI: dict[str, str] = {
    "heosaeng_jeon": "💰",
    "hongbu_jeon": "🐦",
    "hong_gildong_jeon": "⚔️",
    "bakssi_jeon": "🦋",
    "kongjwi_patjwi_jeon": "🌸",
    "tokki_jeon": "🐰",
}

# full 스코프 창작 소스로 쓰는 '전체 줄거리' 3문단 요약(흐름+교훈). 책마다 고정(정적).
SUMMARIES: dict[str, str] = {
    "heosaeng_jeon": (
        "묵적골의 가난한 선비 허생은 글 읽기에만 몰두하고, 아내는 삯바느질로 겨우 생계를 잇는다. "
        "굶주림에 지친 아내가 과거도 보지 않을 글을 읽어 무엇하느냐고 다그치자, 허생은 십 년을 기약했던 "
        "독서를 칠 년 만에 접고 집을 나선다.\n\n"
        "허생은 장안 제일의 부자 변씨에게 만 냥을 빌려 안성에서 과일을 매점해 큰 이익을 얻으며 조선 경제가 "
        "얼마나 허약한지를 드러낸다. 이어 빈 섬으로 도둑 떼를 데려가 농사를 짓고 이상향을 세웠다가, 글 아는 "
        "이들을 데리고 섬을 떠나며 남은 백만 냥을 바다에 버린다. 변씨에게 돈을 갚은 뒤에는 어영대장 이완을 "
        "만나 인재 등용과 북벌의 허세를 통박한다.\n\n"
        "이완이 어느 것도 실행하지 못하자 허생은 그를 꾸짖어 내쫓고, 이튿날 홀연히 자취를 감춘다. 무능한 "
        "지배층과 공허한 북벌론을 풍자하면서, 실용적인 경제관과 지식인의 책임을 일깨우는 이야기다."
    ),
    "hongbu_jeon": (
        "욕심 많은 형 놀부는 부모의 재산을 독차지하고 착한 동생 흥부네 식구를 빈손으로 내쫓는다. 흥부는 "
        "온갖 품을 팔고 매품까지 팔려 하지만 좀처럼 가난을 벗지 못한다.\n\n"
        "어느 봄날 흥부가 다리 부러진 제비를 정성껏 고쳐 주자, 이듬해 제비가 박씨를 물어다 준다. 그 박에서 "
        "금은보화가 쏟아져 흥부는 큰 부자가 된다. 이를 시샘한 놀부가 일부러 제비 다리를 부러뜨렸다가 고쳐 "
        "주지만, 놀부의 박에서는 도깨비와 온갖 재앙이 쏟아져 그는 패가망신한다.\n\n"
        "모든 것을 잃은 놀부는 흥부를 찾아가 잘못을 뉘우치고, 흥부는 형을 너그러이 받아들여 형제가 화해한다. "
        "권선징악과 형제간의 우애, 베풂의 가치를 해학 속에 담아낸 이야기다."
    ),
    "hong_gildong_jeon": (
        "홍판서의 서자로 태어난 길동은 비범한 재주를 지녔으나 신분 탓에 아버지를 아버지라, 형을 형이라 "
        "부르지 못한다. 적서 차별의 한과 초란 일파의 음모 속에 자객의 위협까지 받자, 길동은 도술로 위기를 "
        "넘기고 집을 떠난다.\n\n"
        "길동은 활빈당의 우두머리가 되어 탐관오리의 재물을 빼앗아 백성에게 나누고, 분신술과 둔갑술로 관군과 "
        "포도대장 이흡을 농락한다. 조정이 끝내 그를 잡지 못하자 임금은 길동을 병조판서로 임명해 회유한다.\n\n"
        "소원을 이룬 길동은 조선을 떠나 율도국을 정벌하고 왕이 되어 이상적인 나라를 다스린다. 신분제의 모순을 "
        "비판하고 평등과 이상 사회를 향한 염원을 담은, 우리나라 최초의 한글 소설이다."
    ),
    "bakssi_jeon": (
        "이시백은 박처사의 딸 박씨와 혼인하지만, 신부의 흉한 허물을 보고 그를 멀리한다. 박씨는 모진 박대 "
        "속에서도 의연함을 잃지 않고 신묘한 재주로 집안을 일으킨다.\n\n"
        "마침내 박씨가 허물을 벗고 절세미인으로 변하자 이시백은 자신의 잘못을 뉘우친다. 병자호란이 일어나 "
        "나라가 위태로워지자, 박씨는 자객 기룡대를 물리치고 시비 계화와 함께 도술로 적장 용골대의 군대를 "
        "농락하며 위기에서 나라를 지킨다.\n\n"
        "비록 임금이 항복하는 역사적 비극까지 막지는 못하지만, 박씨는 그 공으로 충렬부인에 봉해진다. "
        "병자호란의 치욕을 여성 영웅의 활약으로 위로하고, 외모보다 내면과 능력의 가치를 일깨운 작품이다."
    ),
    "kongjwi_patjwi_jeon": (
        "콩쥐는 어머니를 여의고 계모 배씨와 이복동생 팥쥐 밑에서 모진 구박을 받는다. 계모는 콩쥐에게 자갈밭 "
        "갈기, 밑 빠진 독에 물 채우기 같은 불가능한 일을 시킨다.\n\n"
        "그때마다 검은 소, 두꺼비, 직녀 같은 신비한 조력자들이 나타나 콩쥐를 돕는다. 콩쥐는 예쁜 옷을 입고 "
        "잔치에 가던 길에 꽃신 한 짝을 잃는데, 그 꽃신을 계기로 감사의 부인이 된다.\n\n"
        "이를 시기한 팥쥐가 콩쥐를 해치고 그 자리를 차지하려 하지만, 콩쥐는 환생해 진실을 밝히고 팥쥐와 "
        "계모는 끝내 벌을 받는다. 착한 사람은 복을 받고 악한 사람은 벌을 받는다는 권선징악을 담은 한국판 "
        "신데렐라 이야기다."
    ),
    "tokki_jeon": (
        "용왕이 깊은 병이 들어 토끼의 간이 약이 된다는 말에, 자라가 육지로 나가 토끼를 용궁으로 꾀어 "
        "데려온다. (이 판본은 토끼가 영덕전에 끌려오는 장면부터 시작한다.)\n\n"
        "용왕 앞에 끌려온 토끼는 죽을 위기에 처하자, 간을 육지에 두고 왔다는 꾀를 낸다. 간을 마음대로 넣었다 "
        "뺐다 한다는 토끼의 능청에 용왕은 깜빡 속아 잔치를 베풀고 다시 육지로 돌려보낸다.\n\n"
        "뭍에 오른 토끼는 자라를 조롱하며 달아나고, 자라는 빈손으로 탄식한다. 약자가 지혜로 강자의 횡포에서 "
        "살아남는 모습을 통해 권력의 어리석음과 기지의 가치를 풍자한 판소리계 우화다."
    ),
}

# 장면별 선택 UI(=Flutter kScenes)와 그 장면이 포함하는 summary 챕터 인덱스(0-based) 매핑.
# (emoji, title, description, [chapter_index, ...]) — segments는 해당 챕터들의 세그먼트를 모은다.
SCENE_MAP: dict[str, list[tuple]] = {
    "hongbu_jeon": [
        ("🏠", "형제의 갈림", "놀부가 흥부네 식구를 집에서 내쫓아요", [0, 1]),
        ("🐦", "제비 구하기", "흥부가 다친 제비 다리를 정성껏 고쳐줘요", [2, 3]),
        ("🎁", "흥부의 박", "제비가 물어온 박씨에서 보물이 쏟아져요", [4]),
        ("😤", "놀부의 욕심", "놀부가 일부러 제비 다리를 부러뜨려요", [5, 6]),
        ("🤝", "형제의 화해", "벌을 받은 놀부가 반성하고 화해해요", [7, 8]),
    ],
    "bakssi_jeon": [
        ("💍", "못난 새 신부", "이시백이 박색 신부 박씨를 멀리해요", [0, 2]),
        ("🌸", "허물을 벗다", "박씨가 허물을 벗고 절세미인이 돼요", [3]),
        ("🏡", "비범한 재주", "박씨가 신통한 능력으로 집안을 일으켜요", [1]),
        ("⚔️", "전쟁이 닥치다", "병자호란이 일어나 나라가 위태로워요", [4, 6]),
        ("🛡️", "나라를 지키다", "박씨가 지혜와 도술로 적을 물리쳐요", [5, 7, 8, 9]),
    ],
    "kongjwi_patjwi_jeon": [
        ("🌾", "불가능한 숙제", "콩쥐가 팥쥐 엄마에게 어려운 일을 받아요", [0]),
        ("🐄", "신기한 도움", "동물들이 나타나 콩쥐 일을 도와줘요", [1, 2, 3]),
        ("👘", "잔치에 가는 날", "예쁜 옷을 입은 콩쥐가 잔치에 나가요", [4]),
        ("👞", "꽃신 한 짝", "서두르다 꽃신 한 짝을 두고 와요", [5]),
        ("🌺", "행복한 결말", "원님이 콩쥐를 찾아 함께 살아가요", [6, 7, 8]),
    ],
    "heosaeng_jeon": [
        ("📚", "가난한 선비", "허생이 글만 읽다 아내의 핀잔을 들어요", [0]),
        ("💰", "큰돈을 빌리다", "허생이 변씨에게 만 냥을 빌려요", [1]),
        ("🍊", "과일을 사들이다", "허생이 과일을 매점해 큰 이익을 봐요", [2]),
        ("🏝️", "빈 섬의 이상국", "허생이 도둑들과 빈 섬에 새 세상을 세워요", [3, 4]),
        ("🙇", "돈을 돌려주다", "허생이 변씨에게 돈을 갚고 홀연히 떠나요", [5, 6, 7, 8]),
    ],
    "hong_gildong_jeon": [
        ("👶", "서자로 태어나", "길동이 서자라 아버지를 아버지라 못 불러요", [0]),
        ("🥷", "집을 떠나다", "차별을 견디던 길동이 집을 떠나요", [1, 2]),
        ("🏹", "활빈당의 우두머리", "길동이 활빈당을 이끌어 탐관오리를 벌해요", [3, 4]),
        ("✨", "도술을 부리다", "길동이 분신술로 관군을 농락해요", [5, 6, 7]),
        ("👑", "율도국의 왕", "길동이 율도국을 세워 이상 나라를 다스려요", [8, 9, 10, 11]),
    ],
    # 토끼전: 이 판본은 자라가 토끼를 꾀는 도입부가 생략되어 1·2장면은 근사 매핑이다.
    "tokki_jeon": [
        ("🐢", "거북이의 초대", "자라가 토끼를 용궁으로 꾀어 데려가요", [0]),
        ("🏰", "용궁 도착", "토끼가 용왕 앞에 끌려가요", [1, 2]),
        ("🧠", "토끼의 꾀", "토끼가 간을 땅에 두고 왔다고 속여요", [3, 4]),
        ("🌊", "탈출 성공", "토끼가 용궁에서 빠져나와 육지로 돌아와요", [5, 6]),
    ],
}


def _chapter_segments(summary: dict) -> list[list[dict]]:
    """summary의 챕터별 세그먼트 묶음을 만든다.  # (Group segments per chapter)

    chapters[i].startSegmentId ~ chapters[i+1].startSegmentId 직전까지를 i번 챕터로 본다.
    """
    chapters = summary.get("chapters", [])
    segments = summary.get("segments", [])
    pos = {s["id"]: i for i, s in enumerate(segments)}
    bounds = [pos[c["startSegmentId"]] for c in chapters] + [len(segments)]
    return [segments[bounds[i] : bounds[i + 1]] for i in range(len(chapters))]


def build_one(slug: str) -> dict:
    """한 권의 book.json 내용을 만든다.  # (Build a single book.json dict)"""
    bdir = DATA_DIR / slug
    meta = json.loads((bdir / "meta.json").read_text(encoding="utf-8"))
    summary_data = json.loads((bdir / "script_summary.json").read_text(encoding="utf-8"))
    ch_segs = _chapter_segments(summary_data)

    scenes = []
    for i, (emoji, title, desc, ch_idx) in enumerate(SCENE_MAP[slug], start=1):
        segs: list[dict] = []
        for ci in ch_idx:
            for s in ch_segs[ci]:
                segs.append(
                    {
                        "order": s["order"],
                        "speaker": s["speaker"],
                        "type": s["type"],
                        "text": s["text"],
                    }
                )
        scenes.append(
            {
                "sceneId": f"scene-{i}",
                "order": i,
                "emoji": emoji,
                "title": title,
                "description": desc,
                "segments": segs,
            }
        )

    characters = [
        {
            "characterId": f"char-{i}",
            "name": c.get("name"),
            "role": c.get("role"),
            "description": c.get("description"),
        }
        for i, c in enumerate(meta.get("characters", []), start=1)
    ]

    return {
        "bookId": slug,
        "title": meta.get("title"),
        "emoji": BOOK_EMOJI.get(slug, "📖"),
        "author": meta.get("author"),
        "era": meta.get("era"),
        "genre": meta.get("genre"),
        "difficulty": meta.get("difficulty"),
        "tags": meta.get("tags", []),
        "coverColor": meta.get("coverColor"),
        "shortDescription": meta.get("shortDescription"),
        "summary": SUMMARIES[slug],
        "characters": characters,
        "scenes": scenes,
    }


def main() -> None:
    targets = sys.argv[1:] or list(SCENE_MAP.keys())
    for slug in targets:
        book = build_one(slug)
        out = DATA_DIR / slug / "book.json"
        out.write_text(json.dumps(book, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        n_seg = sum(len(s["segments"]) for s in book["scenes"])
        print(f"wrote {out}  (scenes={len(book['scenes'])}, segments={n_seg}, chars={len(book['characters'])})")


if __name__ == "__main__":
    main()
