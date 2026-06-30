# 路徑: \lavadesign\app\core_cad\3analysisum\context_combination.py
# 版本: v0.1
# 更版時間: 2026-05-05
# 說明: 負責 3analysisum 階段的「資料聚合總線 (Data Aggregator)」。
# [Feature v0.1]: 封裝 dispatch_doc_recognition_task，收集 doc_recognition 文本、表格與圖片送交 Task 1。
# [Feature v0.1]: 封裝 dispatch_multimodal_vision_task，配對 dxf 摘要與 PNG 預覽，發起 Task 3 視覺認知請求。
# [Feature v0.1]: 封裝 dispatch_global_review_task，聚合所有 doc 與 raw vision 摘要，發射 Task 4 總結。
# [遵守定律]: 全量獨立代碼，確保邏輯清晰且具備極高容錯率。

import os
import json
import traceback
from typing import Dict, Any

# 延遲載入 LLM 任務，避免循環依賴
from app.lava.matching_tasks.task1_doc_parser import execute_doc_summary
from app.lava.matching_tasks.task3_rawvision_parser import execute_vision_analysis
from app.lava.matching_tasks.task4_allreview_parser import execute_global_review

class ContextCombiner:
    def __init__(self, pid: str):
        self.pid = pid
        self.base_dir = os.path.abspath(os.getcwd())
        self.project_dir = os.path.join(self.base_dir, 'data', 'project', self.pid)
        self.step3_dir = os.path.join(self.project_dir, 'step3_context')

    def dispatch_doc_recognition_task(self, filename: str) -> Dict[str, Any]:
        """打包 doc_recognition 下的資源，送交 task1_doc_parser"""
        try:
            doc_rec_dir = os.path.join(self.step3_dir, 'doc_recognition')
            content_dir = os.path.join(doc_rec_dir, 'content')
            table_dir = os.path.join(doc_rec_dir, 'table')
            figure_dir = os.path.join(doc_rec_dir, 'figure')

            # 確保目錄存在
            for d in [content_dir, table_dir, figure_dir]:
                os.makedirs(d, exist_ok=True)

            print(f"[3analysisum] 正在聚合文件 [{filename}] 的文本、表格與圖片資源...")
            
            # 發起 Task 1 (文件摘要)
            result = execute_doc_summary(
                pid=self.pid, 
                filename=filename,
                content_path=content_dir,
                table_path=table_dir,
                figure_path=figure_dir
            )
            return {"ok": True, "data": result}
        except Exception as e:
            traceback.print_exc()
            return {"ok": False, "error": f"聚合文件辨識資源失敗: {str(e)}"}

    def dispatch_multimodal_vision_task(self, filename: str) -> Dict[str, Any]:
        """配對 dxf 幾何摘要與 PNG 圖檔，送交 task3_rawvision_parser"""
        try:
            base_name = os.path.splitext(filename)[0]
            dxf_summary_path = os.path.join(self.step3_dir, 'demo', 'analysis', f"{base_name}_dwgxf_analysisum.json")
            png_view_path = os.path.join(self.step3_dir, 'demo', 'view', f"{base_name}.png")

            if not os.path.exists(dxf_summary_path) or not os.path.exists(png_view_path):
                return {"ok": False, "error": f"無法配對 {base_name} 的 DXF 摘要與 PNG 預覽圖，任務中止。"}

            print(f"[3analysisum] 成功配對 [{base_name}] 幾何與視圖，觸發 MLLM 視覺分析...")
            
            # 發起 Task 3 (多模態視覺認知)
            result = execute_vision_analysis(
                pid=self.pid,
                dxf_summary_path=dxf_summary_path,
                png_path=png_view_path,
                base_name=base_name
            )
            return {"ok": True, "data": result}
        except Exception as e:
            traceback.print_exc()
            return {"ok": False, "error": f"發派多模態視覺任務失敗: {str(e)}"}

    def dispatch_global_review_task(self) -> Dict[str, Any]:
        """收集所有 doc 摘要與 vision raw 摘要，送交 task4_allreview_parser"""
        try:
            analysis_dir = os.path.join(self.step3_dir, 'demo', 'analysis')
            raw_dir = os.path.join(self.step3_dir, 'raw')
            
            doc_summaries = []
            vision_summaries = []

            # 掃描並載入 Task 1 產出的 doc 摘要
            if os.path.exists(analysis_dir):
                for f in os.listdir(analysis_dir):
                    if f.endswith("doc_analysisum.json"):
                        doc_summaries.append(os.path.join(analysis_dir, f))
            
            # 掃描並載入 Task 3 產出的 vision 摘要
            if os.path.exists(raw_dir):
                for f in os.listdir(raw_dir):
                    if f.endswith("_visionraw_sum.json"):
                        vision_summaries.append(os.path.join(raw_dir, f))

            if not doc_summaries and not vision_summaries:
                return {"ok": False, "error": "全域總審查缺乏基礎摘要資料。"}

            print(f"[3analysisum] 匯聚 {len(doc_summaries)} 份文件摘要與 {len(vision_summaries)} 份視覺摘要，發起總審查...")
            
            # 發起 Task 4 (全域總結)
            result = execute_global_review(
                pid=self.pid,
                doc_summaries=doc_summaries,
                vision_summaries=vision_summaries
            )
            return {"ok": True, "data": result}
        except Exception as e:
            traceback.print_exc()
            return {"ok": False, "error": f"發派全域總結任務失敗: {str(e)}"}