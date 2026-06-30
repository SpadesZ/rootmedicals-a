# 路徑: C:\Users\88696\11_projects\rootmedicals-a\llmxx-client\run_main.py
# 版本: v0.2
# 更版時間: 2026-05-04 10:05
# 說明: 
#   1. 實作 Lazy Import (延遲載入)，將 PyTorch/EasyOCR 的龐大 import 移至背景執行緒。
#   2. 確保 UI 介面能瞬間啟動，徹底解決命令列疑似卡死的問題。
# ----------------------------------------------------------------------------------------------------

import sys
import os
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QThread, QObject, Signal, Slot

# 僅在頂層載入輕量級的 UI 模組
from capture import ClientApp

# =========================================================================================
# 背景工作執行緒：OCR Worker
# =========================================================================================
class OCRWorker(QObject):
    ready = Signal()                      
    finished = Signal(str, str)           
    error = Signal(str)                   

    def __init__(self):
        super().__init__()
        self.recognizer = None

    @Slot()
    def init_model(self):
        """
        初始化 OCR 模型。
        【關鍵修改】將 import 移入此處 (Lazy Import)，確保龐大的 PyTorch 不會卡住 UI 啟動。
        """
        try:
            # 延遲載入：只有在背景執行緒啟動後，才開始將 EasyOCR 載入記憶體
            from recognize import HISRecognizer
            
            # 初始化模型 (初次執行若需下載權重檔也會在這裡安全地阻塞背景執行緒)
            self.recognizer = HISRecognizer(gpu=False)
            self.ready.emit()
        except Exception as e:
            self.error.emit(f"OCR 模型載入失敗: {str(e)}")

    @Slot(str)
    def process_image(self, image_path: str):
        if not self.recognizer:
            self.error.emit("OCR 引擎尚未就緒，請稍候...")
            return

        try:
            json_content, json_path = self.recognizer.process_image(image_path)
            self.finished.emit(json_content, json_path)
        except Exception as e:
            self.error.emit(f"OCR 辨識發生錯誤: {str(e)}")

# =========================================================================================
# 系統進入點
# =========================================================================================
def main():
    app = QApplication(sys.argv)
    
    font = app.font()
    font.setPointSize(10)
    app.setFont(font)

    client_ui = ClientApp()

    ocr_worker = OCRWorker()
    ocr_thread = QThread()
    ocr_worker.moveToThread(ocr_thread)

    # 綁定信號
    ocr_thread.started.connect(ocr_worker.init_model)
    client_ui.image_captured.connect(ocr_worker.process_image)
    ocr_worker.ready.connect(client_ui.on_ocr_ready)
    ocr_worker.finished.connect(client_ui.on_ocr_finished)
    ocr_worker.error.connect(client_ui.on_ocr_error)

    # 確保關閉時安全釋放執行緒
    app.aboutToQuit.connect(ocr_thread.quit)
    app.aboutToQuit.connect(ocr_thread.wait)

    # 啟動背景執行緒與顯示畫面
    ocr_thread.start()
    client_ui.show()

    sys.exit(app.exec())

if __name__ == "__main__":
    main()