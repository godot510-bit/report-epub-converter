
import os
import re
import html
import uuid
import tempfile

import fitz
import streamlit as st
from ebooklib import epub


st.set_page_config(
    page_title="증권사 리포트 PDF to EPUB 3",
    page_icon="📚",
    layout="centered"
)

st.title("📚 증권사 리포트 PDF → EPUB 3")
st.caption("본문 리플로우 · 실제 표 재구성 · 고해상도 그래프 및 이미지")


# ============================================================
# 1. 재무제표 페이지 판별
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

    if any(x in compact for x in FINANCIAL_HEADINGS):
        return True

    terms = [
        "유동자산",
        "비유동자산",
        "자산총계",
        "부채총계",
        "자본총계",
        "영업활동현금흐름",
        "투자활동현금흐름",
    ]

    return sum(x in compact for x in terms) >= 4


# ============================================================
# 2. 공통 함수
# ============================================================

def rect_area(rect):
    return max(0, rect.width) * max(0, rect.height)


def overlap_ratio(a, b):
    total = rect_area(a)
    if total <= 0:
        return 0
    return rect_area(a & b) / total


def clean_text(text):
    text = str(text or "").replace("\x00", " ")
    return re.sub(r"\s+", " ", text).strip()


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


def valid_visual_rect(rect, page_rect):
    if rect.width < 55 or rect.height < 25:
        return False

    ratio = rect_area(rect) / max(1, rect_area(page_rect))

    return (
        ratio >= 0.002
        and rect.width <= page_rect.width * 0.98
        and rect.height <= page_rect.height * 0.95
    )


# ============================================================
# 3. 표 데이터 정리
# ============================================================

def normalize_table_data(rows):
    if not rows:
        return []

    max_cols = max(
        (len(row) for row in rows if row),
        default=0
    )

    if max_cols == 0:
        return []

    result = []

    for row in rows:
        row = list(row or [])
        row += [None] * (max_cols - len(row))

        result.append([
            clean_text(cell)
            for cell in row
        ])

    return [
        row for row in result
        if any(row)
    ]


def evaluate_table(rows):
    if len(rows) < 2:
        return False

    cols = max(len(row) for row in rows)

    if cols < 2 or cols > 25 or len(rows) > 150:
        return False

    total = len(rows) * cols

    filled = sum(
        1 for row in rows
        for cell in row
        if cell.strip()
    )

    if filled / max(1, total) < 0.25:
        return False

    numeric = sum(
        1 for row in rows
        for cell in row
        if re.search(r"\d", cell)
    )

    if numeric < 2:
        return False

    if len(set(len(row) for row in rows)) > 1:
        return False

    return True


# ============================================================
# 4. 실제 표 HTML 생성
#    ★ 셀마다 실선 테두리를 직접 지정
# ============================================================

def table_to_html(rows, number):

    table_style = (
        "border-collapse:collapse;"
        "border-spacing:0;"
        "border:1.5px solid #333333;"
        "width:max-content;"
        "min-width:680px;"
        "font-size:0.82em;"
        "line-height:1.55;"
        "writing-mode:horizontal-tb;"
    )

    header_style = (
        "border:1px solid #333333;"
        "border-bottom:2px solid #333333;"
        "padding:7px 9px;"
        "background-color:#e8edf2;"
        "color:#172331;"
        "font-weight:bold;"
        "text-align:center;"
        "vertical-align:middle;"
        "white-space:nowrap;"
        "word-break:keep-all;"
        "writing-mode:horizontal-tb;"
    )

    cell_style = (
        "border:1px solid #555555;"
        "padding:7px 9px;"
        "text-align:right;"
        "vertical-align:middle;"
        "white-space:nowrap;"
        "word-break:keep-all;"
        "overflow-wrap:normal;"
        "writing-mode:horizontal-tb;"
    )

    output = []

    output.append(
        '<div class="table-scroll">'
    )

    # border 속성도 함께 지정해 EPUB 리더 호환성 강화
    output.append(
        f'<table id="table-{number}" '
        f'class="report-table" '
        f'border="1" cellspacing="0" cellpadding="0" '
        f'style="{table_style}">'
    )

    # 헤더
    output.append("<thead><tr>")

    for cell in rows[0]:
        output.append(
            f'<th border="1" style="{header_style}">'
            f'{safe_html(cell)}'
            '</th>'
        )

    output.append("</tr></thead>")
    output.append("<tbody>")

    # 본문 셀
    for row_index, row in enumerate(rows[1:]):

        output.append("<tr>")

        for col_index, cell in enumerate(row):

            style = cell_style

            # 첫 번째 열은 왼쪽 정렬
            if col_index == 0:
                style += "text-align:left;font-weight:500;"

            # 행마다 은은한 배경색 적용
            if row_index % 2 == 1:
                style += "background-color:#f2f5f8;"

            output.append(
                f'<td border="1" style="{style}">'
                f'{safe_html(cell)}'
                '</td>'
            )

        output.append("</tr>")

    output.append("</tbody>")
    output.append("</table>")
    output.append("</div>")

    return "".join(output)


# ============================================================
# 5. 표 검출
# ============================================================

def detect_tables(page):
    results = []

    for strategy in ["lines", "text"]:

        try:
            finder = page.find_tables(strategy=strategy)

            for table in finder.tables:

                rect = fitz.Rect(table.bbox)

                if rect.width < 70 or rect.height < 30:
                    continue

                if any(
                    overlap_ratio(rect, old["rect"]) > 0.75
                    or overlap_ratio(old["rect"], rect) > 0.75
                    for old in results
                ):
                    continue

                try:
                    rows = normalize_table_data(
                        table.extract()
                    )
                except Exception:
                    rows = []

                try:
                    header = table.header

                    if (
                        getattr(header, "external", False)
                        and getattr(header, "names", None)
                    ):
                        names = [
                            clean_text(x)
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
                    "kind": "table",
                    "mode": (
                        "table"
                        if evaluate_table(rows)
                        else "image"
                    )
                })

        except Exception:
            continue

    return results


# ============================================================
# 6. 사진·이미지 검출
# ============================================================

def detect_images(page, table_regions):
    results = []

    try:
        for info in page.get_image_info(xrefs=True):

            bbox = info.get("bbox")

            if not bbox:
                continue

            rect = fitz.Rect(bbox)

            if not valid_visual_rect(rect, page.rect):
                continue

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


# ============================================================
# 7. 벡터 그래프 검출
# ============================================================

def detect_vector_graphs(page, table_regions, image_regions):
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

            if not valid_visual_rect(rect, page.rect):
                continue

            if any(
                overlap_ratio(rect, t["rect"]) > 0.45
                for t in table_regions
            ):
                continue

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
# 8. 그래프 영역 합치기
# ============================================================

def merge_graph_regions(regions):
    merged = []

    for item in sorted(
        regions,
        key=lambda x: x["rect"].y0
    ):

        rect = fitz.Rect(item["rect"])
        matched = False

        for old in merged:

            old_rect = old["rect"]

            expanded = fitz.Rect(
                old_rect.x0 - 55,
                old_rect.y0 - 55,
                old_rect.x1 + 55,
                old_rect.y1 + 55
            )

            if expanded.intersects(rect):

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
# 9. 그래프 축 라벨 포함
# ============================================================

def attach_chart_labels(page, graph_regions, table_regions):
    blocks = page.get_text(
        "dict",
        sort=True
    ).get("blocks", [])

    for graph in graph_regions:

        original = graph["rect"]

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

                line_rect = fitz.Rect(line["bbox"])

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

                if not line_text or len(line_text) > 55:
                    continue

                if re.match(
                    r"^(자료|출처|주\)|주:|참고:)",
                    line_text
                ):
                    continue

                if any(
                    table["rect"].intersects(line_rect)
                    for table in table_regions
                ):
                    continue

                graph["rect"] |= line_rect

    for graph in graph_regions:
        graph["rect"] &= page.rect

    return graph_regions


# ============================================================
# 10. 시각자료 중복 제거
# ============================================================

def deduplicate_visuals(regions):
    selected = []

    regions = sorted(
        regions,
        key=lambda x: rect_area(x["rect"]),
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

                if item["kind"] == "table":
                    old.update(item)

                duplicate = True
                break

        if not duplicate:
            selected.append(item)

    return selected


# ============================================================
# 11. PDF 영역 캡처
# ============================================================

def capture_pdf_region(page, rect, dpi):
    rect = expand_rect(
        rect,
        page.rect,
        margin=4
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
# 12. 본문 텍스트 추출
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

            text = clean_text(
                "".join(
                    span.get("text", "")
                    for span in line.get("spans", [])
                )
            )

            if not text:
                continue

            sizes = [
                span.get("size", 0)
                for span in line.get("spans", [])
                if span.get("text", "").strip()
            ]

            results.append({
                "rect": fitz.Rect(line["bbox"]),
                "text": text,
                "font_size": max(sizes) if sizes else 10
            })

    return results


def line_inside_visual(line_rect, regions):
    center = fitz.Point(
        (line_rect.x0 + line_rect.x1) / 2,
        (line_rect.y0 + line_rect.y1) / 2
    )

    for region in regions:

        rect = region["rect"]

        if rect.contains(center):
            return True

        if overlap_ratio(line_rect, rect) >= 0.45:
            return True

    return False


def text_line_to_html(text, font_size):
    safe = safe_html(text)

    if len(text) < 65 and font_size >= 13:
        return f"<h3>{safe}</h3>"

    return f"<p>{safe}</p>"


# ============================================================
# 13. EPUB CSS
# ============================================================

EPUB_CSS = """
body {
    font-family: "Noto Sans KR",
    "Apple SD Gothic Neo",
    "Malgun Gothic", sans-serif;
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

/* 표 전체 */
.table-scroll {
    display: block;
    width: 100%;
    max-width: 100%;
    overflow-x: auto;
    overflow-y: hidden;
    margin: 1.4em 0;
    page-break-inside: avoid;
}

/* 표 바깥 테두리 */
.report-table {
    border-collapse: collapse !important;
    border-spacing: 0 !important;
    border: 1.5px solid #333333 !important;
    width: max-content;
    min-width: 680px;
    table-layout: auto;
    font-size: 0.82em;
    line-height: 1.55;
}

/* 가로·세로 셀 실선 */
.report-table th,
.report-table td {
    border: 1px solid #555555 !important;
    padding: 7px 9px;
    vertical-align: middle;
    white-space: nowrap !important;
    word-break: keep-all !important;
    overflow-wrap: normal !important;
    writing-mode: horizontal-tb !important;
}

/* 헤더 */
.report-table th {
    background-color: #e8edf2 !important;
    color: #172331;
    font-weight: bold;
    text-align: center;
    border-bottom: 2px solid #333333 !important;
}

/* 첫 번째 열 */
.report-table td:first-child {
    text-align: left;
    font-weight: 500;
}

/* 행 구분 배경 */
.report-table tbody tr:nth-child(even) {
    background-color: #f2f5f8;
}

/* 그래프·사진 */
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
# 14. PDF → EPUB 변환
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
    actual_tables = 0
    image_tables = 0
    image_count = 0

    skipped_pages = []
    warnings = []

    for page_index in range(len(doc)):

        page = doc[page_index]
        page_number = page_index + 1

        page_text = page.get_text("text")

        if (
            skip_financial
            and is_financial_statement(page_text)
        ):
            skipped_pages.append(page_number)
            continue

        elements = []

        # 표
        table_regions = detect_tables(page)

        # 이미지
        image_regions = detect_images(
            page,
            table_regions
        )

        # 그래프
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

        # 모든 시각자료 통합
        visual_regions = (
            table_regions
            + image_regions
            + graph_regions
        )

        visual_regions = deduplicate_visuals(
            visual_regions
        )

        # ----------------------------------------------------
        # 표 처리
        # ----------------------------------------------------

        for region in visual_regions:

            if region["kind"] != "table":
                continue

            rect = region["rect"]

            if region["mode"] == "table":

                actual_tables += 1

                elements.append({
                    "y": rect.y0,
                    "x": rect.x0,
                    "html": table_to_html(
                        region["rows"],
                        actual_tables
                    )
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
        # 그래프 및 사진 처리
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
        # 본문 추출
        # ----------------------------------------------------

        for line in extract_text_lines(page):

            if line_inside_visual(
                line["rect"],
                visual_regions
            ):
                continue

            elements.append({
                "y": line["rect"].y0,
                "x": line["rect"].x0,
                "html": text_line_to_html(
                    line["text"],
                    line["font_size"]
                )
            })

        # ----------------------------------------------------
        # 페이지 순서 정렬
        # ----------------------------------------------------

        elements.sort(
            key=lambda x: (x["y"], x["x"])
        )

        page_content = (
            '<div class="page-number">'
            f'원본 PDF {page_number}페이지'
            '</div>'
        )

        for item in elements:
            page_content += item["html"]

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
              lang="ko">
        <head>
            <meta charset="utf-8"/>
            <title>{safe_html(title)} - {page_number}페이지</title>
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

        book.add_item(chapter)
        chapters.append(chapter)

    # 목차
    book.toc = tuple(chapters)

    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())

    book.spine = ["nav"] + chapters

    epub.write_epub(
        output_path,
        book
    )

    doc.close()

    return {
        "total_pages": len(doc) if not doc.is_closed else (
            len(chapters) + len(skipped_pages)
        ),
        "converted_pages": len(chapters),
        "actual_tables": actual_tables,
        "image_tables": image_tables,
        "images": image_count,
        "skipped_pages": skipped_pages,
        "warnings": warnings
    }


# ============================================================
# 15. 사용자 화면
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
    options=[250, 300, 360, 400],
    value=400
)

skip_financial = st.checkbox(
    "재무제표 페이지 자동 생략",
    value=True
)

st.info(
    "권장 설정: 400 DPI. "
    "표를 재구성할 수 있으면 실제 HTML 표로 변환하고, "
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
                "PDF 분석 및 EPUB 변환 중입니다..."
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
                "그래프·사진 이미지",
                result["images"]
            )

            if result["skipped_pages"]:

                st.warning(
                    "재무제표 생략 페이지: "
                    + ", ".join(
                        map(
                            str,
                            result["skipped_pages"]
                        )
                    )
                )

            if result["warnings"]:

                with st.expander("변환 경고"):

                    for warning in result["warnings"]:
                        st.write(warning)

            with open(temp_epub, "rb") as f:
                epub_data = f.read()

            st.download_button(
                "📥 EPUB 다운로드",
                data=epub_data,
                file_name=f"{custom_title}.epub",
                mime="application/epub+zip",
                use_container_width=True
            )

        except Exception as error:

            st.error(
                f"변환 오류: {error}"
            )

        finally:

            for path in [
                temp_pdf,
                temp_epub
            ]:

                if path and os.path.exists(path):

                    try:
                        os.remove(path)
                    except Exception:
                        pass
