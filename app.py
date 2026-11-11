
import os
import re
import html
import tempfile
from pathlib import Path

import fitz
import streamlit as st
from ebooklib import epub


# ============================================================
# 기본 설정
# ============================================================

st.set_page_config(
    page_title="증권사 PDF → EPUB 3 변환기",
    page_icon="📚",
    layout="centered"
)

st.title("📚 증권사 리포트 PDF → EPUB 3")
st.caption(
    "본문은 확대 가능한 텍스트로 변환하고 "
    "표·그래프·사진은 PDF 원본에서 고해상도로 캡처합니다."
)

FINANCIAL_HEADINGS = [
    "재무상태표",
    "포괄손익계산서",
    "손익계산서",
    "현금흐름표",
    "자본변동표",
    "연결재무제표",
    "요약재무제표",
    "별도재무제표",
]


# ============================================================
# 텍스트 및 영역 처리
# ============================================================

def clean_text(text):
    text = text.replace("\x00", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def rect_area(rect):
    return max(0, rect.width) * max(0, rect.height)


def overlap_ratio(a, b):
    area = rect_area(a)
    if area <= 0:
        return 0

    inter = a & b
    return rect_area(inter) / area


def is_financial_statement(text):
    """
    일반 실적 분석 페이지는 제외하지 않고
    재무제표 제목이나 계정과목이 명확한 경우만 제외.
    """
    compact = re.sub(r"\s+", "", text)

    if any(word in compact for word in FINANCIAL_HEADINGS):
        return True

    account_terms = [
        "유동자산",
        "비유동자산",
        "자산총계",
        "부채총계",
        "자본총계",
        "영업활동현금흐름",
        "투자활동현금흐름",
    ]

    hits = sum(term in compact for term in account_terms)

    return hits >= 4


def valid_rect(rect, page_rect, min_area=0.003):
    if rect.width < 65 or rect.height < 30:
        return False

    if rect_area(rect) / max(1, rect_area(page_rect)) < min_area:
        return False

    return True


def expand_rect(rect, page_rect, margin=4):
    """
    캡처 시 표 테두리와 그래프 가장자리가 잘리지 않도록
    주변 여백을 조금 추가.
    """
    expanded = fitz.Rect(
        rect.x0 - margin,
        rect.y0 - margin,
        rect.x1 + margin,
        rect.y1 + margin
    )

    return expanded & page_rect


# ============================================================
# 표 검출
# ============================================================

def detect_tables(page):
    """
    표 전체의 bounding box를 검출.
    행별 셀을 개별 캡처하지 않음.
    """
    results = []

    strategies = ["lines", "text"]

    for strategy in strategies:
        try:
            finder = page.find_tables(strategy=strategy)

            for table in finder.tables:
                rect = fitz.Rect(table.bbox)

                if rect.width >= 80 and rect.height >= 35:
                    results.append({
                        "rect": rect,
                        "kind": "table",
                        "score": 100
                    })

        except Exception:
            continue

    return results


# ============================================================
# 이미지 및 그래프 검출
# ============================================================

def detect_images(page):
    results = []

    try:
        image_infos = page.get_image_info(xrefs=True)

        for info in image_infos:
            bbox = info.get("bbox")

            if not bbox:
                continue

            rect = fitz.Rect(bbox)

            if rect.width >= 55 and rect.height >= 40:
                results.append({
                    "rect": rect,
                    "kind": "image",
                    "score": 80
                })

    except Exception:
        pass

    return results


def detect_vector_graphs(page):
    """
    PDF 안의 선, 도형, 벡터 그래프 영역 검출.
    작은 장식선이 캡처되지 않도록 크기 제한을 둠.
    """
    results = []

    if not hasattr(page, "cluster_drawings"):
        return results

    try:
        clusters = page.cluster_drawings(
            x_tolerance=8,
            y_tolerance=8
        )

        for cluster in clusters:
            rect = fitz.Rect(cluster)

            if not valid_rect(
                rect,
                page.rect,
                min_area=0.012
            ):
                continue

            # 지나치게 긴 선이나 페이지 배경 장식 제거
            if rect.width > page.rect.width * 0.98:
                continue

            if rect.height > page.rect.height * 0.85:
                continue

            results.append({
                "rect": rect,
                "kind": "graph",
                "score": 60
            })

    except Exception:
        pass

    return results


# ============================================================
# 후보 영역 중복 제거
# ============================================================

def deduplicate_regions(regions):
    """
    같은 위치를 표와 이미지가 중복 검출한 경우
    하나만 남김.

    서로 다른 표나 그래프는 무조건 합치지 않음.
    """
    regions = sorted(
        regions,
        key=lambda x: (
            x["score"],
            rect_area(x["rect"])
        ),
        reverse=True
    )

    selected = []

    for item in regions:
        rect = item["rect"]

        duplicate = False

        for old in selected:
            old_rect = old["rect"]

            ratio1 = overlap_ratio(rect, old_rect)
            ratio2 = overlap_ratio(old_rect, rect)

            intersection = rect & old_rect

            # 한 영역이 다른 영역에 대부분 포함되면 중복
            if ratio1 > 0.78 or ratio2 > 0.78:
                duplicate = True
                break

            # 거의 같은 크기의 영역이 상당 부분 겹치면 중복
            if (
                rect_area(intersection) > 0
                and rect_area(intersection) /
                max(1, min(rect_area(rect), rect_area(old_rect))) > 0.85
            ):
                duplicate = True
                break

        if not duplicate:
            selected.append(item)

    return selected


# ============================================================
# 표·그래프·이미지 고해상도 캡처
# ============================================================

def capture_region(page, rect, dpi):
    """
    PDF 원본을 직접 렌더링.
    이미지 추출보다 벡터 그래프 및 표 보존에 유리.
    """
    rect = expand_rect(rect, page.rect, margin=5)

    pix = page.get_pixmap(
        matrix=fitz.Matrix(dpi / 72, dpi / 72),
        clip=rect,
        colorspace=fitz.csRGB,
        alpha=False,
        annots=True
    )

    return pix.tobytes("png")


# ============================================================
# 텍스트 추출 및 제목 구분
# ============================================================

def extract_text_blocks(page):
    data = page.get_text("dict", sort=True)
    results = []

    for block in data.get("blocks", []):
        if block.get("type") != 0:
            continue

        lines = []

        for line in block.get("lines", []):
            line_text = ""

            for span in line.get("spans", []):
                line_text += span.get("text", "")

            line_text = clean_text(line_text)

            if line_text:
                lines.append(line_text)

        text = clean_text("\n".join(lines))

        if not text:
            continue

        rect = fitz.Rect(block["bbox"])

        max_size = 0

        for line in block.get("lines", []):
            for span in line.get("spans", []):
                max_size = max(
                    max_size,
                    span.get("size", 0)
                )

        results.append({
            "rect": rect,
            "text": text,
            "font_size": max_size
        })

    return results


def text_to_html(text, font_size):
    """
    HTML 특수문자 처리.
    큰 글씨는 소제목으로 변환.
    """
    safe = html.escape(text)

    lines = [
        line.strip()
        for line in safe.split("\n")
        if line.strip()
    ]

    output = []

    for line in lines:
        if re.fullmatch(r"\d+", line):
            continue

        if len(line) < 65 and font_size >= 13:
            output.append(f"<h3>{line}</h3>")
        else:
            output.append(f"<p>{line}</p>")

    return "\n".join(output)


def block_is_inside_region(block_rect, regions):
    """
    캡처된 표·그래프 내부의 텍스트는
    중복 출력하지 않음.
    """
    for region in regions:
        if overlap_ratio(block_rect, region["rect"]) >= 0.45:
            return True

    return False


# ============================================================
# EPUB CSS
# ============================================================

EPUB_CSS = """
body {
    font-family: "Noto Sans KR",
    "Apple SD Gothic Neo",
    "Malgun Gothic",
    sans-serif;
    line-height: 1.75;
    padding: 4%;
    color: #222222;
    word-break: keep-all;
}

h2 {
    font-size: 1.35em;
    line-height: 1.5;
    margin-top: 1.5em;
    margin-bottom: 1em;
    border-bottom: 1px solid #cccccc;
    padding-bottom: 0.4em;
}

h3 {
    font-size: 1.12em;
    line-height: 1.5;
    margin-top: 1.4em;
    margin-bottom: 0.7em;
    font-weight: bold;
}

p {
    font-size: 1em;
    line-height: 1.8;
    margin: 0.4em 0 0.9em 0;
    text-align: justify;
}

.visual {
    margin: 1.2em 0;
    text-align: center;
    page-break-inside: avoid;
}

.visual img {
    display: block;
    width: 100%;
    max-width: 100%;
    height: auto;
    margin: 0 auto;
}

.visual-caption {
    font-size: 0.8em;
    color: #666666;
    margin-top: 0.3em;
    text-align: center;
}

.page-number {
    color: #888888;
    font-size: 0.8em;
    text-align: right;
    margin-bottom: 1em;
}

.skip-notice {
    color: #777777;
    font-style: italic;
    text-align: center;
}
"""


# ============================================================
# PDF → EPUB 변환
# ============================================================

def convert_pdf_to_epub(
    pdf_path,
    output_path,
    title,
    dpi=360,
    skip_financial=True
):

    doc = fitz.open(pdf_path)

    book = epub.EpubBook()

    book.set_identifier(
        f"report-{os.path.basename(pdf_path)}"
    )

    book.set_title(title)
    book.set_language("ko")
    book.add_author("Report EPUB Converter")

    css_item = epub.EpubItem(
        uid="main_css",
        file_name="Styles/main.css",
        media_type="text/css",
        content=EPUB_CSS.encode("utf-8")
    )

    book.add_item(css_item)

    chapters = []
    image_count = 0
    skipped_pages = []
    warnings = []

    for page_index in range(len(doc)):

        page = doc[page_index]
        page_number = page_index + 1

        page_text = page.get_text("text")

        # ----------------------------------------------------
        # 재무제표 페이지 생략
        # ----------------------------------------------------

        if skip_financial and is_financial_statement(page_text):
            skipped_pages.append(page_number)
            continue

        elements = []

        # ----------------------------------------------------
        # 시각자료 영역 검출
        # ----------------------------------------------------

        regions = []

        regions.extend(detect_tables(page))
        regions.extend(detect_images(page))
        regions.extend(detect_vector_graphs(page))

        regions = deduplicate_regions(regions)

        # 지나치게 작은 영역 제거
        regions = [
            item for item in regions
            if valid_rect(
                item["rect"],
                page.rect,
                min_area=0.003
            )
        ]

        # 위에서 아래 순서로 정렬
        regions.sort(
            key=lambda x: (
                x["rect"].y0,
                x["rect"].x0
            )
        )

        # ----------------------------------------------------
        # 시각자료 원본 영역 캡처
        # ----------------------------------------------------

        for region in regions:

            rect = region["rect"]

            try:
                image_bytes = capture_region(
                    page,
                    rect,
                    dpi
                )

                image_count += 1

                image_name = (
                    f"Images/page_{page_number}_"
                    f"visual_{image_count}.png"
                )

                image_item = epub.EpubItem(
                    uid=f"visual_{image_count}",
                    file_name=image_name,
                    media_type="image/png",
                    content=image_bytes
                )

                book.add_item(image_item)

                # XHTML은 Text 폴더 안에 있으므로
                # 이미지 경로는 반드시 ../Images/
                relative_path = (
                    f"../Images/page_{page_number}_"
                    f"visual_{image_count}.png"
                )

                visual_html = f"""
                <div class="visual">
                    <img src="{relative_path}"
                         alt="PDF 원본 시각자료"/>
                </div>
                """

                elements.append({
                    "y": rect.y0,
                    "x": rect.x0,
                    "html": visual_html
                })

            except Exception as error:
                warnings.append(
                    f"{page_number}페이지 시각자료 캡처 실패: {error}"
                )

        # ----------------------------------------------------
        # 본문 텍스트 추출
        # ----------------------------------------------------

        text_blocks = extract_text_blocks(page)

        for block in text_blocks:

            rect = block["rect"]

            # 표·그래프 내부 텍스트 중복 제거
            if block_is_inside_region(rect, regions):
                continue

            text_html = text_to_html(
                block["text"],
                block["font_size"]
            )

            if text_html.strip():

                elements.append({
                    "y": rect.y0,
                    "x": rect.x0,
                    "html": text_html
                })

        # ----------------------------------------------------
        # 텍스트와 이미지 원래 위치 순서로 배치
        # ----------------------------------------------------

        elements.sort(
            key=lambda item: (
                item["y"],
                item["x"]
            )
        )

        page_content = (
            f'<div class="page-number">'
            f'원본 PDF {page_number}페이지'
            f'</div>'
        )

        for element in elements:
            page_content += element["html"]

        if not elements:
            page_content += (
                '<p class="skip-notice">'
                '이 페이지에서 추출할 수 있는 콘텐츠가 없습니다.'
                '</p>'
            )

        # ----------------------------------------------------
        # EPUB 페이지 생성
        # ----------------------------------------------------

        chapter = epub.EpubHtml(
            title=f"{page_number}페이지",
            file_name=f"Text/page_{page_number:04d}.xhtml",
            lang="ko"
        )

        chapter.content = f"""
        <?xml version="1.0" encoding="utf-8"?>
        <html xmlns="http://www.w3.org/1999/xhtml"
              xmlns:epub="http://www.idpf.org/2007/ops"
              lang="ko">
        <head>
            <meta charset="utf-8"/>
            <title>{html.escape(title)} - {page_number}페이지</title>
            <link rel="stylesheet"
                  type="text/css"
                  href="../Styles/main.css"/>
        </head>
        <body>
            <h2>{page_number}페이지</h2>
            {page_content}
        </body>
        </html>
        """

        chapter.add_item(css_item)
        book.add_item(chapter)
        chapters.append(chapter)

    # --------------------------------------------------------
    # 목차 및 EPUB 구조
    # --------------------------------------------------------

    book.toc = tuple(chapters)

    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())

    book.spine = ["nav"] + chapters

    epub.write_epub(output_path, book)

    doc.close()

    return {
        "pages": len(chapters),
        "images": image_count,
        "skipped": skipped_pages,
        "warnings": warnings
    }


# ============================================================
# Streamlit 화면
# ============================================================

uploaded_file = st.file_uploader(
    "증권사 리포트 PDF 업로드",
    type=["pdf"]
)

custom_title = st.text_input(
    "EPUB 제목",
    value="증권사 리포트"
)

dpi_choice = st.select_slider(
    "표·그래프 캡처 해상도",
    options=[250, 300, 360, 400],
    value=360
)

skip_financial = st.checkbox(
    "재무제표 페이지 자동 생략",
    value=True
)

st.info(
    "360 DPI를 기본으로 권장합니다. "
    "400 DPI는 이미지 용량이 더 커질 수 있습니다."
)

if uploaded_file is not None:

    if st.button(
        "🚀 EPUB 3 변환 시작",
        type="primary",
        use_container_width=True
    ):

        temp_pdf = None
        temp_epub = None

        try:

            with tempfile.NamedTemporaryFile(
                delete=False,
                suffix=".pdf"
            ) as pdf_file:

                pdf_file.write(
                    uploaded_file.getvalue()
                )

                temp_pdf = pdf_file.name

            temp_epub = (
                tempfile.mktemp(suffix=".epub")
            )

            with st.spinner(
                "PDF의 본문·표·그래프를 분석하고 있습니다..."
            ):

                result = convert_pdf_to_epub(
                    temp_pdf,
                    temp_epub,
                    custom_title,
                    dpi=dpi_choice,
                    skip_financial=skip_financial
                )

            st.success("EPUB 3 변환이 완료되었습니다.")

            st.write(
                f"변환된 페이지: {result['pages']}페이지"
            )

            st.write(
                f"캡처된 표·그래프·이미지: "
                f"{result['images']}개"
            )

            if result["skipped"]:
                st.warning(
                    "재무제표로 판단해 생략한 페이지: "
                    + ", ".join(map(str, result["skipped"]))
                )

            if result["warnings"]:
                with st.expander("변환 경고 확인"):
                    for warning in result["warnings"]:
                        st.write(warning)

            with open(temp_epub, "rb") as epub_file:

                st.download_button(
                    label="📥 EPUB 파일 다운로드",
                    data=epub_file.read(),
                    file_name=f"{custom_title}.epub",
                    mime="application/epub+zip",
                    use_container_width=True
                )

        except Exception as error:
            st.error(f"변환 오류: {error}")

        finally:

            for path in [temp_pdf, temp_epub]:

                if path and os.path.exists(path):
                    try:
                        os.remove(path)
                    except Exception:
                        pass
