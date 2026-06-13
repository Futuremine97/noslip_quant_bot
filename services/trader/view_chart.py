#!/usr/bin/env python3
import sys
import os
import base64
from PIL import Image

def get_terminal_size():
    try:
        columns, lines = os.get_terminal_size()
        return columns, lines
    except Exception:
        return 80, 24

def print_iterm_image(image_path):
    with open(image_path, "rb") as f:
        data = f.read()
    b64_data = base64.b64encode(data).decode('utf-8')
    sys.stdout.write(f"\033]1337;File=inline=1;width=100%;height=auto;preserveAspectRatio=1:{b64_data}\a\n")
    sys.stdout.flush()

def supports_truecolor():
    """macOS Terminal.app 등 일부 터미널은 24비트 트루컬러를 지원하지 않는다.
    COLORTERM 환경변수로 트루컬러 지원 여부를 판단한다."""
    colorterm = os.getenv("COLORTERM", "").lower()
    return "truecolor" in colorterm or "24bit" in colorterm

def rgb_to_256(r, g, b):
    """RGB(0-255)를 xterm 256색 팔레트 인덱스로 근사 변환."""
    # 회색조 처리
    if abs(r - g) < 8 and abs(g - b) < 8 and abs(r - b) < 8:
        if r < 8:
            return 16
        if r > 248:
            return 231
        return 232 + round((r - 8) / 247 * 24)
    return 16 + 36 * round(r / 255 * 5) + 6 * round(g / 255 * 5) + round(b / 255 * 5)

def print_ansi_image(image_path, max_width=None):
    try:
        img = Image.open(image_path)
    except Exception as e:
        print(f"❌ Failed to load image: {e}")
        return

    if img.mode != "RGB":
        img = img.convert("RGB")

    cols, lines = get_terminal_size()
    if max_width is None:
        max_width = min(cols - 4, 120)
        if max_width < 20:
            max_width = 80

    w, h = img.size
    aspect = h / w

    new_w = max_width
    new_h = int(new_w * aspect * 0.48) # 0.48 aspect ratio correction for tall terminal characters
    if new_h < 10:
        new_h = 10
    if new_h % 2 != 0:
        new_h += 1

    img = img.resize((new_w, new_h), Image.Resampling.BILINEAR)

    truecolor = supports_truecolor()

    # Render using half-blocks: 상단 픽셀은 전경색, 하단 픽셀은 배경색
    for y in range(0, new_h, 2):
        row_str = []
        for x in range(new_w):
            r1, g1, b1 = img.getpixel((x, y))
            r2, g2, b2 = img.getpixel((x, y + 1))
            if truecolor:
                row_str.append(
                    f"\033[38;2;{r1};{g1};{b1}m\033[48;2;{r2};{g2};{b2}m▄"
                )
            else:
                fg = rgb_to_256(r1, g1, b1)
                bg = rgb_to_256(r2, g2, b2)
                row_str.append(f"\033[38;5;{fg}m\033[48;5;{bg}m▄")
        sys.stdout.write("".join(row_str) + "\033[0m\n")
    sys.stdout.flush()

def main():
    if len(sys.argv) < 2:
        print("Usage: python view_chart.py <image_path> [text_report_path]")
        return
        
    img_path = sys.argv[1]
    if not os.path.exists(img_path):
        print(f"❌ Image not found: {img_path}")
        return
        
    term_prog = os.getenv("TERM_PROGRAM", "")
    is_iterm = (term_prog == "iTerm.app" or "ITERM_SESSION_ID" in os.environ)
    
    print("\n" + "═" * 50)
    print("📈 VISUALIZATION PREVIEW")
    print("═" * 50 + "\n")
    
    if is_iterm:
        try:
            print_iterm_image(img_path)
        except Exception:
            print_ansi_image(img_path)
    else:
        print_ansi_image(img_path)
        
    if len(sys.argv) >= 3:
        report_path = sys.argv[2]
        if os.path.exists(report_path):
            print("\n" + "═" * 50)
            print("📋 ANALYSIS REPORT")
            print("═" * 50 + "\n")
            with open(report_path, "r", encoding="utf-8") as f:
                print(f.read())

if __name__ == "__main__":
    main()
