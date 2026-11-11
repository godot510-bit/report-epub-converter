
import os
import re
import html
import uuid
import tempfile

import fitz
import streamlit as st
from ebooklib import epub


# ============================================================
# 1. 화면 설정
# ============================================================

st.set_page_config(
    page_title="증권사 리포트 PDF to EPUB 3",
    page_icon="📚",
    layout="centered"
)

st.title("📚 증권사 리포트 PDF → EPUB 3")
st.caption(
    "본문은 글자 크기 조절이 가능한 텍스트로, "
    "표는 실제 표 또는 고화질 이미지로, "
    "그래프와 사진은 원본 형태로 변환합니다."
)


# ============================================================
# 2. 재무제표 페이지 판별
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

    if any(
        word in compact
        for word in FINANCIAL_HEADINGS
    ):
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

    hits = sum(
        term in compact
        for term in account_terms
    )

    return hits >= 4


# ============================================================
# 3. 공통 영역 함수
# ============================================================

def rect_area(rect):

    return max(0, rect.width) * max(0, rect.height)


def overlap_ratio(a, b):

    total = rect_area(a)

    if total <= 0:
        return 0

    return rect_area(a & b) / total


def clean_text(text):

    text = str(text or "")
    text = text.replace("\x00", " ")
    text = re.sub(r"\s+", " ", text)

    return text.strip()


def safe_html(text):

    return html.escape(str(text or ""))


def expand_rect(rect, page_rect, margin=4):

    expanded = fitz.Rect(
        rect.x0 - margin,
        rect.y0 - margin,
        rect.x1 + margin,
        rect.y1 + margin
    )

    return expanded & page_rect


def point_in_any_region(point, regions):

    for region in regions:

        if region["rect"].contains(point):
            return True

    return False


def valid_visual_rect(rect, page_rect):

    if rect.width < 55 or rect.height < 25:
        return False

    ratio = (
        rect_area(rect) /
        max(1, rect_area(page_rect))
    )

    if ratio < 0.002:
        return False

    if rect.width > page_rect.width * 0.98:
        return False

    if rect.height > page_rect.height * 0.95:
        return False

    return True


# ============================================================
# 4. PDF 표 데이터 정리
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

        row += [None] * (
            max_cols - len(row)
        )

        cleaned_row = []

        for cell in row:

            value = str(cell or "")

            # 셀 안의 줄바꿈을 공백으로 정리
            value = re.sub(
                r"\s+",
                " ",
                value
            ).strip()

            cleaned_row.append(value)

        result.append(cleaned_row)

    # 완전히 빈 행 제거
    result = [
        row for row in result
        if any(cell for cell in row)
    ]

    return result


# ============================================================
# 5. 실제 EPUB 표 변환 가능성 평가
# ============================================================

def evaluate_table(rows):

    if len(rows) < 2:
        return False

    cols = max(
        len(row)
        for row in rows
    )

    if cols < 2:
        return False

    if cols > 25:
        return False

    if len(rows) > 150:
        return False

    total_cells = len(rows) * cols

    filled_cells = sum(
        1
        for row in rows
        for cell in row
        if cell.strip()
    )

    density = (
        filled_cells /
        max(1, total_cells)
    )

    if density < 0.25:
        return False

    numeric_cells = sum(
        1
        for row in rows
        for cell in row
        if re.search(r"\d", cell)
    )

    if numeric_cells < 2:
        return False

    # 행마다 열 수가 다른 경우 이미지 처리
    if len(set(len(row) for row in rows)) > 1:
        return False

    return True


# ============================================================
# 6. 실제 HTML 표 생성
# ============================================================

def table_to_html(rows, number):

    output = []

    output.append(
        '<div class="table-scroll">'
    )

    output.append(
        f'<table class="report-table" id="table-{number}">'
    )

    # 첫 번째 행을 헤더로 사용
    output.append("<thead><tr>")

    for cell in rows[0]:

        output.append(
            f"<th>{safe_html(cell)}</th>"
        )

    output.append("</tr></thead>")

    output.append("<tbody>")

    for row in rows[1:]:

        output.append("<tr>")

        for cell in row:

            output.append(
                f"<td>{safe_html(cell)}</td>"
            )

        output.append("</tr>")

    output.append("</tbody>")
    output.append("</table>")
    output.append("</div>")

    return "".join(output)


# ============================================================
# 7. 표 검출
# ============================================================

def detect_tables(page):

    results = []

    strategies = [
        "lines",
        "text"
    ]

    for strategy in strategies:

        try:

            finder = page.find_tables(
                strategy=strategy
            )

            for table in finder.tables:

                rect = fitz.Rect(table.bbox)

                if rect.width < 70:
                    continue

                if rect.height < 30:
                    continue

                # 중복 검출 방지
                duplicate = False

                for old in results:

                    if (
                        overlap_ratio(
                            rect,
                            old["rect"]
                        ) > 0.75
                        or
                        overlap_ratio(
                            old["rect"],
                            rect
                        ) > 0.75
                    ):

                        duplicate = True
                        break

                if duplicate:
                    continue

                try:

                    rows = normalize_table_data(
                        table.extract()
                    )

                except Exception:

                    rows = []

                # 외부 헤더 보완
                try:

                    header = table.header

                    if (
                        getattr(
                            header,
                            "external",
                            False
                        )
                        and getattr(
                            header,
                            "names",
                            None
                        )
                    ):

                        names = [
                            clean_text(x)
                            for x in header.names
                        ]

                        if names:

                            if not rows or rows[0] != names:
                                rows.insert(0, names)

                except Exception:
                    pass

                mode = (
                    "table"
                    if evaluate_table(rows)
                    else "image"
                )

                results.append({
                    "rect": rect,
                    "rows": rows,
                    "kind": "table",
                    "mode": mode
                })

        except Exception:
            continue

    return results


# ============================================================
# 8. 사진 및 이미지 검출
# ============================================================

def detect_images(page, table_regions):

    results = []

    try:

        image_infos = page.get_image_info(
            xrefs=True
        )

        for info in image_infos:

            bbox = info.get("bbox")

            if not bbox:
                continue

            rect = fitz.Rect(bbox)

            if not valid_visual_rect(
                rect,
                page.rect
            ):
                continue

            # 표 안에 들어 있는 작은 이미지 제외
            if any(
                overlap_ratio(
                    rect,
                    table["rect"]
                ) > 0.55
                for table in table_regions
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


# ============================================================
# 9. 벡터 그래프 검출
# ============================================================

def detect_vector_graphs(
    page,
    table_regions,
    image_regions
):

    results = []

    if not hasattr(
        page,
        "cluster_drawings"
    ):
        return results

    try:

        clusters = page.cluster_drawings(
            x_tolerance=8,
            y_tolerance=8
        )

        for cluster in clusters:

            rect = fitz.Rect(cluster)

            if not valid_visual_rect(
                rect,
                page.rect
            ):
                continue

            # 표 내부 그래프 조각 제외
            if any(
                overlap_ratio(
                    rect,
                    table["rect"]
                ) > 0.45
                for table in table_regions
            ):
                continue

            # 기존 이미지와 중복 제외
            if any(
                overlap_ratio(
                    rect,
                    image["rect"]
                ) > 0.75
                for image in image_regions
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
# 10. 그래프 영역 통합
# ============================================================

def merge_graph_regions(graph_regions):

    merged = []

    for item in sorted(
        graph_regions,
        key=lambda x: x["rect"].y0
    ):

        rect = fitz.Rect(
            item["rect"]
        )

        matched = False

        for old in merged:

            old_rect = old["rect"]

            expanded_old = fitz.Rect(
                old_rect.x0 - 55,
                old_rect.y0 - 55,
                old_rect.x1 + 55,
                old_rect.y1 + 55
            )

            expanded_new = fitz.Rect(
                rect.x0 - 55,
                rect.y0 - 55,
                rect.x1 + 55,
                rect.y1 + 55
            )

            if expanded_old.intersects(
                expanded_new
            ):

                old["rect"] |= rect

                matched = True
                break

        if not matched:

            merged.append({
                "rect": rect,
                "kind": "graph",
                "mode": "image"
            })

    return merged


# ============================================================
# 11. 그래프 축 숫자·날짜·범례 포함
# ============================================================

def attach_chart_labels(
    page,
    graph_regions,
    table_regions
):

    if not graph_regions:
        return graph_regions

    blocks = page.get_text(
        "dict",
        sort=True
    ).get("blocks", [])

    for graph in graph_regions:

        original = fitz.Rect(
            graph["rect"]
        )

        # 축과 범례가 놓일 수 있는 주변 범위
        search_rect = fitz.Rect(
            original.x0 - 65,
            original.y0 - 75,
            original.x1 + 65,
            original.y1 + 75
        ) & page.rect

        for block in blocks:

            if block.get("type") != 0:
                continue

            for line in block.get("lines", []):

                line_rect = fitz.Rect(
                    line["bbox"]
                )

                center = fitz.Point(
                    (line_rect.x0 + line_rect.x1) / 2,
                    (line_rect.y0 + line_rect.y1) / 2
                )

                if not search_rect.contains(center):
                    continue

                line_text = clean_text(
                    "".join(
                        span.get("text", "")
                        for span in line.get("spans", [])
                    )
                )

                if not line_text:
                    continue

                # 자료 출처나 주석은 그래프 축 라벨에서 제외
                if re.match(
                    r"^(자료|출처|주\)|주:|참고:)",
                    line_text
                ):
                    continue

                # 긴 본문 문장 제외
                if len(line_text) > 55:
                    continue

                # 표 영역에 속하는 글자는 제외
                if any(
                    table["rect"].intersects(
                        line_rect
                    )
                    for table in table_regions
                ):
                    continue

                # 축 라벨을 그래프 캡처 영역에 포함
                graph["rect"] |= line_rect

    # 최종 영역을 페이지 안으로 제한
    for graph in graph_regions:

        graph["rect"] = (
            graph["rect"] & page.rect
        )

    return graph_regions


# ============================================================
# 12. 시각자료 중복 제거
# ============================================================

def deduplicate_visuals(regions):

    selected = []

    regions = sorted(
        regions,
        key=lambda x: rect_area(
            x["rect"]
        ),
        reverse=True
    )

    for item in regions:

        duplicate = False

        for old in selected:

            r1 = overlap_ratio(
                item["rect"],
                old["rect"]
            )

            r2 = overlap_ratio(
                old["rect"],
                item["rect"]
            )

            if r1 > 0.85 or r2 > 0.85:

                # 표 영역을 다른 이미지보다 우선
                if item["kind"] == "table":

                    old["rect"] = item["rect"]
                    old["kind"] = "table"
                    old["mode"] = item["mode"]
                    old["rows"] = item.get(
                        "rows",
                        []
                    )

                duplicate = True
                break

        if not duplicate:
            selected.append(item)

    return selected


# ============================================================
# 13. 고해상도 원본 캡처
# ============================================================

def capture_pdf_region(
    page,
    rect,
    dpi
):

    rect = expand_rect(
        rect,
        page.rect,
        margin=4
    )

    pix = page.get_pixmap(
        matrix=fitz.Matrix(
            dpi / 72,
            dpi / 72
        ),
        clip=rect,
        colorspace=fitz.csRGB,
        alpha=False,
        annots=True
    )

    return pix.tobytes("png")


# ============================================================
# 14. 텍스트 줄 단위 추출
# ============================================================

def extract_text_lines(page):

    data = page.get_text(
        "dict",
        sort=True
    )

    results = []

    for block in data.get("blocks", []):

        if block.get("type") != 0:
            continue

        for line in block.get("lines", []):

            line_text = clean_text(
                "".join(
                    span.get("text", "")
                    for span in line.get("spans", [])
                )
            )

            if not line_text:
                continue

            sizes = [
                span.get("size", 0)
                for span in line.get("spans", [])
                if span.get("text", "").strip()
            ]

            font_size = (
                max(sizes)
                if sizes else 10
            )

            results.append({
                "rect": fitz.Rect(
                    line["bbox"]
                ),
                "text": line_text,
                "font_size": font_size
            })

    return results


# ============================================================
# 15. 그래프·표 안의 텍스트 중복 방지
# ============================================================

def line_inside_visual(line_rect, regions):

    center = fitz.Point(
        (line_rect.x0 + line_rect.x1) / 2,
        (line_rect.y0 + line_rect.y1) / 2
    )

    for region in regions:

        rect = region["rect"]

        if rect.contains(center):
            return True

        if overlap_ratio(
            line_rect,
            rect
        ) >= 0.45:
            return True

    return False


# ============================================================
# 16. 본문 HTML 생성
# ============================================================

def text_line_to_html(text, font_size):

    safe = safe_html(text)

    if (
        len(text) < 65
        and font_size >= 13
    ):

        return f"<h3>{safe}</h3>"

    return f"<p>{safe}</p>"


# ============================================================
# 17. EPUB CSS
# ============================================================

EPUB_CSS = """
body {
    font-family:
        "Noto Sans KR",
        "Apple SD Gothic Neo",
        "Malgun Gothic",
        sans-serif;

    line-height: 1.75;
    padding: 4%;
    color: #222;
    word-break: keep-all;
    writing-mode: horizontal-tb;
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
    margin-top: 1.2em;
    margin-bottom: 0.6em;
}

p {
    font-size: 1em;
    line-height: 1.8;
    margin: 0.4em 0 0.9em;
    text-align: justify;
}

/* 실제 표 영역 */

.table-scroll {
    display: block;
    width: 100%;
    max-width: 100%;
    overflow-x: auto;
    overflow-y: hidden;
    margin: 1.4em 0;
    page-break-inside: avoid;
}

.report-table {
    border-collapse: collapse;
    border: 1.5px solid #65717d;

    width: max-content;
    min-width: 680px;

    table-layout: auto;
    font-size: 0.82em;
    line-height: 1.55;

    writing-mode: horizontal-tb;
}

.report-table th,
.report-table td {
    border: 1px solid #89939e;

    padding: 0.55em 0.7em;

    vertical-align: middle;
    text-align: right;

    white-space: nowrap !important;
    word-break: keep-all !important;
    overflow-wrap: normal !important;

    writing-mode: horizontal-tb !important;
}

.report-table th {
    background: #e8edf2;
    color: #172331;

    font-weight: bold;
    text-align: center;

    border-bottom: 2px solid #65717d;
}

.report-table td:first-child {
    text-align: left;
    font-weight: 500;
}

.report-table tbody tr:nth-child(even) {
    background: #f2f5f8;
}

.report-table tbody tr:last-child td {
    border-bottom: 1.5px solid #65717d;
}

/* 그래프·사진·복잡한 표 이미지 */

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
# 18. EPUB 변환 본체
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

    book.set_identifier(
        str(uuid.uuid4())
    )

    book.set_title(title)
    book.set_language("ko")
    book.add_author(
        "Report EPUB Converter"
    )

    css_item = epub.EpubItem(
        uid="main_css",
        file_name="Styles/main.css",
        media_type="text/css",
        content=EPUB_CSS.encode("utf-8")
    )

    book.add_item(css_item)

    chapters = []

    actual_tables = 0
    image_tables = 0
    image_count = 0

    skipped_pages = []
    warnings = []

    total_pages = len(doc)

    for page_index in range(total_pages):

        page = doc[page_index]
        page_number = page_index + 1

        page_text = page.get_text("text")

        # ----------------------------------------------------
        # 재무제표 페이지 생략
        # ----------------------------------------------------

        if (
            skip_financial
            and is_financial_statement(page_text)
        ):

            skipped_pages.append(
                page_number
            )

            continue

        elements = []

        # ----------------------------------------------------
        # 표 검출
        # ----------------------------------------------------

        table_regions = detect_tables(page)

        # ----------------------------------------------------
        # 사진 및 이미지 검출
        # ----------------------------------------------------

        image_regions = detect_images(
            page,
            table_regions
        )

        # ----------------------------------------------------
        # 그래프 검출
        # ----------------------------------------------------

        graph_regions = detect_vector_graphs(
            page,
            table_regions,
            image_regions
        )

        graph_regions = merge_graph_regions(
            graph_regions
        )

        graph_regions = attach_chart_labels(
            page,
            graph_regions,
            table_regions
        )

        # ----------------------------------------------------
        # 시각자료 통합
        # ----------------------------------------------------

        visual_regions = []

        visual_regions.extend(
            table_regions
        )

        visual_regions.extend(
            image_regions
        )

        visual_regions.extend(
            graph_regions
        )

        visual_regions = deduplicate_visuals(
            visual_regions
        )

        # ----------------------------------------------------
        # 실제 EPUB 표 및 이미지 표 처리
        # ----------------------------------------------------

        for region in visual_regions:

            if region["kind"] != "table":
                continue

            rect = region["rect"]

            if region["mode"] == "table":

                actual_tables += 1

                table_html = table_to_html(
                    region["rows"],
                    actual_tables
                )

                elements.append({
                    "y": rect.y0,
                    "x": rect.x0,
                    "html": table_html
                })

            else:

                try:

                    image_bytes = capture_pdf_region(
                        page,
                        rect,
                        dpi
                    )

                    image_count += 1
                    image_tables += 1

                    image_name = (
                        f"Images/table_{page_number}_"
                        f"{image_count}.png"
                    )

                    book.add_item(
                        epub.EpubItem(
                            uid=f"table_{image_count}",
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
                        f"{page_number}페이지 표 오류: {error}"
                    )

        # ----------------------------------------------------
        # 그래프 및 사진 캡처
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
                        'alt="그래프 또는 원본 이미지"/>'
                        '</div>'
                    )
                })

            except Exception as error:

                warnings.append(
                    f"{page_number}페이지 이미지 오류: {error}"
                )

        # ----------------------------------------------------
        # 본문 텍스트 추출
        # ----------------------------------------------------

        text_lines = extract_text_lines(page)

        for line in text_lines:

            rect = line["rect"]

            # 그래프 축 숫자, 날짜, 표 데이터 중복 차단
            if line_inside_visual(
                rect,
                visual_regions
            ):
                continue

            line_html = text_line_to_html(
                line["text"],
                line["font_size"]
            )

            elements.append({
                "y": rect.y0,
                "x": rect.x0,
                "html": line_html
            })

        # ----------------------------------------------------
        # 원본 페이지의 위에서 아래 순서대로 배치
        # ----------------------------------------------------

        elements.sort(
            key=lambda item: (
                item["y"],
                item["x"]
            )
        )

        page_content = (
            '<div class="page-number">'
            f'원본 PDF {page_number}페이지'
            '</div>'
        )

        for item in elements:

            page_content += item["html"]

        # ----------------------------------------------------
        # EPUB 챕터 생성
        # ----------------------------------------------------

        chapter = epub.EpubHtml(
            title=f"{page_number}페이지",
            file_name=(
                f"Text/page_{page_number:04d}.xhtml"
            ),
            lang="ko"
        )

        chapter.content = f"""
        <?xml version="1.0" encoding="utf-8"?>
        <html xmlns="http://www.w3.org/1999/xhtml"
              lang="ko">
        <head>
            <meta charset="utf-8"/>
            <title>
                {safe_html(title)} - {page_number}페이지
            </title>

            <link
                rel="stylesheet"
                type="text/css"
                href="../Styles/main.css"/>
        </head>

        <body>
            <h2>{page_number}페이지</h2>
            {page_content}
        </body>
        </html>
        """

        book.add_item(chapter)

        chapters.append(chapter)

    # --------------------------------------------------------
    # 목차 및 EPUB 구조
    # --------------------------------------------------------

    book.toc = tuple(chapters)

    book.add_item(
        epub.EpubNcx()
    )

    book.add_item(
        epub.EpubNav()
    )

    book.spine = ["nav"] + chapters

    # --------------------------------------------------------
    # EPUB 파일 저장
    # --------------------------------------------------------

    epub.write_epub(
        output_path,
        book
    )

    doc.close()

    return {
        "total_pages": total_pages,
        "converted_pages": len(chapters),
        "actual_tables": actual_tables,
        "image_tables": image_tables,
        "images": image_count,
        "skipped_pages": skipped_pages,
        "warnings": warnings
    }


# ============================================================
# 19. 사용자 화면
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
    "그래프·표 이미지 해상도",
    options=[
        250,
        300,
        360,
        400
    ],
    value=400
)

skip_financial = st.checkbox(
    "재무제표 페이지 자동 생략",
    value=True
)

st.info(
    "권장 설정은 400 DPI입니다. "
    "실제 표로 재구성할 수 있는 표는 HTML 표로 만들고, "
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

                f.write(
                    uploaded_file.getvalue()
                )

                temp_pdf = f.name

            temp_epub = tempfile.mktemp(
                suffix=".epub"
            )

            with st.spinner(
                "PDF 분석 및 EPUB 변환 중입니다..."
            ):

                result = convert_pdf_to_epub(
                    temp_pdf,
                    temp_epub,
                    custom_title,
                    dpi=dpi,
                    skip_financial=skip_financial
                )

            st.success(
                "EPUB 3 변환이 완료되었습니다."
            )

            st.subheader(
                "변환 결과"
            )

            col1, col2 = st.columns(2)

            col1.metric(
                "전체 페이지",
                result["total_pages"]
            )

            col2.metric(
                "변환 페이지",
                result["converted_pages"]
            )

            col3, col4 = st.columns(2)

            col3.metric(
                "실제 EPUB 표",
                result["actual_tables"]
            )

            col4.metric(
                "이미지 표",
                result["image_tables"]
            )

            col5, col6 = st.columns(2)

            col5.metric(
                "그래프·사진 이미지",
                result["images"]
            )

            col6.metric(
                "생략 페이지",
                len(result["skipped_pages"])
            )

            if result["skipped_pages"]:

                st.warning(
                    "재무제표로 판별하여 생략한 페이지: "
                    + ", ".join(
                        map(
                            str,
                            result["skipped_pages"]
                        )
                    )
                )

            if result["warnings"]:

                with st.expander(
                    "변환 과정에서 발생한 경고"
                ):

                    for warning in result["warnings"]:
                        st.write(warning)

            with open(
                temp_epub,
                "rb"
            ) as f:

                epub_data = f.read()

            st.download_button(
                label="📥 EPUB 다운로드",
                data=epub_data,
                file_name=f"{custom_title}.epub",
                mime="application/epub+zip",
                use_container_width=True
            )

        except Exception as error:

            st.error(
                f"변환 중 오류가 발생했습니다: {error}"
            )

        finally:

            for path in [
                temp_pdf,
                temp_epub
            ]:

                if (
                    path
                    and os.path.exists(path)
                ):

                    try:
                        os.remove(path)
                    except Exception:
                        pass
