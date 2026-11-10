import streamlit as st
import os
import tempfile
import fitz  # PyMuPDF
from ebooklib import epub

st.set_page_config(
    page_title="증권사 리포트 PDF to EPUB 3 변환기", 
    page_icon="📚", 
    layout="centered"
)

def is_financial_statement(page_text):
    """재무제표 페이지 여부 판단 (재무제표 자동 생략)"""
    fin_keywords = ["재무제표", "포괄손익계산서", "재무상태표", "현금흐름표", "K-IFRS 연결", "매출액", "영업이익", "당기순이익"]
    match_count = sum(1 for kw in fin_keywords if kw in page_text)
    return match_count >= 3

def convert_pdf_to_epub_v3(pdf_path, output_epub_path, title="증권사 리포트"):
    book = epub.EpubBook()
    book.set_title(title)
    book.set_language('ko')
    book.add_metadata('DC', 'creator', 'Report EPUB Converter')

    # 이미지 중심 레이아웃 CSS 스타일
    epub_css = '''
    @namespace epub "http://www.idpf.org/2007/ops";
    body {
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
        background-color: #ffffff;
        margin: 0;
        padding: 0;
        text-align: center;
    }
    .page-container {
        width: 100%;
        margin: 0 auto;
        page-break-after: always;
    }
    img {
        max-width: 100%;
        height: auto;
        display: block;
        margin: 0 auto;
    }
    .skip-notice {
        padding: 40px;
        color: #666666;
        font-size: 1.1em;
        font-style: italic;
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
        
        # 재무제표 페이지는 생략 안내 문구 삽입
        if is_financial_statement(page_text):
            page_content = f'<div class="skip-notice">[안내] 본 페이지의 재무제표 영역은 생략되었습니다. (Page {display_page_num})</div>'
        else:
            # 원본 레이아웃이 절대로 깨지지 않도록 페이지 전체를 고화질(200 DPI) 이미지로 스냅샷 캡처
            pix = page.get_pixmap(dpi=200)
            img_counter += 1
            img_name = f"Images/page_{img_counter}.png"
            
            book.add_item(epub.EpubItem(
                uid=f"img_page_{img_counter}",
                file_name=img_name,
                media_type="image/png",
                content=pix.tobytes("png")
            ))
            page_content = f'<div class="page-container"><img src="{img_name}" alt="Page {display_page_num}" /></div>'

        # EPUB 챕터 구성
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
st.markdown("모든 표, 그래프, 본문 레이아웃을 원본 고화질 이미지 스냅샷으로 완벽하게 보존합니다.")

uploaded_file = st.file_uploader("변환할 증권사 리포트 PDF 파일을 업로드하세요", type=["pdf"])
custom_title = st.text_input("생성될 EPUB 파일 제목", value="증권사 리포트 분석")

if uploaded_file is not None and st.button("🚀 EPUB 3 변환 시작", type="primary"):
    with st.spinner("PDF 원본 레이아웃 고화질 이미지 캡처 및 EPUB 패키징 중..."):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_pdf:
            tmp_pdf.write(uploaded_file.getvalue())
            tmp_pdf_path = tmp_pdf.name
            
        output_epub_path = tmp_pdf_path.replace(".pdf", ".epub")
        
        try:
            convert_pdf_to_epub_v3(tmp_pdf_path, output_epub_path, title=custom_title)
            st.success("✨ 원본 완벽 보존 EPUB 변환 완료!")
            
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
