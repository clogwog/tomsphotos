"""Left tree panel. Lazy QAbstractItemModel backed by the filesystem (with
media counts from the index DB when available)."""

import os

from PySide6.QtCore import QAbstractItemModel, QModelIndex, Qt, Signal
from PySide6.QtWidgets import QTreeView

from config import SOURCE_ROOT, TREE_WIDTH
from scanner import is_ignored_dir


class TreeNode:
    __slots__ = ("path", "name", "media_count", "dir_count", "parent", "children", "loaded")

    def __init__(self, path, name, media_count, dir_count, parent=None):
        self.path = path
        self.name = name
        self.media_count = media_count
        self.dir_count = dir_count
        self.parent = parent
        self.children = []
        self.loaded = False


def _list_subdirs(path):
    """Direct subdirectories of path (real filesystem), sorted by name."""
    out = []
    try:
        with os.scandir(path) as it:
            for entry in it:
                try:
                    if entry.is_dir(follow_symlinks=True) and not is_ignored_dir(entry.name):
                        out.append(entry)
                except OSError:
                    continue
    except OSError:
        pass
    out.sort(key=lambda e: e.name)
    return out


class TreeModel(QAbstractItemModel):
    def __init__(self, db, root=SOURCE_ROOT, parent=None):
        super().__init__(parent)
        self.db = db
        self.root_path = root
        self._root = TreeNode(root, os.path.basename(root) or root, 0, 0)
        self._load_root_info()

    def _load_root_info(self):
        row = self.db.get_dir(self.root_path)
        if row:
            self._root.media_count = row[3]
            self._root.dir_count = row[4]

    # -- model API -------------------------------------------------------
    def index(self, row, column, parent=QModelIndex()):
        if not self.hasIndex(row, column, parent):
            return QModelIndex()
        pnode = self._node(parent)
        if row >= len(pnode.children):
            return QModelIndex()
        return self.createIndex(row, column, pnode.children[row])

    def parent(self, index):
        if not index.isValid():
            return QModelIndex()
        node = self._node(index)
        if node is self._root or node.parent is self._root:
            return QModelIndex()
        pnode = node.parent
        return self.createIndex(pnode.parent.children.index(pnode), 0, pnode)

    def rowCount(self, parent=QModelIndex()):
        node = self._node(parent)
        return len(node.children)

    def columnCount(self, parent=QModelIndex()):
        return 1

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        node = self._node(index)
        if role == Qt.DisplayRole:
            if node.media_count:
                return f"{node.name} ({node.media_count:,})"
            return node.name
        if role == Qt.ToolTipRole:
            return node.path
        if role == Qt.UserRole:
            return node.path
        return None

    def flags(self, index):
        if not index.isValid():
            return Qt.NoItemFlags
        return Qt.ItemIsEnabled | Qt.ItemIsSelectable

    def canFetchMore(self, parent):
        node = self._node(parent)
        return not node.loaded and node.dir_count > 0

    def fetchMore(self, parent):
        node = self._node(parent)
        if node.loaded:
            return
        node.loaded = True
        children = []
        for entry in _list_subdirs(node.path):
            row = self.db.get_dir(entry.path)
            media = row[3] if row else 0
            subdirs = row[4] if row else 0
            children.append(TreeNode(entry.path, entry.name, media, subdirs, parent=node))
        self.beginInsertRows(parent, 0, max(0, len(children) - 1))
        node.children = children
        self.endInsertRows()

    def hasChildren(self, parent):
        node = self._node(parent)
        if node.loaded:
            return len(node.children) > 0
        if node.dir_count > 0:
            return True
        # Not in the index yet (freshly configured directory): ask the
        # filesystem so the tree is populated immediately.
        return bool(_list_subdirs(node.path))

    # -- helpers ---------------------------------------------------------
    def _node(self, index):
        if not index.isValid():
            return self._root
        return index.internalPointer()

    def refresh_root(self):
        """Reload root children (year dirs) after a scan."""
        self.beginResetModel()
        self._root = TreeNode(self.root_path, os.path.basename(self.root_path) or self.root_path, 0, 0)
        self._load_root_info()
        self.endResetModel()
        self.fetchMore(QModelIndex())

    def ensure_loaded(self, path):
        """Expand path nodes so the directory is visible in the tree."""
        rel = os.path.relpath(path, self.root_path)
        parts = [] if rel == "." else rel.split(os.sep)
        parent_idx = QModelIndex()
        node = self._root
        for part in parts:
            self.fetchMore(parent_idx)
            found = None
            for i, child in enumerate(node.children):
                if child.name == part:
                    found = (i, child)
                    break
            if found is None:
                return
            i, child = found
            parent_idx = self.index(i, 0, parent_idx)
            node = child
        return parent_idx


class TreePanel(QTreeView):
    directory_selected = Signal(str)

    def __init__(self, db, root=SOURCE_ROOT, parent=None):
        super().__init__(parent)
        self.db = db
        self.model = TreeModel(db, root=root)
        self.setModel(self.model)
        self.setFixedWidth(TREE_WIDTH)
        self.setHeaderHidden(True)
        self.setUniformRowHeights(True)
        self.setAnimated(False)
        self.setExpandsOnDoubleClick(True)
        self.setIndentation(14)
        self.selectionModel().selectionChanged.connect(self._on_selection)

    def _on_selection(self, selected, deselected):
        idxs = selected.indexes()
        if not idxs:
            return
        path = idxs[0].data(Qt.UserRole)
        if path:
            self.directory_selected.emit(path)

    def select_path(self, path):
        idx = self.model.ensure_loaded(path)
        if idx is not None:
            self.setCurrentIndex(idx)
            self.scrollTo(idx)

    def set_root(self, path):
        """Point the tree at a new photo directory and rebuild its model."""
        self.model = TreeModel(self.db, root=path)
        self.setModel(self.model)
        self.selectionModel().selectionChanged.connect(self._on_selection)
        self.model.fetchMore(QModelIndex())
        self.expandToDepth(0)