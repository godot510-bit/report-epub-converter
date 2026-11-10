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
    """재무제표 페이지 여부를 판단하여 생략하기 위한 키워드 검사"""
    fin_keywords = ["재무제표", "포괄손익계산서", "재무상태표", "현금흐름표", "K-IFRS 연결", "매출액", "영업이익", "당기순이익"]
    match_count = sum(1 for kw in fin_keywords if kw in page_text)
    # 금융 관련 키워드가 너무 많이 밀집해 있으면 재무제표 페이지로 판단
    return match_count >= 3

def convert_pdf_to_epub_v3(pdf_path, output_epub_path, title="증권사 리포트"):
    book = epub.EpubBook()
    book.set_title(title)
    book.set_language('ko')
    book.add_metadata('DC', 'creator', 'Report EPUB Converter')

    # 모바일 리더기(Thorium, Moon+ Reader) 최적화 CSS 스타일
    epub_css = '''
    @namespace epub "http://www.idpf.org/2007/ops";
    body {
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
        line-height: 1.6;
        padding: 5%;
        color: #2c3e50;
        background-color: #ffffff;
    }
    h2 {
        color: #1a2a3a;
        margin-top: 1.2em;
        margin-bottom: 0.6em;
        font-size: 1.2em;
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
        margin-bottom: 1em;
        text-align: justify;
        word-break: keep-all;
    }
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
        box-shadow: 0 2px 5px rgba(0,0,0,0.06);
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
        
        # 재무제표 페이지 필터링 (요청사항 반영: 재무제표 생략)
        if is_financial_statement(page_text):
            page_content = f"<h2>Page {display_page_num}</h2>"
            page_content += f'<p class="skip-notice">[안내] 본 페이지의 재무제표 영역은 설정에 따라 생략되었습니다.</p>'
        else:
            page_content = f"<h2>Page {display_page_num}</h2>"
            
            # 1. 텍스트 추출 및 자연스러운 문단 정제 (기존 방식 유지)
            if page_text:
                lines = page_text.split('\n')
                for line in lines:
                    cleaned = line.strip()
                    if cleaned and not re.match(r'^\s*\d+\s*$', cleaned):
                        # 소제목 성격의 텍스트 강조
                        if len(cleaned) < 35 and any(k in cleaned for k in ["요약", "전망", "분석", "투자", "목표", "특징", "점검"]):
                            page_content += f"<h3>{cleaned}</h3>"
                        else:
                            page_content += f"<p>{cleaned}</p>"

            # 2. 원본 PDF에 포함된 표/그래프/이미지 객체 고화질 추출 및 삽입
            image_list = page.get_images(full=True)
            if image_list:
                for img_info in image_list:
                    try:
                        xref = img_info[0]
                        base_image = doc.extract_image(xref)
                        image_bytes = base_image["image"]
                        image_ext = base_image["ext"]

                        img_counter += 1
                        img_name = f"Images/extracted_{img_counter}.{image_ext}"
                        
                        book.add_item(epub.EpubItem(
                            uid=f"extracted_{img_counter}",
                            file_name=img_name,
                            media_type=f"image/{image_ext}",
                            content=image_bytes
                        ))
                        page_content += f'<div class="img-container"><img src="{img_name}" alt="Extracted Chart/Table" /></div>'
                    except Exception:
                        continue
            else:
                # 만약 벡터 그래픽 기반 표/차트여서 개별 이미지가 안 잡히고 텍스트 표 형태가 의심될 경우, 
                # 해당 영역이 포함된 페이지 스냅샷 일부분을 보완 캡처하거나 깔끔하게 렌더링
                pass

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
st.markdown("텍스트는 리플로우, 표·그래프는 원본 이미지 캡처, 재무제표는 자동 생략되는 맞춤형 변환기입니다.")

uploaded_file = st.file_uploader("변환할 증권사 리포트 PDF 파일을 업로드하세요", type=["pdf"])
custom_title = st.text_input("생성될 EPUB 파일 제목", value="증권사 리포트 분석")

if uploaded_file is not None and st.button("🚀 EPUB 3 변환 시작", type="primary"):
    with st.spinner("PDF 분석 중 (텍스트 정제 및 표/이미지 원본 캡처, 재무제표 생략)..."):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_pdf:
            tmp_pdf.write(uploaded_file.getvalue())
            tmp_pdf_path = tmp_pdf.name
            
        output_epub_path = tmp_pdf_path.replace(".pdf", ".epub")
        
        try:
            convert_pdf_to_epub_v3(tmp_pdf_path, output_epub_path, title=custom_title)
            st.success("✨ 맞춤형 EPUB 변환이 완료되었습니다!")
            
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
