from typing import List, Optional
from PySide6.QtWidgets import QComboBox, QCompleter
from PySide6.QtCore import Qt


class DatabaseComboBox(QComboBox):
    """支持下拉选择与模糊输入过滤的数据库选择控件"""

    def __init__(self, parent=None, placeholder: str = ""):
        super().__init__(parent)
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.NoInsert)
        if placeholder:
            self.setPlaceholderText(placeholder)
            self.setCurrentIndex(-1)

        self._setup_completer()

    def _setup_completer(self):
        """配置模糊包含匹配补全器"""
        comp = self.completer()
        if comp:
            comp.setFilterMode(Qt.MatchContains)
            comp.setCaseSensitivity(Qt.CaseInsensitive)
            comp.setCompletionMode(QCompleter.PopupCompletion)

    def setModel(self, model):
        super().setModel(model)
        self._setup_completer()

    def addItems(self, texts: List[str]):
        super().addItems(texts)
        self._setup_completer()

    def set_databases(self, dbs: List[str], default_text: Optional[str] = None):
        """更新数据库列表并平滑保持当前输入/选中状态

        :param dbs: 数据库名称列表
        :param default_text: 指定选中的文本，为 None 时尝试保留原输入
        """
        target_text = default_text if default_text is not None else self.currentText().strip()

        self.blockSignals(True)
        self.clear()
        if dbs:
            self.addItems(dbs)
            self._setup_completer()

            if target_text:
                idx = self.findText(target_text)
                if idx >= 0:
                    self.setCurrentIndex(idx)
                else:
                    self.setEditText(target_text)
            else:
                self.setCurrentIndex(-1)
        else:
            if target_text:
                self.setEditText(target_text)
            else:
                self.setCurrentIndex(-1)
        self.blockSignals(False)
