import streamlit as st
import os
import tempfile
import re
import fitz  # PyMuPDF
import pdfplumber
from ebooklib import epub

st.set_page_config(
    page_title="증권사 리포트 PDF to EPUB 3 변환기", 
    page_icon="📚", 
    layout="centered"
)

def convert_pdf_to_epub_v3(pdf_path, output_epub_path, title="증권사 리포트"):
    book = epub.EpubBook()
    book.set_title(title)
    book.set_language('ko')
    book.add_metadata('DC', 'creator', 'Report EPUB Converter')

    # 모바일 리더기(Thorium, Moon+ Reader 등) 최적화 CSS 스타일
    epub_css = '''
    @namespace epub "http://www.idpf.org/2007/ops";
    body {
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
        line-height: 1.6;
        padding: 5%;
        color: #2c3e50;
        background-color: #ffffff;
    }
    h1, h2, h3 {
        color: #1a2a3a;
        margin-top: 1.5em;
        font-weight: bold;
    }
    p {
        margin-bottom: 1em;
        text-align: justify;
    }
    img {
        max-width: 100%;
        height: auto;
        display: block;
        margin: 1.5em auto;
        border-radius: 4px;
    }
    table {
        width: 100%;
        border-collapse: collapse;
        margin: 1.5em 0;
        font-size: 0.9em;
    }
    th, td {
        border: 1px solid #d0d5dd;
        padding: 8px 10px;
        text-align: left;
    }
    th {
        background-color: #f8f9fa;
        font-weight: bold;
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
    
    doc_fitz = fitz.open(pdf_path)
    
    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages, start=1):
            page_content = f"<h2>Page {page_num}</h2>"
            
            # 1. 본문 텍스트 추출 및 정제 (단락별 태그 처리)
            text = page.extract_text()
            if text:
                for p in text.split('\n'):
                    cleaned_p = p.strip()
                    # 단순 페이지 번호 등 불필요한 단락 필터링
                    if cleaned_p and not re.match(r'^\s*\d+\s*$', cleaned_p):
                        # 제목성 텍스트 감지 시 소제목 태그 적용
                        if len(cleaned_p) < 30 and (p.isupper() or "요약" in cleanedp or "전망" in cleanedp or "분석" in cleanedp):
                            page_content += f"<h3>{cleaned_p}</h3>"
                        else:
                            page_content += f"<p>{cleaned_p}</p>"

            # 2. 표(Table) 추출 및 HTML 변환 (재무제표 등 복잡한 구조 대응)
            tables = page.extract_tables()
            if tables:
                for table in tables:
                    page_content += "<table>"
                    for r_idx, row in enumerate(table):
                        page_content += "<tr>"
                        for cell in row:
                            cell_text = cell.replace('\n', ' ') if cell else ""
                            tag = "th" if r_idx == 0 else "td"
                            page_content += f"<{tag}>{cell_text}</{tag}>"
                        page_content += "</tr>"
                    page_content += "</table>"

            # 3. 이미지(사진/그래프) 추출 및 삽입
            fitz_page = doc_fitz[page_num - 1]
            image_list = fitz_page.get_images(full=True)
            for img_index, img in enumerate(image_list):
                try:
                    base_image = doc_fitz.extract_image(img[0])
                    image_bytes, image_ext = base_image["image"], base_image["ext"]
                    image_filename = f"Images/page_{page_num}_img_{img_index + 1}.{image_ext}"
                    
                    book.add_item(epub.EpubItem(
                        uid=f"img_{page_num}_{img_index}", 
                        file_name=image_filename, 
                        media_type=f"image/{image_ext}", 
                        content=image_bytes
                    ))
                    page_content += f'<div><img src="{image_filename}" alt="Report Image" /></div>'
                except Exception:
                    continue

            # EPUB XHTML 챕터 생성
            chap = epub.EpubHtml(
                title=f"Page {page_num}", 
                file_name=f"Text/page_{page_num}.xhtml", 
                lang='ko'
            )
            chap.content = f'''<?xml version="1.0" encoding="UTF-8"?>
            <html xmlns="http://www.w3.org/1999/xhtml">
            <head>
                <link rel="stylesheet" href="../Styles/style.css" type="text/css"/>
                <title>Page {page_num}</title>
            </head>
            <body>{page_content}</body>
            </html>'''
            
            book.add_item(chap)
            spine_items.append(chap)
            toc_list.append(chap)

    # EPUB 3 구조 메타데이터 설정
    book.toc = tuple(toc_list)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    
    spine_items.append('nav')
    book.spine = spine_items
    
    epub.write_epub(output_epub_path, book)

# --- Streamlit 웹 UI 구성 ---
st.title("📊 증권사 리포트 PDF to EPUB 3 변환기")
st.markdown("모바일 리더기(Thorium, Moon+ Reader)에 최적화된 리플로우형 EPUB 변환 클라우드 앱입니다.")

uploaded_file = st.file_uploader("변환할 증권사 리포트 PDF 파일을 업로드하세요", type=["pdf"])
custom_title = st.text_input("생성될 EPUB 파일 제목", value="증권사 리포트 분석")

if uploaded_file is not None and st.button("🚀 EPUB 3 변환 시작", type="primary"):
    with st.spinner("PDF 분석, 이미지 및 표 추출, EPUB 구조 재구성 중..."):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_pdf:
            tmp_pdf.write(uploaded_file.getvalue())
            tmp_pdf_path = tmp_pdf.name
            
        output_epub_path = tmp_pdf_path.replace(".pdf", ".epub")
        
        try:
            convert_pdf_to_epub_v3(tmp_pdf_path, output_epub_path, title=custom_title)
            st.success("✨ EPUB 변환이 완료되었습니다!")
            
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