"""Python highlighting with persistent multiline string state."""
import builtins
import keyword
import re
from PyQt5 import QtGui as G


class PythonHighlighter(G.QSyntaxHighlighter):
    tokens = re.compile(
        r"#[^\n]*|\"\"\"|'''|(?:[rRuUbBfF]{0,2})(?:\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*')"
        r"|\b(?:0[xX][\da-fA-F]+|\d+(?:\.\d*)?(?:[eE][+-]?\d+)?j?)\b"
        r"|\b[A-Za-z_]\w*\b|[-+*/%=<>!&|^~:@]+"
    )

    def __init__(self, document):
        super().__init__(document)
        self.styles = {}
        for name, color in dict(keyword="#7c3aed", string="#15803d", comment="#64748b",
                                number="#b45309", builtin="#0369a1", function="#1d4ed8",
                                operator="#be185d").items():
            fmt = G.QTextCharFormat(); fmt.setForeground(G.QColor(color))
            if name == "keyword": fmt.setFontWeight(G.QFont.Bold)
            self.styles[name] = fmt

    def highlightBlock(self, text):
        self.setCurrentBlockState(0)
        pos = 0
        previous = self.previousBlockState()
        if previous in (1, 2):
            delimiter = "'''" if previous == 1 else '"""'
            end = text.find(delimiter)
            if end < 0:
                self.setFormat(0, len(text), self.styles["string"])
                self.setCurrentBlockState(previous); return
            pos = end + 3
            self.setFormat(0, pos, self.styles["string"])
        while pos < len(text):
            match = self.tokens.search(text, pos)
            if match is None: break
            token = match.group(); start, end = match.span(); style = None
            if token in ("'''", '"""'):
                close = text.find(token, end)
                if close < 0:
                    self.setFormat(start, len(text)-start, self.styles["string"])
                    self.setCurrentBlockState(1 if token == "'''" else 2); return
                end = close + 3; style = "string"
            elif token.startswith('#'): style = "comment"
            elif '"' in token or "'" in token: style = "string"
            elif keyword.iskeyword(token) or token in ("match", "case"): style = "keyword"
            elif token[0].isdigit(): style = "number"
            elif token in vars(builtins): style = "builtin"
            elif text[end:].lstrip().startswith('('): style = "function"
            elif not token[0].isalnum() and token[0] != '_': style = "operator"
            if style: self.setFormat(start, end-start, self.styles[style])
            pos = end
