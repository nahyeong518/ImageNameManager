import ctypes
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
from pathlib import Path, PureWindowsPath
from datetime import datetime
import urllib.request
import webbrowser

APP_VERSION = "v1.2.0"
GITHUB_REPO = "nahyeong518/ImageNameManager"
LATEST_RELEASE_URL = f"https://github.com/{GITHUB_REPO}/releases/latest"
GITHUB_API_LATEST = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest" 

try:
    from PIL import Image, ImageGrab, ImageTk, ImageDraw, ImageFont
except ImportError:
    Image = ImageGrab = ImageTk = ImageDraw = ImageFont = None

try:
    import pystray
    from pystray import MenuItem as pystray_item
except ImportError:
    pystray = None

# Windows 고해상도(DPI) 스케일링 설정
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass

# 실행 파일 기준 경로 설정
APP_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "ImageNameData"
IMAGES_DIR = DATA_DIR / "images"
NOTES_DIR = DATA_DIR / "notes"
DATA_FILE = DATA_DIR / "data.json"

# 바탕화면 설치 시 기존 데이터 폴더 마이그레이션
if getattr(sys, "frozen", False) and not DATA_DIR.exists():
    old_data_dir = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "Programs" / "ImageNameManager" / "ImageNameData"
    if old_data_dir.is_dir():
        try:
            shutil.copytree(old_data_dir, DATA_DIR)
        except Exception:
            pass

for folder in (DATA_DIR, IMAGES_DIR, NOTES_DIR):
    folder.mkdir(parents=True, exist_ok=True)


def read_text_file_safe(path):
    """다양한 인코딩(UTF-8, CP949, EUC-KR 등)을 지원하여 텍스트 파일 내용을 안전하게 읽습니다."""
    p = Path(path)
    if not p.is_file():
        return "파일을 찾을 수 없습니다."
    for enc in ("utf-8", "cp949", "euc-kr", "utf-16", "latin1"):
        try:
            return p.read_text(encoding=enc)
        except Exception:
            pass
    return "지원하지 않는 인코딩이거나 읽을 수 없는 텍스트 파일입니다."


def render_note_to_image(text, title="메모장"):
    """메모장 텍스트를 고화질 디지털 노트패드 이미지로 렌더링합니다."""
    lines = text.splitlines() or [""]
    line_h = 28
    pad_x = 35
    pad_y = 35
    w = 900
    h = max(650, len(lines) * line_h + pad_y * 2 + 60)

    img = Image.new("RGB", (w, h), color="#191e27")
    d = ImageDraw.Draw(img)

    try:
        font_title = ImageFont.truetype("malgun.ttf", 17)
        font_body = ImageFont.truetype("malgun.ttf", 12)
    except Exception:
        font_title = font_body = ImageFont.load_default()

    # 상단 헤더 박스
    d.rounded_rectangle([15, 15, w - 15, 62], radius=8, fill="#222833")
    d.text((30, 26), f"📝 {title}", fill="#5c7cfa", font=font_title)

    # 텍스트 라인 렌더링
    y = 85
    for line in lines:
        d.text((pad_x, y), line, fill="#e1e7f0", font=font_body)
        y += line_h

    return img


def create_tray_icon_image():
    """트레이에 표시할 고해상도 카메라/이미지 아이콘을 생성합니다."""
    size = (64, 64)
    if Image is None or ImageDraw is None:
        return None
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([4, 4, 60, 60], radius=14, fill="#3b5bdb")
    d.rounded_rectangle([13, 21, 51, 49], radius=6, fill="white")
    d.ellipse([23, 26, 41, 44], fill="#3b5bdb")
    d.ellipse([27, 30, 37, 40], fill="white")
    d.rectangle([20, 16, 28, 21], fill="white")
    return img


class ImageNameManager:
    def __init__(self, root):
        self.root = root
        self.root.title("이미지 & 메모장 관리")
        self.root.geometry("1080x720")
        self.root.minsize(880, 580)
        self.items = []
        self.folders = ["기본"]
        self.selected_id = None
        self.current_image_path = None
        self.current_note_path = None
        self.current_item_type = "image"  # "image" 또는 "note"
        self.preview_photo = None
        self.preview_source_image = None
        self.copied_item = None

        # 시스템 트레이 및 자동 감지 관련 변수
        self.tray_icon = None
        self._auto_save_enabled = False
        self._last_clip_seq = 0
        self._tray_notified_minimize = False

        self.load_data()
        self.build_ui()
        self.refresh_folder_list()
        self.refresh_items()

        # 창 닫기(X) 누를 때: 트레이로 최소화 (완전 종료는 메뉴 또는 트레이에서 가능)
        self.root.protocol("WM_DELETE_WINDOW", self.on_window_close)
        self.root.bind("<FocusIn>", self.on_window_focus_in)

        # 트레이 아이콘 및 클립보드 모니터링 시작
        self.setup_tray_icon()
        self.start_clipboard_monitor()

        # 1초 후 조용히 백그라운드 최신 버전 확인
        self.root.after(1000, lambda: self.check_for_updates(silent=True))

    def load_data(self):
        if not DATA_FILE.exists():
            return
        try:
            try:
                raw = json.loads(DATA_FILE.read_text(encoding="utf-8"))
            except Exception as primary_error:
                backup_file = DATA_FILE.with_suffix(DATA_FILE.suffix + ".bak")
                if not backup_file.exists():
                    raise primary_error
                raw = json.loads(backup_file.read_text(encoding="utf-8"))
                messagebox.showwarning(
                    "저장 데이터 복구",
                    "기본 저장 파일을 읽지 못해 이전 백업 데이터를 불러왔습니다.\n"
                    f"백업 파일: {backup_file.name}",
                    parent=self.root
                )
            if isinstance(raw, list):
                self.items = raw
                self.folders = ["기본"]
                for item in self.items:
                    item.setdefault("folder", item.get("category", "기본"))
                    if item["folder"] not in self.folders:
                        self.folders.append(item["folder"])
            else:
                self.items = raw.get("items", [])
                self.folders = raw.get("folders", ["기본"]) or ["기본"]
            normalized = []
            for folder in self.folders:
                folder = str(folder).strip().strip("/")
                if folder:
                    parts = folder.split("/")
                    for i in range(1, len(parts) + 1):
                        parent_path = "/".join(parts[:i])
                        if parent_path not in normalized:
                            normalized.append(parent_path)
            paths_migrated = False
            for item in self.items:
                item.setdefault("id", str(datetime.now().timestamp()))
                item.setdefault("name", "이름 없음")
                item.setdefault("folder", "기본")
                item.setdefault("image", "")
                item.setdefault("note_path", "")
                item.setdefault("item_type", "note" if item.get("note_path") else "image")
                item["folder"] = str(item.get("folder") or "기본").strip().strip("/") or "기본"

                # 이미지 경로 복구 로직
                old_image = str(item.get("image") or "").strip()
                if old_image and not Path(old_image).is_file():
                    filename = PureWindowsPath(old_image).name if "\\" in old_image else Path(old_image).name
                    if filename:
                        direct_candidate = IMAGES_DIR / filename
                        recovered = direct_candidate if direct_candidate.is_file() else None
                        if recovered is None and DATA_DIR.exists():
                            try:
                                recovered = next(
                                    (candidate for candidate in DATA_DIR.rglob(filename)
                                     if candidate.is_file() and candidate != DATA_FILE),
                                    None
                                )
                            except OSError:
                                recovered = None
                        if recovered is not None:
                            item["image"] = str(recovered.resolve())
                            paths_migrated = True

                # 메모장 경로 복구 로직
                old_note = str(item.get("note_path") or "").strip()
                if old_note and not Path(old_note).is_file():
                    filename = PureWindowsPath(old_note).name if "\\" in old_note else Path(old_note).name
                    if filename:
                        direct_candidate = NOTES_DIR / filename
                        if direct_candidate.is_file():
                            item["note_path"] = str(direct_candidate.resolve())
                            paths_migrated = True

                parts = item["folder"].split("/")
                for i in range(1, len(parts) + 1):
                    parent_path = "/".join(parts[:i])
                    if parent_path not in normalized:
                        normalized.append(parent_path)
            self.folders = normalized or ["기본"]
            if "기본" not in self.folders:
                self.folders.insert(0, "기본")
            if paths_migrated:
                self.save_data()
        except Exception as e:
            messagebox.showwarning("데이터 읽기 오류", f"저장 데이터를 읽지 못했습니다.\n{e}")

    def save_data(self):
        """JSON을 임시 파일에 완전히 기록한 뒤 원자적으로 교체해 저장 중단에 대비합니다."""
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {"folders": self.folders, "items": self.items},
            ensure_ascii=False, indent=2
        )
        temp_file = DATA_FILE.with_suffix(DATA_FILE.suffix + ".tmp")
        try:
            with temp_file.open("w", encoding="utf-8", newline="\n") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
            if DATA_FILE.exists():
                try:
                    with DATA_FILE.open("r", encoding="utf-8") as current:
                        json.load(current)
                    backup_file = DATA_FILE.with_suffix(DATA_FILE.suffix + ".bak")
                    shutil.copy2(DATA_FILE, backup_file)
                except (json.JSONDecodeError, OSError):
                    pass
            os.replace(temp_file, DATA_FILE)
        except Exception:
            try:
                if temp_file.exists():
                    temp_file.unlink()
            except OSError:
                pass
            raise

    # ------------------ 시스템 트레이 및 원클릭 저장 기능 ------------------

    def setup_tray_icon(self):
        if pystray is None:
            return

        icon_img = create_tray_icon_image()
        if icon_img is None:
            return

        def make_menu():
            return pystray.Menu(
                pystray.MenuItem("📸 클립보드 스크린샷 바로 저장 (원클릭)", self.tray_quick_save, default=True),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("⚡ 스크린샷 자동 저장", self.tray_toggle_auto_save, checked=lambda item: self._auto_save_enabled),
                pystray.MenuItem("📂 이미지 저장 폴더 열기", self.tray_open_folder),
                pystray.MenuItem("📝 메모장 저장 폴더 열기", self.tray_open_notes_folder),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("🪟 창 열기 / 숨기기", self.tray_toggle_window),
                pystray.MenuItem("❌ 프로그램 완전 종료", self.tray_quit)
            )

        self.tray_icon = pystray.Icon(
            "ImageNameManager",
            icon_img,
            "이미지 & 메모장 관리 (클릭: 스크린샷 즉시 저장)",
            menu=make_menu()
        )
        try:
            self.tray_icon.run_detached()
        except Exception as e:
            print(f"트레이 아이콘 실행 실패: {e}")
            self.tray_icon = None

    def tray_quick_save(self, icon=None, item=None):
        self.root.after(0, lambda: self.quick_save_clipboard(notify=True, source="트레이 원클릭"))

    def tray_toggle_auto_save(self, icon=None, item=None):
        self.root.after(0, self.toggle_auto_save)

    def tray_open_folder(self, icon=None, item=None):
        self.root.after(0, self.open_images_folder)

    def tray_open_notes_folder(self, icon=None, item=None):
        self.root.after(0, self.open_notes_folder)

    def tray_toggle_window(self, icon=None, item=None):
        self.root.after(0, self.toggle_window)

    def tray_quit(self, icon=None, item=None):
        self.root.after(0, self.quit_application)

    def toggle_window(self):
        if self.root.state() == "withdrawn":
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
        else:
            self.root.withdraw()

    def on_window_close(self):
        try:
            self.save_data()
        except Exception:
            pass

        if self.tray_icon:
            self.root.withdraw()
            if not self._tray_notified_minimize:
                self._tray_notified_minimize = True
                self.notify_user(
                    "프로그램이 작업표시줄 트레이로 최소화되었습니다.\n"
                    "트레이 아이콘을 클릭하면 언제든 스크린샷이 저장됩니다.\n"
                    "완전 종료는 트레이 아이콘 우클릭 → '프로그램 완전 종료'를 누르세요.",
                    "이미지 & 메모장 관리"
                )
        else:
            self.confirm_save_and_close()

    def quit_application(self):
        try:
            self.save_data()
        except Exception as e:
            messagebox.showerror("저장 오류", f"종료 중 데이터 저장에 실패했습니다.\n{e}", parent=self.root)
            return

        if self.tray_icon:
            try:
                self.tray_icon.stop()
            except Exception:
                pass
            self.tray_icon = None

        self.root.destroy()
        sys.exit(0)

    def open_images_folder(self):
        try:
            os.startfile(IMAGES_DIR)
        except Exception as e:
            messagebox.showerror("폴더 열기 오류", f"폴더를 열 수 없습니다.\n{e}", parent=self.root)

    def open_notes_folder(self):
        try:
            os.startfile(NOTES_DIR)
        except Exception as e:
            messagebox.showerror("폴더 열기 오류", f"폴더를 열 수 없습니다.\n{e}", parent=self.root)

    def notify_user(self, message, title="이미지 & 메모장 관리"):
        if self.tray_icon:
            try:
                self.tray_icon.notify(message, title)
            except Exception:
                pass

    def quick_save_clipboard(self, notify=True, source="원클릭"):
        if ImageGrab is None:
            if notify:
                self.notify_user("Pillow 라이브러리가 필요합니다.", "오류")
            return False

        try:
            grabbed = ImageGrab.grabclipboard()
            if isinstance(grabbed, Image.Image):
                now = datetime.now()
                time_code = now.strftime("%Y%m%d_%H%M%S")
                filename = f"screenshot_{time_code}_{now.strftime('%f')[:3]}.png"
                path = IMAGES_DIR / filename
                grabbed.save(path, "PNG")

                target_folder = self.selected_folder() or self.folder_var.get().strip() or "기본"
                if target_folder not in self.folders:
                    target_folder = "기본" if "기본" in self.folders else (self.folders[0] if self.folders else "기본")

                item_name = f"스크린샷_{time_code}"
                new_item = {
                    "id": str(now.timestamp()),
                    "name": item_name,
                    "folder": target_folder,
                    "item_type": "image",
                    "image": str(path.resolve()),
                    "note_path": ""
                }
                self.items.append(new_item)
                self.save_data()

                self.refresh_items()
                self.status_var.set(f"[{source}] '{item_name}' 이미지가 '{target_folder}' 폴더에 저장되었습니다.")

                if notify:
                    self.notify_user(
                        f"스크린샷이 저장되었습니다!\n파일명: {filename}\n폴더: [{target_folder}]",
                        "스크린샷 원클릭 저장 완료"
                    )
                return True

            elif isinstance(grabbed, list) and grabbed:
                src_path = Path(grabbed[0])
                if src_path.is_file():
                    ext = src_path.suffix.lower()
                    now = datetime.now()
                    time_code = now.strftime("%Y%m%d_%H%M%S")
                    target_folder = self.selected_folder() or self.folder_var.get().strip() or "기본"
                    if target_folder not in self.folders:
                        target_folder = "기본" if "기본" in self.folders else (self.folders[0] if self.folders else "기본")

                    if ext in (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"):
                        filename = f"file_{time_code}_{src_path.name}"
                        dest = IMAGES_DIR / filename
                        shutil.copy2(src_path, dest)
                        item_name = src_path.stem
                        new_item = {
                            "id": str(now.timestamp()),
                            "name": item_name,
                            "folder": target_folder,
                            "item_type": "image",
                            "image": str(dest.resolve()),
                            "note_path": ""
                        }
                        self.items.append(new_item)
                        self.save_data()
                        self.refresh_items()
                        self.status_var.set(f"[{source}] '{item_name}' 이미지를 저장했습니다.")
                        if notify:
                            self.notify_user(f"클립보드 파일이 저장되었습니다!\n파일명: {filename}\n폴더: [{target_folder}]", "이미지 저장 완료")
                        return True
                    elif ext in (".txt", ".md", ".log", ".csv", ".json"):
                        filename = f"note_{time_code}_{src_path.name}"
                        dest = NOTES_DIR / filename
                        shutil.copy2(src_path, dest)
                        item_name = src_path.stem
                        new_item = {
                            "id": str(now.timestamp()),
                            "name": item_name,
                            "folder": target_folder,
                            "item_type": "note",
                            "image": "",
                            "note_path": str(dest.resolve())
                        }
                        self.items.append(new_item)
                        self.save_data()
                        self.refresh_items()
                        self.status_var.set(f"[{source}] '{item_name}' 메모장을 저장했습니다.")
                        if notify:
                            self.notify_user(f"메모장 파일이 저장되었습니다!\n파일명: {filename}\n폴더: [{target_folder}]", "메모장 저장 완료")
                        return True

            if notify:
                self.notify_user(
                    "클립보드에 이미지가 없습니다.\nWin + Shift + S 또는 스크린샷을 찍은 후 다시 눌러주세요.",
                    "스크린샷 없음"
                )
            return False

        except Exception as e:
            if notify:
                self.notify_user(f"저장 중 오류 발생: {e}", "오류")
            return False

    def toggle_auto_save(self):
        self._auto_save_enabled = not self._auto_save_enabled
        state_str = "ON" if self._auto_save_enabled else "OFF"
        if hasattr(self, "auto_save_btn"):
            self.auto_save_btn.configure(
                text=f"⚡ 자동 저장: {state_str}",
                style="Accent.TButton" if self._auto_save_enabled else "Soft.TButton"
            )
        if self.tray_icon:
            try:
                self.tray_icon.update_menu()
            except Exception:
                pass

        if self._auto_save_enabled:
            try:
                self._last_clip_seq = ctypes.windll.user32.GetClipboardSequenceNumber()
            except Exception:
                pass
            self.notify_user(
                "⚡ 스크린샷 자동 저장이 활성화되었습니다.\n"
                "캡처만 하면 클릭 없이도 폴더에 바로 저장됩니다!",
                "자동 저장 ON"
            )
            self.status_var.set("⚡ 스크린샷 자동 저장 활성화됨 (캡처 시 즉시 자동 저장)")
        else:
            self.notify_user("스크린샷 자동 저장이 해제되었습니다.", "자동 저장 OFF")
            self.status_var.set("스크린샷 자동 저장 해제됨")

    def start_clipboard_monitor(self):
        try:
            self._last_clip_seq = ctypes.windll.user32.GetClipboardSequenceNumber()
        except Exception:
            self._last_clip_seq = 0

        def monitor_tick():
            if self._auto_save_enabled:
                try:
                    seq = ctypes.windll.user32.GetClipboardSequenceNumber()
                    if seq != self._last_clip_seq:
                        self._last_clip_seq = seq
                        self.root.after(250, lambda: self.quick_save_clipboard(notify=True, source="자동 감지"))
                except Exception:
                    pass
            self.root.after(500, monitor_tick)

        self.root.after(500, monitor_tick)

    # ------------------ 메모장(.txt) 관련 신규 기능 ------------------

    def choose_note_file(self):
        """기존 텍스트 파일을 선택하여 데이터 폴더(ImageNameData/notes)에 복사본으로 안전하게 등록합니다. 원본은 보존됩니다."""
        path = filedialog.askopenfilename(
            title="메모장(.txt) 파일 선택 (복사본으로 가져오기)",
            filetypes=[("텍스트 파일", "*.txt *.md *.log *.csv *.json"), ("모든 파일", "*.*")]
        )
        if not path:
            return

        src = Path(path)
        if not src.is_file():
            return

        try:
            safe_name = src.name.replace(" ", "_")
            time_code = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            dest = NOTES_DIR / f"note_{time_code}_{safe_name}"

            # 기존 원본 파일은 그대로 두고 데이터 폴더로 복사본 생성
            shutil.copy2(src, dest)

            self.current_item_type = "note"
            self.current_note_path = str(dest.resolve())
            self.current_image_path = None
            self.name_var.set(src.stem)

            self.show_note_preview(self.current_note_path)
            self.status_var.set(f"메모장 파일을 복사하여 준비했습니다: '{src.name}' (원본 파일은 안전하게 보존됩니다)")
        except Exception as e:
            messagebox.showerror("메모장 가져오기 오류", f"메모장 파일을 가져오지 못했습니다.\n{e}", parent=self.root)

    def create_new_note(self):
        """새 메모장을 데이터 폴더에 생성하고 즉시 윈도우 메모장(Notepad)으로 엽니다."""
        name = simpledialog.askstring("새 메모장 만들기", "메모장 제목을 입력하세요:", parent=self.root, initialvalue="새 메모")
        if not name or not name.strip():
            return
        name = name.strip()

        try:
            safe_name = "".join(c for c in name if c.isalnum() or c in (" ", "_", "-")).strip() or "새메모"
            time_code = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            filename = f"note_{time_code}_{safe_name}.txt"
            dest = NOTES_DIR / filename

            # 초기 텍스트 작성
            header_text = f"제목: {name}\n생성일시: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n" + ("=" * 40) + "\n\n"
            dest.write_text(header_text, encoding="utf-8")

            target_folder = self.selected_folder() or self.folder_var.get().strip() or "기본"
            if target_folder not in self.folders:
                target_folder = "기본" if "기본" in self.folders else (self.folders[0] if self.folders else "기본")

            new_item = {
                "id": str(datetime.now().timestamp()),
                "name": name,
                "folder": target_folder,
                "item_type": "note",
                "image": "",
                "note_path": str(dest.resolve())
            }
            self.items.append(new_item)
            self.save_data()
            self.refresh_items()

            self.current_item_type = "note"
            self.current_note_path = str(dest.resolve())
            self.current_image_path = None
            self.name_var.set(name)
            self.show_note_preview(self.current_note_path)

            # 즉시 윈도우 메모장으로 실행하여 작성할 수 있게 함
            self.open_current_in_notepad(self.current_note_path)
            self.status_var.set(f"새 메모장 '{name}'을 생성하여 열었습니다.")
        except Exception as e:
            messagebox.showerror("메모장 생성 오류", f"새 메모장을 만들지 못했습니다.\n{e}", parent=self.root)

    def open_current_in_notepad(self, path=None):
        """지정한 파일 또는 현재 미리보기 중인 메모장을 윈도우 메모장(notepad.exe)으로 엽니다."""
        target_path = path or getattr(self, "current_preview_note_path", None) or self.current_note_path
        if not target_path:
            sel = self.listbox.curselection()
            if sel and 0 <= sel[0] < len(self.visible_items):
                item = self.visible_items[sel[0]]
                target_path = item.get("note_path")

        if not target_path or not Path(target_path).is_file():
            messagebox.showinfo("알림", "열 수 있는 메모장 파일이 없습니다.", parent=self.root)
            return

        try:
            subprocess.Popen(["notepad.exe", str(Path(target_path).resolve())])
        except Exception:
            try:
                os.startfile(target_path)
            except Exception as e:
                messagebox.showerror("실행 오류", f"메모장을 열 수 없습니다.\n{e}", parent=self.root)

    def show_note_preview(self, path):
        """우측 미리보기 영역에 메모장 내용을 표시합니다."""
        self.current_preview_note_path = str(path)
        self.preview.pack_forget()
        self.note_preview_wrap.pack(fill="both", expand=True, padx=12, pady=12)

        content = read_text_file_safe(path)
        self.note_text.configure(state="normal")
        self.note_text.delete("1.0", tk.END)
        self.note_text.insert("1.0", content)
        self.note_text.configure(state="disabled")
        self.note_path_label.configure(text=f"파일: {Path(path).name}")

    def on_window_focus_in(self, event=None):
        """메모장을 편집하고 창으로 돌아왔을 때 미리보기 내용을 자동으로 갱신합니다."""
        if getattr(self, "current_preview_note_path", None) and Path(self.current_preview_note_path).is_file():
            try:
                if self.note_preview_wrap.winfo_manager() == "pack":
                    content = read_text_file_safe(self.current_preview_note_path)
                    self.note_text.configure(state="normal")
                    self.note_text.delete("1.0", tk.END)
                    self.note_text.insert("1.0", content)
                    self.note_text.configure(state="disabled")
            except Exception:
                pass

    # ------------------ 기존 UI 및 관리 로직 ------------------

    def check_for_updates(self, silent=True):
        """GitHub 최신 릴리즈를 확인하여 새 버전이 있으면 알림을 제공합니다."""
        def safe_after(fn):
            try:
                if self.root and self.root.winfo_exists():
                    self.root.after(0, fn)
            except Exception:
                pass

        def worker():
            try:
                req = urllib.request.Request(
                    GITHUB_API_LATEST,
                    headers={"User-Agent": "ImageNameManager-App"}
                )
                with urllib.request.urlopen(req, timeout=5) as resp:
                    if resp.status != 200:
                        return
                    data = json.loads(resp.read().decode("utf-8"))

                latest_tag = data.get("tag_name", "").strip()
                if not latest_tag:
                    return

                def parse_ver(v_str):
                    nums = re.findall(r"\d+", str(v_str))
                    return [int(n) for n in nums] if nums else [0]

                cur_nums = parse_ver(APP_VERSION)
                new_nums = parse_ver(latest_tag)

                if new_nums > cur_nums:
                    def notify_update():
                        if hasattr(self, "update_btn"):
                            self.update_btn.configure(
                                text=f"🔔 새 버전 ({latest_tag}) 발견!",
                                style="Accent.TButton"
                            )
                        self.status_var.set(f"🔔 새로운 버전({latest_tag})이 출시되었습니다. 상단 버튼을 눌러 다운로드하세요!")
                        if messagebox.askyesno(
                            "새로운 버전 업데이트 알림",
                            f"새로운 버전({latest_tag})이 출시되었습니다!\n\n"
                            f"현재 버전: {APP_VERSION}\n"
                            f"최신 버전: {latest_tag}\n\n"
                            "지금 다운로드 페이지(웹 브라우저)를 열어 최신 설치 파일을 받으시겠습니까?",
                            parent=self.root
                        ):
                            webbrowser.open(LATEST_RELEASE_URL)
                    safe_after(notify_update)
                else:
                    if not silent:
                        def notify_latest():
                            messagebox.showinfo(
                                "업데이트 확인",
                                f"현재 최신 버전({APP_VERSION})을 사용하고 있습니다! 👍\n\n새로운 업데이트가 없습니다.",
                                parent=self.root
                            )
                        safe_after(notify_latest)
            except Exception as e:
                if not silent:
                    def notify_error():
                        messagebox.showwarning(
                            "업데이트 확인 오류",
                            f"최신 버전 정보를 확인하지 못했습니다.\n인터넷 연결 상태를 확인해 주세요.\n\n({e})",
                            parent=self.root
                        )
                    safe_after(notify_error)

        threading.Thread(target=worker, daemon=True).start()

    def confirm_save_and_close(self):
        try:
            self.save_data()
            with DATA_FILE.open("r", encoding="utf-8") as f:
                saved = json.load(f)
            if (not isinstance(saved, dict)
                    or not isinstance(saved.get("items"), list)
                    or not isinstance(saved.get("folders"), list)):
                raise ValueError("저장 파일의 구조가 올바르지 않습니다.")
        except Exception as e:
            messagebox.showerror(
                "저장 실패",
                f"데이터를 저장하거나 확인하지 못했습니다. 프로그램을 종료하지 않았습니다.\n\n{e}",
                parent=self.root
            )
            return

        if self.tray_icon:
            try:
                self.tray_icon.stop()
            except Exception:
                pass

        messagebox.showinfo(
            "저장 완료",
            "저장되었습니다.\n목록과 폴더 정보가 안전하게 저장되었습니다.",
            parent=self.root
        )
        self.root.destroy()
        sys.exit(0)

    def build_ui(self):
        self.root.configure(bg="#171a21")
        self.root.option_add("*Font", ("Malgun Gothic", 10))
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TFrame", background="#171a21")
        style.configure("TLabel", background="#171a21", foreground="#e1e7f0")
        style.configure("TButton", padding=(9, 6), background="#303947", foreground="#e1e7f0", borderwidth=0)
        style.map("TButton", background=[("active", "#3b4657")])
        style.configure("Accent.TButton", padding=(11, 7), background="#4263eb", foreground="white", font=("Malgun Gothic", 10, "bold"))
        style.map("Accent.TButton", background=[("active", "#5278f2")], foreground=[("active", "white")])
        style.configure("Soft.TButton", padding=(9, 6), background="#29364b", foreground="#a9c0ff")
        style.map("Soft.TButton", background=[("active", "#35445d")])
        style.configure("TEntry", padding=8, fieldbackground="#171d26", bordercolor="#465163")
        style.configure("TCombobox", padding=7, fieldbackground="#171d26", background="#303947", foreground="#e1e7f0", arrowcolor="#c3ccda")
        style.map("TCombobox", fieldbackground=[("readonly", "#252c37"), ("focus", "#252c37")], foreground=[("readonly", "#e1e7f0")], selectbackground=[("readonly", "#344b70")], selectforeground=[("readonly", "#ffffff")])
        style.configure("Treeview", background="#191e27", fieldbackground="#191e27", foreground="#dce3ee", rowheight=25, bordercolor="#343c49")
        style.map("Treeview", background=[("selected", "#344b70")], foreground=[("selected", "#ffffff")])
        style.configure("Treeview.Heading", background="#252c37", foreground="#e1e7f0", relief="flat")
        style.map("Treeview.Heading", background=[("active", "#303947")])
        style.configure("Vertical.TScrollbar", background="#303947", troughcolor="#191e27", arrowcolor="#c3ccda", bordercolor="#191e27")
        style.configure("TScrollbar", background="#303947", troughcolor="#191e27", arrowcolor="#c3ccda", bordercolor="#191e27")
        self.root.option_add("*Menu.background", "#252c37")
        self.root.option_add("*Menu.foreground", "#e1e7f0")
        self.root.option_add("*Menu.activeBackground", "#344b70")
        self.root.option_add("*Menu.activeForeground", "#ffffff")

        outer = tk.Frame(self.root, bg="#171a21")
        outer.pack(fill="both", expand=True, padx=18, pady=16)

        # 상단 배너 영역
        banner = tk.Frame(outer, bg="#111827", height=82)
        banner.pack(fill="x", pady=(0, 14))
        banner.pack_propagate(False)

        left_titles = tk.Frame(banner, bg="#111827")
        left_titles.pack(side="left", padx=22, fill="y")
        tk.Label(left_titles, text="IMAGE & NOTE LIBRARY", bg="#111827", fg="#5c7cfa", font=("Malgun Gothic", 16, "bold")).pack(anchor="w", pady=(12, 0))
        tk.Label(left_titles, text="이미지와 메모장을 안전하게 관리하세요 (트레이 원클릭 저장 지원)", bg="#111827", fg="#aebbd0", font=("Malgun Gothic", 9)).pack(anchor="w")

        right_controls = tk.Frame(banner, bg="#111827")
        right_controls.pack(side="right", anchor="e", padx=20)

        ttk.Button(right_controls, text="📂 이미지 폴더", style="Soft.TButton", command=self.open_images_folder).pack(side="left", padx=3)
        ttk.Button(right_controls, text="📂 메모장 폴더", style="Soft.TButton", command=self.open_notes_folder).pack(side="left", padx=3)
        self.auto_save_btn = ttk.Button(right_controls, text="⚡ 자동 저장: OFF", style="Soft.TButton", command=self.toggle_auto_save)
        self.auto_save_btn.pack(side="left", padx=3)
        ttk.Button(right_controls, text="🔽 트레이로 숨기기", command=self.toggle_window).pack(side="left", padx=3)

        self.update_btn = ttk.Button(
            right_controls,
            text=f"🔄 업데이트 확인 ({APP_VERSION})",
            style="Soft.TButton",
            command=lambda: self.check_for_updates(silent=False)
        )
        self.update_btn.pack(side="left", padx=3)

        body = tk.Frame(outer, bg="#171a21")
        body.pack(fill="both", expand=True)

        # 좌측 폴더 트리 패널
        side = tk.Frame(body, bg="#222833", highlightbackground="#343c49", highlightthickness=1, width=280)
        side.pack(side="left", fill="y", padx=(0, 12))
        side.pack_propagate(False)
        tk.Label(side, text="폴더 분류", bg="#222833", fg="#e6eaf2", font=("Malgun Gothic", 12, "bold")).pack(anchor="w", padx=15, pady=(15, 3))
        tk.Label(side, text="분류를 선택하세요", bg="#222833", fg="#929daf", font=("Malgun Gothic", 9)).pack(anchor="w", padx=15, pady=(0, 9))
        tree_wrap = tk.Frame(side, bg="#222833")
        tree_wrap.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.folder_tree = ttk.Treeview(tree_wrap, show="tree", selectmode="extended")
        tree_scroll = ttk.Scrollbar(tree_wrap, orient="vertical", command=self.folder_tree.yview)
        self.folder_tree.configure(yscrollcommand=tree_scroll.set)
        self.folder_tree.pack(side="left", fill="both", expand=True)
        tree_scroll.pack(side="right", fill="y")
        self.folder_tree.bind("<<TreeviewSelect>>", self.on_folder_select)

        self._folder_drag_start_y = None
        self._folder_drag_start_x = None
        self._folder_dragging = False
        self.folder_tree.bind("<ButtonPress-1>", self.begin_folder_marquee, add="+")
        self.folder_tree.bind("<B1-Motion>", self.update_folder_marquee, add="+")
        self.folder_tree.bind("<ButtonRelease-1>", self.end_folder_marquee, add="+")
        self.folder_tree.bind("<Button-3>", self.show_folder_context_menu)
        self.folder_context_menu = tk.Menu(self.root, tearoff=0)
        self.folder_context_menu.add_command(label="＋ 선택 폴더 안에 하위 폴더 추가", command=self.add_folder_from_context)
        self.folder_context_menu.add_command(label="선택 폴더 삭제", command=self.delete_selected_folders)
        self.folder_context_menu.add_separator()
        self.folder_move_menu = tk.Menu(self.folder_context_menu, tearoff=0)
        self.folder_context_menu.add_cascade(label="선택 폴더 이동", menu=self.folder_move_menu)

        tk.Frame(side, bg="#343c49", height=1).pack(fill="x", padx=14, pady=(0, 7))
        ttk.Button(side, text="＋ 폴더 추가", style="Soft.TButton", command=self.add_root_folder).pack(fill="x", padx=10, pady=2)
        ttk.Button(side, text="＋ 하위 폴더 추가", style="Soft.TButton", command=self.add_folder).pack(fill="x", padx=10, pady=2)
        ttk.Button(side, text="이름 변경", command=self.rename_folder).pack(fill="x", padx=10, pady=2)
        ttk.Button(side, text="폴더 이동", command=self.move_folder).pack(fill="x", padx=10, pady=2)
        ttk.Button(side, text="폴더 삭제", command=self.delete_folder).pack(fill="x", padx=10, pady=(2, 10))

        # 중앙 목록 및 편집 패널
        main = tk.Frame(body, bg="#222833", highlightbackground="#343c49", highlightthickness=1)
        main.pack(side="left", fill="both", expand=True)

        top = tk.Frame(main, bg="#222833")
        top.pack(fill="x", padx=17, pady=(14, 8))
        tk.Label(top, text="목록", bg="#222833", fg="#e6eaf2", font=("Malgun Gothic", 14, "bold")).pack(side="left")
        self.search_var = tk.StringVar()
        ttk.Button(top, text="검색 지우기", command=self.clear_search).pack(side="right", padx=(6, 0))
        ent = ttk.Entry(top, textvariable=self.search_var, width=26)
        ent.pack(side="right")
        ent.bind("<KeyRelease>", lambda e: self.refresh_items())
        ent.insert(0, "")

        wrap = tk.Frame(main, bg="#222833")
        wrap.pack(fill="both", expand=True, padx=17, pady=(2, 12))
        self.listbox = tk.Listbox(
            wrap, selectmode="extended", exportselection=False, relief="flat",
            bg="#191e27", fg="#dce3ee", selectbackground="#344b70", selectforeground="#ffffff",
            activestyle="none", font=("Malgun Gothic", 10), highlightthickness=1, highlightbackground="#343c49"
        )
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=sb.set)
        self.listbox.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.listbox.bind("<<ListboxSelect>>", self.on_list_selection)
        self.listbox.bind("<Button-3>", self.show_item_context_menu)
        self.listbox.bind("<Control-c>", self.copy_item_name)
        self.listbox.bind("<Control-v>", self.paste_item)
        self.listbox.bind("<Double-Button-1>", self.on_listbox_double_click)

        self.item_context_menu = tk.Menu(self.root, tearoff=0)
        self.item_context_menu.add_command(label="항목 열기 (이미지/메모장)", command=self.open_current_target)
        self.item_context_menu.add_separator()
        self.item_context_menu.add_command(label="이름 복사", command=self.copy_item_name)
        self.item_context_menu.add_command(label="항목 복제 (Ctrl+V용)", command=self.copy_item_for_duplicate)
        self.item_context_menu.add_separator()
        self.move_context_menu = tk.Menu(self.item_context_menu, tearoff=0)
        self.item_context_menu.add_cascade(label="선택 항목 폴더로 이동", menu=self.move_context_menu)
        self.item_context_menu.add_separator()
        self.item_context_menu.add_command(label="선택 항목 삭제", command=self.delete_selected_items)

        # 항목 등록 및 편집 프레임
        edit = tk.Frame(main, bg="#252c37", highlightbackground="#394352", highlightthickness=1)
        edit.pack(fill="x", padx=17, pady=(0, 15))

        tk.Label(edit, text="등록 및 편집", bg="#252c37", fg="#e6eaf2", font=("Malgun Gothic", 11, "bold")).grid(row=0, column=0, columnspan=4, sticky="w", padx=13, pady=(9, 4))
        tk.Label(edit, text="이름", bg="#252c37", fg="#aab5c5").grid(row=1, column=0, padx=(13, 4), sticky="w")
        self.name_var = tk.StringVar()
        ttk.Entry(edit, textvariable=self.name_var).grid(row=1, column=1, sticky="ew", padx=5, pady=4)
        tk.Label(edit, text="폴더", bg="#252c37", fg="#aab5c5").grid(row=1, column=2, padx=(10, 4))
        self.folder_var = tk.StringVar(value="기본")
        self.folder_combo = ttk.Combobox(edit, textvariable=self.folder_var, state="readonly", width=12)
        self.folder_combo.grid(row=1, column=3, padx=(4, 13), pady=4)
        edit.columnconfigure(1, weight=1)

        # 1행 버튼들: 파일 선택 & 메모장 기능
        acts1 = tk.Frame(edit, bg="#252c37")
        acts1.grid(row=2, column=0, columnspan=4, sticky="ew", padx=13, pady=(4, 2))
        ttk.Button(acts1, text="🖼️ 이미지 선택", command=self.choose_image).pack(side="left", padx=(0, 4))
        ttk.Button(acts1, text="📝 메모장 선택 (복사 등록)", command=self.choose_note_file).pack(side="left", padx=4)
        ttk.Button(acts1, text="➕ 새 메모장 작성", style="Soft.TButton", command=self.create_new_note).pack(side="left", padx=4)
        ttk.Button(acts1, text="📸 스크린샷 붙여넣기", style="Soft.TButton", command=self.paste_screenshot).pack(side="left", padx=4)

        # 2행 버튼들: 등록, 수정, 삭제, 초기화
        acts2 = tk.Frame(edit, bg="#252c37")
        acts2.grid(row=3, column=0, columnspan=4, sticky="ew", padx=13, pady=(2, 10))
        ttk.Button(acts2, text="등록 / 수정", style="Accent.TButton", command=self.save_item).pack(side="left", padx=(0, 4))
        ttk.Button(acts2, text="선택 항목 불러오기", command=self.load_selected_item).pack(side="left", padx=4)
        ttk.Button(acts2, text="선택 삭제", command=self.delete_item).pack(side="left", padx=4)
        ttk.Button(acts2, text="초기화", command=self.clear_form).pack(side="right")

        # 우측 미리보기 패널
        prev = tk.Frame(body, bg="#222833", highlightbackground="#343c49", highlightthickness=1, width=280)
        prev.pack(side="right", fill="y", padx=(12, 0))
        prev.pack_propagate(False)

        prev_header = tk.Frame(prev, bg="#222833")
        prev_header.pack(fill="x", padx=14, pady=(15, 2))
        tk.Label(prev_header, text="미리보기", bg="#222833", fg="#e6eaf2", font=("Malgun Gothic", 12, "bold")).pack(side="left")

        tk.Label(prev, text="더블클릭 시 큰 창으로 보기 (이미지/메모장)", bg="#222833", fg="#929daf", font=("Malgun Gothic", 8)).pack(anchor="w", padx=14, pady=(0, 8))

        # 1) 이미지 미리보기 라벨
        self.preview = tk.Label(prev, text="항목을 선택하거나\n이미지/메모장을 추가하세요.", bg="#191e27", fg="#8995a8", justify="center", font=("Malgun Gothic", 9))
        self.preview.pack(fill="both", expand=True, padx=12, pady=12)
        self.preview.bind("<Configure>", self.render_preview)
        self.preview.bind("<Double-Button-1>", self.open_original_preview)

        # 2) 메모장 미리보기 텍스트 컨테이너 (기본은 숨김)
        self.note_preview_wrap = tk.Frame(prev, bg="#191e27")
        self.note_path_label = tk.Label(self.note_preview_wrap, text="파일: note.txt", bg="#222833", fg="#a9c0ff", font=("Malgun Gothic", 8), anchor="w", padx=8, pady=4)
        self.note_path_label.pack(fill="x")
        note_text_box = tk.Frame(self.note_preview_wrap, bg="#191e27")
        note_text_box.pack(fill="both", expand=True)
        self.note_text = tk.Text(note_text_box, bg="#191e27", fg="#dce3ee", font=("Malgun Gothic", 9), relief="flat", wrap="word", padx=8, pady=8, state="disabled")
        note_sb = ttk.Scrollbar(note_text_box, orient="vertical", command=self.note_text.yview)
        self.note_text.configure(yscrollcommand=note_sb.set)
        self.note_text.pack(side="left", fill="both", expand=True)
        note_sb.pack(side="right", fill="y")
        self.note_text.bind("<Double-Button-1>", lambda e: self.open_note_viewer())

        note_btn_bar = tk.Frame(self.note_preview_wrap, bg="#191e27")
        note_btn_bar.pack(fill="x", pady=6)
        ttk.Button(note_btn_bar, text="📝 메모장(Notepad)으로 편집", style="Accent.TButton", command=lambda: self.open_current_in_notepad()).pack(fill="x", padx=6)

        # 하단 상태 바
        foot = tk.Frame(outer, bg="#171a21")
        foot.pack(fill="x", pady=(8, 0))
        self.status_var = tk.StringVar(value="준비됨 · 이미지 및 메모장을 안전하게 관리하세요.")
        tk.Label(foot, textvariable=self.status_var, bg="#171a21", fg="#929daf", font=("Malgun Gothic", 9)).pack(side="left")
        tk.Label(foot, text="트레이 아이콘 클릭: 스크린샷 즉시 저장  ·  더블클릭: 원본/메모장 열기", bg="#171a21", fg="#8995a8", font=("Malgun Gothic", 9)).pack(side="right")
        self.root.bind_all("<Control-v>", self.on_paste_shortcut)

    def on_paste_shortcut(self, event):
        widget = self.root.focus_get()
        if widget and widget.winfo_class() in ("Entry", "TEntry", "Text", "TCombobox"):
            return
        self.paste_screenshot()
        return "break"

    def refresh_folder_list(self, select_path=None):
        if not hasattr(self, "folder_tree"):
            return
        current = select_path if select_path is not None else self.selected_folder()
        self.folder_tree.delete(*self.folder_tree.get_children(""))
        self.folder_tree.insert("", "end", iid="__ALL__", text="전체 항목", open=True)
        for folder in sorted(set(self.folders), key=lambda x: (x.count("/"), x.casefold())):
            parts = folder.split("/")
            parent = "__ALL__"
            built = []
            for part in parts:
                built.append(part)
                path = "/".join(built)
                if path not in self.folder_tree.get_children(parent):
                    if not self.folder_tree.exists(path):
                        self.folder_tree.insert(parent, "end", iid=path, text=part, open=False)
                parent = path
        self.folder_combo["values"] = self.folders
        if self.folder_var.get() not in self.folders:
            self.folder_var.set(current if current in self.folders else (self.folders[0] if self.folders else "기본"))
        target = current if current and current in self.folders else "__ALL__"
        if self.folder_tree.exists(target):
            ancestor = target
            while ancestor and ancestor != "__ALL__":
                parent = self.folder_tree.parent(ancestor)
                if parent:
                    self.folder_tree.item(parent, open=True)
                ancestor = parent
            self.folder_tree.selection_set(target)
            self.folder_tree.focus(target)
            self.folder_tree.see(target)

    def begin_folder_marquee(self, event):
        self._folder_drag_start_y = event.y
        self._folder_drag_start_x = event.x
        self._folder_dragging = False

    def update_folder_marquee(self, event):
        if self._folder_drag_start_y is None:
            return
        if abs(event.y - self._folder_drag_start_y) + abs(event.x - self._folder_drag_start_x) < 5:
            return
        self._folder_dragging = True
        top, bottom = sorted((self._folder_drag_start_y, event.y))
        picked = []

        def walk(parent=""):
            for iid in self.folder_tree.get_children(parent):
                if iid != "__ALL__":
                    box = self.folder_tree.bbox(iid)
                    if box:
                        row_top, row_height = box[1], box[3]
                        if row_top + row_height >= top and row_top <= bottom:
                            picked.append(iid)
                if self.folder_tree.item(iid, "open"):
                    walk(iid)
        walk()
        self.folder_tree.selection_set(picked)
        if picked:
            self.folder_tree.focus(picked[-1])
        return "break"

    def end_folder_marquee(self, event):
        was_dragging = self._folder_dragging
        self._folder_drag_start_y = None
        self._folder_drag_start_x = None
        self._folder_dragging = False
        if was_dragging:
            return "break"

    def selected_folders(self):
        return [x for x in self.folder_tree.selection() if x != "__ALL__"]

    def show_folder_context_menu(self, event):
        row = self.folder_tree.identify_row(event.y)
        if row and row != "__ALL__" and row not in self.folder_tree.selection():
            self.folder_tree.selection_set(row)
            self.folder_tree.focus(row)
            self.on_folder_select()
        self._folder_context_target = row if row and row != "__ALL__" else ""
        selected = self.selected_folders()
        self.folder_context_menu.entryconfigure("선택 폴더 삭제", state="normal" if selected else "disabled")
        self.folder_context_menu.entryconfigure("＋ 선택 폴더 안에 하위 폴더 추가", state="normal")
        self.folder_move_menu.delete(0, tk.END)
        if not selected:
            self.folder_move_menu.add_command(label="선택된 폴더 없음", state="disabled")
        else:
            roots = [f for f in selected if not any(f.startswith(other + "/") for other in selected if other != f)]
            blocked = set()
            for source in roots:
                blocked.add(source)
                blocked.update(f for f in self.folders if f.startswith(source + "/"))
            destinations = ["(최상위)"] + [f for f in self.folders if f not in blocked and not any(f.startswith(src + "/") for src in roots)]
            for target in destinations:
                label = "(최상위)" if target == "(최상위)" else target
                self.folder_move_menu.add_command(label=label, command=lambda dest=target: self.move_selected_folders(dest))
        self.folder_context_menu.tk_popup(event.x_root, event.y_root)
        return "break"

    def add_folder_from_context(self):
        parent = getattr(self, "_folder_context_target", "")
        if parent and parent not in self.folders:
            parent = ""
        self._create_folder_at(parent, "하위 폴더 추가" if parent else "전체 항목 하위 폴더 추가")

    def delete_selected_folders(self):
        selected = self.selected_folders()
        if not selected:
            messagebox.showinfo("폴더 선택", "삭제할 폴더를 선택하세요.")
            return
        roots = [f for f in selected if not any(f.startswith(other + "/") for other in selected if other != f)]
        if "기본" in roots:
            messagebox.showinfo("알림", "기본 폴더는 삭제할 수 없습니다.")
            return
        subtree = sorted({f for root in roots for f in self.folders if f == root or f.startswith(root + "/")})
        occupied = [item for item in self.items if item.get("folder", "기본") in subtree]
        if occupied:
            messagebox.showwarning("폴더 삭제 불가", "선택한 폴더 또는 하위 폴더에 항목이 있습니다. 먼저 항목을 다른 폴더로 옮기거나 삭제하세요.")
            return
        preview = "\n".join(roots)
        if not messagebox.askyesno("폴더 삭제", f"다음 폴더와 하위 폴더를 삭제할까요?\n\n{preview}\n\n총 {len(subtree)}개 폴더"):
            return
        remove = set(subtree)
        self.folders = [f for f in self.folders if f not in remove]
        if "기본" not in self.folders:
            self.folders.insert(0, "기본")
        self.save_data()
        self.refresh_folder_list("")
        self.folder_var.set("기본")
        self.refresh_items()

    def move_selected_folders(self, target_parent):
        selected = self.selected_folders()
        if not selected:
            return
        roots = [f for f in selected if not any(f.startswith(other + "/") for other in selected if other != f)]
        parent = "" if target_parent == "(최상위)" else target_parent
        if parent and parent not in self.folders:
            return
        if any(parent == src or parent.startswith(src + "/") for src in roots):
            messagebox.showwarning("이동 불가", "선택한 폴더 안으로는 이동할 수 없습니다.")
            return
        replacements = {}
        for source in roots:
            name = source.rsplit("/", 1)[-1]
            new_root = f"{parent}/{name}" if parent else name
            subtree = [f for f in self.folders if f == source or f.startswith(source + "/")]
            for old_path in subtree:
                replacements[old_path] = new_root + old_path[len(source):]
        affected = set(replacements)
        new_paths = list(replacements.values())
        if len(new_paths) != len(set(new_paths)) or any(path in self.folders and path not in affected for path in new_paths):
            messagebox.showwarning("이동 불가", "이동 위치에 같은 이름의 폴더가 이미 있습니다.")
            return
        if all(old == new for old, new in replacements.items()):
            return
        self.folders = [replacements.get(f, f) for f in self.folders]
        for item in self.items:
            item_folder = item.get("folder", "기본")
            if item_folder in replacements:
                item["folder"] = replacements[item_folder]
        self.save_data()
        focus = replacements.get(roots[0], roots[0]) if roots else ""
        self.refresh_folder_list(focus)
        self.folder_var.set(focus if focus in self.folders else (self.folders[0] if self.folders else "기본"))
        self.refresh_items()
        self.status_var.set(f"선택한 폴더 {len(roots)}개를 이동했습니다.")

    def on_folder_select(self, event=None):
        self.selected_id = None
        self.current_image_path = None
        self.current_note_path = None
        self.name_var.set("")
        selected = self.selected_folder()
        if selected:
            self.folder_var.set(selected)
        elif self.folders:
            self.folder_var.set(self.folders[0])
        self.preview_photo = None
        self.preview_source_image = None
        self.note_preview_wrap.pack_forget()
        self.preview.pack(fill="both", expand=True, padx=12, pady=12)
        self.preview.configure(image="", text="항목을 선택하거나\n새 항목을 등록하세요.")
        self.refresh_items()

    def on_list_selection(self, event=None):
        sel = self.listbox.curselection()
        if not sel:
            return
        item = self.visible_items[sel[0]]
        item_type = item.get("item_type", "note" if item.get("note_path") else "image")

        if item_type == "note":
            note_path = item.get("note_path") or ""
            if note_path and Path(note_path).exists():
                self.show_note_preview(note_path)
                self.status_var.set(f"미리보기: 📝 {item.get('name', '이름 없음')} (더블클릭 시 메모장으로 열기)")
            else:
                self.note_preview_wrap.pack_forget()
                self.preview.pack(fill="both", expand=True, padx=12, pady=12)
                self.preview.configure(image="", text="저장된 메모장 파일을 찾을 수 없습니다.")
                self.status_var.set(f"'{item.get('name', '이름 없음')}' 메모장 파일을 찾을 수 없습니다.")
        else:
            image_path = item.get("image") or ""
            if image_path and Path(image_path).exists():
                self.show_preview(image_path)
                self.status_var.set(f"미리보기: 🖼️ {item.get('name', '이름 없음')} (더블클릭 시 원본 크기 보기)")
            else:
                self.note_preview_wrap.pack_forget()
                self.preview.pack(fill="both", expand=True, padx=12, pady=12)
                self.preview_photo = None
                self.preview.configure(image="", text="저장된 이미지 파일을 찾을 수 없습니다.")
                self.status_var.set(f"'{item.get('name', '이름 없음')}' 이미지 파일을 찾을 수 없습니다.")

    def on_listbox_double_click(self, event=None):
        """목록 더블클릭 시 이미지면 원본 뷰어(그림판), 메모장이면 큰 창 보기(읽기 전용)를 실행합니다."""
        sel = self.listbox.curselection()
        if not sel:
            return
        item = self.visible_items[sel[0]]
        item_type = item.get("item_type", "note" if item.get("note_path") else "image")
        if item_type == "note":
            note_path = item.get("note_path") or ""
            if note_path and Path(note_path).exists():
                self.open_note_viewer(note_path)
        else:
            self.open_original_preview()

    def open_current_target(self):
        sel = self.listbox.curselection()
        if not sel:
            if self.current_item_type == "note" and self.current_note_path:
                self.open_note_viewer(self.current_note_path)
            elif self.preview_source_image:
                self.open_original_preview()
            return
        item = self.visible_items[sel[0]]
        item_type = item.get("item_type", "note" if item.get("note_path") else "image")
        if item_type == "note":
            note_path = item.get("note_path") or ""
            if note_path:
                self.open_note_viewer(note_path)
        else:
            self.open_original_preview()

    def open_note_viewer(self, path=None):
        """메모장을 디지털 노트패드로 열어 읽기 및 펜 필기 그림판을 제공합니다."""
        target_path = path or getattr(self, "current_preview_note_path", None) or self.current_note_path
        if not target_path:
            sel = self.listbox.curselection()
            if sel and 0 <= sel[0] < len(self.visible_items):
                item = self.visible_items[sel[0]]
                target_path = item.get("note_path")

        if not target_path or not Path(target_path).is_file():
            messagebox.showinfo("알림", "열 수 있는 메모장 파일이 없습니다.", parent=self.root)
            return

        content = read_text_file_safe(target_path)
        note_name = Path(target_path).name
        base_name = Path(target_path).stem

        sel = self.listbox.curselection()
        base_item = self.visible_items[sel[0]] if sel and 0 <= sel[0] < len(self.visible_items) else None
        target_folder = base_item.get("folder", "기본") if base_item else (self.selected_folder() or self.folder_var.get().strip() or "기본")

        note_img = render_note_to_image(content, base_name)
        self.open_image_paint_viewer(
            note_img,
            title=f"메모장 뷰어 & 필기 그림판 - {note_name}",
            base_name=base_name,
            target_folder=target_folder,
            note_source_path=target_path
        )

    def load_selected_item(self):
        sel = self.listbox.curselection()
        if not sel:
            messagebox.showinfo("선택 필요", "불러올 항목을 목록에서 선택하세요.")
            return
        item = self.visible_items[sel[0]]
        self.selected_id = item["id"]
        self.name_var.set(item.get("name", ""))
        self.folder_var.set(item.get("folder", "기본"))
        item_type = item.get("item_type", "note" if item.get("note_path") else "image")
        self.current_item_type = item_type

        if item_type == "note":
            self.current_note_path = item.get("note_path") or None
            self.current_image_path = None
            if self.current_note_path and Path(self.current_note_path).exists():
                self.show_note_preview(self.current_note_path)
            else:
                self.note_preview_wrap.pack_forget()
                self.preview.pack(fill="both", expand=True, padx=12, pady=12)
                self.preview.configure(text="저장된 메모장 파일을 찾을 수 없습니다.", image="")
            self.status_var.set(f"📝 '{item.get('name', '이름 없음')}' 메모장 항목을 수정할 수 있습니다.")
        else:
            self.current_image_path = item.get("image") or None
            self.current_note_path = None
            if self.current_image_path and Path(self.current_image_path).exists():
                self.show_preview(self.current_image_path)
            else:
                self.note_preview_wrap.pack_forget()
                self.preview.pack(fill="both", expand=True, padx=12, pady=12)
                self.preview.configure(text="저장된 이미지 파일을 찾을 수 없습니다.", image="")
            self.status_var.set(f"🖼️ '{item.get('name', '이름 없음')}' 이미지 항목을 수정할 수 있습니다.")

    def selected_folder(self):
        if not hasattr(self, "folder_tree"):
            return "기본"
        sel = self.folder_tree.selection()
        if not sel or sel[0] == "__ALL__":
            return ""
        return sel[0]

    def refresh_items(self):
        if not hasattr(self, "listbox"):
            return
        query = self.search_var.get().strip().casefold()
        folder = self.selected_folder()
        self.listbox.delete(0, tk.END)
        self.visible_items = []
        for item in self.items:
            item_folder = item.get("folder", "기본")
            name = item.get("name", "이름 없음")
            item_type = item.get("item_type", "note" if item.get("note_path") else "image")
            icon = "📝 " if item_type == "note" else "🖼️ "
            target_file_name = Path(item.get("note_path") or item.get("image") or "").name
            searchable = f"{name} {item_folder} {target_file_name}".casefold()
            if query:
                if query not in searchable:
                    continue
            elif folder and not (item_folder == folder or item_folder.startswith(folder + "/")):
                continue
            self.visible_items.append(item)
            display_name = f"{icon}{name}    ·    [{item_folder}]" if query else f"{icon}{name}"
            self.listbox.insert(tk.END, display_name)
        if query:
            self.status_var.set(f"전체 검색 · '{query}' · {len(self.visible_items)}개")
        else:
            self.status_var.set(f"{folder or '전체 항목'} · {len(self.visible_items)}개")
        if hasattr(self, "header_count"):
            self.header_count.configure(text=f"{len(self.items)} ITEMS")

    def _create_folder_at(self, parent, dialog_title):
        name = simpledialog.askstring(dialog_title, "새 폴더 이름을 입력하세요:", parent=self.root)
        if name is None:
            return
        name = name.strip().strip("/")
        if not name or "/" in name or "\\" in name:
            messagebox.showwarning("폴더 이름", "폴더 이름에는 / 또는 \\ 를 사용할 수 없습니다.")
            return
        path = f"{parent}/{name}" if parent else name
        if path in self.folders:
            messagebox.showinfo("알림", "같은 위치에 이미 존재하는 폴더입니다.")
            return
        self.folders.append(path)
        self.save_data()
        self.refresh_folder_list(path)
        self.folder_var.set(path)
        self.refresh_items()

    def add_root_folder(self):
        self._create_folder_at("", "최상위 폴더 추가")

    def add_folder(self):
        parent = self.selected_folder()
        self._create_folder_at(parent, "하위 폴더 추가" if parent else "최상위 폴더 추가")

    def ask_wide_folder_name(self, initialvalue=""):
        dialog = tk.Toplevel(self.root)
        dialog.title("폴더 이름 변경")
        dialog.configure(bg="#222833")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()
        width, height = 500, 165
        self.root.update_idletasks()
        x = self.root.winfo_rootx() + max(0, (self.root.winfo_width() - width) // 2)
        y = self.root.winfo_rooty() + max(0, (self.root.winfo_height() - height) // 2)
        dialog.geometry(f"{width}x{height}+{x}+{y}")
        tk.Label(dialog, text="새 폴더 이름을 입력하세요:", bg="#222833", fg="#e6eaf2", font=("Malgun Gothic", 10)).pack(anchor="w", padx=22, pady=(20, 8))
        value = tk.StringVar(value=initialvalue)
        entry = ttk.Entry(dialog, textvariable=value, width=48)
        entry.pack(fill="x", padx=22, pady=(0, 14))
        result = {"value": None}

        def accept(event=None):
            result["value"] = value.get()
            dialog.destroy()

        def cancel(event=None):
            dialog.destroy()

        buttons = tk.Frame(dialog, bg="#222833")
        buttons.pack(fill="x", padx=22)
        ttk.Button(buttons, text="취소", command=cancel).pack(side="right")
        ttk.Button(buttons, text="확인", style="Accent.TButton", command=accept).pack(side="right", padx=(0, 8))
        dialog.bind("<Return>", accept)
        dialog.bind("<Escape>", cancel)
        entry.focus_set()
        entry.selection_range(0, tk.END)
        self.root.wait_window(dialog)
        return result["value"]

    def rename_folder(self):
        old = self.selected_folder()
        if not old:
            messagebox.showinfo("폴더 선택", "이름을 변경할 폴더를 선택하세요.")
            return
        old_name = old.rsplit("/", 1)[-1]
        new = self.ask_wide_folder_name(old_name)
        if not new or not new.strip():
            return
        new = new.strip().strip("/")
        if not new or "/" in new or "\\" in new:
            messagebox.showwarning("폴더 이름", "폴더 이름에는 / 또는 \\ 를 사용할 수 없습니다.")
            return
        parent = old.rsplit("/", 1)[0] if "/" in old else ""
        new_path = f"{parent}/{new}" if parent else new
        affected = [f for f in self.folders if f == old or f.startswith(old + "/")]
        replacements = {f: new_path + f[len(old):] for f in affected}
        if any(v in self.folders and v not in affected for v in replacements.values()):
            messagebox.showwarning("이름 중복", "같은 위치에 같은 이름의 폴더가 이미 있습니다.")
            return
        self.folders = [replacements.get(f, f) for f in self.folders]
        for item in self.items:
            folder = item.get("folder", "기본")
            if folder in replacements:
                item["folder"] = replacements[folder]
        self.save_data()
        self.refresh_folder_list(new_path)
        self.folder_var.set(new_path)
        self.refresh_items()

    def move_folder(self):
        old = self.selected_folder()
        if not old:
            messagebox.showinfo("폴더 선택", "이동할 폴더를 선택하세요.")
            return
        choices = ["(최상위)"] + [f for f in self.folders if not (f == old or f.startswith(old + "/"))]
        parent = simpledialog.askstring("폴더 이동", "이동할 상위 폴더 경로를 입력하세요.\n최상위로 이동하려면 (최상위)를 입력하세요.\n\n가능한 위치:\n" + "\n".join(choices), initialvalue="(최상위)", parent=self.root)
        if parent is None:
            return
        parent = parent.strip()
        if parent == "(최상위)" or parent == "":
            parent = ""
        elif parent not in self.folders or parent == old or parent.startswith(old + "/"):
            messagebox.showwarning("이동 위치 오류", "목록에 있는 유효한 상위 폴더를 입력하세요.")
            return
        name = old.rsplit("/", 1)[-1]
        new_path = f"{parent}/{name}" if parent else name
        affected = [f for f in self.folders if f == old or f.startswith(old + "/")]
        replacements = {f: new_path + f[len(old):] for f in affected}
        if any(v in self.folders and v not in affected for v in replacements.values()):
            messagebox.showwarning("이동 불가", "이동 위치에 같은 이름의 폴더가 이미 있습니다.")
            return
        self.folders = [replacements.get(f, f) for f in self.folders]
        for item in self.items:
            folder = item.get("folder", "기본")
            if folder in replacements:
                item["folder"] = replacements[folder]
        self.save_data()
        self.refresh_folder_list(new_path)
        self.folder_var.set(new_path)
        self.refresh_items()

    def delete_folder(self):
        folder = self.selected_folder()
        if not folder:
            messagebox.showinfo("폴더 선택", "삭제할 폴더를 선택하세요.")
            return
        if folder == "기본":
            messagebox.showinfo("알림", "기본 폴더는 삭제할 수 없습니다.")
            return
        subtree = [f for f in self.folders if f == folder or f.startswith(folder + "/")]
        if any(x.get("folder", "기본") in subtree for x in self.items):
            messagebox.showwarning("폴더 삭제 불가", "이 폴더 또는 하위 폴더에 항목이 있습니다. 먼저 항목을 다른 폴더로 옮기거나 삭제하세요.")
            return
        if messagebox.askyesno("폴더 삭제", f"'{folder}' 및 하위 폴더 {len(subtree)-1}개를 삭제할까요?"):
            self.folders = [f for f in self.folders if f not in subtree]
            if "기본" not in self.folders:
                self.folders.insert(0, "기본")
            self.save_data()
            self.refresh_folder_list("")
            self.refresh_items()

    def choose_image(self):
        path = filedialog.askopenfilename(
            title="이미지 선택 (복사본으로 가져오기)",
            filetypes=[("이미지 파일", "*.png *.jpg *.jpeg *.gif *.bmp *.webp"), ("모든 파일", "*.*")]
        )
        if path:
            self.current_item_type = "image"
            self.current_image_path = path
            self.current_note_path = None
            self.show_preview(path)

    def paste_screenshot(self):
        if ImageGrab is None:
            messagebox.showerror("필요한 라이브러리", "Pillow가 설치되어 있지 않습니다.\ninstall_and_run.bat을 다시 실행하세요.")
            return
        try:
            grabbed = ImageGrab.grabclipboard()
            if isinstance(grabbed, Image.Image):
                filename = f"clipboard_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.png"
                path = IMAGES_DIR / filename
                grabbed.save(path, "PNG")
                self.current_item_type = "image"
                self.current_image_path = str(path)
                self.current_note_path = None
                self.show_preview(path)
                self.status_var.set("스크린샷을 붙여넣었습니다. 이름을 입력하고 등록하세요.")
            elif isinstance(grabbed, list) and grabbed:
                self.current_item_type = "image"
                self.current_image_path = grabbed[0]
                self.current_note_path = None
                self.show_preview(grabbed[0])
            else:
                messagebox.showinfo("스크린샷 없음", "클립보드에 이미지가 없습니다.\nWin + Shift + S로 캡처한 뒤 다시 시도하세요.")
        except Exception as e:
            messagebox.showerror("붙여넣기 오류", f"클립보드 이미지를 가져오지 못했습니다.\n{e}")

    def show_preview(self, path):
        self.note_preview_wrap.pack_forget()
        self.preview.pack(fill="both", expand=True, padx=12, pady=12)

        if Image is None:
            self.preview.configure(text=str(path), image="")
            return
        try:
            with Image.open(path) as source:
                self.preview_source_image = source.copy()
            self.render_preview()
        except Exception as e:
            self.preview_source_image = None
            self.preview.configure(text=f"미리보기를 표시할 수 없습니다.\n{e}", image="")

    def render_preview(self, event=None):
        if Image is None or self.preview_source_image is None:
            return
        width = max(40, self.preview.winfo_width() - 16)
        height = max(40, self.preview.winfo_height() - 16)
        img = self.preview_source_image.copy()
        img.thumbnail((width, height), Image.Resampling.LANCZOS if hasattr(Image, "Resampling") else Image.LANCZOS)
        self.preview_photo = ImageTk.PhotoImage(img)
        self.preview.configure(image=self.preview_photo, text="")

    def open_original_preview(self, event=None):
        """이미지 원본 크기 보기 및 그림판 창을 엽니다."""
        if Image is None or self.preview_source_image is None:
            messagebox.showinfo("원본 이미지 보기", "먼저 이미지 파일을 선택하거나 스크린샷을 붙여넣어 주세요.", parent=self.root)
            return "break"

        sel = self.listbox.curselection()
        base_item = self.visible_items[sel[0]] if sel and 0 <= sel[0] < len(self.visible_items) else None
        base_name = base_item.get("name", "이미지") if base_item else (Path(self.current_image_path).stem if self.current_image_path else "이미지")
        target_folder = base_item.get("folder", "기본") if base_item else (self.selected_folder() or self.folder_var.get().strip() or "기본")

        self.open_image_paint_viewer(
            self.preview_source_image,
            title=f"이미지 뷰어 & 그림판 - {self.preview_source_image.width} × {self.preview_source_image.height} ({base_name})",
            base_name=base_name,
            target_folder=target_folder
        )
        return "break"

    def open_image_paint_viewer(self, image, title, base_name, target_folder, note_source_path=None):
        """이미지 또는 메모장 캔버스를 열어 자유로운 펜 필기/그림판 및 다른 이름으로 저장 등록을 지원합니다."""
        if image is None:
            messagebox.showinfo("알림", "표시할 이미지가 없습니다.", parent=self.root)
            return

        try:
            original = image.copy()
            width, height = original.size

            viewer = tk.Toplevel(self.root)
            viewer.title(title)
            viewer.transient(self.root)
            viewer.configure(bg="#171a21")
            viewer.minsize(640, 460)

            screen_w = max(640, viewer.winfo_screenwidth())
            screen_h = max(480, viewer.winfo_screenheight())
            window_w = min(width + 60, int(screen_w * 0.94))
            window_h = min(height + 170, int(screen_h * 0.92))
            viewer.geometry(f"{window_w}x{window_h}")

            tool_mode = tk.StringVar(value="pen")
            current_color = tk.StringVar(value="#ff3b30")
            current_width = tk.IntVar(value=4)
            strokes = []
            current_stroke = {"val": None}

            # 1. 상단 툴바 프레임
            toolbar = tk.Frame(viewer, bg="#222833", padx=10, pady=7)
            toolbar.pack(fill="x")

            left_tools = tk.Frame(toolbar, bg="#222833")
            left_tools.pack(side="left")

            pen_btn = ttk.Button(left_tools, text="✏️ 펜", style="Accent.TButton")
            pan_btn = ttk.Button(left_tools, text="🖐️ 이동", style="Soft.TButton")
            pen_btn.pack(side="left", padx=2)
            pan_btn.pack(side="left", padx=2)

            tk.Frame(left_tools, bg="#394352", width=1, height=22).pack(side="left", padx=6)

            color_palette = [
                ("#ff3b30", "빨강"),
                ("#ffd60a", "노랑"),
                ("#34c759", "초록"),
                ("#007aff", "파랑"),
                ("#ffffff", "흰색"),
                ("#111827", "검정")
            ]
            color_buttons = {}

            for c, name in color_palette:
                btn = tk.Button(
                    left_tools, bg=c, activebackground=c, width=2, height=1,
                    relief="flat", cursor="hand2", bd=1, highlightthickness=1, highlightbackground="#394352",
                    command=lambda col=c: set_color(col)
                )
                btn.pack(side="left", padx=2)
                color_buttons[c] = btn

            tk.Frame(left_tools, bg="#394352", width=1, height=22).pack(side="left", padx=6)

            size_buttons = {}
            for w_val, w_label in [(2, "2px"), (4, "4px"), (8, "8px")]:
                btn = ttk.Button(left_tools, text=w_label, width=4, command=lambda sz=w_val: set_width(sz))
                btn.pack(side="left", padx=1)
                size_buttons[w_val] = btn

            tk.Frame(left_tools, bg="#394352", width=1, height=22).pack(side="left", padx=6)

            undo_btn = ttk.Button(left_tools, text="↩️ 실행취소")
            clear_btn = ttk.Button(left_tools, text="🧹 지우기")
            undo_btn.pack(side="left", padx=2)
            clear_btn.pack(side="left", padx=2)

            # 우측 액션 버튼들
            right_tools = tk.Frame(toolbar, bg="#222833")
            right_tools.pack(side="right")

            if note_source_path:
                def copy_note_text():
                    txt = read_text_file_safe(note_source_path)
                    self.root.clipboard_clear()
                    self.root.clipboard_append(txt)
                    self.root.update()
                    messagebox.showinfo("복사 완료", "전체 메모 내용이 클립보드에 복사되었습니다.", parent=viewer)

                ttk.Button(right_tools, text="📋 텍스트 복사", command=copy_note_text).pack(side="left", padx=3)
                ttk.Button(right_tools, text="📝 메모장(Notepad)으로 편집", style="Soft.TButton",
                           command=lambda: self.open_current_in_notepad(note_source_path)).pack(side="left", padx=3)

            save_btn = ttk.Button(right_tools, text="💾 다른 이름으로 저장 및 등록", style="Accent.TButton")
            close_btn = ttk.Button(right_tools, text="닫기", command=viewer.destroy)
            save_btn.pack(side="left", padx=4)
            close_btn.pack(side="left", padx=2)

            # 2. 안내 정보바
            info_bar = tk.Frame(viewer, bg="#1a202c", padx=14, pady=5)
            info_bar.pack(fill="x")
            info_text = f"크기: {width:,} × {height:,} px   |   ✏️ [펜] 마우스로 자유롭게 필기/낙서   |   🖐️ [이동] 마우스 드래그로 화면 스크롤   |   💾 저장 시 새 이미지로 자동 등록"
            if note_source_path:
                info_text = f"메모장: {Path(note_source_path).name}   |   ✏️ [펜] 메모 위에 자유롭게 체크/필기   |   💾 저장 시 필기된 새 이미지로 등록"
            tk.Label(info_bar, text=info_text, bg="#1a202c", fg="#a0aec0", font=("Malgun Gothic", 9)).pack(side="left")

            # 3. 캔버스 영역 (반드시 도구 함수 실행 전에 생성!)
            container = tk.Frame(viewer, bg="#191e27")
            container.pack(fill="both", expand=True)

            canvas = tk.Canvas(container, bg="#191e27", highlightthickness=0, cursor="crosshair")
            hbar = ttk.Scrollbar(container, orient="horizontal", command=canvas.xview)
            vbar = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
            canvas.configure(xscrollcommand=hbar.set, yscrollcommand=vbar.set)
            canvas.grid(row=0, column=0, sticky="nsew")
            vbar.grid(row=0, column=1, sticky="ns")
            hbar.grid(row=1, column=0, sticky="ew")
            container.rowconfigure(0, weight=1)
            container.columnconfigure(0, weight=1)

            viewer.original_photo = ImageTk.PhotoImage(original)
            canvas.create_image(0, 0, anchor="nw", image=viewer.original_photo)
            canvas.configure(scrollregion=(0, 0, width, height))
            canvas.xview_moveto(0)
            canvas.yview_moveto(0)

            # 도구 제어 함수들 (canvas 생성 후 안전하게 동작)
            def set_tool(mode):
                tool_mode.set(mode)
                if mode == "pen":
                    pen_btn.configure(style="Accent.TButton")
                    pan_btn.configure(style="Soft.TButton")
                    canvas.configure(cursor="crosshair")
                else:
                    pen_btn.configure(style="Soft.TButton")
                    pan_btn.configure(style="Accent.TButton")
                    canvas.configure(cursor="fleur")

            pen_btn.configure(command=lambda: set_tool("pen"))
            pan_btn.configure(command=lambda: set_tool("pan"))

            def set_color(c):
                current_color.set(c)
                set_tool("pen")
                for col_code, b in color_buttons.items():
                    if col_code == c:
                        b.configure(relief="solid", bd=2, highlightthickness=2, highlightbackground="#ffffff")
                    else:
                        b.configure(relief="flat", bd=1, highlightthickness=1, highlightbackground="#394352")

            def set_width(w):
                current_width.set(w)
                set_tool("pen")
                for sz, b in size_buttons.items():
                    b.configure(style="Accent.TButton" if sz == w else "Soft.TButton")

            set_color("#ff3b30")
            set_width(4)

            def do_undo():
                if strokes:
                    last = strokes.pop()
                    for item_id in last.get("ids", []):
                        canvas.delete(item_id)

            def do_clear():
                if strokes:
                    if messagebox.askyesno("그림 지우기", "그린 내용을 모두 지울까요?", parent=viewer):
                        canvas.delete("pen_stroke")
                        strokes.clear()

            undo_btn.configure(command=do_undo)
            clear_btn.configure(command=do_clear)

            def make_composite_image():
                base_rgba = original.convert("RGBA")
                overlay = Image.new("RGBA", original.size, (0, 0, 0, 0))
                d = ImageDraw.Draw(overlay)
                for s in strokes:
                    pts = s.get("points", [])
                    if len(pts) >= 2:
                        d.line(pts, fill=s["color"], width=s["width"], joint="curve")
                    elif len(pts) == 1:
                        x, y = pts[0]
                        r = max(1, s["width"] // 2)
                        d.ellipse([x - r, y - r, x + r, y + r], fill=s["color"])
                return Image.alpha_composite(base_rgba, overlay)

            def save_as_new_item():
                if not strokes:
                    messagebox.showinfo("알림", "펜으로 그린 내용이 없습니다.\n그림을 그린 후 저장해 주세요.", parent=viewer)
                    return

                default_name = f"{base_name}_그림메모" if not note_source_path else f"{base_name}_필기메모"
                new_name = simpledialog.askstring("다른 이름으로 저장 및 등록", "새로 등록할 이미지 이름을 입력하세요:", initialvalue=default_name, parent=viewer)
                if not new_name or not new_name.strip():
                    return
                new_name = new_name.strip()

                try:
                    comp_img = make_composite_image()
                    time_code = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                    filename = f"image_{time_code}.png"
                    dest_path = IMAGES_DIR / filename

                    comp_img.convert("RGB").save(dest_path, "PNG")

                    new_item = {
                        "id": str(datetime.now().timestamp()),
                        "name": new_name,
                        "folder": target_folder,
                        "item_type": "image",
                        "image": str(dest_path.resolve()),
                        "note_path": ""
                    }
                    self.items.append(new_item)
                    self.save_data()
                    self.refresh_items()

                    messagebox.showinfo(
                        "저장 및 등록 완료",
                        f"그린 내용이 적용된 새 이미지가 저장 및 등록되었습니다!\n\n"
                        f"이름: {new_name}\n"
                        f"폴더: [{target_folder}]\n"
                        f"파일명: {filename}",
                        parent=viewer
                    )
                except Exception as e:
                    messagebox.showerror("저장 오류", f"이미지 저장에 실패했습니다.\n{e}", parent=viewer)

            save_btn.configure(command=save_as_new_item)

            def on_canvas_press(event):
                if tool_mode.get() == "pen":
                    cx = canvas.canvasx(event.x)
                    cy = canvas.canvasy(event.y)
                    w = current_width.get()
                    col = current_color.get()
                    current_stroke["val"] = {
                        "color": col,
                        "width": w,
                        "points": [(cx, cy)],
                        "ids": []
                    }
                    r = max(1.0, w / 2.0)
                    dot_id = canvas.create_oval(cx - r, cy - r, cx + r, cy + r, fill=col, outline=col, tags="pen_stroke")
                    current_stroke["val"]["ids"].append(dot_id)
                else:
                    canvas.scan_mark(event.x, event.y)

            def on_canvas_drag(event):
                if tool_mode.get() == "pen":
                    if current_stroke["val"] is None:
                        return
                    cx = canvas.canvasx(event.x)
                    cy = canvas.canvasy(event.y)
                    px, py = current_stroke["val"]["points"][-1]
                    current_stroke["val"]["points"].append((cx, cy))
                    w = current_stroke["val"]["width"]
                    col = current_stroke["val"]["color"]
                    lid = canvas.create_line(px, py, cx, cy, fill=col, width=w, capstyle=tk.ROUND, joinstyle=tk.ROUND, tags="pen_stroke")
                    current_stroke["val"]["ids"].append(lid)
                else:
                    canvas.scan_dragto(event.x, event.y, gain=1)

            def on_canvas_release(event):
                if tool_mode.get() == "pen" and current_stroke["val"]:
                    strokes.append(current_stroke["val"])
                    current_stroke["val"] = None

            canvas.bind("<ButtonPress-1>", on_canvas_press)
            canvas.bind("<B1-Motion>", on_canvas_drag)
            canvas.bind("<ButtonRelease-1>", on_canvas_release)
            canvas.bind("<ButtonPress-2>", lambda e: canvas.scan_mark(e.x, e.y))
            canvas.bind("<B2-Motion>", lambda e: canvas.scan_dragto(e.x, e.y, gain=1))

            viewer.bind("<Escape>", lambda e: viewer.destroy())
            viewer.focus_set()
        except Exception as e:
            messagebox.showerror("뷰어 실행 오류", f"뷰어를 열지 못했습니다.\n{e}", parent=self.root)
        return "break"

    def show_item_context_menu(self, event):
        index = self.listbox.nearest(event.y)
        row_box = self.listbox.bbox(index) if 0 <= index < len(self.visible_items) else None
        on_row = bool(row_box and row_box[1] <= event.y <= row_box[1] + row_box[3])
        if on_row and index not in self.listbox.curselection():
            self.listbox.selection_clear(0, tk.END)
            self.listbox.selection_set(index)
            self.listbox.activate(index)
            self.on_list_selection()
        elif not on_row:
            self.listbox.selection_clear(0, tk.END)

        selected = self.listbox.curselection()
        enabled = "normal" if selected else "disabled"
        self.item_context_menu.entryconfigure("항목 열기 (이미지/메모장)", state=enabled)
        self.item_context_menu.entryconfigure("이름 복사", state=enabled)
        self.item_context_menu.entryconfigure("항목 복제 (Ctrl+V용)", state=enabled)
        self.item_context_menu.entryconfigure("선택 항목 삭제", state=enabled)
        self.move_context_menu.delete(0, tk.END)
        if selected:
            for folder in self.folders:
                self.move_context_menu.add_command(
                    label=folder,
                    command=lambda target=folder: self.move_selected_items(target)
                )
        else:
            self.move_context_menu.add_command(label="선택된 항목 없음", state="disabled")
        self.item_context_menu.tk_popup(event.x_root, event.y_root)

    def move_selected_items(self, target_folder):
        indices = self.listbox.curselection()
        if not indices:
            return
        if target_folder not in self.folders:
            return
        moving = [self.visible_items[i] for i in indices if 0 <= i < len(self.visible_items)]
        if not moving:
            return
        for item in moving:
            item["folder"] = target_folder
        self.save_data()
        self.refresh_folder_list(target_folder)
        self.refresh_items()
        self.status_var.set(f"선택한 항목 {len(moving)}개를 '{target_folder}' 폴더로 이동했습니다.")

    def copy_item_for_duplicate(self):
        sel = self.listbox.curselection()
        if not sel:
            return
        self.copied_item = dict(self.visible_items[sel[0]])
        self.status_var.set(f"'{self.copied_item.get('name', '이름 없음')}' 항목을 복사했습니다. 원하는 폴더에서 Ctrl+V를 누르세요.")

    def copy_item_name(self, event=None):
        sel = self.listbox.curselection()
        if not sel:
            return "break"
        names = [str(self.visible_items[i].get("name", "이름 없음")) for i in sel]
        copied_text = "\n".join(names)
        self.root.clipboard_clear()
        self.root.clipboard_append(copied_text)
        self.root.update()
        self.status_var.set(f"이름 {len(names)}개를 클립보드에 복사했습니다.")
        return "break"

    def paste_item(self, event=None):
        if not self.copied_item:
            return "break"
        source = self.copied_item
        target_folder = self.selected_folder() or self.folder_var.get().strip() or self.folders[0]
        if target_folder not in self.folders:
            target_folder = self.folders[0]

        source_type = source.get("item_type", "note" if source.get("note_path") else "image")
        new_note_path = ""
        new_image_path = ""

        if source_type == "note" and source.get("note_path") and Path(source.get("note_path")).is_file():
            # 메모장 파일 복사본 생성
            src_note = Path(source["note_path"])
            dest_note = NOTES_DIR / f"note_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}_{src_note.name}"
            shutil.copy2(src_note, dest_note)
            new_note_path = str(dest_note.resolve())
        else:
            new_image_path = source.get("image", "")

        duplicate = {
            "id": str(datetime.now().timestamp()),
            "name": source.get("name", "이름 없음") + " - 복사본",
            "folder": target_folder,
            "item_type": source_type,
            "image": new_image_path,
            "note_path": new_note_path
        }
        self.items.append(duplicate)
        self.save_data()
        self.search_var.set("")
        try:
            self.refresh_folder_list(target_folder)
        except (ValueError, tk.TclError):
            pass
        self.refresh_items()
        self.status_var.set(f"'{duplicate['name']}' 항목을 '{target_folder}' 폴더에 붙여넣었습니다.")
        return "break"

    def save_item(self):
        name = self.name_var.get().strip()
        folder = self.folder_var.get().strip() or self.selected_folder()
        if not name:
            messagebox.showwarning("이름 필요", "항목 이름을 입력하세요.")
            return

        if not self.current_image_path and not self.current_note_path and self.selected_id is None:
            messagebox.showwarning("파일 필요", "이미지 또는 메모장 파일을 선택하거나 스크린샷을 붙여넣으세요.")
            return

        if folder not in self.folders:
            folder = self.folders[0]

        image_path = None
        note_path = None
        item_type = self.current_item_type

        # 이미지 처리 (외부 파일인 경우 내부 폴더로 복사)
        if self.current_image_path:
            src = Path(self.current_image_path)
            try:
                if src.resolve().parent == IMAGES_DIR.resolve():
                    image_path = str(src)
                else:
                    suffix = src.suffix.lower() if src.suffix else ".png"
                    dest = IMAGES_DIR / f"image_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}{suffix}"
                    shutil.copy2(src, dest)
                    image_path = str(dest)
                item_type = "image"
            except Exception as e:
                messagebox.showerror("이미지 저장 오류", str(e))
                return

        # 메모장 처리 (외부 파일인 경우 내부 폴더로 복사)
        if self.current_note_path:
            src = Path(self.current_note_path)
            try:
                if src.resolve().parent == NOTES_DIR.resolve():
                    note_path = str(src)
                else:
                    dest = NOTES_DIR / f"note_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}_{src.name}"
                    shutil.copy2(src, dest)
                    note_path = str(dest)
                item_type = "note"
            except Exception as e:
                messagebox.showerror("메모장 저장 오류", str(e))
                return

        if self.selected_id is not None:
            item = next((x for x in self.items if x["id"] == self.selected_id), None)
            if item:
                item["name"] = name
                item["folder"] = folder
                if image_path:
                    item["image"] = image_path
                    item["item_type"] = "image"
                    item["note_path"] = ""
                elif note_path:
                    item["note_path"] = note_path
                    item["item_type"] = "note"
                    item["image"] = ""
        else:
            self.items.append({
                "id": str(datetime.now().timestamp()),
                "name": name,
                "folder": folder,
                "item_type": item_type,
                "image": image_path or "",
                "note_path": note_path or ""
            })
        self.save_data()

        try:
            self.refresh_folder_list(folder)
        except (ValueError, tk.TclError):
            pass

        self.refresh_items()
        self.clear_form()
        self.status_var.set(f"'{folder}' 폴더에 저장했습니다.")

    def delete_selected_items(self):
        indices = self.listbox.curselection()
        selected = [self.visible_items[i] for i in indices if 0 <= i < len(self.visible_items)]
        if not selected:
            return
        names = [str(item.get("name", "이름 없음")) for item in selected]
        preview = "\n".join(names[:8])
        if len(names) > 8:
            preview += f"\n외 {len(names) - 8}개"
        if not messagebox.askyesno(
            "항목 삭제",
            f"선택한 항목 {len(selected)}개를 목록에서 삭제할까요?\n\n{preview}\n\n"
            "※ 기존 컴퓨터 원본 파일은 안전하게 보존됩니다.",
            parent=self.root
        ):
            return
        selected_ids = {item.get("id") for item in selected}
        self.items = [item for item in self.items if item.get("id") not in selected_ids]
        self.save_data()
        self.refresh_items()
        self.clear_form()
        self.status_var.set(f"항목 {len(selected)}개를 목록에서 삭제했습니다. (기존 원본 파일 보존됨)")

    def delete_item(self):
        sel = self.listbox.curselection()
        if not sel:
            messagebox.showinfo("선택 필요", "삭제할 항목을 목록에서 선택하세요.")
            return
        item = self.visible_items[sel[0]]
        if messagebox.askyesno("항목 삭제", f"'{item['name']}' 항목을 삭제할까요?\n\n※ 기존 컴퓨터 원본 파일은 안전하게 보존됩니다."):
            self.items.remove(item)
            self.save_data()
            self.refresh_items()
            self.clear_form()

    def clear_form(self):
        self.selected_id = None
        self.current_image_path = None
        self.current_note_path = None
        self.current_item_type = "image"
        self.name_var.set("")
        self.folder_var.set(self.selected_folder())
        self.preview_photo = None
        self.preview_source_image = None
        self.note_preview_wrap.pack_forget()
        self.preview.pack(fill="both", expand=True, padx=12, pady=12)
        self.preview.configure(image="", text="항목을 선택하거나\n새 항목을 등록하세요.")
        self.listbox.selection_clear(0, tk.END)

    def clear_search(self):
        self.search_var.set("")
        self.refresh_items()


def main():
    root = tk.Tk()
    app = ImageNameManager(root)
    root.mainloop()


if __name__ == "__main__":
    main()
