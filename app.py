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
        padding: 4%;
        color: #2c3e50;
        background-color: #ffffff;
    }
    h1, h2, h3 {
        color: #1a2a3a;
        margin-top: 1.2em;
        margin-bottom: 0.5em;
        font-weight: bold;
    }
    p {
        margin-bottom: 1em;
        text-align: justify;
        word-break: keep-all;
    }
    /* 이미지 및 선명한 표 크래핑 영역 스타일링 */
    .img-container {
        text-align: center;
        margin: 1.5em 0;
    }
    img {
        max-width: 100%;
        height: auto;
        display: block;
        margin: 0 auto;
        border: 1px solid #e1e4e8;
        border-radius: 4px;
        box-shadow: 0 2px 4px rgba(0,0,0,0.05);
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
        page_content = f"<h2>Page {display_page_num}</h2>"
        
        # 1. 텍스트 추출 및 정제 (단락 처리)
        text = page.get_text("text")
        if text:
            lines = text.split('\n')
            for line in lines:
                cleaned = line.strip()
                # 단순 페이지 번호 등 무의미한 텍스트 제외
                if cleaned and not re.match(r'^\s*\d+\s*$', cleaned):
                    # 주요 제목 및 소제목 자동 강조
                    if len(cleaned) < 40 and any(keyword in cleaned for keyword in ["요약", "전망", "분석", "투자", "목표"]):
                        page_content += f"<h3>{cleaned}</h3>"
                    else:
                        page_content += f"<p>{cleaned}</p>"

        # 2. 이미지 및 크롭 대상(표/그래프) 추출 (고해상도 렌더링)
        # 2-1) embedded 이미지 추출
        image_list = page.get_images(full=True)
        for img_info in image_list:
            xref = img_info[0]
            base_image = doc.extract_image(xref)
            image_bytes = base_image["image"]
            image_ext = base_image["ext"]

            img_counter += 1
            img_name = f"Images/img_{img_counter}.{image_ext}"
            
            book.add_item(epub.EpubItem(
                uid=f"img_{img_counter}",
                file_name=img_name,
                media_type=f"image/{image_ext}",
                content=image_bytes
            ))
            page_content += f'<div class="img-container"><img src="{img_name}" alt="Report Graphic" /></div>'

        # 2-2) vector graphics 기반의 표/그래프 형태 캡처 (선명도 확보를 위한 2배율 고해상도 크롭)
        # 이미지가 포함되지 않은 페이지이거나 추가 표 시각화가 필요한 경우 고화질 전체 페이지 백업 스냅샷 제공
        if len(image_list) == 0:
            pix = page.get_pixmap(dpi=200) # 고화질 200 DPI
            img_counter += 1
            img_name = f"Images/page_crop_{img_counter}.png"
            
            book.add_item(epub.EpubItem(
                uid=f"img_crop_{img_counter}",
                file_name=img_name,
                media_type="image/png",
                content=pix.tobytes("png")
            ))
            page_content += f'<div class="img-container"><img src="{img_name}" alt="Page Graphic Snapshot" /></div>'

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

    # EPUB 3 목차 및 스파인 설정
    book.toc = tuple(toc_list)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    
    spine_items.append('nav')
    book.spine = spine_items
    
    epub.write_epub(output_epub_path, book)

# --- Streamlit UI ---
st.title("📊 증권사 리포트 PDF to EPUB 3 변환기")
st.markdown("본문 텍스트 리플로우 및 선명한 원본 표/그래프 이미지 자동 최적화 앱입니다.")

uploaded_file = st.file_uploader("변환할 증권사 리포트 PDF 파일을 업로드하세요", type=["pdf"])
custom_title = st.text_input("생성될 EPUB 파일 제목", value="증권사 리포트 분석")

if uploaded_file is not None and st.button("🚀 EPUB 3 변환 시작", type="primary"):
    with st.spinner("PDF 분석, 고해상도 표/이미지 크래핑 및 EPUB 생성 중..."):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_pdf:
            tmp_pdf.write(uploaded_file.getvalue())
            tmp_pdf_path = tmp_pdf.name
            
        output_epub_path = tmp_pdf_path.replace(".pdf", ".epub")
        
        try:
            convert_pdf_to_epub_v3(tmp_pdf_path, output_epub_path, title=custom_title)
            st.success("✨ 고화질 이미지/표 최적화 EPUB 변환 완료!")
            
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
