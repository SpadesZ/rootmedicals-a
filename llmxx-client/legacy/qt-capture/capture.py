# 路徑: C:\Users\88696\11_projects\rootmedicals-a\llmxx-client\capture.py
# 版本: v0.4
# 更版時間: 2026-05-04 09:48
# 說明: 
#   1. 配合 run_main.py 多執行緒架構，新增 image_captured 信號與狀態更新 Slot。
#   2. 保留所有 UI 功能、動態儲存與高頻擷取機制。
# ----------------------------------------------------------------------------------------------------

import os
import sys
from datetime import datetime
from typing import Optional, Tuple, List

from PySide6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout, 
                               QLabel, QComboBox, QPushButton, QRadioButton, 
                               QButtonGroup, QMessageBox, QGroupBox, QDoubleSpinBox,
                               QDialog, QLineEdit, QFileDialog, QFormLayout)
from PySide6.QtCore import Qt, QTimer, Signal, Slot, QRect
from PySide6.QtGui import QPainter, QPen, QColor

from PIL import Image
import mss

try:
    import win32gui
    import win32ui
    import win32con
except Exception as e:
    win32gui = win32ui = win32con = None
    print(f"Warning: win32 API imports failed. Error: {e}")

APP_TITLE = "Rootmedicals llmxx-client"
APP_VERSION = "v0.4"
APP_RELEASE_TIME = "2026-05-04 09:48"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CAPTURE_BASE = os.path.join(BASE_DIR, "data", "image_proc", "capture")

def list_visible_windows() -> List[Tuple[int, str]]:
    res = []
    if not win32gui: return res
    def callback(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd).strip()
            if title: res.append((hwnd, title))
        return True
    win32gui.EnumWindows(callback, None)
    res.sort(key=lambda x: x[1].lower())
    return res

def get_window_rect(hwnd: int) -> Optional[Tuple[int, int, int, int]]:
    if not win32gui: return None
    try: return win32gui.GetWindowRect(hwnd)
    except Exception: return None

def capture_window(hwnd: int) -> Optional[Image.Image]:
    rect = get_window_rect(hwnd)
    if not rect: return None
    left, top, right, bottom = rect
    try:
        with mss.mss() as sct:
            mon = {"left": left, "top": top, "width": max(1, right - left), "height": max(1, bottom - top)}
            shot = sct.grab(mon)
            return Image.frombytes("RGB", shot.size, shot.rgb)
    except Exception:
        return None

def capture_region(rect: Tuple[int, int, int, int]) -> Optional[Image.Image]:
    left, top, width, height = rect
    try:
        with mss.mss() as sct:
            mon = {"left": int(left), "top": int(top), "width": max(1, int(width)), "height": max(1, int(height))}
            shot = sct.grab(mon)
            return Image.frombytes("RGB", shot.size, shot.rgb)
    except Exception:
        return None

class SettingsDialog(QDialog):
    def __init__(self, current_settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("⚙️ Client 設定")
        self.setModal(True)
        self.resize(550, 250)
        self.current_settings = current_settings
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        form_layout = QFormLayout()

        path_layout = QHBoxLayout()
        self.line_path = QLineEdit(self.current_settings.get("base_path", DEFAULT_CAPTURE_BASE))
        self.btn_browse = QPushButton("瀏覽...")
        self.btn_browse.clicked.connect(self.browse_path)
        path_layout.addWidget(self.line_path)
        path_layout.addWidget(self.btn_browse)
        form_layout.addRow("影像儲存路徑:", path_layout)
        
        lbl_hint = QLabel("提示: 系統將自動在此路徑下建立 YYYYMMDD 日期資料夾")
        lbl_hint.setStyleSheet("color: gray; font-size: 11px;")
        form_layout.addRow("", lbl_hint)

        self.combo_format = QComboBox()
        self.combo_format.addItems(["PNG", "JPEG", "JPG"])
        self.combo_format.setCurrentText(self.current_settings.get("format", "PNG"))
        form_layout.addRow("影像儲存格式:", self.combo_format)

        self.line_server = QLineEdit(self.current_settings.get("server_url", "http://localhost:8000/api/intake"))
        form_layout.addRow("Server 端點 URL:", self.line_server)

        layout.addLayout(form_layout)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        self.btn_save = QPushButton("儲存設定")
        self.btn_save.setDefault(True)
        self.btn_save.clicked.connect(self.accept)
        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(self.btn_cancel)
        btn_layout.addWidget(self.btn_save)
        layout.addLayout(btn_layout)

    def browse_path(self):
        directory = QFileDialog.getExistingDirectory(self, "選擇影像儲存路徑", self.line_path.text())
        if directory:
            self.line_path.setText(directory)

    def get_settings(self):
        return {
            "base_path": self.line_path.text().strip(),
            "format": self.combo_format.currentText().upper(),
            "server_url": self.line_server.text().strip()
        }

class RegionSelector(QWidget):
    region_selected = Signal(tuple)

    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | 
                            Qt.WindowType.WindowStaysOnTopHint | 
                            Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.showFullScreen()
        self.start_pos = None
        self.current_rect = QRect()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 100))
        if not self.current_rect.isNull():
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
            painter.fillRect(self.current_rect, Qt.GlobalColor.transparent)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            painter.setPen(QPen(Qt.GlobalColor.red, 2))
            painter.drawRect(self.current_rect)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.start_pos = event.globalPos()
            self.current_rect.setTopLeft(self.start_pos)
            self.current_rect.setBottomRight(self.start_pos)

    def mouseMoveEvent(self, event):
        if self.start_pos is not None:
            self.current_rect.setBottomRight(event.globalPos())
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.start_pos is not None:
            final_rect = self.current_rect.normalized()
            region = (final_rect.x(), final_rect.y(), final_rect.width(), final_rect.height())
            if region[2] > 5 and region[3] > 5:
                self.region_selected.emit(region)
            self.close()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.close()

class ClientApp(QWidget):
    # 【新增】發送給背景 OCR Worker 的自訂信號
    image_captured = Signal(str)

    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.setWindowFlags(Qt.WindowType.WindowStaysOnTopHint)
        self.resize(480, 380)

        self.mode = "window"
        self.target_hwnd = None
        self.target_region = None
        self.capture_count = 0
        self.is_running = False
        
        self.settings = {
            "base_path": DEFAULT_CAPTURE_BASE,
            "format": "PNG", 
            "server_url": "http://localhost:8000/api/intake"
        }

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.perform_capture)

        self.setup_ui()
        self.refresh_windows()

    def setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(10)

        group_mode = QGroupBox("1. 擷取模式設定")
        layout_mode = QVBoxLayout()
        row_radio = QHBoxLayout()
        self.btn_grp = QButtonGroup(self)
        self.radio_win = QRadioButton("指定 HIS 視窗")
        self.radio_reg = QRadioButton("自訂區域選取")
        self.radio_win.setChecked(True)
        self.btn_grp.addButton(self.radio_win, 1)
        self.btn_grp.addButton(self.radio_reg, 2)
        self.btn_grp.idToggled.connect(self.on_mode_changed)
        row_radio.addWidget(self.radio_win)
        row_radio.addWidget(self.radio_reg)
        row_radio.addStretch()
        layout_mode.addLayout(row_radio)

        row_win = QHBoxLayout()
        self.combo_win = QComboBox()
        self.combo_win.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.btn_refresh = QPushButton("🔄 重整視窗")
        self.btn_refresh.clicked.connect(self.refresh_windows)
        row_win.addWidget(QLabel("目標視窗:"))
        row_win.addWidget(self.combo_win, 1)
        row_win.addWidget(self.btn_refresh)
        layout_mode.addLayout(row_win)

        row_reg = QHBoxLayout()
        self.lbl_region = QLabel("(目前尚未選取區域)")
        self.lbl_region.setStyleSheet("color: gray;")
        self.btn_pick = QPushButton("📐 開始框選區域")
        self.btn_pick.clicked.connect(self.start_region_select)
        self.btn_pick.setEnabled(False)
        row_reg.addWidget(QLabel("目標區域:"))
        row_reg.addWidget(self.lbl_region, 1)
        row_reg.addWidget(self.btn_pick)
        layout_mode.addLayout(row_reg)

        group_mode.setLayout(layout_mode)
        main_layout.addWidget(group_mode)

        group_sys = QGroupBox("2. 系統與排程參數")
        layout_sys = QVBoxLayout()
        row_sys = QHBoxLayout()
        row_sys.addWidget(QLabel("擷取頻率 (秒):"))
        self.spin_interval = QDoubleSpinBox()
        self.spin_interval.setRange(0.1, 60.0)
        self.spin_interval.setSingleStep(0.1)
        self.spin_interval.setValue(1.0)
        row_sys.addWidget(self.spin_interval)
        
        row_sys.addSpacing(20)
        row_sys.addWidget(QLabel("壓縮品質 (JPG):"))
        self.spin_quality = QDoubleSpinBox()
        self.spin_quality.setDecimals(0)
        self.spin_quality.setRange(10, 100)
        self.spin_quality.setValue(85)
        self.spin_quality.setEnabled(False)
        row_sys.addWidget(self.spin_quality)
        row_sys.addStretch()
        layout_sys.addLayout(row_sys)
        group_sys.setLayout(layout_sys)
        main_layout.addWidget(group_sys)

        layout_ctrl = QHBoxLayout()
        self.btn_start = QPushButton("▶ 開始擷取")
        self.btn_start.setMinimumHeight(45)
        self.btn_start.setStyleSheet("font-weight: bold; color: green; font-size: 14px;")
        self.btn_stop = QPushButton("⏹ 停止擷取")
        self.btn_stop.setMinimumHeight(45)
        self.btn_stop.setStyleSheet("font-size: 14px;")
        self.btn_stop.setEnabled(False)
        
        self.btn_setup = QPushButton("⚙️ 設定")
        self.btn_setup.setMinimumHeight(45)
        
        self.btn_start.clicked.connect(self.start_capture)
        self.btn_stop.clicked.connect(self.stop_capture)
        self.btn_setup.clicked.connect(self.open_settings)
        
        layout_ctrl.addWidget(self.btn_start, 2)
        layout_ctrl.addWidget(self.btn_stop, 2)
        layout_ctrl.addWidget(self.btn_setup, 1)
        main_layout.addLayout(layout_ctrl)

        main_layout.addStretch()
        self.lbl_status = QLabel("🔄 正在載入 OCR 背景模型，請稍候...")
        self.lbl_status.setStyleSheet("font-weight: bold; color: #d35400;")
        main_layout.addWidget(self.lbl_status)
        
        lbl_version = QLabel(f"版本: {APP_VERSION} | 發表時間: {APP_RELEASE_TIME}")
        lbl_version.setAlignment(Qt.AlignmentFlag.AlignRight)
        lbl_version.setStyleSheet("color: #888888; font-size: 10px;")
        main_layout.addWidget(lbl_version)

    # --- 背景 OCR 執行緒回呼 (Callbacks) ---
    @Slot()
    def on_ocr_ready(self):
        self.lbl_status.setStyleSheet("font-weight: bold; color: blue;")
        self.lbl_status.setText(f"✅ 系統就緒。圖片將存於: {self.settings['base_path']}")

    @Slot(str, str)
    def on_ocr_finished(self, json_content: str, json_path: str):
        filename = os.path.basename(json_path)
        self.lbl_status.setText(f"✅ 解析完成 | 產出: {filename}")

    @Slot(str)
    def on_ocr_error(self, error_msg: str):
        self.lbl_status.setStyleSheet("font-weight: bold; color: red;")
        self.lbl_status.setText(f"🔴 解析錯誤: {error_msg}")

    # --- 原有 UI 控制邏輯 ---
    def open_settings(self):
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec():
            self.settings = dialog.get_settings()
            if self.settings["format"] == "PNG":
                self.spin_quality.setEnabled(False)
            else:
                self.spin_quality.setEnabled(True)
            self.lbl_status.setStyleSheet("font-weight: bold; color: blue;")
            self.lbl_status.setText(f"✅ 設定已更新。基底路徑: {self.settings['base_path']}")

    def on_mode_changed(self, btn_id, checked):
        if not checked: return
        if btn_id == 1:
            self.mode = "window"
            self.combo_win.setEnabled(True)
            self.btn_refresh.setEnabled(True)
            self.btn_pick.setEnabled(False)
        else:
            self.mode = "region"
            self.combo_win.setEnabled(False)
            self.btn_refresh.setEnabled(False)
            self.btn_pick.setEnabled(True)

    def refresh_windows(self):
        self.combo_win.clear()
        windows = list_visible_windows()
        for hwnd, title in windows:
            self.combo_win.addItem(f"{hex(hwnd)} - {title[:40]}", hwnd)

    def start_region_select(self):
        self.selector = RegionSelector()
        self.selector.region_selected.connect(self.on_region_selected)

    def on_region_selected(self, region):
        self.target_region = region
        self.lbl_region.setText(f"已鎖定: (X:{region[0]}, Y:{region[1]}, W:{region[2]}, H:{region[3]})")
        self.lbl_region.setStyleSheet("color: black; font-weight: bold;")

    def start_capture(self):
        if self.mode == "window" and not self.combo_win.currentData():
            QMessageBox.warning(self, "啟動失敗", "請先選擇目標視窗。")
            return
        elif self.mode == "region" and not self.target_region:
            QMessageBox.warning(self, "啟動失敗", "請先點擊 [開始框選區域]。")
            return

        self.target_hwnd = self.combo_win.currentData() if self.mode == "window" else None
        self.is_running = True
        self.btn_start.setEnabled(False)
        self.btn_setup.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.group_ui_lock(True)
        self.capture_count = 0
        
        interval_ms = int(self.spin_interval.value() * 1000)
        self.timer.start(interval_ms)
        self.lbl_status.setStyleSheet("font-weight: bold; color: green;")
        self.lbl_status.setText(f"🟢 擷取執行中... (每 {self.spin_interval.value()} 秒)")
        self.perform_capture()

    def stop_capture(self):
        self.is_running = False
        self.timer.stop()
        self.btn_start.setEnabled(True)
        self.btn_setup.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.group_ui_lock(False)
        self.lbl_status.setStyleSheet("font-weight: bold; color: blue;")
        self.lbl_status.setText("⏹ 擷取已停止。")

    def group_ui_lock(self, locked: bool):
        self.radio_win.setEnabled(not locked)
        self.radio_reg.setEnabled(not locked)
        self.spin_interval.setEnabled(not locked)
        if self.settings["format"] != "PNG":
            self.spin_quality.setEnabled(not locked)
        if not locked:
            self.on_mode_changed(self.btn_grp.checkedId(), True)
        else:
            self.combo_win.setEnabled(False)
            self.btn_refresh.setEnabled(False)
            self.btn_pick.setEnabled(False)

    def perform_capture(self):
        if not self.is_running: return

        img = None
        if self.mode == "window":
            img = capture_window(self.target_hwnd)
        else:
            img = capture_region(self.target_region)

        if img:
            self.capture_count += 1
            date_str = datetime.now().strftime("%Y%m%d")
            save_dir = os.path.join(self.settings["base_path"], date_str)
            os.makedirs(save_dir, exist_ok=True)

            img_format = self.settings["format"]
            ext = img_format.lower()
            timestamp = datetime.now().strftime("%H%M%S_%f")[:10]
            filename = f"cap_{timestamp}.{ext}"
            filepath = os.path.join(save_dir, filename)
            
            try:
                if img_format in ["JPEG", "JPG"]:
                    q = int(self.spin_quality.value())
                    img.save(filepath, img_format, quality=q, optimize=True)
                elif img_format == "PNG":
                    img.save(filepath, "PNG", optimize=True)
                
                # 【關鍵修改】發送圖片路徑給背景 OCR Worker
                self.image_captured.emit(filepath)
                
            except Exception as e:
                self.lbl_status.setStyleSheet("font-weight: bold; color: red;")
                self.lbl_status.setText(f"🔴 [錯誤] 儲存失敗: {str(e)}")
        else:
            self.lbl_status.setStyleSheet("font-weight: bold; color: #d35400;")
            self.lbl_status.setText("🟡 [警告] 擷取空畫面，視窗可能已關閉。")