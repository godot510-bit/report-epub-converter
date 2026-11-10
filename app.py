import streamlit as st
import os
import tempfile
import re
import fitz  # PyMuPDF
from ebooklib import epub

st.set_page_config(
    page_title="증권사 리포트 PDF to EPUB 3 변환기", 
    page_icon="📚", 
    layout="centered"
)

def is_financial_statement(page_text):
    """재무제표 페이지 여부 판단 (요청사항: 재무제표 생략)"""
    fin_keywords = ["재무제표", "포괄손익계산서", "재무상태표", "현금흐름표", "K-IFRS 연결", "매출액", "영업이익", "당기순이익"]
    match_count = sum(1 for kw in fin_keywords if kw in page_text)
    return match_count >= 3

def convert_pdf_to_epub_v3(pdf_path, output_epub_path, title="증권사 리포트"):
    book = epub.EpubBook()
    book.set_title(title)
    book.set_language('ko')
    book.add_metadata('DC', 'creator', 'Report EPUB Converter')

    # 모바일 리더기 최적화 CSS
    epub_css = '''
    @namespace epub "http://www.idpf.org/2007/ops";
    body {
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
        line-height: 1.6;
        padding: 4%;
        color: #2c3e50;
        background-color: #ffffff;
    }
    h2 {
        color: #1a2a3a;
        margin-top: 1em;
        margin-bottom: 0.5em;
        font-size: 1.1em;
        border-bottom: 2px solid #eaeaea;
        padding-bottom: 0.3em;
    }
    h3 {
        color: #2c3e50;
        margin-top: 1em;
        font-size: 1.05em;
        font-weight: bold;
    }
    p {
        margin-bottom: 0.8em;
        text-align: justify;
        word-break: keep-all;
    }
    .img-container {
        text-align: center;
        margin: 1.2em 0;
    }
    img {
        max-width: 100%;
        height: auto;
        display: block;
        margin: 0 auto;
        border: 1px solid #e1e4e8;
        border-radius: 6px;
        box-shadow: 0 2px 5px rgba(0,0,0,0.08);
    }
    .skip-notice {
        color: #888888;
        font-style: italic;
        font-size: 0.9em;
        margin-top: 2em;
    }
    '''
    css_item = epub.EpubItem(
        uid="style_main", 
        file_name="Styles/style.css", 
        media_type="text/css", 
        content=epub_css
    )
    book.add_item(css_item)

    spine_items = ['nav']
    toc_list = []
    
    doc = fitz.open(pdf_path)
    img_counter = 0

    for page_num in range(len(doc)):
        page = doc[page_num]
        display_page_num = page_num + 1
        page_text = page.get_text("text")
        
        # 1. 재무제표 페이지 생략 처리
        if is_financial_statement(page_text):
            page_content = f"<h2>Page {display_page_num}</h2>"
            page_content += f'<p class="skip-notice">[안내] 본 페이지의 재무제표 영역은 생략되었습니다.</p>'
        else:
            page_content = f"<h2>Page {display_page_num}</h2>"
            
            # 2. 페이지 내 표(Table) 영역 위치 파악 (표 안의 텍스트가 일반 본문으로 새어나가지 않도록 방지)
            table_rects = []
            try:
                tables = page.find_tables()
                if tables:
                    for t in tables:
                        table_rects.append(t.rect)
            except Exception:
                pass

            # 3. 표 영역에 포함되지 않은 순수 본문 텍스트만 추출하여 리플로우형으로 구성
            blocks = page.get_text("blocks")
            for b in blocks:
                if b[6] == 0:  # 텍스트 블록
                    bx0, by0, bx1, by1, btext = b[0], b[1], b[2], b[3], b[4]
                    
                    # 표 영역 내부에 있는 텍스트는 제외
                    is_in_table = False
                    for trect in table_rects:
                        if not (bx1 < trect.x0 or bx0 > trect.x1 or by1 < trect.y0 or by0 > trect.y1):
                            is_in_table = True
                            break
                    if is_in_table:
                        continue
                    
                    for line in btext.split('\n'):
                        cleaned = line.strip()
                        if cleaned and len(cleaned) > 1 and not re.match(r'^\s*\d+\s*$', cleaned):
                            if len(cleaned) < 40 and any(k in cleaned for k in ["요약", "전망", "분석", "투자", "목표", "Key", "Charts"]):
                                page_content += f"<h3>{cleaned}</h3>"
                            else:
                                page_content += f"<p>{cleaned}</p>"

            # 4. 감지된 표 영역들을 고화질(200 DPI) 이미지로 깔끔하게 캡처하여 삽입
            try:
                tables = page.find_tables()
                if tables:
                    for t in tables:
                        pix = page.get_pixmap(clip=t.rect, dpi=200)
                        img_counter += 1
                        img_name = f"Images/table_{img_counter}.png"
                        book.add_item(epub.EpubItem(
                            uid=f"table_{img_counter}",
                            file_name=img_name,
                            media_type="image/png",
                            content=pix.tobytes("png")
                        ))
                        page_content += f'<div class="img-container"><img src="{img_name}" alt="Table Image" /></div>'
            except Exception:
                pass

            # 5. 페이지 내 독립된 이미지/그래프 객체 추출 및 삽입
            image_list = page.get_images(full=True)
            if image_list:
                for img_info in image_list:
                    try:
                        xref = img_info[0]
                        base_image = doc.extract_image(xref)
                        image_bytes = base_image["image"]
                        image_ext = base_image["ext"]

                        # 너무 작은 아이콘/로고는 제외
                        if base_image["width"] < 60 or base_image["height"] < 60:
                            continue

                        img_counter += 1
                        img_name = f"Images/img_{img_counter}.{image_ext}"
                        book.add_item(epub.EpubItem(
                            uid=f"img_{img_counter}",
                            file_name=img_name,
                            media_type=f"image/{image_ext}",
                            content=image_bytes
                        ))
                        page_content += f'<div class="img-container"><img src="{img_name}" alt="Chart/Image" /></div>'
                    except Exception:
                        continue

        # EPUB 챕터 생성
        chap = epub.EpubHtml(
            title=f"Page {display_page_num}", 
            file_name=f"Text/page_{display_page_num}.xhtml", 
            lang='ko'
        )
        chap.content = f'''<?xml version="1.0" encoding="UTF-8"?>
        <html xmlns="http://www.w3.org/1999/xhtml">
        <head>
            <link rel="stylesheet" href="../Styles/style.css" type="text/css"/>
            <title>Page {display_page_num}</title>
        </head>
        <body>{page_content}</body>
        </html>'''
        
        book.add_item(chap)
        spine_items.append(chap)
        toc_list.append(chap)

    book.toc = tuple(toc_list)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    
    spine_items.append('nav')
    book.spine = spine_items
    
    epub.write_epub(output_epub_path, book)

# --- Streamlit UI ---
st.title("📊 증권사 리포트 PDF to EPUB 3 변환기")
st.markdown("텍스트는 리플로우, 표와 그래프는 원본 고화질 이미지 캡처, 재무제표는 자동 생략됩니다.")

uploaded_file = st.file_uploader("변환할 증권사 리포트 PDF 파일을 업로드하세요", type=["pdf"])
custom_title = st.text_input("생성될 EPUB 파일 제목", value="증권사 리포트 분석")

if uploaded_file is not None and st.button("🚀 EPUB 3 변환 시작", type="primary"):
    with st.spinner("PDF 분석 중 (텍스트 리플로우 추출 및 표/그래프 영역 고화질 이미지 캡처)..."):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_pdf:
            tmp_pdf.write(uploaded_file.getvalue())
            tmp_pdf_path = tmp_pdf.name
            
        output_epub_path = tmp_pdf_path.replace(".pdf", ".epub")
        
        try:
            convert_pdf_to_epub_v3(tmp_pdf_path, output_epub_path, title=custom_title)
            st.success("✨ 맞춤형 EPUB 변환 완료!")
            
            with open(output_epub_path, "rb") as f:
                st.download_button(
                    label="📥 변환된 EPUB 파일 다운로드", 
                    data=f.read(), 
                    file_name=f"{custom_title}.epub", 
                    mime="application/epub+zip"
                )
        except Exception as e:
            st.error(f"변환 중 오류가 발생했습니다: {e}")
        finally:
            if os.path.exists(tmp_pdf_path): 
                os.remove(tmp_pdf_path)
