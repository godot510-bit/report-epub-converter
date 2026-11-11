
import os
import re
import html
import tempfile
import uuid

import fitz
import streamlit as st
from ebooklib import epub


# ============================================================
# 1. Streamlit
# ============================================================

st.set_page_config(
    page_title="증권사 PDF EPUB 3 변환기",
    page_icon="📚",
    layout="centered"
)

st.title("📚 증권사 리포트 PDF → EPUB 3")
st.caption(
    "본문은 리플로우 텍스트, 표는 실제 EPUB 표 또는 고해상도 이미지, "
    "그래프와 사진은 원본 캡처로 변환합니다."
)


# ============================================================
# 2. 재무제표 판별
# ============================================================

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


def is_financial_statement(text):
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


# ============================================================
# 3. 공통 영역 함수
# ============================================================

def area(rect):
    return max(0, rect.width) * max(0, rect.height)


def overlap_ratio(a, b):
    total = area(a)

    if total <= 0:
        return 0

    return area(a & b) / total


def clean_text(text):
    text = text.replace("\x00", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def escape_text(text):
    return html.escape(str(text or ""))


def expand_rect(rect, page_rect, margin=4):
    result = fitz.Rect(
        rect.x0 - margin,
        rect.y0 - margin,
        rect.x1 + margin,
        rect.y1 + margin
    )

    return result & page_rect


def is_valid_visual(rect, page_rect):
    if rect.width < 65 or rect.height < 35:
        return False

    ratio = area(rect) / max(1, area(page_rect))

    if ratio < 0.004:
        return False

    if rect.height > page_rect.height * 0.92:
        return False

    return True


# ============================================================
# 4. 표 데이터 품질 평가
# ============================================================

def normalize_table_data(rows):
    result = []

    if not rows:
        return result

    max_cols = max(
        (len(row) for row in rows if row),
        default=0
    )

    if max_cols == 0:
        return result

    for row in rows:
        row = list(row or [])

        row += [None] * (max_cols - len(row))

        result.append([
            clean_text(str(cell)) if cell is not None else ""
            for cell in row
        ])

    # 완전히 비어 있는 행 제거
    result = [
        row for row in result
        if any(cell.strip() for cell in row)
    ]

    return result


def evaluate_table(rows):
    """
    실제 EPUB 표로 재구성할 수 있는지 평가.
    셀 데이터가 지나치게 비어 있거나 구조가 불규칙하면
    원본 캡처 방식으로 전환.
    """

    if len(rows) < 2:
        return False

    cols = max(len(row) for row in rows)

    if cols < 2:
        return False

    if cols > 25 or len(rows) > 150:
        return False

    total_cells = len(rows) * cols

    filled_cells = sum(
        1 for row in rows
        for cell in row
        if cell.strip()
    )

    density = filled_cells / max(1, total_cells)

    if density < 0.25:
        return False

    # 표 데이터가 실제로 존재하는지 검사
    numeric_cells = sum(
        1 for row in rows
        for cell in row
        if re.search(r"\d", cell)
    )

    if numeric_cells < 2:
        return False

    # 행별 열 수가 지나치게 불규칙하면 이미지 처리
    row_lengths = [len(row) for row in rows]

    if len(set(row_lengths)) > 1:
        return False

    return True


# ============================================================
# 5. EPUB 표 HTML 생성
# ============================================================

def table_to_html(rows, table_number):
    """
    실제 HTML table로 재구성.
    첫 번째 행을 헤더로 표시.
    """

    output = [
        '<div class="table-scroll">',
        f'<table class="report-table" id="table-{table_number}">',
        '<thead><tr>'
    ]

    first_row = rows[0]

    for cell in first_row:
        output.append(
            f'<th>{escape_text(cell)}</th>'
        )

    output.append('</tr></thead><tbody>')

    for row in rows[1:]:

        output.append('<tr>')

        for cell in row:
            output.append(
                f'<td>{escape_text(cell)}</td>'
            )

        output.append('</tr>')

    output.append('</tbody></table></div>')

    return "".join(output)


# ============================================================
# 6. 표 검출
# ============================================================

def detect_tables(page):
    """
    표 전체를 검출합니다.
    숫자 밀집 텍스트 블록을 표로 추측하는 기능은 사용하지 않습니다.
    """

    results = []

    try:
        finder = page.find_tables(strategy="lines")

        for table in finder.tables:

            rect = fitz.Rect(table.bbox)

            if rect.width < 70 or rect.height < 30:
                continue

            try:
                rows = normalize_table_data(table.extract())
            except Exception:
                rows = []

            # 외부 헤더가 별도로 검출되는 경우 헤더 보완
            try:
                header = table.header

                if (
                    getattr(header, "external", False)
                    and getattr(header, "names", None)
                ):
                    names = [
                        clean_text(str(x or ""))
                        for x in header.names
                    ]

                    if names and (
                        not rows or rows[0] != names
                    ):
                        rows.insert(0, names)

            except Exception:
                pass

            results.append({
                "rect": rect,
                "rows": rows,
                "mode": (
                    "table"
                    if evaluate_table(rows)
                    else "image"
                ),
                "kind": "table"
            })

    except Exception:
        pass

    # 선이 없는 표 보조 검출
    try:
        finder = page.find_tables(strategy="text")

        for table in finder.tables:

            rect = fitz.Rect(table.bbox)

            if rect.width < 90 or rect.height < 35:
                continue

            # 이미 검출된 표와 대부분 겹치면 중복 제외
            if any(
                overlap_ratio(rect, old["rect"]) > 0.75
                or overlap_ratio(old["rect"], rect) > 0.75
                for old in results
            ):
                continue

            try:
                rows = normalize_table_data(table.extract())
            except Exception:
                rows = []

            try:
                header = table.header

                if (
                    getattr(header, "external", False)
                    and getattr(header, "names", None)
                ):
                    names = [
                        clean_text(str(x or ""))
                        for x in header.names
                    ]

                    if names and (
                        not rows or rows[0] != names
                    ):
                        rows.insert(0, names)

            except Exception:
                pass

            results.append({
                "rect": rect,
                "rows": rows,
                "mode": (
                    "table"
                    if evaluate_table(rows)
                    else "image"
                ),
                "kind": "table"
            })

    except Exception:
        pass

    return results


# ============================================================
# 7. 사진 및 그래프 영역 검출
# ============================================================

def detect_images(page, table_regions):
    results = []

    try:
        infos = page.get_image_info(xrefs=True)

        for info in infos:

            bbox = info.get("bbox")

            if not bbox:
                continue

            rect = fitz.Rect(bbox)

            if not is_valid_visual(rect, page.rect):
                continue

            # 표 안의 이미지 조각은 별도 삽입하지 않음
            if any(
                overlap_ratio(rect, t["rect"]) > 0.55
                for t in table_regions
            ):
                continue

            results.append({
                "rect": rect,
                "kind": "image",
                "mode": "image"
            })

    except Exception:
        pass

    return results


def detect_vector_graphs(page, table_regions, image_regions):
    """
    벡터 그래프 및 도형 검출.
    작은 선 조각은 제외.
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

            if not is_valid_visual(rect, page.rect):
                continue

            if rect.width > page.rect.width * 0.98:
                continue

            if rect.height > page.rect.height * 0.85:
                continue

            # 표 내부 도형은 표 캡처에 포함
            if any(
                overlap_ratio(rect, t["rect"]) > 0.45
                for t in table_regions
            ):
                continue

            # 이미 이미지로 검출된 영역은 제외
            if any(
                overlap_ratio(rect, im["rect"]) > 0.75
                for im in image_regions
            ):
                continue

            results.append({
                "rect": rect,
                "kind": "graph",
                "mode": "image"
            })

    except Exception:
        pass

    return results


# ============================================================
# 8. 시각자료 중복 제거
# ============================================================

def deduplicate_visuals(regions):
    selected = []

    # 큰 영역 우선
    regions = sorted(
        regions,
        key=lambda x: area(x["rect"]),
        reverse=True
    )

    for item in regions:

        rect = item["rect"]

        duplicate = False

        for old in selected:

            old_rect = old["rect"]

            r1 = overlap_ratio(rect, old_rect)
            r2 = overlap_ratio(old_rect, rect)

            if r1 > 0.82 or r2 > 0.82:
                duplicate = True
                break

        if not duplicate:
            selected.append(item)

    return selected


# ============================================================
# 9. 원본 PDF 고해상도 캡처
# ============================================================

def capture_pdf_region(page, rect, dpi):

    rect = expand_rect(
        rect,
        page.rect,
        margin=5
    )

    pix = page.get_pixmap(
        matrix=fitz.Matrix(dpi / 72, dpi / 72),
        clip=rect,
        colorspace=fitz.csRGB,
        alpha=False,
        annots=True
    )

    return pix.tobytes("png")


# ============================================================
# 10. 텍스트 추출
# ============================================================

def extract_blocks(page):
    data = page.get_text("dict", sort=True)

    blocks = []

    for block in data.get("blocks", []):

        if block.get("type") != 0:
            continue

        lines = []
        font_sizes = []

        for line in block.get("lines", []):

            line_text = ""

            for span in line.get("spans", []):

                line_text += span.get("text", "")

                if span.get("text", "").strip():
                    font_sizes.append(
                        span.get("size", 0)
                    )

            line_text = clean_text(line_text)

            if line_text:
                lines.append(line_text)

        text = clean_text("\n".join(lines))

        if not text:
            continue

        blocks.append({
            "rect": fitz.Rect(block["bbox"]),
            "text": text,
            "font_size": (
                max(font_sizes)
                if font_sizes else 10
            )
        })

    return blocks


def text_block_overlaps_visual(rect, regions):
    """
    표·그래프·사진 내부 텍스트를 완전히 차단.
    기존의 부분 겹침 방식보다 강하게 제외.
    """

    for region in regions:

        visual_rect = region["rect"]

        # 텍스트 블록의 절반 이상이 시각자료 안에 있으면 제외
        if overlap_ratio(rect, visual_rect) >= 0.45:
            return True

        # 텍스트 블록 중심이 시각자료 내부에 있는 경우
        center = fitz.Point(
            (rect.x0 + rect.x1) / 2,
            (rect.y0 + rect.y1) / 2
        )

        if visual_rect.contains(center):
            return True

    return False


def text_to_html(text, font_size):

    output = []

    for line in text.split("\n"):

        line = line.strip()

        if not line:
            continue

        if re.fullmatch(r"\d+", line):
            continue

        safe = escape_text(line)

        if len(line) < 65 and font_size >= 13:
            output.append(f"<h3>{safe}</h3>")
        else:
            output.append(f"<p>{safe}</p>")

    return "\n".join(output)


# ============================================================
# 11. EPUB CSS
# ============================================================

EPUB_CSS = """
body {
    font-family: "Noto Sans KR",
    "Apple SD Gothic Neo",
    "Malgun Gothic",
    sans-serif;
    line-height: 1.75;
    padding: 4%;
    color: #222;
    word-break: keep-all;
}

h2 {
    font-size: 1.35em;
    margin-top: 1.4em;
    margin-bottom: 1em;
    border-bottom: 1px solid #ccc;
    padding-bottom: 0.4em;
}

h3 {
    font-size: 1.12em;
    margin-top: 1.3em;
    margin-bottom: 0.6em;
}

p {
    font-size: 1em;
    line-height: 1.8;
    margin: 0.4em 0 0.9em;
    text-align: justify;
}

/* 실제 표: 모바일 가로 스크롤 */
.table-scroll {
    width: 100%;
    max-width: 100%;
    overflow-x: auto;
    overflow-y: hidden;
    margin: 1.2em 0;
    page-break-inside: avoid;
}

.report-table {
    border-collapse: collapse;
    width: max-content;
    min-width: 100%;
    font-size: 0.85em;
    line-height: 1.5;
    word-break: keep-all;
}

.report-table th,
.report-table td {
    border: 1px solid #c9cdd2;
    padding: 0.45em 0.55em;
    text-align: right;
    vertical-align: middle;
    white-space: nowrap;
}

.report-table th {
    background: #edf1f5;
    font-weight: bold;
    text-align: center;
}

.report-table td:first-child {
    text-align: left;
}

.report-table tr:nth-child(even) {
    background: #f8f9fa;
}

/* 고해상도 캡처 이미지 */
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

.page-number {
    color: #888;
    font-size: 0.8em;
    text-align: right;
    margin-bottom: 1em;
}
"""


# ============================================================
# 12. PDF → EPUB 변환
# ============================================================

def convert_pdf_to_epub(
    pdf_path,
    output_path,
    title,
    dpi=400,
    skip_financial=True
):

    doc = fitz.open(pdf_path)

    book = epub.EpubBook()

    book.set_identifier(str(uuid.uuid4()))
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
    table_count = 0
    image_table_count = 0
    skipped_pages = []
    warnings = []

    for page_index in range(len(doc)):

        page = doc[page_index]
        page_number = page_index + 1

        page_text = page.get_text("text")

        # 재무제표 생략
        if skip_financial and is_financial_statement(page_text):
            skipped_pages.append(page_number)
            continue

        elements = []

        # ----------------------------------------------------
        # A. 표 검출
        # ----------------------------------------------------

        table_regions = detect_tables(page)

        # ----------------------------------------------------
        # B. 이미지 검출
        # ----------------------------------------------------

        image_regions = detect_images(
            page,
            table_regions
        )

        # ----------------------------------------------------
        # C. 벡터 그래프 검출
        # ----------------------------------------------------

        graph_regions = detect_vector_graphs(
            page,
            table_regions,
            image_regions
        )

        # ----------------------------------------------------
        # D. 표·그래프·이미지 영역 구성
        # ----------------------------------------------------

        visual_regions = []

        for item in table_regions:
            visual_regions.append(item)

        for item in image_regions:
            visual_regions.append(item)

        for item in graph_regions:
            visual_regions.append(item)

        visual_regions = deduplicate_visuals(
            visual_regions
        )

        # ----------------------------------------------------
        # E. 표 처리
        # ----------------------------------------------------

        for region in table_regions:

            # 중복 제거 과정에서 제외된 표는 처리하지 않음
            if not any(
                region is x or (
                    region["rect"] == x["rect"]
                    and x["kind"] == "table"
                )
                for x in visual_regions
            ):
                continue

            rect = region["rect"]

            if region["mode"] == "table":

                table_count += 1

                table_html = table_to_html(
                    region["rows"],
                    table_count
                )

                elements.append({
                    "y": rect.y0,
                    "x": rect.x0,
                    "html": table_html
                })

            else:

                # 구조가 복잡하면 표 전체를 이미지로 캡처
                try:

                    image_bytes = capture_pdf_region(
                        page,
                        rect,
                        dpi
                    )

                    image_count += 1
                    image_table_count += 1

                    image_name = (
                        f"Images/table_{page_number}_"
                        f"{image_count}.png"
                    )

                    book.add_item(
                        epub.EpubItem(
                            uid=f"table_image_{image_count}",
                            file_name=image_name,
                            media_type="image/png",
                            content=image_bytes
                        )
                    )

                    relative_path = (
                        f"../Images/table_{page_number}_"
                        f"{image_count}.png"
                    )

                    elements.append({
                        "y": rect.y0,
                        "x": rect.x0,
                        "html": (
                            '<div class="visual">'
                            f'<img src="{relative_path}" '
                            'alt="원본 표"/>'
                            '</div>'
                        )
                    })

                except Exception as error:
                    warnings.append(
                        f"{page_number}페이지 표 캡처 실패: {error}"
                    )

        # ----------------------------------------------------
        # F. 사진 및 그래프 캡처
        # ----------------------------------------------------

        for region in visual_regions:

            if region["kind"] == "table":
                continue

            rect = region["rect"]

            try:

                image_bytes = capture_pdf_region(
                    page,
                    rect,
                    dpi
                )

                image_count += 1

                image_name = (
                    f"Images/visual_{page_number}_"
                    f"{image_count}.png"
                )

                book.add_item(
                    epub.EpubItem(
                        uid=f"visual_{image_count}",
                        file_name=image_name,
                        media_type="image/png",
                        content=image_bytes
                    )
                )

                relative_path = (
                    f"../Images/visual_{page_number}_"
                    f"{image_count}.png"
                )

                elements.append({
                    "y": rect.y0,
                    "x": rect.x0,
                    "html": (
                        '<div class="visual">'
                        f'<img src="{relative_path}" '
                        'alt="원본 그래프 또는 이미지"/>'
                        '</div>'
                    )
                })

            except Exception as error:
                warnings.append(
                    f"{page_number}페이지 이미지 캡처 실패: {error}"
                )

        # ----------------------------------------------------
        # G. 본문 추출
        # ----------------------------------------------------

        text_blocks = extract_blocks(page)

        for block in text_blocks:

            rect = block["rect"]

            # 표, 그래프, 사진 내부 텍스트 차단
            if text_block_overlaps_visual(
                rect,
                visual_regions
            ):
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
        # H. 원본 페이지 순서대로 배치
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

        for item in elements:
            page_content += item["html"]

        # ----------------------------------------------------
        # I. EPUB 챕터 생성
        # ----------------------------------------------------

        chapter = epub.EpubHtml(
            title=f"{page_number}페이지",
            file_name=f"Text/page_{page_number:04d}.xhtml",
            lang="ko"
        )

        chapter.content = f"""
        <?xml version="1.0" encoding="utf-8"?>
        <html xmlns="http://www.w3.org/1999/xhtml"
              lang="ko">
        <head>
            <meta charset="utf-8"/>
            <title>{escape_text(title)} - {page_number}페이지</title>
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
    # EPUB 목차
    # --------------------------------------------------------

    book.toc = tuple(chapters)

    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())

    book.spine = ["nav"] + chapters

    epub.write_epub(output_path, book)

    doc.close()

    return {
        "total_pages": len(chapters) + len(skipped_pages),
        "converted_pages": len(chapters),
        "actual_tables": table_count,
        "image_tables": image_table_count,
        "images": image_count,
        "skipped_pages": skipped_pages,
        "warnings": warnings
    }


# ============================================================
# 13. 사용자 화면
# ============================================================

uploaded_file = st.file_uploader(
    "증권사 리포트 PDF 업로드",
    type=["pdf"]
)

custom_title = st.text_input(
    "EPUB 제목",
    value="증권사 리포트"
)

dpi = st.select_slider(
    "표·그래프 캡처 해상도",
    options=[250, 300, 360, 400],
    value=400
)

skip_financial = st.checkbox(
    "재무제표 페이지 자동 생략",
    value=True
)

st.info(
    "권장 설정: 400 DPI. "
    "실제 EPUB 표는 텍스트로 재구성하며, "
    "복잡한 표는 원본 이미지로 보존합니다."
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
            ) as f:

                f.write(uploaded_file.getvalue())
                temp_pdf = f.name

            temp_epub = tempfile.mktemp(
                suffix=".epub"
            )

            with st.spinner(
                "표 구조 분석 및 고해상도 변환 중..."
            ):

                result = convert_pdf_to_epub(
                    temp_pdf,
                    temp_epub,
                    custom_title,
                    dpi=dpi,
                    skip_financial=skip_financial
                )

            st.success("EPUB 3 변환 완료")

            st.subheader("변환 결과")

            col1, col2 = st.columns(2)

            col1.metric(
                "변환 페이지",
                result["converted_pages"]
            )

            col2.metric(
                "실제 EPUB 표",
                result["actual_tables"]
            )

            col3, col4 = st.columns(2)

            col3.metric(
                "이미지 표",
                result["image_tables"]
            )

            col4.metric(
                "그래프·사진 등 이미지",
                result["images"]
            )

            if result["skipped_pages"]:
                st.warning(
                    "생략된 재무제표 페이지: "
                    + ", ".join(
                        map(str, result["skipped_pages"])
                    )
                )

            if result["warnings"]:

                with st.expander("변환 경고"):
                    for warning in result["warnings"]:
                        st.write(warning)

            with open(temp_epub, "rb") as f:

                st.download_button(
                    "📥 EPUB 다운로드",
                    data=f.read(),
                    file_name=f"{custom_title}.epub",
                    mime="application/epub+zip",
                    use_container_width=True
                )

        except Exception as error:

            st.error(
                f"변환 중 오류가 발생했습니다: {error}"
            )

        finally:

            for path in [temp_pdf, temp_epub]:

                if path and os.path.exists(path):
                    try:
                        os.remove(path)
                    except Exception:
                        pass
